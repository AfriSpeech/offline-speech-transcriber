#!/usr/bin/env python3
"""LEIN Volunteer Submission Ingestion & HF Training Dataset Builder.

Takes volunteer submission files (ZIP or CSV), extracts verified transcriptions,
maps each clip back to the original high-quality audio in the source dataset,
and appends verified speech-text pairs to the target training dataset on Hugging Face.

Usage:
    python ingest_submissions.py \
        --submission path/to/submission.zip (or path/to/submissions_dir) \
        --source-dataset michsethowusu/liberian-english-speech \
        --target-dataset michsethowusu/liberian-english-speech-transcribed
"""

import os
import io
import sys
import csv
import json
import shutil
import zipfile
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import HfApi, hf_hub_download, create_repo

DEFAULT_SOURCE_DATASET = "AfriSpeech/liberian-english-speech"
DEFAULT_TARGET_DATASET = "AfriSpeech/liberian-english-speech-transcribed"
REGISTRY_REPO = "AfriSpeech/liberian-english-volunteer-registry"
INGESTION_MANIFEST_NAME = "ingested_manifest.json"


class AudioSourceIndex:
    """Fast index mapping clip_id -> (parquet_filename, row_index)."""

    def __init__(self, source_repo: str, cache_dir: Path, api: HfApi):
        self.source_repo = source_repo
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api = api
        self.index_file = self.cache_dir / "clip_to_parquet_index.json"
        self.index: Dict[str, Dict[str, Any]] = {}
        self._load_or_build()

    def _load_or_build(self):
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    self.index = json.load(f)
                print(f"[AudioSourceIndex] Loaded cached index with {len(self.index):,} clips.")
                return
            except Exception as e:
                print(f"[AudioSourceIndex] Rebuilding index due to: {e}")

        print(f"[AudioSourceIndex] Building source audio index from {self.source_repo}...")
        info = self.api.dataset_info(self.source_repo)
        parquets = sorted([s.rfilename for s in info.siblings if s.rfilename.endswith(".parquet")])

        for p_idx, p in enumerate(parquets):
            local_fp = hf_hub_download(repo_id=self.source_repo, repo_type="dataset", filename=p)
            # Read only id column to be extremely fast and lightweight
            table = pq.read_table(local_fp, columns=["id"])
            ids = table["id"].to_pylist()
            for r_idx, cid in enumerate(ids):
                self.index[cid] = {"parquet": p, "row": r_idx}
            if (p_idx + 1) % 15 == 0 or (p_idx + 1) == len(parquets):
                print(f"  Indexed {p_idx + 1}/{len(parquets)} parquets ({len(self.index):,} clips)...")

        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(self.index, f)
        print(f"[AudioSourceIndex] Finished indexing {len(self.index):,} clips. Saved to {self.index_file}")

    def fetch_clip_audio_data(self, clip_id: str) -> Optional[Dict[str, Any]]:
        loc = self.index.get(clip_id)
        if not loc:
            return None
        parquet_rel = loc["parquet"]
        row_idx = loc["row"]

        local_fp = hf_hub_download(repo_id=self.source_repo, repo_type="dataset", filename=parquet_rel)
        table = pq.read_table(local_fp)
        row = table.slice(row_idx, 1)

        audio_struct = row["audio"][0].as_py()
        return {
            "id": row["id"][0].as_py(),
            "audio_bytes": audio_struct.get("bytes"),
            "audio_path": audio_struct.get("path"),
            "duration": row["duration"][0].as_py(),
            "sample_rate": row["sample_rate"][0].as_py() if "sample_rate" in row.column_names else 24000,
            "source": row["source"][0].as_py() if "source" in row.column_names else "",
            "raw_text": row["text"][0].as_py() if "text" in row.column_names else ""
        }


ENCRYPTION_KEY = 0x5A


def decode_registry_line(line: str) -> dict:
    import base64
    try:
        clean = line.strip().replace('-', '+').replace('_', '/')
        clean += '=' * (-len(clean) % 4)
        masked = base64.b64decode(clean)
        raw = bytes([b ^ ENCRYPTION_KEY for b in masked]).decode('utf-8')
        return json.loads(raw)
    except Exception:
        return {}


def load_volunteer_registry(api: HfApi) -> Dict[str, Dict[str, Any]]:
    """Loads active volunteer registrations from registry mapped by bundle and package names."""
    registry = {}
    try:
        local_fp = hf_hub_download(repo_id=REGISTRY_REPO, repo_type="dataset", filename="registrations_encoded.jsonl")
        with open(local_fp, "r", encoding="utf-8") as f:
            for line in f:
                rec = decode_registry_line(line)
                if rec and rec.get("status") != "cancelled" and rec.get("name"):
                    bfile = rec.get("bundle_file", "")
                    if bfile:
                        registry[bfile.replace(".zip", "")] = rec
                    code = rec.get("volunteer_code", "")
                    if code:
                        registry[code] = rec
                    for p in rec.get("assigned_packages", []):
                        fn = Path(p).stem
                        registry[fn] = rec
    except Exception as e:
        print(f"[Warning] Could not load volunteer registry: {e}")
    return registry


def parse_submission_file(file_path: Path, registry: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Parses a volunteer submission ZIP or CSV file into a list of transcript records."""
    records = []
    meta = {}

    if file_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(file_path, "r") as zf:
            meta_name = next((n for n in zf.namelist() if n.endswith("submission_meta.json")), None)
            if meta_name:
                try:
                    meta = json.loads(zf.read(meta_name).decode("utf-8"))
                except Exception:
                    pass

            csv_name = next((n for n in zf.namelist() if n.endswith("transcripts.csv")), None)
            if not csv_name:
                csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), None)

            if csv_name:
                content = zf.read(csv_name).decode("utf-8")
                reader = csv.DictReader(io.StringIO(content))
                for row in reader:
                    records.append(row)
    elif file_path.suffix.lower() == ".csv":
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)

    # Cross-reference with registry if package name or code is known
    pkg_name = meta.get("package_name", "")
    code = meta.get("volunteer_code", "")
    reg_match = None
    if registry:
        reg_match = registry.get(pkg_name) or registry.get(code) or registry.get(Path(file_path).stem)

    for r in records:
        if reg_match:
            if not r.get("volunteer_name") or "Volunteer" in r.get("volunteer_name", ""):
                r["volunteer_name"] = reg_match.get("name", r.get("volunteer_name", ""))
            if not r.get("volunteer_email"):
                r["volunteer_email"] = reg_match.get("email", "")
            if not r.get("volunteer_phone"):
                r["volunteer_phone"] = reg_match.get("phone", "")
            if not r.get("volunteer_code"):
                r["volunteer_code"] = reg_match.get("volunteer_code", code)
        else:
            if not r.get("volunteer_name") and meta.get("volunteer_name"):
                r["volunteer_name"] = meta["volunteer_name"]
            if not r.get("volunteer_email") and meta.get("volunteer_email"):
                r["volunteer_email"] = meta["volunteer_email"]
            if not r.get("volunteer_phone") and meta.get("volunteer_phone"):
                r["volunteer_phone"] = meta["volunteer_phone"]
            if not r.get("volunteer_code") and meta.get("volunteer_code"):
                r["volunteer_code"] = meta["volunteer_code"]

    return records


def ingest_submissions(
    submissions_path: Path,
    source_dataset: str = DEFAULT_SOURCE_DATASET,
    target_dataset: str = DEFAULT_TARGET_DATASET,
    work_dir: Path = Path("ingestion_workspace"),
    hf_token: Optional[str] = None
):
    """Processes submissions and appends verified clips with source audio to the training dataset."""
    api = HfApi(token=hf_token)
    work_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("LEIN VOLUNTEER SUBMISSION INGESTION & TRAINING DATASET BUILDER")
    print(f"Source Audio Dataset: {source_dataset}")
    print(f"Target Training Repo: {target_dataset}")
    print(f"Submissions Input:    {submissions_path}")
    print("=" * 70)

    # 1. Collect submission files
    files_to_process = []
    if submissions_path.is_file():
        files_to_process.append(submissions_path)
    elif submissions_path.is_dir():
        for p in submissions_path.glob("**/*"):
            if p.suffix.lower() in [".zip", ".csv"] and not p.name.startswith("."):
                files_to_process.append(p)

    if not files_to_process:
        print(f"[Error] No .zip or .csv submission files found in {submissions_path}.")
        return

    print(f"[Ingest] Found {len(files_to_process)} submission file(s) to process.")

    # 2. Load Ingestion Manifest (tracking clips already ingested into training dataset)
    manifest_file = work_dir / INGESTION_MANIFEST_NAME
    ingested_ids = set()
    total_ingested_batches = 0

    try:
        remote_manifest = hf_hub_download(repo_id=target_dataset, repo_type="dataset", filename=INGESTION_MANIFEST_NAME)
        with open(remote_manifest, "r", encoding="utf-8") as f:
            mdata = json.load(f)
            ingested_ids = set(mdata.get("ingested_clip_ids", []))
            total_ingested_batches = mdata.get("total_batches", 0)
        print(f"[Manifest] Downloaded existing manifest: {len(ingested_ids):,} verified clips already in {target_dataset}.")
    except Exception:
        if manifest_file.exists():
            with open(manifest_file, "r", encoding="utf-8") as f:
                mdata = json.load(f)
                ingested_ids = set(mdata.get("ingested_clip_ids", []))
                total_ingested_batches = mdata.get("total_batches", 0)
            print(f"[Manifest] Loaded local manifest: {len(ingested_ids):,} verified clips.")
        else:
            print("[Manifest] Starting with empty target manifest.")

    # 3. Load active volunteer registry for automatic profile decoding
    registry = load_volunteer_registry(api)
    print(f"[Registry] Loaded {len(registry)} active volunteer registrations for automatic identification.")

    # 4. Parse all submission records
    all_new_records = []
    for sf in files_to_process:
        print(f"  Reading submission: {sf.name}...")
        recs = parse_submission_file(sf, registry=registry)
        new_count = 0
        for r in recs:
            cid = r.get("clip_id") or Path(r.get("audio_filename", "")).stem
            txt = (r.get("transcript") or "").strip()
            status = r.get("status", "completed")

            # Check validity: non-empty verified text and not yet ingested
            if cid and txt and cid not in ingested_ids:
                r["resolved_clip_id"] = cid
                r["verified_text"] = txt
                r["clean_status"] = status
                all_new_records.append(r)
                new_count += 1

        print(f"    --> {new_count} new verified clips from this file.")

    if not all_new_records:
        print("\n[Ingest] All clips from these submissions are already ingested into the target dataset!")
        return

    print(f"\n[Ingest] Total unique verified clips to ingest: {len(all_new_records):,}")

    # 4. Initialize Source Audio Index
    source_index = AudioSourceIndex(source_dataset, cache_dir=work_dir / "source_cache", api=api)

    # 5. Fetch Original High-Quality Audio & Build Parquet Rows
    print("[Ingest] Merging verified transcripts with original source FLAC audio...")
    ids_list = []
    audio_struct_list = []
    text_list = []
    original_text_list = []
    duration_list = []
    sample_rate_list = []
    source_list = []
    volunteer_name_list = []
    volunteer_email_list = []
    volunteer_code_list = []
    status_list = []
    timestamp_list = []

    matched_count = 0
    for r in all_new_records:
        cid = r["resolved_clip_id"]
        source_data = source_index.fetch_clip_audio_data(cid)
        if not source_data:
            print(f"  [Warning] Clip {cid} not found in source dataset. Skipping.")
            continue

        ids_list.append(cid)
        audio_struct_list.append({
            "bytes": source_data["audio_bytes"],
            "path": source_data["audio_path"]
        })
        text_list.append(r["verified_text"])
        original_text_list.append(source_data["raw_text"])
        duration_list.append(float(source_data["duration"]))
        sample_rate_list.append(int(source_data["sample_rate"]))
        source_list.append(source_data["source"])
        volunteer_name_list.append(r.get("volunteer_name", "Anonymous"))
        volunteer_email_list.append(r.get("volunteer_email", ""))
        volunteer_code_list.append(r.get("volunteer_code", ""))
        status_list.append(r["clean_status"])
        timestamp_list.append(r.get("timestamp") or datetime.utcnow().isoformat() + "Z")

        ingested_ids.add(cid)
        matched_count += 1

    print(f"[Ingest] Successfully mapped {matched_count:,} clips to original source audio!")

    # 6. Build Parquet Table
    audio_type = pa.struct([
        ("bytes", pa.binary()),
        ("path", pa.string())
    ])

    batch_table = pa.Table.from_arrays([
        pa.array(ids_list, type=pa.string()),
        pa.array(audio_struct_list, type=audio_type),
        pa.array(text_list, type=pa.string()),
        pa.array(original_text_list, type=pa.string()),
        pa.array(duration_list, type=pa.float64()),
        pa.array(sample_rate_list, type=pa.int64()),
        pa.array(source_list, type=pa.string()),
        pa.array(volunteer_name_list, type=pa.string()),
        pa.array(volunteer_email_list, type=pa.string()),
        pa.array(volunteer_code_list, type=pa.string()),
        pa.array(status_list, type=pa.string()),
        pa.array(timestamp_list, type=pa.string())
    ], names=[
        "id", "audio", "text", "original_text", "duration", "sample_rate",
        "source", "volunteer_name", "volunteer_email", "volunteer_code", "status", "verified_at"
    ])

    # 7. Write Sharded Parquet
    batch_num = total_ingested_batches + 1
    new_parquet_filename = f"train-verified-{batch_num:05d}.parquet"
    out_parquet_path = work_dir / new_parquet_filename
    pq.write_table(batch_table, out_parquet_path, compression="snappy")
    print(f"[Ingest] Generated new verified training shard: {out_parquet_path.name} ({out_parquet_path.stat().st_size / (1024**2):.1f} MB)")

    # 8. Save updated manifest
    manifest_data = {
        "version": 1,
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "total_clips": len(ingested_ids),
        "total_batches": batch_num,
        "source_dataset": source_dataset,
        "ingested_clip_ids": sorted(list(ingested_ids))
    }
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # 9. Push to Hugging Face
    print(f"[Hub] Ensuring target dataset exists: {target_dataset}...")
    create_repo(repo_id=target_dataset, repo_type="dataset", exist_ok=True, token=hf_token)

    print(f"[Hub] Uploading new training shard {new_parquet_filename} to {target_dataset}...")
    api.upload_file(
        path_or_fileobj=str(out_parquet_path),
        path_in_repo=f"data/{new_parquet_filename}",
        repo_id=target_dataset,
        repo_type="dataset",
        commit_message=f"Ingest verified submission batch #{batch_num} ({matched_count} clips)"
    )

    print(f"[Hub] Uploading updated ingestion manifest...")
    api.upload_file(
        path_or_fileobj=str(manifest_file),
        path_in_repo=INGESTION_MANIFEST_NAME,
        repo_id=target_dataset,
        repo_type="dataset",
        commit_message=f"Update ingestion manifest ({len(ingested_ids):,} total verified clips)"
    )

    # Generate and upload README for training dataset
    readme_content = f"""---
license: cc-by-4.0
task_categories:
- automatic-speech-recognition
- text-to-speech
language:
- lir
- en
pretty_name: Liberian English Human-Verified Speech Dataset
tags:
- speech
- audio
- liberian-english
- kolokwa
- verified
- high-quality
configs:
- config_name: default
  data_files:
  - split: train
    path: data/*.parquet
---

# Liberian English Human-Verified Speech Dataset

High-fidelity speech-text dataset in **Liberian English (`en-LR` / `lir`)**, featuring human-verified transcriptions produced by community volunteers.

- **Audio Fidelity**: Full-quality 24kHz FLAC enhanced audio (identical to [`{source_dataset}`](https://huggingface.co/datasets/{source_dataset})).
- **Transcriptions**: Human-verified and corrected using the LEIN Speech Transcriber Studio.
- **Current Shards**: {batch_num} batch shards.
- **Total Verified Clips**: {len(ingested_ids):,} clips.
"""
    readme_path = work_dir / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)

    api.upload_file(
        path_or_fileobj=str(readme_path),
        path_in_repo="README.md",
        repo_id=target_dataset,
        repo_type="dataset",
        commit_message="Update dataset README with verified clip statistics"
    )

    print(f"\n[Success] Verified clips ingested successfully into training dataset!")
    print(f"          Dataset URL: https://huggingface.co/datasets/{target_dataset}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest volunteer submissions into HF training dataset")
    parser.add_argument("--submission", required=True, help="Path to volunteer submission .zip or .csv (or directory of submissions)")
    parser.add_argument("--source-dataset", default=DEFAULT_SOURCE_DATASET, help="Source HF dataset with original 24kHz audio")
    parser.add_argument("--target-dataset", default=DEFAULT_TARGET_DATASET, help="Target HF dataset for human-verified speech")
    parser.add_argument("--work-dir", default="ingestion_workspace", help="Local workspace directory")

    args = parser.parse_args()
    ingest_submissions(
        submissions_path=Path(args.submission),
        source_dataset=args.source_dataset,
        target_dataset=args.target_dataset,
        work_dir=Path(args.work_dir)
    )

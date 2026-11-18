#!/usr/bin/env python3
"""LEIN Speech Dataset Volunteer Packaging Pipeline.

Splits Hugging Face audio datasets into daily workload packages for volunteers,
bundles each day with the offline Python speech transcriber GUI, zips them,
and uploads them to Hugging Face.

Maintains a persistent manifest (locally and in the HF repo) tracking all
allocated clip IDs so subsequent runs will never reuse clips already packaged.
"""

import os
import sys
import io
import csv
import json
import time
import shutil
import zipfile
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

import subprocess
from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
from huggingface_hub import HfApi, hf_hub_download, create_repo, upload_folder

DEFAULT_SOURCE_REPO = "michsethowusu/liberian-english-speech"
DEFAULT_TARGET_REPO = "michsethowusu/liberian-english-speech-packages"
MANIFEST_FILENAME = "manifest.json"


def upload_file_with_retry(api: HfApi, path_or_fileobj: Any, path_in_repo: str, repo_id: str, max_retries: int = 4):
    for attempt in range(1, max_retries + 1):
        try:
            api.upload_file(
                path_or_fileobj=path_or_fileobj,
                path_in_repo=path_in_repo,
                repo_id=repo_id,
                repo_type="dataset"
            )
            return True
        except Exception as e:
            print(f"    [Upload Warning] Attempt {attempt}/{max_retries} failed for {path_in_repo}: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
            else:
                print(f"    [Upload Error] All retries failed for {path_in_repo}.")
                raise


class ManifestManager:
    """Tracks packaged clip IDs and package metadata to prevent duplicate assignments."""

    def __init__(self, manifest_path: Path, target_repo: Optional[str] = None, api: Optional[HfApi] = None, reset: bool = False):
        self.manifest_path = manifest_path
        self.target_repo = target_repo
        self.api = api or HfApi()
        self.seen_clip_ids: Set[str] = set()
        self.packages_meta: List[Dict[str, Any]] = []
        if not reset:
            self._load()
        else:
            print("[Manifest] Force repackage requested: starting fresh.")

    def _load(self):
        # 1. Try local manifest file
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.seen_clip_ids = set(data.get("packaged_clip_ids", []))
                    self.packages_meta = data.get("packages", [])
                    print(f"[Manifest] Loaded local manifest: {len(self.seen_clip_ids)} existing clips across {len(self.packages_meta)} packages.")
                    return
            except Exception as e:
                print(f"[Manifest] Warning loading local manifest: {e}")

        # 2. Try fetching from remote HF repo
        if self.target_repo:
            try:
                remote_path = hf_hub_download(
                    repo_id=self.target_repo,
                    repo_type="dataset",
                    filename=MANIFEST_FILENAME
                )
                with open(remote_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.seen_clip_ids = set(data.get("packaged_clip_ids", []))
                    self.packages_meta = data.get("packages", [])
                    # Cache locally
                    self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(remote_path, self.manifest_path)
                    print(f"[Manifest] Downloaded remote manifest from {self.target_repo}: {len(self.seen_clip_ids)} existing clips across {len(self.packages_meta)} packages.")
                    return
            except Exception:
                # Repo or manifest doesn't exist remotely yet
                pass

        print("[Manifest] Starting with a fresh, empty manifest.")

    def is_clip_packaged(self, clip_id: str) -> bool:
        return clip_id in self.seen_clip_ids

    def record_package(self, pkg_name: str, volunteer_id: str, day_str: str,
                       clip_ids: List[str], total_duration_sec: float, extra: Optional[Dict[str, Any]] = None):
        self.seen_clip_ids.update(clip_ids)
        pkg_entry = {
            "package_name": pkg_name,
            "volunteer_id": volunteer_id,
            "day": day_str,
            "num_clips": len(clip_ids),
            "total_duration_seconds": round(total_duration_sec, 2),
            "total_duration_minutes": round(total_duration_sec / 60.0, 2),
            "clip_ids": clip_ids,
            "created_at": datetime.utcnow().isoformat() + "Z"
        }
        if extra:
            pkg_entry.update(extra)
        self.packages_meta.append(pkg_entry)

    def save(self):
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "total_clips_packaged": len(self.seen_clip_ids),
            "total_packages": len(self.packages_meta),
            "packages": self.packages_meta,
            "packaged_clip_ids": sorted(list(self.seen_clip_ids))
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[Manifest] Saved {len(self.seen_clip_ids)} clips across {len(self.packages_meta)} packages to {self.manifest_path}")


def get_source_parquet_files(source_repo: str, api: HfApi) -> List[str]:
    """Retrieves sorted list of parquet files in the source dataset."""
    info = api.dataset_info(source_repo)
    parquets = [s.rfilename for s in info.siblings if s.rfilename.endswith(".parquet")]
    parquets.sort()
    return parquets


def compress_audio_to_mp3(audio_bytes: bytes) -> bytes:
    """Compresses audio bytes to 16kHz mono MP3 at 32 kbps using ffmpeg."""
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-b:a", "32k", "-f", "mp3", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        mp3_bytes, _ = proc.communicate(input=audio_bytes)
        if mp3_bytes and len(mp3_bytes) > 0:
            return mp3_bytes
    except Exception as e:
        print(f"[Compress Warning] ffmpeg pipe failed: {e}")
    return audio_bytes


def build_single_package(
    volunteer_id: str,
    volunteer_name: str,
    day_num: int,
    clips: List[Dict[str, Any]],
    app_template_dir: Path,
    output_zip_path: Path
) -> Dict[str, Any]:
    """Creates a standalone ZIP package containing the app, audio, and metadata for one volunteer day."""
    day_str = f"Day {day_num:02d}"
    pkg_slug = f"{volunteer_id}_day_{day_num:02d}"
    total_dur = sum(c["duration"] for c in clips)

    output_zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        # 1. Package Config
        config_data = {
            "volunteer_id": volunteer_id,
            "volunteer_name": volunteer_name,
            "day": day_str,
            "package_name": pkg_slug,
            "package_title": f"LEIN Speech Transcriber — {volunteer_name} ({day_str})",
            "num_clips": len(clips),
            "total_duration_seconds": round(total_dur, 2),
            "total_duration_minutes": round(total_dur / 60.0, 2),
            "created_at": datetime.utcnow().isoformat() + "Z"
        }
        zf.writestr("speech-transcriber/config.json", json.dumps(config_data, indent=2))

        # 2. App code
        src = app_template_dir / "index.html"
        if src.exists():
            zf.write(src, arcname="speech-transcriber/index.html")

        # 3. Readme / Instructions for the volunteer
        readme_content = f"""# LEIN Speech Transcriber — {volunteer_name} ({day_str})

Welcome to your daily transcription batch!

### Package Summary:
- **Volunteer**: {volunteer_name}
- **Assigned Batch**: {day_str} ({pkg_slug})
- **Clips**: {len(clips)} clips
- **Audio Workload**: ~{total_dur / 60.0:.1f} minutes of speech (compressed 16kHz MP3)

---

## How to Run:
1. Double-click **`index.html`** to open the Excel-style transcription studio in Chrome, Edge, Safari, or Firefox.
2. Drag and drop this `.zip` file onto the page (or click "Open Package (.ZIP)").
3. Transcribe your clips offline:
   - Unverified clips start in the **"To Do"** tab in soft **Red**.
   - Press **`Tab`** or **`Space`** to play/pause audio.
   - Edit the text, then click **`[✓ Correct]`** (or press **`Ctrl + Enter`**).
   - The row turns **Green** and immediately moves to the **"Done"** tab.
4. When finished, click **"Export Submission (.ZIP)"** at top right.
5. Email your generated submission file (tiny size under 50 KB, no audio files included) to:
   **info@africancaribbeannetwork.org**

Thank you for your valuable contribution to the Liberian English Speech Dataset!
"""
        zf.writestr("speech-transcriber/README.md", readme_content)

        # 4. Audio files and metadata.csv (compressed 16kHz MP3)
        def process_clip(c):
            mp3_bytes = compress_audio_to_mp3(c["audio_bytes"])
            cid = c["id"]
            mp3_fn = f"{cid}.mp3"
            return {
                "filename": mp3_fn,
                "audio_bytes": mp3_bytes,
                "clip_id": cid,
                "sentence": c["text"],
                "duration_seconds": f"{c['duration']:.3f}",
                "source": c.get("source", ""),
                "sample_rate": 16000
            }

        with ThreadPoolExecutor(max_workers=16) as pool:
            processed_clips = list(pool.map(process_clip, clips))

        meta_rows = []
        for p in processed_clips:
            zf.writestr(f"speech-transcriber/audio/{p['filename']}", p["audio_bytes"])
            meta_rows.append({
                "audio_filename": p["filename"],
                "sentence": p["sentence"],
                "duration_seconds": p["duration_seconds"],
                "clip_id": p["clip_id"],
                "source": p["source"],
                "sample_rate": p["sample_rate"]
            })

        # Write metadata.csv
        csv_buffer = io.StringIO()
        fieldnames = ["audio_filename", "sentence", "duration_seconds", "clip_id", "source", "sample_rate"]
        writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
        writer.writeheader()
        for r in meta_rows:
            writer.writerow(r)
        zf.writestr("speech-transcriber/metadata.csv", csv_buffer.getvalue())

    return {
        "package_name": pkg_slug,
        "volunteer_id": volunteer_id,
        "day": day_str,
        "clip_ids": [c["id"] for c in clips],
        "total_duration_sec": total_dur,
        "zip_path": str(output_zip_path),
        "zip_size_bytes": output_zip_path.stat().st_size
    }


def generate_dataset_readme(target_repo: str, source_repo: str, manifest: ManifestManager) -> str:
    """Generates the README.md for the target Hugging Face dataset repo."""
    total_packages = len(manifest.packages_meta)
    total_clips = len(manifest.seen_clip_ids)
    total_duration_hrs = sum(p.get("total_duration_seconds", 0) for p in manifest.packages_meta) / 3600.0

    lines = [
        "---",
        "license: cc-by-4.0",
        "task_categories:",
        "- automatic-speech-recognition",
        "tags:",
        "- audio",
        "- speech",
        "- liberian-english",
        "- annotation",
        "- transcription",
        "pretty_name: Liberian English Volunteer Transcription Packages",
        "---",
        "",
        "# Liberian English Volunteer Transcription Packages",
        "",
        "> 🚀 **Step 1: Register on the Volunteer Portal**  ",
        "> 👉 [**Volunteer Registration & Batch Allocation Portal**](https://huggingface.co/spaces/michsethowusu/liberian-english-volunteer-signup)  ",
        "> *Enter your name, email, and committed days to get your unique Volunteer Code and direct batch downloads.*",
        "",
        "> 📥 **Step 2: Download the Offline App**  ",
        "> 👉 [**Download Speech Transcriber App (`speech-transcriber-app.zip`)**](speech-transcriber-app.zip) (~48 KB)  ",
        "> *Extract and double-click `index.html` to run locally in any browser (Chrome, Edge, Safari, Firefox) — 100% offline, zero installation.*",
        "",
        f"Bundled daily offline transcription packages for volunteers, sourced from [`{source_repo}`](https://huggingface.co/datasets/{source_repo}).",
        "",
        "Each daily `.zip` package contains:",
        "- **Excel-Style Local Web Transcriber** (`index.html` — zero install, runs 100% offline in any browser)",
        "- **Python Desktop App** (`speech-transcriber` with `run.bat` for Windows & `run.sh` for Mac/Linux)",
        "- **Ultra-Lightweight Compressed Audio** (~12 MB per day, compressed 16kHz mono MP3)",
        "- **Initial Text Metadata** to listen, verify, and correct",
        "- **Direct Submission Export** (generates tiny submission zip without audio)",
        "",
        "## Overall Progress & Statistics",
        f"- **Total Packages**: {total_packages}",
        f"- **Total Clips Assigned**: {total_clips:,}",
        f"- **Total Audio Bundled**: ~{total_duration_hrs:.2f} hours",
        f"- **Source Dataset**: `{source_repo}`",
        "",
        "## Volunteer Packages Directory",
        "",
        "| Volunteer | Day | Clips | Duration (Mins) | Package Download |",
        "| :--- | :--- | :--- | :--- | :--- |"
    ]

    for p in manifest.packages_meta:
        vol = p.get("volunteer_id", "")
        day = p.get("day", "")
        n_clips = p.get("num_clips", 0)
        dur_mins = p.get("total_duration_minutes", 0.0)
        slug = p.get("package_name", "")
        link = f"[Download {slug}.zip](packages/{vol}/{slug}.zip)"
        lines.append(f"| {vol.replace('_', ' ').title()} | {day} | {n_clips} | {dur_mins:.1f} min | {link} |")

    lines.extend([
        "",
        "## Instructions for Collaborators",
        "1. **Register & Get Code**: Visit the [Volunteer Portal](https://huggingface.co/spaces/michsethowusu/liberian-english-volunteer-signup) to get your Volunteer Code.",
        "2. **Download the App**: Click [**`speech-transcriber-app.zip`**](speech-transcriber-app.zip) above and extract it on your laptop.",
        "3. **Open the App**: Double-click **`index.html`** in the extracted folder. It opens immediately in Chrome, Edge, Safari, or Firefox completely offline.",
        "4. **Download Your Assigned Day**: Download your assigned `.zip` file from the table above (e.g. `volunteer_01_day_01.zip`).",
        "5. **Load the Audio**: Drag and drop your downloaded daily `.zip` file onto the open browser window (or click **Open Daily ZIP**).",
        "6. **Transcribe in the Excel Grid**:",
        "   - Press `Tab` or `Space` to Play / Pause audio (auto-repeats 3 times with 3s pauses).",
        "   - Press `Shift + Tab` to rewind 2 seconds.",
        "   - Listen, verify/correct the transcription in the formula bar, and press `Ctrl + Enter` to save & advance to the next clip.",
        "7. **Package & Submit**: When you finish, click **Package & Export Submission (.ZIP)** at the top right to download your verified submission file (under 100 KB, contains no audio files).",
        "8. **Email Submission**: Send your exported submission file to: **info@africancaribbeannetwork.org**"
    ])

    return "\n".join(lines)


def get_next_available_slot(existing_pkg_names: Set[str], days_per_volunteer: int = 10):
    """Finds the next volunteer and day slot not yet created."""
    vol_idx = 1
    while True:
        vol_id = f"volunteer_{vol_idx:02d}"
        vol_name = f"Volunteer_{vol_idx:02d}"
        for day_idx in range(1, days_per_volunteer + 1):
            pkg_name = f"{vol_id}_day_{day_idx:02d}"
            if pkg_name not in existing_pkg_names:
                return {
                    "volunteer_id": vol_id,
                    "volunteer_name": vol_name,
                    "day_num": day_idx,
                    "package_name": pkg_name
                }
        vol_idx += 1


def run_pipeline(
    source_repo: str = DEFAULT_SOURCE_REPO,
    target_repo: str = DEFAULT_TARGET_REPO,
    days_per_volunteer: int = 10,
    target_duration_mins: float = 48.0,
    max_volunteers: Optional[int] = None,
    output_dir: Path = Path("generated_packages"),
    app_template_dir: Path = Path("."),
    push_to_hub: bool = True,
    force_repackage: bool = False,
    hf_token: Optional[str] = None
):
    """Executes the complete packaging pipeline."""
    api = HfApi(token=hf_token)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_FILENAME

    print("=" * 70)
    print("LEIN SPEECH TRANSCRIBER — VOLUNTEER PACKAGING PIPELINE")
    print(f"Source Dataset:        {source_repo}")
    print(f"Target HF Repo:        {target_repo}")
    print(f"Days per Volunteer:    {days_per_volunteer} working days (2-week schedule)")
    print(f"Target Duration / Day: {target_duration_mins:.1f} mins (~{target_duration_mins * 60:.0f}s)")
    if max_volunteers:
        print(f"Max Volunteers:        {max_volunteers} (max {max_volunteers * days_per_volunteer} packages)")
    else:
        print(f"Auto-Expand:           Covering all available clips across as many volunteers as needed")
    print(f"Compressed Audio:      16kHz mono MP3 @ 32k (ultra-compact, ~12 MB/day)")
    print(f"Force Repackage:       {force_repackage}")
    print(f"Push to Hub:           {push_to_hub}")
    print("=" * 70)

    # 1. Initialize Manifest Manager (loads existing packaged clips unless reset requested)
    manifest = ManifestManager(manifest_path=manifest_path, target_repo=target_repo, api=api, reset=force_repackage)
    print(f"[Manifest] Already packaged clips in history: {len(manifest.seen_clip_ids):,}")
    print(f"[Manifest] Already created packages in history: {len(manifest.packages_meta)}")

    if push_to_hub:
        try:
            create_repo(repo_id=target_repo, repo_type="dataset", exist_ok=True, token=hf_token)
        except Exception as e:
            print(f"[Hub] Repo note: {e}")

    # 2. Get Parquet Files from Source Dataset
    print(f"[Hub] Fetching parquet file list from {source_repo}...")
    parquets = get_source_parquet_files(source_repo, api)
    print(f"[Hub] Found {len(parquets)} parquet files in source repo.")

    existing_pkg_names = {p["package_name"] for p in manifest.packages_meta}

    # 3. Stream parquets and build packages
    target_dur_sec = target_duration_mins * 60.0
    current_clip_buffer: List[Dict[str, Any]] = []
    current_dur_buffer = 0.0
    packages_created_in_run: List[Dict[str, Any]] = []

    for p_idx, parquet_rel_path in enumerate(parquets):
        print(f"\n[Parquet {p_idx + 1}/{len(parquets)}] Inspecting {parquet_rel_path}...")
        local_parquet_path = hf_hub_download(repo_id=source_repo, repo_type="dataset", filename=parquet_rel_path)

        table = pq.read_table(local_parquet_path)
        num_rows = len(table)

        ids = table["id"].to_pylist()
        durations = table["duration"].to_pylist()
        texts = table["text"].to_pylist()
        sample_rates = table["sample_rate"].to_pylist() if "sample_rate" in table.column_names else [24000] * num_rows
        sources = table["source"].to_pylist() if "source" in table.column_names else [""] * num_rows
        audio_col = table["audio"]

        rows_added = 0
        for i in range(num_rows):
            clip_id = ids[i]
            # Check manifest deduplication
            if manifest.is_clip_packaged(clip_id):
                continue

            audio_struct = audio_col[i].as_py()
            audio_bytes = audio_struct.get("bytes")
            audio_path = audio_struct.get("path") or f"{clip_id}.flac"
            audio_fn = Path(audio_path).name

            dur = float(durations[i]) if durations[i] is not None else 0.0
            txt = texts[i] or ""

            current_clip_buffer.append({
                "id": clip_id,
                "filename": audio_fn,
                "audio_bytes": audio_bytes,
                "duration": dur,
                "text": txt,
                "sample_rate": sample_rates[i],
                "source": sources[i]
            })
            current_dur_buffer += dur
            rows_added += 1

            # Check if current buffer meets target day duration
            if current_dur_buffer >= target_dur_sec:
                pkg_info = get_next_available_slot(existing_pkg_names, days_per_volunteer)
                vol_num = int(pkg_info["volunteer_id"].split("_")[-1])
                if max_volunteers and vol_num > max_volunteers:
                    print(f"[Pipeline] Reached max volunteer limit ({max_volunteers}). Stopping.")
                    break

                vol_id = pkg_info["volunteer_id"]
                vol_name = pkg_info["volunteer_name"]
                day_num = pkg_info["day_num"]
                pkg_slug = pkg_info["package_name"]

                zip_out = output_dir / "packages" / vol_id / f"{pkg_slug}.zip"
                print(f"  --> Building package: {pkg_slug} ({len(current_clip_buffer)} clips, {current_dur_buffer/60.0:.1f} mins)...")
                res = build_single_package(
                    volunteer_id=vol_id,
                    volunteer_name=vol_name,
                    day_num=day_num,
                    clips=current_clip_buffer,
                    app_template_dir=app_template_dir,
                    output_zip_path=zip_out
                )

                # Record in manifest
                manifest.record_package(
                    pkg_name=pkg_slug,
                    volunteer_id=vol_id,
                    day_str=f"Day {day_num:02d}",
                    clip_ids=[c["id"] for c in current_clip_buffer],
                    total_duration_sec=current_dur_buffer,
                    extra={"zip_file": f"packages/{vol_id}/{pkg_slug}.zip"}
                )
                existing_pkg_names.add(pkg_slug)
                manifest.save()
                packages_created_in_run.append(res)

                # Reset buffer
                current_clip_buffer = []
                current_dur_buffer = 0.0

        print(f"  Finished {parquet_rel_path}: {rows_added} new clips added to buffer (skipped {num_rows - rows_added} already packaged).")

        # Clean up parquet to save disk space
        try:
            if Path(local_parquet_path).exists():
                os.remove(local_parquet_path)
        except Exception:
            pass

    # If remaining clips in buffer, create final package
    if current_clip_buffer:
        pkg_info = get_next_available_slot(existing_pkg_names, days_per_volunteer)
        vol_id = pkg_info["volunteer_id"]
        vol_name = pkg_info["volunteer_name"]
        day_num = pkg_info["day_num"]
        pkg_slug = pkg_info["package_name"]

        zip_out = output_dir / "packages" / vol_id / f"{pkg_slug}.zip"
        print(f"  --> Building final remainder package: {pkg_slug} ({len(current_clip_buffer)} clips, {current_dur_buffer/60.0:.1f} mins)...")
        res = build_single_package(
            volunteer_id=vol_id,
            volunteer_name=vol_name,
            day_num=day_num,
            clips=current_clip_buffer,
            app_template_dir=app_template_dir,
            output_zip_path=zip_out
        )
        manifest.record_package(
            pkg_name=pkg_slug,
            volunteer_id=vol_id,
            day_str=f"Day {day_num:02d}",
            clip_ids=[c["id"] for c in current_clip_buffer],
            total_duration_sec=current_dur_buffer,
            extra={"zip_file": f"packages/{vol_id}/{pkg_slug}.zip"}
        )
        manifest.save()
        packages_created_in_run.append(res)

    print(f"\n[Pipeline] Completed building all packages! Total in manifest: {len(manifest.packages_meta)} packages, {len(manifest.seen_clip_ids):,} clips.")

    # Final Hub Sync: Upload entire folder in a single commit via upload_folder
    if push_to_hub:
        readme_text = generate_dataset_readme(target_repo, source_repo, manifest)
        readme_path = output_dir / "README.md"
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme_text)

        print(f"\n[Hub] Syncing all packages, manifest, and README to {target_repo} via upload_folder...")
        while True:
            try:
                upload_folder(
                    repo_id=target_repo,
                    repo_type="dataset",
                    folder_path=str(output_dir),
                    commit_message=f"Upload volunteer transcription packages ({len(manifest.packages_meta)} packages, {len(manifest.seen_clip_ids):,} clips)"
                )
                print(f"\n[Hub] Successfully published all packages! Browse and download at:")
                print(f"      https://huggingface.co/datasets/{target_repo}")
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate limit" in err_str.lower():
                    print(f"  [Hub Rate Limit] 429 commit rate limit active. Retrying in 60 seconds...")
                    time.sleep(60)
                else:
                    print(f"  [Hub Error] {e}. Retrying in 30 seconds...")
                    time.sleep(30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package Liberian English Speech Dataset for Volunteers")
    parser.add_argument("--source-repo", default=DEFAULT_SOURCE_REPO, help="Source HF dataset repo")
    parser.add_argument("--target-repo", default=DEFAULT_TARGET_REPO, help="Target HF dataset repo for packages")
    parser.add_argument("--days-per-volunteer", type=int, default=10, help="Number of days per volunteer (default: 10)")
    parser.add_argument("--target-duration-mins", type=float, default=48.0, help="Target audio duration per day in minutes (default: 48)")
    parser.add_argument("--max-volunteers", type=int, default=None, help="Optional max volunteers limit")
    parser.add_argument("--output-dir", default="generated_packages", help="Local directory to store packages and manifest")
    parser.add_argument("--no-push", action="store_true", help="Do not upload to Hugging Face")
    parser.add_argument("--force-repackage", action="store_true", help="Repackage all clips with compressed MP3 audio")

    args = parser.parse_args()

    run_pipeline(
        source_repo=args.source_repo,
        target_repo=args.target_repo,
        days_per_volunteer=args.days_per_volunteer,
        target_duration_mins=args.target_duration_mins,
        max_volunteers=args.max_volunteers,
        output_dir=Path(args.output_dir),
        push_to_hub=not args.no_push,
        force_repackage=args.force_repackage
    )

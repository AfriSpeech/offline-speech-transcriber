#!/usr/bin/env python3
"""Generates all-in-one volunteer workspace bundles.

Each bundle contains:
- speech-transcriber/README.pdf (visual starter guide)
- speech-transcriber/index.html (offline web transcriber)
- speech-transcriber/metadata.js (pre-loaded window.LEIN_DATA)
- speech-transcriber/metadata.csv
- speech-transcriber/README.md
- speech-transcriber/audio/*.mp3
"""

import os
import io
import csv
import json
import zipfile
import argparse
from pathlib import Path

HOUR_CONFIGS = [
    (1, [1]),               # 1 hr: Day 1 (~50 mins)
    (2, [1, 2]),            # 2 hrs: Days 1-2 (~1.7 hrs)
    (3, [1, 2, 3]),         # 3 hrs: Days 1-3 (~2.5 hrs)
    (4, [1, 2, 3, 4]),      # 4 hrs: Days 1-4 (~3.3 hrs)
    (5, [1, 2, 3, 4, 5, 6]) # 5 hrs: Days 1-6 (~5.0 hrs max)
]

def build_all_bundles(
    packages_dir: Path,
    bundles_dir: Path,
    app_html_path: Path,
    app_readme_path: Path,
    readme_pdf_path: Path,
    upload_repo: str = None
):
    bundles_dir.mkdir(parents=True, exist_ok=True)
    print("=== Generating All-in-One Volunteer Workspace Bundles ===")
    print(f"Source Packages: {packages_dir}")
    print(f"Output Bundles:  {bundles_dir}")

    html_bytes = app_html_path.read_bytes() if app_html_path.exists() else b""
    readme_bytes = app_readme_path.read_bytes() if app_readme_path.exists() else b""
    pdf_bytes = readme_pdf_path.read_bytes() if readme_pdf_path.exists() else b""

    bundles_created = 0
    vol_dirs = sorted([d for d in packages_dir.iterdir() if d.is_dir() and d.name.startswith("volunteer_")])

    for vol_dir in vol_dirs:
        vol_id = vol_dir.name
        for hours, days in HOUR_CONFIGS:
            bundle_name = f"{vol_id}_{hours}hrs.zip"
            bundle_out = bundles_dir / bundle_name

            audio_files = {}
            merged_meta_rows = []

            for d in days:
                day_slug = f"{vol_id}_day_{d:02d}.zip"
                pkg_path = vol_dir / day_slug
                if not pkg_path.exists():
                    continue

                with zipfile.ZipFile(pkg_path, "r") as zf:
                    meta_name = next((n for n in zf.namelist() if n.endswith("metadata.csv")), None)
                    if meta_name:
                        csv_text = zf.read(meta_name).decode("utf-8")
                        reader = csv.DictReader(io.StringIO(csv_text))
                        for r in reader:
                            merged_meta_rows.append(r)

                    for n in zf.namelist():
                        if "/audio/" in n and n.lower().endswith((".mp3", ".flac", ".wav")):
                            base_fn = n.split("/")[-1]
                            if base_fn not in audio_files:
                                audio_files[base_fn] = zf.read(n)

            if not audio_files:
                continue

            metadata_js = f"window.LEIN_DATA = {{\n  package_name: {json.dumps(bundle_name.replace('.zip', ''))},\n  volunteer_id: {json.dumps(vol_id)},\n  hours: {hours},\n  clips: {json.dumps(merged_meta_rows, indent=2)}\n}};\n"

            with zipfile.ZipFile(bundle_out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bzf:
                if pdf_bytes:
                    bzf.writestr("speech-transcriber/README.pdf", pdf_bytes)
                if html_bytes:
                    bzf.writestr("speech-transcriber/index.html", html_bytes)
                if readme_bytes:
                    bzf.writestr("speech-transcriber/README.md", readme_bytes)
                bzf.writestr("speech-transcriber/metadata.js", metadata_js.encode("utf-8"))

                buf = io.StringIO()
                fieldnames = ["audio_filename", "sentence", "duration_seconds", "clip_id", "source", "sample_rate"]
                writer = csv.DictWriter(buf, fieldnames=fieldnames)
                writer.writeheader()
                for r in merged_meta_rows:
                    writer.writerow(r)
                bzf.writestr("speech-transcriber/metadata.csv", buf.getvalue().encode("utf-8"))

                for fn, data in audio_files.items():
                    bzf.writestr(f"speech-transcriber/audio/{fn}", data)

            bundles_created += 1
            print(f"Created: {bundle_name} ({len(audio_files)} clips, {bundle_out.stat().st_size / (1024**2):.1f} MB)")

    print(f"\nSuccessfully generated {bundles_created} all-in-one bundles!")

    if upload_repo:
        from huggingface_hub import upload_folder
        print(f"\n[Hub] Uploading bundles to {upload_repo}...")
        upload_folder(
            folder_path=str(bundles_dir),
            path_in_repo="bundles",
            repo_id=upload_repo,
            repo_type="dataset",
            commit_message="Upload all-in-one volunteer bundles"
        )
        print("[Hub] Upload complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build all-in-one volunteer workspace bundles")
    parser.add_argument("--packages-dir", default="release/packages", help="Directory containing volunteer_XX daily zips")
    parser.add_argument("--bundles-dir", default="release/bundles", help="Output directory for all-in-one bundles")
    parser.add_argument("--app-html", default="app/index.html", help="Path to app index.html")
    parser.add_argument("--app-readme", default="app/README.md", help="Path to app README.md")
    parser.add_argument("--readme-pdf", default="app/README.pdf", help="Path to starter guide README.pdf")
    parser.add_argument("--upload-repo", default=None, help="Optional HF dataset repo to upload bundles")

    args = parser.parse_args()
    build_all_bundles(
        packages_dir=Path(args.packages_dir),
        bundles_dir=Path(args.bundles_dir),
        app_html_path=Path(args.app_html),
        app_readme_path=Path(args.app_readme),
        readme_pdf_path=Path(args.readme_pdf),
        upload_repo=args.upload_repo
    )

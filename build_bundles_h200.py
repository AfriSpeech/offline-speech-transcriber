#!/usr/bin/env python3
"""Generates all-in-one volunteer bundles on H200.

Each bundle contains:
- speech-transcriber/index.html
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
from pathlib import Path

BASE_DIR = Path("/mnt/volume_d2wey28/projects/speech-transcriber-packaging")
PACKAGES_DIR = BASE_DIR / "release" / "packages"
BUNDLES_DIR = BASE_DIR / "release" / "bundles"
APP_HTML = BASE_DIR / "index.html"
APP_README = BASE_DIR / "README.md"

BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

HOUR_CONFIGS = [
    (1, [1]),          # 1 hr: Day 1 (~50 mins)
    (2, [1, 2]),       # 2 hrs: Days 1-2 (~1.7 hrs)
    (3, [1, 2, 3]),    # 3 hrs: Days 1-3 (~2.5 hrs)
    (4, [1, 2, 3, 4]), # 4 hrs: Days 1-4 (~3.3 hrs)
    (5, [1, 2, 3, 4, 5, 6]) # 5 hrs: Days 1-6 (~5.0 hrs max)
]

print("=== Generating All-in-One Volunteer Workspace Bundles ===")

with open(APP_HTML, "r", encoding="utf-8") as f:
    html_content = f.read()

with open(APP_README, "r", encoding="utf-8") as f:
    readme_content = f.read()

bundles_created = 0

for v in range(1, 21):
    vol_id = f"volunteer_{v:02d}"
    vol_dir = PACKAGES_DIR / vol_id
    if not vol_dir.exists():
        continue

    for hours, days in HOUR_CONFIGS:
        bundle_name = f"{vol_id}_{hours}hrs.zip"
        bundle_out = BUNDLES_DIR / bundle_name

        audio_files = {} # fn -> bytes
        merged_meta_rows = []

        for d in days:
            day_slug = f"{vol_id}_day_{d:02d}.zip"
            pkg_path = vol_dir / day_slug
            if not pkg_path.exists():
                continue

            with zipfile.ZipFile(pkg_path, "r") as zf:
                # Find metadata.csv
                meta_name = next((n for n in zf.namelist() if n.endswith("metadata.csv")), None)
                if meta_name:
                    csv_text = zf.read(meta_name).decode("utf-8")
                    reader = csv.DictReader(io.StringIO(csv_text))
                    for r in reader:
                        merged_meta_rows.append(r)

                # Extract audio files
                for n in zf.namelist():
                    if "/audio/" in n and n.lower().endswith(".mp3"):
                        base_fn = n.split("/")[-1]
                        if base_fn not in audio_files:
                            audio_files[base_fn] = zf.read(n)

        if not audio_files:
            continue

        # Build metadata.js
        metadata_js_content = f"window.LEIN_DATA = {{\n  package_name: {json.dumps(bundle_name.replace('.zip', ''))},\n  hours: {hours},\n  clips: {json.dumps(merged_meta_rows, indent=2)}\n}};\n"

        # Build bundle zip
        with zipfile.ZipFile(bundle_out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bzf:
            bzf.writestr("speech-transcriber/index.html", html_content)
            bzf.writestr("speech-transcriber/README.md", readme_content)
            bzf.writestr("speech-transcriber/metadata.js", metadata_js_content)

            # Write merged metadata.csv
            buf = io.StringIO()
            fieldnames = ["audio_filename", "sentence", "duration_seconds", "clip_id", "source", "sample_rate"]
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            for r in merged_meta_rows:
                writer.writerow(r)
            bzf.writestr("speech-transcriber/metadata.csv", buf.getvalue())

            # Write audio files
            for fn, data in audio_files.items():
                bzf.writestr(f"speech-transcriber/audio/{fn}", data)

        bundles_created += 1
        print(f"Created: {bundle_name} ({len(audio_files)} clips, {bundle_out.stat().st_size / (1024**2):.1f} MB)")

print(f"\nSuccessfully generated {bundles_created} all-in-one volunteer bundles!")

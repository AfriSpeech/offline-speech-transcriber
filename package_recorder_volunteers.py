#!/usr/bin/env python3
"""Splits the 15,408 Kolokwa prompts into 4 parts and packages ready-to-use recorder apps for Volunteers 1-4."""

import os
import io
import csv
import json
import zipfile
from pathlib import Path

PROMPTS_FILE = Path("kolokwa_prompts.json")
RECORDER_HTML = Path("recorder-app/index.html")
OUTPUT_DIR = Path("recorder-packages")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
    all_prompts = json.load(f)

total_prompts = len(all_prompts)
num_volunteers = 4
part_size = total_prompts // num_volunteers

print(f"Total Kolokwa Prompts: {total_prompts:,}")
print(f"Splitting into {num_volunteers} parts of ~{part_size:,} prompts each...\n")

with open(RECORDER_HTML, "r", encoding="utf-8") as f:
    html_content = f.read()

for v_idx in range(1, num_volunteers + 1):
    vol_id = f"volunteer_{v_idx:02d}"
    vol_name = f"Volunteer {v_idx:02d}"
    start_i = (v_idx - 1) * part_size
    end_i = v_idx * part_size if v_idx < num_volunteers else total_prompts
    vol_prompts = all_prompts[start_i:end_i]

    zip_filename = f"recorder_{vol_id}.zip"
    zip_out = OUTPUT_DIR / zip_filename

    # 1. prompts.js
    prompts_js = f"window.LEIN_RECORDER_DATA = {{\n  volunteer_id: {json.dumps(vol_id)},\n  total_prompts: {len(vol_prompts)},\n  prompts: {json.dumps(vol_prompts, indent=2)}\n}};\n"

    # 2. prompts.csv
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=["prompt_id", "text"])
    writer.writeheader()
    for p in vol_prompts:
        writer.writerow(p)
    csv_content = csv_buf.getvalue()

    # 3. README.md
    readme_content = f"""# LEIN Speech Recorder — {vol_name}

Welcome to your dedicated speech recording workspace!

### Package Details:
- **Volunteer**: {vol_name} ({vol_id})
- **Dialect**: Authentic Liberian English (Kolokwa)
- **Assigned Prompts**: {len(vol_prompts):,} sentences
- **Prompts Range**: #{start_i + 1:,} to #{end_i:,}

---

## How to Record (Zero Installation):
1. Extract this `.zip` file on your laptop.
2. Double-click **`index.html`** to open the recorder in your web browser (Chrome, Edge, Safari, Firefox).
3. Click **"Begin Recording"** and allow microphone access when prompted.
4. All your assigned prompts are already pre-loaded into the studio!

---

## Keyboard Shortcuts:
- **`Spacebar`**: Toggle Record / Stop
- **`Tab`**: Play back your recorded take to check audio clarity
- **`R`**: Retake current sentence
- **`Ctrl + Enter`**: Mark **[✓ Done]** and advance to the next prompt!
- **`Alt + Left`**: Return to previous prompt

---

## Submitting Your Audio:
- When you finish recording your prompts, click **"Export Recordings (.ZIP)"** on the top bar.
- The app will package all your pristine 16-bit PCM WAV audio files and `metadata.csv` into a single zip.
- Email your exported recordings file to: **info@africancaribbeannetwork.org**

Thank you for contributing your voice to preserve and advance Liberian English speech AI!
"""

    with zipfile.ZipFile(zip_out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        zf.writestr("speech-recorder/index.html", html_content)
        zf.writestr("speech-recorder/prompts.js", prompts_js)
        zf.writestr("speech-recorder/prompts.csv", csv_content)
        zf.writestr("speech-recorder/README.md", readme_content)

    print(f"Created: {zip_filename} ({len(vol_prompts):,} prompts, {zip_out.stat().st_size / (1024**2):.1f} MB)")

print(f"\nSuccessfully created all 4 ready-to-use recorder packages in {OUTPUT_DIR}/!")

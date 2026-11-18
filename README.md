# Offline Speech Transcriber & Volunteer Campaign Toolkit

An end-to-end, open-source operational framework for speech data collection, transcription, and community verification. Designed for African and low-resource languages, it provides a frictionless offline web application, an automated volunteer allocation portal, an executive monitoring dashboard, and automated Hugging Face ingestion pipelines.

---

## 🌟 System Architecture & Components

```
offline-speech-transcriber/
├── app/                  # 100% Offline Single-File HTML Transcriber Studio
│   ├── index.html        # Clean, focused single-clip studio with inlined JSZip
│   ├── jszip.min.js      # Embedded offline ZIP decompression engine
│   ├── README.pdf        # Visual 1-page volunteer starter guide
│   └── README.md         # Quick reference instructions
│
├── portal/               # Hugging Face Space: Volunteer Signup & Allocation
│   ├── index.html        # Two-column Airbnb/Google layout with local phone support
│   ├── README.md         # Hugging Face Space configuration
│   └── config.example.js # Customizable portal settings template
│
├── dashboard/            # Hugging Face Space: Executive Monitoring Dashboard
│   ├── index.html        # Passcode-protected dashboard (SHA-256 verification)
│   ├── README.md         # Space definition
│   └── config.example.js # Dashboard configuration & passcode hash
│
├── poster/               # Campus & Community Recruitment Poster Templates
│   ├── poster.html       # Print-ready vector HTML template
│   ├── generate_poster.py# Python CLI to render custom PDF & PNG posters
│   └── LEIN_Volunteer_Recruitment_Poster.docx # Fully editable Word template
│
├── pipelines/            # Python CLI Pipelines for Dataset Lifecycle
│   ├── package_dataset.py# Audio compression (16kHz MP3) & volunteer slicing
│   ├── build_bundles.py  # All-in-one workspace bundle builder (1 to 5 hours)
│   └── ingest_submissions.py # Maps volunteer text back to 24kHz FLAC audio
│
├── config.example.json   # Unified project configuration schema
└── .gitignore            # Security rules (ignores secrets, passcodes, and docs)
```

---

## 🚀 1. The Offline Transcriber App (`app/`)

A native, browser-based transcription studio that runs **100% offline** on any laptop (Windows, Mac, Linux, Chromebook) in Chrome, Edge, Safari, or Firefox:
- **Zero Software Installation:** No Python, no terminal commands, and no audio driver dependencies.
- **Single-Clip Focus Studio:** Eliminates visual clutter by displaying only the active clip in view with live progress (`Clip 14 of 75 • 18% Done`).
- **Paced Autoplay & Replay:** Features a calm **3-second countdown** before audio begins, and automatically replays audio in **3 seconds** (up to 3 takes) so volunteers can type without taking their hands off the keyboard.
- **Sequential Progression:** Automatically locks subsequent rows until the current clip is verified, preventing skipped clips.
- **Accidental-Forward Check:** Prompts for confirmation if a volunteer clicks `[✓ Done]` without modifying the pre-filled transcription.
- **Audio-Free Submission Export:** Clicking **"Export Submission (.ZIP)"** creates a tiny package (**under 50 KB**, containing only verified text and timestamps — **no audio files**) to email to coordinators.

### Keyboard Shortcuts
| Shortcut | Action |
| :--- | :--- |
| `Tab` or `Space` | Play / Pause audio |
| `Shift + Tab` | Rewind 2 seconds |
| `Ctrl + Enter` | Mark `[✓ Done]` & unlock next clip |
| `0.75x / 1.0x / 1.25x` | Playback speed selector |

---

## 📝 2. Volunteer Signup & Batch Allocation Portal (`portal/`)

A public Hugging Face Space providing a modern, two-column registration interface:
- **Hours-Based Commitment:** Volunteers select their commitment (**1 to 5 Hours Maximum**).
- **Local Phone Number Support:** Volunteers enter standard local phone numbers (e.g. `077...` / `088...`) with zero country code required.
- **Least-Assigned Round-Robin:** Dynamically allocates unassigned packages from the dataset pool (ensuring all packages are assigned 1x, then 2x, etc.).
- **Single All-in-One Download:** Issues a single download button for their complete package (contains app, guide, and pre-loaded audio).
- **Self-Service Cancellation:** Returning volunteers can click **"Cancel Registration"** at any time to automatically release their package back to the active pool.
- **Encrypted Registry:** Stores registrations in an encrypted/obfuscated dataset repository on Hugging Face.

---

## 📊 3. Passcode-Protected Executive Dashboard (`dashboard/`)

A Hugging Face Space for leadership and project coordinators:
- **Cryptographic Security:** Protected with client-side **SHA-256 hashing**; plaintext passcodes are never exposed in the source code.
- **Real-Time Tracking:** Live stats for Total Volunteers, Committed Work Hours, and Package Pool Coverage.
- **1-Click WhatsApp Community:** Click the `💬 WhatsApp` button next to any volunteer's phone number to immediately message them or onboard them to the group.
- **Instant CSV Export:** Download the complete volunteer roster with contact details with one click.

---

## 📦 4. Data Lifecycle Pipelines (`pipelines/`)

### A. Package Dataset into Compressed Bundles
Compresses raw source broadcast audio to **16kHz mono MP3 at 32 kbps** (reducing daily package size by ~85% from 80 MB to ~11.5 MB) while keeping identical clip IDs:
```bash
python pipelines/package_dataset.py \
    --source-repo "AfriSpeech/liberian-english-speech" \
    --target-repo "AfriSpeech/liberian-english-speech-packages" \
    --target-duration-mins 50.0
```

### B. Build All-in-One Multi-Hour Bundles
Assembles pre-packaged bundles (1, 2, 3, 4, and 5 hours) containing `index.html`, `README.pdf`, `metadata.js`, and audio:
```bash
python pipelines/build_bundles.py \
    --packages-dir "release/packages" \
    --bundles-dir "release/bundles" \
    --upload-repo "AfriSpeech/liberian-english-speech-packages"
```

### C. Ingest Volunteer Submissions
When volunteers email their completed submission ZIPs, map verified text back to the **original 24kHz FLAC broadcast audio** and append to the training dataset:
```bash
python pipelines/ingest_submissions.py \
    --submission path/to/volunteer_submission.zip \
    --source-dataset "AfriSpeech/liberian-english-speech" \
    --target-dataset "AfriSpeech/liberian-english-speech-transcribed"
```

---

## 🎨 5. Customization for Your Organization

To adapt this toolkit for your own organization or language project:
1. Copy `config.example.json` and fill in your organization name, language, and Hugging Face repositories.
2. In `portal/` and `dashboard/`, customize `config.example.js` with your repository names and deploy as static Hugging Face Spaces.
3. In `poster/`, run `python generate_poster.py --config my_config.json` to generate custom recruitment posters.

---

## 📄 License
MIT License. Developed for the Liberian English AI Initiative (LEIN) and AfriSpeech.

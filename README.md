# LEIN Speech Transcriber (Offline Studio)

An offline audio listening, speech transcription, and text verification application designed for speech data collection projects. Operates 100% offline, handles local audio directories or `.zip` archives directly, and provides an ergonomic keyboard-driven transcription workflow.

---

## Quick Start
1. Double-click **`index.html`** to launch the offline transcription studio in your web browser (Chrome, Edge, Safari, or Firefox).
2. Drag and drop your assigned bundle `.zip` file directly onto the window (or click **"Open Package (.ZIP)"**).
3. Transcribe your clips:
   - Unverified clips start in the **"To Do"** tab with a soft **Red** background.
   - Press **`Tab`** or **`Space`** to play/pause audio.
   - Edit the text, then click **`[✓ Done]`** (or press **`Ctrl + Enter`**).
   - The row turns **Green** and immediately moves to the **"Done"** tab.
4. When finished, click **"Export Submission (.ZIP)"** (generates a tiny file under 50 KB, containing only verified text — no audio files).
5. Email your generated submission file to: **`info@africancaribbeannetwork.org`**.
*No Python, no terminal, and no dependencies needed. 100% offline.*

---

## Keyboard Shortcuts
- **`Tab`** or **`Space`**: Play / Pause audio.
- **`Shift + Tab`**: Rewind 2 seconds to replay unclear words.
- **`Ctrl + Enter`**: Mark Done on active row and advance to the next clip!
- **Playback Speed Selector**: $0.75\times$, $1.0\times$, $1.25\times$.

---

## Features
- **Direct ZIP Extraction in Browser**: Decompresses and plays audio clips straight from memory using Web Audio / HTML5.
- **Clean Table Experience**: Focused, sequential row progression with status badges (`✓ Done`, `⚠️ Review`).
- **Length Quality Check**: Detects unusually short or long transcripts with an override option.
- **Local Storage Auto-Save**: Work is automatically saved in your browser on every keystroke.
- **One-Click Export**: Exports a clean, finalized spreadsheet with verified transcripts.

---

## License
MIT License.

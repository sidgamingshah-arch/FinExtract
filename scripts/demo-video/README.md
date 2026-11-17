# Demo video kit

Records the FinExtract walkthrough as a subtitled MP4: a live run on a filing with a visible cursor
(template choice, upload, the run, the Workspace, a note, the review queue, export), ending on the
configuration slide (`config_slide.html`).

```bash
scripts/demo-video/record-demo.sh path/to/apple_10k_2025.pdf /some/empty/folder
# -> /some/empty/folder/final/FinExtract_demo.mp4 and FinExtract_demo.srt
```

Needs Python with the backend installed, Node with the frontend's `node_modules`, a Playwright
Chromium, and `ffmpeg` with libass. It runs on its own fresh database, so the filing is a new
upload and the run happens on camera.

**The model.** The run uses the model `backend/config.local.toml` names (git-ignored; see
`config.local.example.toml`), with the key read from the environment variable its
`[llm].api_key_env` gives. If no model is reachable, the run is rule-based and the results page
says "Rule-based mapping only".

**The captions describe one run.** `record.cjs` is written for Apple's FY2025 10-K on the US GAAP
template: the template it picks, the note it opens (N13, segments), and what its review-queue
caption says is left. On another filing, or after a change that moves what the run finds, read
the captions against what the run shows before using the video.

**How the timing holds.** The browser video's clock can drift from the script's on a busy
machine. At every caption and cut point the recorder flips a small marker in the bottom-left
corner; `burn.py` finds each flip in the video, pins captions and cuts to it, cuts the waits
(upload, run, slow page loads), burns the subtitles in, and crops the marker strip off. It
refuses to burn if the marks found and logged disagree.

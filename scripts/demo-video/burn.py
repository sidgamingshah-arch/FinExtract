"""captions.json + the browser video -> the edited video, captions pinned to the video's own clock.

The recorder flips a small red/blue marker in the bottom-left corner at every caption and cut
point and logs its own time for each flip (`anchors`). The video's clock drifts from the
recorder's when the machine is busy, so the times are mapped through the flips found in the
video (piecewise linear), then the waits are cut, the captions burned in, and the marker strip
cropped off.
"""
import json
import pathlib
import subprocess
import sys

FPS = 25
rec, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
c = json.loads((rec / "captions.json").read_text())
W, H, MW, MH = 1600, 900, 32, 6

# 1. Find the marker flips in the video.
raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", c["video"], "-vf",
                      f"fps={FPS},crop={MW - 8}:{MH - 2}:2:{H - MH + 1}", "-f", "rawvideo",
                      "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
size = (MW - 8) * (MH - 2) * 3
flips, last = [], None
for i in range(len(raw) // size):
    px = raw[i * size:(i + 1) * size]
    n = len(px) // 3
    r = sum(px[0::3]) / n
    g = sum(px[1::3]) / n
    b = sum(px[2::3]) / n
    col = "R" if r > 170 and g < 90 and b < 90 else "B" if b > 170 and r < 90 and g < 90 else None
    if col and col != last:
        flips.append(i / FPS)
        last = col
anchors = c["anchors"]
print(f"{len(anchors)} sync marks logged, {len(flips)} found in the video")
if len(flips) != len(anchors):
    sys.exit("marker count mismatch: not burning a video whose captions could be misplaced")
drift = [round(v - a, 2) for a, v in zip(anchors, flips)]
print("video minus recorder clock, per mark:", drift)


def to_video(t: float) -> float:
    """The recorder's time t on the video's clock (linear between marks, slope 1 outside)."""
    if t <= anchors[0]:
        return t + flips[0] - anchors[0]
    for (a0, v0), (a1, v1) in zip(zip(anchors, flips), zip(anchors[1:], flips[1:])):
        if t <= a1:
            return v0 + (t - a0) * ((v1 - v0) / (a1 - a0) if a1 > a0 else 1.0)
    return t + flips[-1] - anchors[-1]


cuts = [[to_video(x), to_video(y)] for x, y in (c["marks"].get("cuts") or [])]
run_gap = to_video(c["marks"]["cut_to"]) - to_video(c["marks"]["cut_from"])


def shift(t: float) -> float:
    s = t
    for x, y in cuts:
        s -= (y - x) if t >= y else max(0.0, t - x)
    return s


def ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


lines, n = [], 0
for q in c["cues"]:
    start, end = shift(to_video(q["start"])), shift(to_video(q["end"]))
    if end - start < 0.5:
        continue
    text = q["text"]
    if text.startswith("Done."):
        span = "Under a minute" if run_gap < 55 else "About a minute" if run_gap < 90 else \
            f"About {round(run_gap / 60)} minutes"
        text = f"{span} later, the run is done." + text[len("Done."):]
    n += 1
    lines += [str(n), f"{ts(start + 0.1)} --> {ts(end - 0.05)}", text, ""]
srt = out / "FinExtract_demo.srt"
srt.write_text("\n".join(lines), encoding="utf-8")

style = ("FontName=Inter,FontSize=11,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,"
         "BorderStyle=4,Outline=0,Shadow=0,MarginV=12,MarginL=40,MarginR=40,Alignment=2")
sel = "+".join(f"between(t,{x:.3f},{y:.3f})" for x, y in cuts) or "0"
vf = (f"fps={FPS},select='not({sel})',setpts=N/{FPS}/TB,crop={W}:{H - 8}:0:0,scale={W}:{H},"
      f"subtitles={srt}:force_style='{style}'")
mp4 = out / "FinExtract_demo.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", c["video"], "-vf", vf,
                "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(mp4)], check=True)
print(mp4, srt, f"run cut {run_gap:.0f}s, {len(cuts)} cuts")

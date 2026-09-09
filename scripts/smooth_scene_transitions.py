from pathlib import Path
import re

SOURCE = Path("src/what_if_daily_v2.py")
MARKER = "SMOOTH_TRANSITIONS_V1"

NEW_CREATE_VIDEO = r'''def create_video(scene_info):
    # SMOOTH_TRANSITIONS_V1: short visual crossfades at scene boundaries.
    # Each clip is extended just enough to overlap the next scene while
    # keeping visual scene timing aligned with the 7.25s audio scene grid.
    clips = []
    clip_duration = 7.46875
    transition = 0.25
    for i, info in enumerate(scene_info):
        clip = OUTPUT / f"clip_{i:02d}.mp4"
        txt = OUTPUT / f"typing_{i:02d}.txt"
        lines = []
        for p in info["frames"]:
            lines += [f"file '{Path(p).as_posix()}'", "duration 0.10"]
        hold = clip_duration - (len(info["frames"]) * 0.10)
        lines += [f"file '{Path(info['frames'][-1]).as_posix()}'", f"duration {max(0.10, hold):.3f}", f"file '{Path(info['frames'][-1]).as_posix()}'"]
        txt.write_text("\n".join(lines), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(txt),
             "-vf", "fps=30,format=yuv420p", "-t", str(clip_duration), "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", str(clip)])
        clips.append(clip)

    # xfade keeps the change between scenes smooth instead of a hard cut.
    # Offset is chosen so the 0.25s transition sits almost exactly on the
    # existing 7.25s narration boundary.
    inputs = []
    for clip in clips:
        inputs += ["-i", str(clip)]
    current = "[0:v]"
    offset = clip_duration - transition
    for i in range(1, len(clips)):
        out = f"[v{i}]"
        fc = f"{current}[{i}:v]xfade=transition=fade:duration={transition}:offset={offset:.5f}{out}"
        if i == 1:
            filter_complex = fc
        else:
            filter_complex += ";" + fc
        current = out
        offset += clip_duration - transition

    silent = OUTPUT / "video_silent.mp4"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
         "-map", current, "-t", str(DURATION), "-an", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(silent)])
    return silent
'''

text = SOURCE.read_text(encoding="utf-8")
if MARKER in text:
    print("Smooth scene transitions already applied.")
    raise SystemExit(0)

pattern = r"def create_video\(scene_info\):.*?\n\ndef create_voice\(text\):"
replacement = NEW_CREATE_VIDEO + "\n\ndef create_voice(text):"
updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise RuntimeError("Could not locate create_video() for smooth-transition patch")
SOURCE.write_text(updated, encoding="utf-8")
print("Applied smooth scene crossfade transitions to create_video().")

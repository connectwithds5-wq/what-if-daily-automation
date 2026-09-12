from pathlib import Path
import re
import subprocess

SOURCE = Path("src/what_if_daily_v2.py")
MARKER = "WAN22_PRIMARY_V3"

WAN_CREATE_VIDEO = r'''def create_video(scene_info):
    # WAN22_PRIMARY_V3: Wan 2.2 is the primary visual video engine.
    # If the Wan Space/model/API fails anywhere in the run, the original
    # smooth-transition renderer is used for the complete video.
    try:
        from gradio_client import Client
        import shutil

        space = os.getenv("HF_WAN_SPACE", "zerogpu-aoti/wan2-2-fp8da-aoti")
        token = os.getenv("HF_TOKEN") or None
        steps = int(os.getenv("WAN_STEPS", "4"))
        guidance = float(os.getenv("WAN_GUIDANCE", "1.0"))
        guidance2 = float(os.getenv("WAN_GUIDANCE_2", "3.0"))
        client = Client(space, token=token) if token else Client(space)
        raw_clips = []
        print(f"WAN PRIMARY: {space} — Wan 2.2 14B T2V")

        for i, scene in enumerate(scene_info):
            out = OUTPUT / f"wan22_scene_{i:02d}.mp4"
            prompt = ("Premium cinematic photorealistic scientific visualization, vertical 9:16, "
                      f"exact scene action: {scene.get('visual_prompt', '')}. "
                      "Dynamic but physically believable motion, cinematic camera movement, realistic lighting, "
                      "detailed environments, strong depth, high-end documentary cinematography. "
                      "No text, captions, letters, logos, UI or watermark.")[:1500]
            negative = "text, subtitles, letters, logo, watermark, blurry, low quality, distorted anatomy, extra limbs, duplicate objects, flicker, jitter, flat illustration"
            print(f"WAN PRIMARY scene {i + 1}/{len(scene_info)}")
            result = client.predict(prompt, negative, 3.5, guidance, guidance2, steps, 7000 + i, False, api_name="/generate_video")

            def extract(value):
                if isinstance(value, dict):
                    for key in ("video", "output", "file", "path"):
                        if isinstance(value.get(key), str) and value.get(key):
                            return value[key]
                if isinstance(value, (tuple, list)):
                    for item in value:
                        try:
                            return extract(item)
                        except RuntimeError:
                            pass
                if isinstance(value, str) and value:
                    return value
                raise RuntimeError(f"No Wan video path in result: {value!r}")

            source = Path(extract(result))
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, out)
            raw_clips.append(out)

        # Wan Space currently produces short clips. Slow each clip to the
        # existing 7.46875s scene grid so the narration/audio timing remains intact.
        clips = []
        target = 7.46875
        for i, raw in enumerate(raw_clips):
            clip = OUTPUT / f"clip_{i:02d}.mp4"
            run(["ffmpeg", "-y", "-i", str(raw), "-vf", f"setpts={target / 3.5:.6f}*PTS,tpad=stop_mode=clone:stop_duration=1,fps=30,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1", "-t", str(target), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(clip)])
            clips.append(clip)

        inputs = []
        for clip in clips:
            inputs += ["-i", str(clip)]
        current = "[0:v]"
        offset = target - 0.25
        filters = []
        for i in range(1, len(clips)):
            out = f"[v{i}]"
            filters.append(f"{current}[{i}:v]xfade=transition=fade:duration=0.25:offset={offset:.5f}{out}")
            current = out
            offset += target - 0.25
        silent = OUTPUT / "video_silent.mp4"
        run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", current, "-t", str(DURATION), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(silent)])
        if not silent.is_file() or silent.stat().st_size < 50000:
            raise RuntimeError("Wan 2.2 produced invalid final video")
        print("WAN PRIMARY SUCCESS")
        return silent
    except Exception as exc:
        print(f"WAN 2.2 PRIMARY FAILED: {exc}")
        print("FALLBACK: existing smooth cinematic renderer")
        return create_video_fallback(scene_info)
'''

text = SOURCE.read_text(encoding="utf-8")
if MARKER in text:
    print("Wan 2.2 primary already enabled.")
    raise SystemExit(0)

pattern = r"def create_video\(scene_info\):.*?\n\ndef create_voice\(text\):"
match = re.search(pattern, text, flags=re.S)
if not match:
    raise RuntimeError("Could not locate create_video()")
old = match.group(0)
old_body = old[:-len("\n\ndef create_voice(text):")]
renamed = old_body.replace("def create_video(scene_info):", "def create_video_fallback(scene_info):", 1)
replacement = renamed + "\n\n" + WAN_CREATE_VIDEO.rstrip() + "\n\ndef create_voice(text):"
updated = text[:match.start()] + replacement + text[match.end():]
compile(updated, str(SOURCE), "exec")
SOURCE.write_text(updated, encoding="utf-8")
print("Wan 2.2 primary + existing-renderer fallback enabled; source syntax check passed.")

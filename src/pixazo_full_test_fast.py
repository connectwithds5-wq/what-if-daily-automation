import os
import json
import time
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import what_if_daily_v2 as wf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "pixazo_full_test_fast"
AUDIO = OUT / "audio"
CLIPS = OUT / "clips"
OUT.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)
CLIPS.mkdir(parents=True, exist_ok=True)
wf.AUDIO = AUDIO

PIXAZO_KEY = os.getenv("PIXAZO_API_KEY", "").strip()
API_BASE = "https://gateway.pixazo.ai"
SUBMIT_URL = f"{API_BASE}/ltx-video/v1/text-to-video"
SCENE_SECONDS = 5
POLL_SECONDS = 3
MAX_WORKERS = 4

STORY = {
    "title": "What If Earth Suddenly Stopped Spinning?",
    "scenes": [
        {"narration": "What if Earth suddenly stopped spinning? The change would begin almost instantly.", "on_screen": "EARTH STOPS SPINNING", "sfx_type": "space", "visual_prompt": "Premium cinematic scientific visualization, vertical 9:16. Realistic Earth from near-space, high orbital drone camera slowly pushing toward the planet while Earth rotates visibly. Blue oceans, continents, moving white clouds, realistic atmosphere, dramatic sunlight and subtle rim light. Smooth controlled camera movement, photorealistic CGI, polished documentary quality, no text, no logos, no watermark."},
        {"narration": "The ground would stop, but the air and oceans would keep rushing at incredible speed.", "on_screen": "AIR AND OCEANS KEEP MOVING", "sfx_type": "wind", "visual_prompt": "Premium cinematic scientific visualization, vertical 9:16. Realistic coastal city and huge ocean from a sweeping high aerial camera. Solid ground is suddenly still while massive air currents and ocean water surge across the landscape. Controlled cinematic drone movement, realistic water and clouds, dramatic sunlight, photorealistic scientific CGI, no text, no logos, no watermark."},
        {"narration": "Near the equator, that motion would be strongest, creating devastating winds and enormous waves.", "on_screen": "MASSIVE WINDS AND WAVES", "sfx_type": "ocean", "visual_prompt": "Premium cinematic scientific visualization, vertical 9:16. Near-equatorial coastline from a dramatic elevated tracking camera. Enormous ocean waves race inland while powerful winds drive clouds and spray. Huge scale and motion, realistic physics-inspired water and atmosphere, cinematic lighting, sharp photorealistic CGI, professional science documentary look, no text, no logos, no watermark."},
        {"narration": "And over time, Earth would become a completely different world, with oceans shifting toward the poles.", "on_screen": "A VERY DIFFERENT EARTH", "sfx_type": "rumble", "visual_prompt": "Premium cinematic scientific visualization, vertical 9:16. Epic near-space orbital view of Earth after rotation has stopped, camera slowly pulling back to reveal a changed planet with oceans redistributed toward polar regions. Detailed continents, realistic atmosphere and clouds, warm horizon sunlight, deep cinematic depth, elegant controlled camera movement, photorealistic high-end CGI, final dramatic payoff, no text, no logos, no watermark."},
    ],
}

NEGATIVE = "low quality, blurry, static shot, shaky camera, frantic movement, random zoom, flicker, jitter, warped Earth, deformed continents, duplicated clouds, melting objects, cartoon, anime, text, captions, subtitles, letters, numbers, logo, watermark, UI, border"


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        print(p.stdout[-4000:])
        raise RuntimeError(f"Command failed: {cmd}")
    return p.stdout


def pixazo(prompt, index):
    headers = {"Content-Type": "application/json", "Ocp-Apim-Subscription-Key": PIXAZO_KEY}
    payload = {"prompt": prompt, "negative": NEGATIVE, "aspect": "9:16", "num_frames": 121, "frame_rate": 24, "steps": 8, "cfg": 3.0}
    print(f"🎬 Scene {index + 1}/4 submitted")
    r = requests.post(SUBMIT_URL, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    request_id = data.get("request_id")
    status_url = data.get("polling_url") or f"{API_BASE}/v2/requests/status/{request_id}"
    if not request_id:
        raise RuntimeError(f"No request_id from Pixazo: {data}")
    for n in range(500):
        time.sleep(POLL_SECONDS)
        s = requests.get(status_url, headers={"Ocp-Apim-Subscription-Key": PIXAZO_KEY}, timeout=60)
        s.raise_for_status()
        status = s.json()
        state = str(status.get("status", "")).upper()
        if n % 5 == 0:
            print(f"Scene {index + 1}: {state} | {n * POLL_SECONDS}s")
        if state == "COMPLETED":
            output = status.get("output") or {}
            media = output.get("media_url")
            if isinstance(media, list):
                media = media[0] if media else None
            if not media:
                raise RuntimeError(f"Completed without media_url: {status}")
            raw = CLIPS / f"raw_{index + 1:02d}.mp4"
            with requests.get(media, stream=True, timeout=180) as d:
                d.raise_for_status()
                with raw.open("wb") as f:
                    for chunk in d.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
            return index, raw
        if state in {"FAILED", "ERROR", "CANCELLED", "CANCELED"}:
            raise RuntimeError(f"Pixazo scene {index + 1} failed: {status}")
    raise TimeoutError(f"Pixazo scene {index + 1} exceeded timeout")


def normalize_and_caption(raw, scene, index):
    out = CLIPS / f"final_{index + 1:02d}.mp4"
    text = scene["on_screen"].replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{text}':fontcolor=white:fontsize=68:borderw=5:bordercolor=black@0.8:x=(w-text_w)/2:y=h*0.72"
    )
    run(["ffmpeg", "-y", "-i", str(raw), "-vf", vf, "-t", str(SCENE_SECONDS), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
    return out


def build_audio():
    scene_audio = []
    for i, scene in enumerate(STORY["scenes"]):
        voice = wf.create_scene_voice(scene["narration"], i)
        sfx = wf.create_scene_sfx(scene.get("sfx_type", "none"), i)
        mixed = AUDIO / f"scene_{i + 1:02d}.m4a"
        run(["ffmpeg", "-y", "-i", str(voice), "-i", str(sfx), "-filter_complex", "[0:a]volume=1.0[v];[1:a]volume=0.32[s];[v][s]amix=inputs=2:duration=longest:dropout_transition=0.1,alimiter=limit=0.90[a]", "-map", "[a]", "-t", str(SCENE_SECONDS), "-c:a", "aac", "-b:a", "160k", str(mixed)])
        scene_audio.append(mixed)
    music = wf.create_music()
    concat = AUDIO / "scene_audio_concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in scene_audio), encoding="utf-8")
    speech_sfx = AUDIO / "speech_sfx.m4a"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(speech_sfx)])
    final_audio = AUDIO / "final_audio.m4a"
    run(["ffmpeg", "-y", "-i", str(speech_sfx), "-i", str(music), "-filter_complex", "[0:a]volume=1.0[a0];[1:a]volume=0.18[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0.2,alimiter=limit=0.90[a]", "-map", "[a]", "-t", "20", "-c:a", "aac", "-b:a", "160k", str(final_audio)])
    return final_audio


def concat_video(clips, audio):
    concat = OUT / "video_concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    silent = OUT / "video_silent.mp4"
    final = OUT / "pixazo_what_if_full_test.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)])
    run(["ffmpeg", "-y", "-i", str(silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-t", "20", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final)])
    return final


def main():
    if not PIXAZO_KEY:
        raise RuntimeError("PIXAZO_API_KEY is missing")
    print("=== FAST PIXAZO WHAT-IF TEST ===")
    print(STORY["title"])

    # Start all four Pixazo jobs together. In parallel, build voice/music/SFX.
    audio_future = None
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        pixazo_futures = [pool.submit(pixazo, scene["visual_prompt"], i) for i, scene in enumerate(STORY["scenes"])]
        audio_future = pool.submit(build_audio)
        results = [None] * len(STORY["scenes"])
        for future in as_completed(pixazo_futures):
            index, raw = future.result()
            results[index] = raw
            print(f"✅ Pixazo scene {index + 1}/4 ready")
        audio = audio_future.result()

    final_clips = [normalize_and_caption(results[i], STORY["scenes"][i], i) for i in range(len(results))]
    final = concat_video(final_clips, audio)
    (OUT / "story.json").write_text(json.dumps(STORY, indent=2), encoding="utf-8")
    print(f"FINAL_VIDEO={final}")
    print(f"SIZE_MB={final.stat().st_size / (1024 * 1024):.2f}")


if __name__ == "__main__":
    main()

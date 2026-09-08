import os
import re
import json
import random
import shutil
import subprocess
import hashlib
import time
import base64
from pathlib import Path
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

WIDTH, HEIGHT, FPS, DURATION = 1080, 1920, 30, 60
SCENES = 8
SCENE_DURATION = DURATION / SCENES
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
FRAMES = OUTPUT / "frames"
AUDIO = OUTPUT / "audio"
VIDEO = OUTPUT / "what_if_daily.mp4"
METADATA = OUTPUT / "metadata.json"
HISTORY = ROOT / "topic_history.json"

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VOICE = os.getenv("TTS_VOICE", "en-US-AndrewMultilingualNeural")
TTS_RATE = os.getenv("TTS_RATE", "+5%")
CF_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CF_ACCOUNT = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
VISUALS_ENABLED = os.getenv("VISUALS_ENABLED", "true").lower() == "true"

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")
CLIENT = genai.Client(api_key=GEMINI_API_KEY)


def run(cmd):
    print("RUN:", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(p.stdout[-5000:])
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return p


def clean():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    FRAMES.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)


def safe_ascii(text, max_len=5000):
    text = str(text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9 .,!?':;()/%+\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:max_len]


def font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def wrap(text, f, max_width):
    words = safe_ascii(text, 1200).split()
    lines, current = [], ""
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for word in words:
        test = word if not current else current + " " + word
        if d.textbbox((0, 0), test, font=f)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def load_history():
    if not HISTORY.exists():
        return []
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(items):
    HISTORY.write_text(json.dumps(items[-1000:], ensure_ascii=False, indent=2), encoding="utf-8")


def transient(exc):
    s = str(exc).upper()
    return any(x in s for x in ["500", "502", "503", "504", "429", "INTERNAL", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED", "HIGH DEMAND", "RATE LIMIT", "TEMPORARY"])


def is_quota_error(exc):
    s = str(exc).upper()
    return any(x in s for x in ["RESOURCE_EXHAUSTED", "QUOTA", "GENERATEREQUESTSPERDAY", "FREE_TIER", "FREE TIER"])


def gemini_text(prompt, attempts=2):
    models = list(dict.fromkeys([m for m in [GEMINI_MODEL, GEMINI_FALLBACK_MODEL] if m]))
    last_error = None
    for model in models:
        for i in range(attempts):
            try:
                print(f"Gemini model: {model} | attempt {i + 1}/{attempts}")
                response = CLIENT.models.generate_content(model=model, contents=prompt)
                text = (getattr(response, "text", "") or "").strip()
                if text:
                    return text
                raise RuntimeError("Gemini returned an empty response")
            except Exception as exc:
                last_error = exc
                print(f"Gemini {model} attempt {i + 1}/{attempts} failed: {exc}")
                upper = str(exc).upper()
                if is_quota_error(exc) or "503" in upper or "UNAVAILABLE" in upper or "HIGH DEMAND" in upper:
                    print(f"Switching away from {model} immediately.")
                    break
                if not transient(exc) or i == attempts - 1:
                    break
                wait = 2 ** i
                print(f"Retrying {model} in {wait}s...")
                time.sleep(wait)
    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def generate_unique_topic():
    history = load_history()
    prompt = f"""
You create one fresh topic for a YouTube Shorts channel called WHAT IF DAILY.
Return ONLY one topic, no quotes, no numbering.
It must start with What If and be scientifically plausible, surprising, highly visual, and different from all previous topics.
Keep it under 80 characters.
Previous topics:
{json.dumps(history[-200:], ensure_ascii=False)}
"""
    old = {re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip() for x in history}
    for _ in range(5):
        topic = safe_ascii(gemini_text(prompt, attempts=2), 100).strip().strip('"')
        norm = re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()
        if topic and norm not in old:
            history.append(topic)
            save_history(history)
            return topic
        prompt += "\nGenerate a completely different topic."
    raise RuntimeError("Could not generate a unique topic")


def create_story(topic):
    prompt = f"""
Create an exciting 60-second WHAT IF DAILY science short about: {topic}
Return ONLY valid JSON with exactly this structure:
{{"title":"...","description":"...","keywords":["..."],"hashtags":["#..."],"scenes":[{{"narration":"...","on_screen":"...","visual_prompt":"..."}}]}}
Rules:
- Exactly 8 scenes.
- Total narration should fit about 55-60 seconds.
- Each narration is 18-30 words, natural spoken English, factual but entertaining.
- on_screen is punchy English text, maximum 55 characters.
- visual_prompt must describe the EXACT thing being narrated in that scene.
- visual_prompt must be a cinematic, photorealistic scientific visualization suitable for a vertical 9:16 Short.
- Every scene must have a clearly different visual event from the previous scene.
- Use concrete objects, environments, scale and motion relevant to the narration.
- No generic portraits or random people unless the narration requires them.
- No text, letters, numbers, logos, labels, UI, watermark, infographic text, or captions inside generated images.
- The final scene must show the actual consequence/payoff, not repeat the opening.
- Story progression: hook -> immediate effect -> escalation -> consequence -> surprising detail -> peak -> twist -> final payoff.
"""
    raw = gemini_text(prompt, attempts=2)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"Story JSON parse failed: {exc}\n{raw[:1200]}")
    scenes = data.get("scenes", [])
    if len(scenes) != SCENES:
        raise RuntimeError(f"Gemini returned {len(scenes)} scenes; expected {SCENES}")
    for i, scene in enumerate(scenes):
        scene["narration"] = safe_ascii(scene.get("narration", ""), 700)
        scene["on_screen"] = safe_ascii(scene.get("on_screen", ""), 55)
        scene["visual_prompt"] = safe_ascii(scene.get("visual_prompt", ""), 1800)
        if not scene["narration"]:
            raise RuntimeError(f"Empty narration scene {i + 1}")
        if not scene["on_screen"]:
            scene["on_screen"] = scene["narration"][:55]
        if not scene["visual_prompt"]:
            raise RuntimeError(f"Empty visual prompt scene {i + 1}")
    return data


def cloudflare_image(prompt, output_path):
    if not (VISUALS_ENABLED and CF_TOKEN and CF_ACCOUNT):
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}"
    request_prompt = f"""Cinematic scientific visualization for a premium YouTube Shorts video, vertical 9:16. Photorealistic, dramatic but scientifically grounded, realistic scale, strong depth, clear foreground/midground/background, one obvious visual event. No text, letters, numbers, logos, labels, UI, watermark, captions or infographic elements. Original visual only. SCENE: {prompt}"""
    payload = {"prompt": request_prompt[:3500], "steps": 4}
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}
    for attempt in range(5):
        try:
            print(f"Cloudflare visual attempt {attempt + 1}/5")
            response = requests.post(url, headers=headers, json=payload, timeout=180)
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "")
                wait = int(retry) if retry.isdigit() else min(60, 10 * (attempt + 1))
                print(f"Cloudflare rate limit; waiting {wait}s")
                if attempt < 4:
                    time.sleep(wait)
                    continue
                return False
            if response.status_code >= 500:
                print(f"Cloudflare server error {response.status_code}")
                if attempt < 4:
                    time.sleep(min(60, 8 * (attempt + 1)))
                    continue
                return False
            response.raise_for_status()
            data = response.json()
            result = data.get("result", data)
            image_b64 = result.get("image") if isinstance(result, dict) else None
            if not image_b64 and isinstance(result, dict) and isinstance(result.get("images"), list) and result["images"]:
                image_b64 = result["images"][0]
            if not image_b64:
                raise RuntimeError(f"Cloudflare response did not contain an image: {str(data)[:800]}")
            if image_b64.startswith("data:image"):
                image_b64 = image_b64.split(",", 1)[1]
            output_path.write_bytes(base64.b64decode(image_b64))
            if output_path.exists() and output_path.stat().st_size > 0:
                print("Visual saved:", output_path)
                return True
        except Exception as exc:
            print("Cloudflare visual error:", exc)
            if attempt < 4:
                time.sleep(min(60, 5 * (attempt + 1)))
    return False


def make_base_visual(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.png"
    if cloudflare_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            return path
        except Exception as exc:
            print("Visual decode/resize failed:", exc)
    print(f"Visual generation failed for scene {index + 1}; using cinematic fallback.")
    seed = int(hashlib.sha256(f"{topic}:{index}".encode()).hexdigest()[:8], 16)
    random.seed(seed)
    base = Image.new("RGB", (WIDTH, HEIGHT), (6, 8, 16))
    draw = ImageDraw.Draw(base)
    cx, cy = random.randint(150, 930), random.randint(450, 1450)
    for radius in (600, 450, 300, 180):
        draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=(35, 38, 55), width=3)
    base.save(path)
    return path


def overlay_frame(background, on_screen, narration, index, progress):
    image = background.copy()
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(0, 430):
        alpha = int(190 * (1 - y / 430))
        draw.rectangle((0, y, WIDTH, y + 1), fill=(0, 0, 0, alpha))
    for y in range(1450, HEIGHT):
        alpha = int(210 * ((y - 1450) / (HEIGHT - 1450)))
        draw.rectangle((0, y, WIDTH, y + 1), fill=(0, 0, 0, alpha))
    small = font(30, True)
    main = font(94, True)
    sub = font(38)
    draw.text((60, 60), "WHAT IF DAILY", font=small, fill=(255, 255, 255, 235))
    counter = f"SCENE {index + 1}/{SCENES}"
    bb = draw.textbbox((0, 0), counter, font=small)
    draw.text((WIDTH - 60 - (bb[2] - bb[0]), 63), counter, font=small, fill=(220, 220, 220, 230))
    on = safe_ascii(on_screen, 55).upper()
    typed = on[:max(1, int(len(on) * progress))]
    lines = wrap(typed, main, 900)[:3] or [""]
    y = 650 - (len(lines) * 115) // 2
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=main, stroke_width=3)
        x = (WIDTH - (bb[2] - bb[0])) // 2
        draw.text((x + 5, y + 7), line, font=main, fill=(0, 0, 0, 220), stroke_width=7, stroke_fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=main, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += 115
    if progress > 0.45:
        for j, line in enumerate(wrap(narration, sub, 900)[:2]):
            bb = draw.textbbox((0, 0), line, font=sub)
            x = (WIDTH - (bb[2] - bb[0])) // 2
            draw.text((x, 1510 + j * 52), line, font=sub, fill=(235, 235, 235, 235), stroke_width=1, stroke_fill=(0, 0, 0, 180))
    draw.rectangle((60, 1815, WIDTH - 60, 1822), fill=(90, 90, 90, 170))
    draw.rectangle((60, 1815, 60 + int((WIDTH - 120) * ((index + progress) / SCENES)), 1822), fill=(255, 255, 255, 240))
    return image


def create_typography_scene(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    source = make_base_visual(topic, scene, index)
    base = Image.open(source).convert("RGB")
    paths = []
    for frame_index in range(18):
        scale = 1.0 + 0.045 * (frame_index / 17)
        crop_w = int(WIDTH / scale)
        crop_h = int(HEIGHT / scale)
        left = int((WIDTH - crop_w) * (0.15 + 0.70 * frame_index / 17))
        top = int((HEIGHT - crop_h) * (0.55 - 0.35 * frame_index / 17))
        left = max(0, min(WIDTH - crop_w, left))
        top = max(0, min(HEIGHT - crop_h, top))
        frame = base.crop((left, top, left + crop_w, top + crop_h)).resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        frame = overlay_frame(frame, scene["on_screen"], scene["narration"], index, (frame_index + 1) / 18)
        path = folder / f"frame_{frame_index:02d}.jpg"
        frame.save(path, quality=90, optimize=True)
        paths.append(path)
    return {"frames": [str(p) for p in paths]}


def create_video(scene_info):
    clips = []
    for i, info in enumerate(scene_info):
        clip = OUTPUT / f"clip_{i:02d}.mp4"
        listing = OUTPUT / f"frames_{i:02d}.txt"
        lines = []
        for path in info["frames"]:
            lines += [f"file '{Path(path).as_posix()}'", "duration 0.10"]
        lines += [f"file '{Path(info['frames'][-1]).as_posix()}'", f"duration {SCENE_DURATION - 1.8:.3f}", f"file '{Path(info['frames'][-1]).as_posix()}'"]
        listing.write_text("\n".join(lines), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-vf", "fps=30,format=yuv420p", "-t", str(SCENE_DURATION), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip)])
        clips.append(clip)
    concat = OUTPUT / "concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    silent = OUTPUT / "video_silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)])
    return silent


def create_voice(text):
    out = AUDIO / "narration.mp3"
    run(["edge-tts", "--voice", VOICE, "--rate", TTS_RATE, "--text", safe_ascii(text, 5000), "--write-media", str(out)])
    return out


def create_music():
    out = AUDIO / "music.m4a"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=44100", "-f", "lavfi", "-i", "sine=frequency=165:sample_rate=44100", "-t", "60", "-filter_complex", "[0:a]volume=0.025[a];[1:a]volume=0.012[b];[a][b]amix=inputs=2:duration=first", "-c:a", "aac", "-b:a", "128k", str(out)])
    return out


def create_sound_design():
    out = AUDIO / "sfx.m4a"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.004:sample_rate=44100", "-t", "60", "-c:a", "aac", "-b:a", "96k", str(out)])
    return out


def mix_audio(video, narration, music, sfx):
    audio = AUDIO / "final_audio.m4a"
    run(["ffmpeg", "-y", "-i", str(narration), "-i", str(music), "-i", str(sfx), "-filter_complex", "[0:a]volume=1.12[n];[1:a]volume=0.70[m];[2:a]volume=0.25[s];[n][m][s]amix=inputs=3:duration=longest:dropout_transition=2,alimiter=limit=0.9[a]", "-map", "[a]", "-t", "60", "-c:a", "aac", "-b:a", "192k", str(audio)])
    run(["ffmpeg", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(VIDEO)])
    return VIDEO


def save_metadata(story):
    title = safe_ascii(story.get("title", "WHAT IF DAILY"), 95)
    description = safe_ascii(story.get("description", ""), 4500) + "\n\nWHAT IF DAILY - IMAGINE. WATCH. WONDER.\n\nVisual format: cinematic scientific visualization + kinetic typography."
    data = {"title": title, "description": description, "keywords": story.get("keywords", [])[:25], "hashtags": story.get("hashtags", [])[:8], "created_at": datetime.utcnow().isoformat() + "Z", "voice": VOICE, "tts_rate": TTS_RATE, "visual_style": "cinematic scientific visualization + kinetic typography"}
    METADATA.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def upload_youtube(meta):
    raw = os.getenv("YOUTUBE_OAUTH_JSON")
    if not raw:
        print("YOUTUBE_OAUTH_JSON missing; skipping upload")
        return
    data = json.loads(raw)
    credentials = Credentials(None, refresh_token=data["refresh_token"], token_uri="https://oauth2.googleapis.com/token", client_id=data["client_id"], client_secret=data["client_secret"], scopes=["https://www.googleapis.com/auth/youtube.upload"])
    youtube = build("youtube", "v3", credentials=credentials)
    description = safe_ascii(meta["description"], 4900)
    hashtags = meta.get("hashtags", [])
    if hashtags:
        description += "\n\n" + " ".join(hashtags)
    body = {"snippet": {"title": meta["title"], "description": description, "tags": meta.get("keywords", [])[:25], "categoryId": "28"}, "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    print("Uploading to YouTube:", body["snippet"]["title"])
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(VIDEO), mimetype="video/mp4", resumable=True))
    result = request.execute()
    print("YouTube upload complete:", result.get("id"))


def main():
    clean()
    manual_topic = os.getenv("WHAT_IF_TOPIC", "").strip()
    topic = manual_topic or generate_unique_topic()
    print("Topic:", topic)
    print("Voice:", VOICE, "Rate:", TTS_RATE)
    print("Visuals enabled:", VISUALS_ENABLED, "Cloudflare configured:", bool(CF_TOKEN and CF_ACCOUNT))
    story = create_story(topic)
    scenes = [create_typography_scene(topic, scene, i) for i, scene in enumerate(story["scenes"])]
    narration = create_voice(" ".join(scene["narration"] for scene in story["scenes"]))
    music = create_music()
    sfx = create_sound_design()
    silent = create_video(scenes)
    mix_audio(silent, narration, music, sfx)
    metadata = save_metadata(story)
    upload_youtube(metadata)
    print("DONE:", VIDEO)


if __name__ == "__main__":
    main()

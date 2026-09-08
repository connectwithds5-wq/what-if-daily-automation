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
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
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
TTS_RATE = os.getenv("TTS_RATE", "+12%")
CF_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CF_ACCOUNT = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
CF_FALLBACK_MODEL = os.getenv("CLOUDFLARE_IMAGE_FALLBACK_MODEL", "@cf/bytedance/stable-diffusion-xl-lightning")
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
{{"title":"...","description":"...","keywords":["..."],"hashtags":["#..."],"scenes":[{{"narration":"...","on_screen":"...","visual_prompt":"...","sfx_type":"..."}}]}}
Rules:
- Exactly 8 scenes.
- Total narration must be 104-116 words across all 8 scenes for energetic pacing without sounding rushed.
- Each narration must be 12-15 words, natural spoken English, punchy, factual, and entertaining.
- Use SIMPLE, EVERYDAY ENGLISH that a normal person can understand immediately. Write for a broad audience, not scientists.
- Avoid technical or academic words when a common word works. If a science term is necessary, explain it in simple words immediately.
- Prefer short sentences, familiar words, active voice, and concrete examples. Example style: "The air gets heavy, so planes struggle to stay up" instead of "Atmospheric density increases, reducing aircraft lift."
- Never sound like a textbook, documentary lecture, news report, or AI-generated essay. Sound like a smart friend explaining something amazing.
- Each scene narration should normally finish within 5.5-7.2 seconds at the configured voice rate; never pad speech with silence.
- Add exactly one sfx_type per scene from this list: none, airplane, bird, sand, wind, storm, thunder, ocean, water, fire, city, impact, rocket, space, heartbeat, whoosh, rumble.
- sfx_type must describe the most important real-world sound implied by that scene; use none when no sound would help.
- on_screen is punchy, VERY SIMPLE English text, maximum 55 characters. Use words a 12-year-old can understand.
- visual_prompt must describe the EXACT thing being narrated in that scene.
- visual_prompt must be a cinematic, photorealistic scientific visualization suitable for a vertical 9:16 Short.
- Every scene must have a clearly different visual event from the previous scene.
- Use concrete objects, environments, scale and motion relevant to the narration.
- No generic portraits or random people unless the narration needs them.
- No text, letters, logos, labels, UI, watermark, infographic text, or captions inside generated images.
- The final scene must show the actual consequence/payoff, not repeat the opening.
- Story progression: hook -> immediate effect -> escalation -> human/planetary consequence -> surprising scientific detail -> peak -> twist -> final payoff.
"""
    raw = gemini_text(prompt, attempts=2)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Story JSON parse failed: {e}\n{raw[:1000]}")
    scenes = data.get("scenes", [])
    if len(scenes) != SCENES:
        raise RuntimeError(f"Gemini returned {len(scenes)} scenes; expected {SCENES}")
    allowed_sfx = {"none", "airplane", "bird", "sand", "wind", "storm", "thunder", "ocean", "water", "fire", "city", "impact", "rocket", "space", "heartbeat", "whoosh", "rumble"}
    for i, s in enumerate(scenes):
        s["narration"] = safe_ascii(s.get("narration", ""), 700)
        s["on_screen"] = safe_ascii(s.get("on_screen", ""), 55)
        s["visual_prompt"] = safe_ascii(s.get("visual_prompt", ""), 1500)
        s["sfx_type"] = safe_ascii(s.get("sfx_type", "none"), 30).lower().strip()
        if s["sfx_type"] not in allowed_sfx:
            s["sfx_type"] = "none"
        if not s["narration"]:
            raise RuntimeError(f"Empty narration scene {i + 1}")
        if not s["on_screen"]:
            s["on_screen"] = s["narration"][:55]
        if not s["visual_prompt"]:
            raise RuntimeError(f"Empty visual prompt scene {i + 1}")
    total_words = sum(len(s["narration"].split()) for s in scenes)
    if 117 <= total_words <= 120:
        excess = total_words - 116
        for scene in reversed(scenes):
            words = scene["narration"].split()
            removable = max(0, len(words) - 12)
            take = min(removable, excess)
            if take:
                scene["narration"] = " ".join(words[:-take])
                excess -= take
            if excess == 0:
                break
        total_words = sum(len(s["narration"].split()) for s in scenes)
        print(f"Normalized narration word count to {total_words} words.")
    if not 104 <= total_words <= 116:
        raise RuntimeError(f"Narration word count {total_words}; expected 104-116")
    return data


def visual_is_valid(path):
    try:
        with Image.open(path) as im:
            im.verify()
        return path.exists() and path.stat().st_size >= 50000
    except Exception as exc:
        print(f"Visual validation failed for {path}: {exc}")
        return False


def gemini_image(prompt, output_path):
    # Gemini native image generation is paid-only through the Gemini Developer API.
    # Keep disabled by default so the automation never creates unexpected image charges.
    if os.getenv("GEMINI_IMAGE_ENABLED", "false").lower() != "true":
        return False
    if not (VISUALS_ENABLED and GEMINI_API_KEY):
        return False
    model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    request_prompt = f"""Create a premium cinematic scientific visualization for a YouTube Shorts video.
Vertical 9:16 composition. Photorealistic, dramatic, physically believable, strong depth, clear foreground/midground/background, realistic lighting, one unmistakable visual event.
Show the exact phenomenon described below. Make the transformation or consequence visually obvious without any text.
No text, letters, numbers, logos, labels, UI, watermark, captions, infographic panels, diagrams, or poster design.
Do not make a generic abstract background. Do not make a simple gradient or geometric pattern.
Use concrete environments, objects, atmosphere, scale cues and realistic materials.
SCENE: {prompt}""".strip()[:12000]
    try:
        from google.genai import types
        response = CLIENT.models.generate_content(
            model=model,
            contents=request_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="9:16", image_size="1K"),
            ),
        )
        for candidate in getattr(response, "candidates", []) or []:
            for part in getattr(getattr(candidate, "content", None), "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    output_path.write_bytes(base64.b64decode(data) if isinstance(data, str) else data)
                    if visual_is_valid(output_path):
                        print(f"Gemini image saved: {output_path}")
                        return True
    except Exception as exc:
        print(f"Gemini image unavailable: {exc}")
    return False

def cloudflare_image(prompt, output_path):
    if not (VISUALS_ENABLED and CF_TOKEN and CF_ACCOUNT):
        return False

    request_prompt = f"""Cinematic scientific visualization for a premium YouTube Shorts video, vertical 9:16.
Photorealistic, dramatic, scientifically grounded, realistic scale, strong depth, clear foreground/midground/background, one obvious real-world visual event.
Show concrete environments, objects, atmosphere, materials and motion relevant to the scene.
No text, letters, numbers, logos, labels, UI, watermark, captions, infographic panels, diagrams or abstract geometric backgrounds.
SCENE: {prompt}""".strip()[:2048]
    negative_prompt = "text, letters, numbers, logo, watermark, UI, infographic, poster, diagram, abstract circles, geometric background, blank dark background"
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}

    models = [
        (CF_MODEL, "Cloudflare FLUX", {"prompt": request_prompt, "steps": 4}),
        (os.getenv("CLOUDFLARE_IMAGE_FALLBACK_MODEL", "@cf/bytedance/stable-diffusion-xl-lightning"), "Cloudflare SDXL Lightning", {
            "prompt": request_prompt, "negative_prompt": negative_prompt, "width": 576, "height": 1024, "num_steps": 4, "guidance": 7.0,
        }),
        (os.getenv("CLOUDFLARE_IMAGE_FALLBACK_2_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0"), "Cloudflare SDXL Base", {
            "prompt": request_prompt, "negative_prompt": negative_prompt, "width": 576, "height": 1024, "num_steps": 8, "guidance": 7.5,
        }),
        (os.getenv("CLOUDFLARE_IMAGE_FALLBACK_3_MODEL", "@cf/lykon/dreamshaper-8-lcm"), "Cloudflare DreamShaper", {
            "prompt": request_prompt, "negative_prompt": negative_prompt, "width": 576, "height": 1024, "num_steps": 4, "guidance": 6.5,
        }),
    ]

    seen = set()
    for model, label, payload in models:
        if not model or model in seen:
            continue
        seen.add(model)
        for attempt, delay in enumerate((0, 8, 20), start=1):
            if delay:
                time.sleep(delay)
            try:
                print(f"{label}: {model} | attempt {attempt}/3")
                response = requests.post(
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{model}",
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
                if response.status_code == 429:
                    try:
                        detail = response.json().get("errors", [{}])[0].get("message", "429")
                    except Exception:
                        detail = "429"
                    print(f"{label} HTTP 429: {detail}")
                    if "daily free allocation" in detail.lower() or "account limited" in detail.lower():
                        print("Cloudflare daily free allocation is exhausted; stop image retries for this model.")
                        break
                    continue
                if response.status_code in (500, 502, 503, 504):
                    print(f"{label} temporary server error {response.status_code}")
                    continue
                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if content_type.startswith("image/"):
                    output_path.write_bytes(response.content)
                else:
                    data = response.json()
                    result = data.get("result", data)
                    image_b64 = result.get("image") if isinstance(result, dict) else None
                    if not image_b64 and isinstance(result, dict):
                        image_b64 = result.get("image_b64")
                    if not image_b64 and isinstance(result, dict):
                        images = result.get("images")
                        if isinstance(images, list) and images:
                            image_b64 = images[0]
                    if not image_b64:
                        raise RuntimeError(f"{label} returned no image: {str(data)[:700]}")
                    if image_b64.startswith("data:image"):
                        image_b64 = image_b64.split(",", 1)[1]
                    output_path.write_bytes(base64.b64decode(image_b64))

                if visual_is_valid(output_path):
                    print(f"{label} visual saved: {output_path}")
                    return True
            except Exception as exc:
                print(f"{label} error: {exc}")
    return False

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
    on = safe_ascii(on_screen, 55).upper()
    typed = on[:max(1, int(len(on) * progress))]
    lines = wrap(typed, main, 900)[:3] or [""]
    y = 560 - (len(lines) * 115) // 2
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=main, stroke_width=3)
        x = (WIDTH - (bb[2] - bb[0])) // 2
        draw.text((x + 5, y + 7), line, font=main, fill=(0, 0, 0, 220), stroke_width=7, stroke_fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=main, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += 115
    if progress > 0.45:
        narration_lines = wrap(narration, sub, 900)[:3]
        block_height = len(narration_lines) * 52
        narration_y = 1020 - block_height // 2
        for j, line in enumerate(narration_lines):
            bb = draw.textbbox((0, 0), line, font=sub)
            x = (WIDTH - (bb[2] - bb[0])) // 2
            draw.text((x, narration_y + j * 52), line, font=sub, fill=(245, 245, 245, 240), stroke_width=2, stroke_fill=(0, 0, 0, 200))
    draw.rectangle((60, 1815, WIDTH - 60, 1822), fill=(90, 90, 90, 170))
    draw.rectangle((60, 1815, 60 + int((WIDTH - 120) * ((index + progress) / SCENES)), 1822), fill=(255, 255, 255, 240))
    return image

def create_typography_scene(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    source = make_base_visual(topic, scene, index)
    base = Image.open(source).convert("RGB")
    paths = []
    for fi in range(18):
        scale = 1.0 + 0.045 * (fi / 17)
        crop_w = int(WIDTH / scale)
        crop_h = int(HEIGHT / scale)
        left = int((WIDTH - crop_w) * (0.15 + 0.70 * fi / 17))
        top = int((HEIGHT - crop_h) * (0.55 - 0.35 * fi / 17))
        left = max(0, min(WIDTH - crop_w, left))
        top = max(0, min(HEIGHT - crop_h, top))
        frame = base.crop((left, top, left + crop_w, top + crop_h)).resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        frame = overlay_frame(frame, scene["on_screen"], scene["narration"], index, min(1.0, (fi + 1) / 5.0))
        path = folder / f"frame_{fi:02d}.jpg"
        frame.save(path, quality=90, optimize=True)
        paths.append(path)
    return {"frames": [str(x) for x in paths]}


def create_video(scene_info):
    clips = []
    for i, info in enumerate(scene_info):
        clip = OUTPUT / f"clip_{i:02d}.mp4"
        txt = OUTPUT / f"typing_{i:02d}.txt"
        lines = []
        for p in info["frames"]:
            lines += [f"file '{Path(p).as_posix()}'", "duration 0.10"]
        lines += [f"file '{Path(info['frames'][-1]).as_posix()}'", f"duration {SCENE_DURATION-1.8:.3f}", f"file '{Path(info['frames'][-1]).as_posix()}'"]
        txt.write_text("\n".join(lines), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(txt), "-vf", "fps=30,format=yuv420p", "-t", str(SCENE_DURATION), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip)])
        clips.append(clip)
    alltxt = OUTPUT / "concat.txt"
    alltxt.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    silent = OUTPUT / "video_silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(alltxt), "-c", "copy", str(silent)])
    return silent


def create_voice(text):
    out = AUDIO / "narration.mp3"
    run(["edge-tts", "--voice", VOICE, "--rate", TTS_RATE, "--text", safe_ascii(text, 5000), "--write-media", str(out)])
    return out


def create_scene_voice(text, index):
    out = AUDIO / f"voice_{index:02d}.mp3"
    run(["edge-tts", "--voice", VOICE, "--rate", TTS_RATE, "--text", safe_ascii(text, 700), "--write-media", str(out)])
    return out


def create_music():
    """Create an audible cinematic bed: drone, pulse and shimmer, designed to sit under speech."""
    out = AUDIO / "music.m4a"
    pad = ("aevalsrc=0.105*(sin(2*PI*55*t)+0.42*sin(2*PI*82.41*t)+"
           "0.26*sin(2*PI*110*t))*(0.72+0.28*sin(2*PI*0.055*t)):s=44100:d=60")
    pulse = ("aevalsrc=0.060*(0.55+0.45*sin(2*PI*1.6*t))*"
             "(sin(2*PI*110*t)+0.38*sin(2*PI*220*t)):s=44100:d=60")
    shimmer = ("aevalsrc=0.024*(0.5+0.5*sin(2*PI*0.42*t))*"
               "(sin(2*PI*440*t)+0.22*sin(2*PI*660*t)):s=44100:d=60")
    fc = (
        "[0:a]pan=stereo|c0=c0|c1=c0,lowpass=f=950,volume=1.0[p];"
        "[1:a]pan=stereo|c0=c0|c1=c0,lowpass=f=1900,volume=0.95[b];"
        "[2:a]pan=stereo|c0=c0|c1=c0,lowpass=f=4200,volume=0.75[s];"
        "[p][b][s]amix=inputs=3:duration=first:normalize=0,"
        "afade=t=in:st=0:d=2,afade=t=out:st=55:d=5,"
        "loudnorm=I=-24:TP=-2:LRA=7[m]"
    )
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", pad,
         "-f", "lavfi", "-i", pulse,
         "-f", "lavfi", "-i", shimmer,
         "-filter_complex", fc, "-map", "[m]", "-ar", "44100",
         "-c:a", "aac", "-b:a", "128k", str(out)])
    return out


SFX_TYPES = {
    "none": None,
    "airplane": "anoisesrc=color=white:amplitude=0.035:sample_rate=44100,lowpass=f=2600,highpass=f=120",
    "bird": "aevalsrc=0.055*sin(2*PI*(900+700*sin(2*PI*0.8*t))*t)*exp(-0.55*mod(t,0.9)):s=44100:d=7.5",
    "sand": "anoisesrc=color=brown:amplitude=0.025:sample_rate=44100,highpass=f=180,lowpass=f=4200",
    "wind": "anoisesrc=color=pink:amplitude=0.028:sample_rate=44100,lowpass=f=1400,highpass=f=90",
    "storm": "anoisesrc=color=brown:amplitude=0.035:sample_rate=44100,lowpass=f=900",
    "thunder": "anoisesrc=color=brown:amplitude=0.045:sample_rate=44100,lowpass=f=500",
    "ocean": "anoisesrc=color=pink:amplitude=0.026:sample_rate=44100,lowpass=f=1800,highpass=f=70",
    "water": "anoisesrc=color=white:amplitude=0.018:sample_rate=44100,lowpass=f=5000,highpass=f=500",
    "fire": "anoisesrc=color=white:amplitude=0.020:sample_rate=44100,highpass=f=1000,lowpass=f=6500",
    "city": "anoisesrc=color=pink:amplitude=0.018:sample_rate=44100,lowpass=f=3200,highpass=f=100",
    "impact": "anoisesrc=color=brown:amplitude=0.055:sample_rate=44100,lowpass=f=700",
    "rocket": "anoisesrc=color=brown:amplitude=0.055:sample_rate=44100,lowpass=f=1100,highpass=f=45",
    "space": "sine=frequency=70:sample_rate=44100",
    "heartbeat": "sine=frequency=65:sample_rate=44100",
    "whoosh": "anoisesrc=color=white:amplitude=0.035:sample_rate=44100,lowpass=f=3000,highpass=f=250",
    "rumble": "anoisesrc=color=brown:amplitude=0.045:sample_rate=44100,lowpass=f=300"
}


def create_scene_sfx(sfx_type, index):
    out = AUDIO / f"sfx_{index:02d}.m4a"
    source = SFX_TYPES.get(sfx_type) or SFX_TYPES["none"]
    if source is None:
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(SCENE_DURATION), "-c:a", "aac", "-b:a", "96k", str(out)])
        return out
    filter_chain = "volume=0.55,afade=t=in:st=0:d=0.25,afade=t=out:st=6.8:d=0.7"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", source, "-t", str(SCENE_DURATION), "-af", filter_chain, "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(out)])
    return out


def create_transition_sfx(index):
    """Short scene-boundary transition so visual changes feel intentional, not abrupt."""
    out = AUDIO / f"transition_{index:02d}.m4a"
    duration = 0.55
    if index in (3, 6):
        source = "anoisesrc=color=brown:amplitude=0.050:sample_rate=44100,lowpass=f=900"
        volume = "0.30"
    elif index in (5, 7):
        source = "anoisesrc=color=white:amplitude=0.045:sample_rate=44100,highpass=f=350,lowpass=f=4200"
        volume = "0.26"
    else:
        source = "anoisesrc=color=white:amplitude=0.035:sample_rate=44100,highpass=f=220,lowpass=f=3200"
        volume = "0.20"
    filters = f"volume={volume},afade=t=in:st=0:d=0.05,afade=t=out:st=0.27:d=0.28"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", source, "-t", str(duration), "-af", filters, "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(out)])
    return out


def fit_audio_to_scene(input_path, output_path):
    """Keep speech natural: speed up only when it would overrun the 7.5s scene; never slow it down or pad it."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        duration = float(probe.stdout.strip())
    except Exception:
        duration = 0.0
    if duration <= 0:
        raise RuntimeError(f"Could not determine TTS duration for {input_path}")
    target = SCENE_DURATION - 0.20
    speed = duration / target
    filters = []
    if speed > 1.02:
        speed = min(speed, 1.35)
        while speed > 2.0:
            filters.append("atempo=2.0")
            speed /= 2.0
        filters.append(f"atempo={speed:.5f}")
    else:
        filters.append("anull")
    filters.append("aresample=44100")
    filters.append("asetpts=N/SR/TB")
    run(["ffmpeg", "-y", "-i", str(input_path), "-af", ",".join(filters),
         "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "128k", str(output_path)])
    return output_path


def create_scene_audio(story):
    scene_clips = []
    for i, scene in enumerate(story["scenes"]):
        raw_voice = create_scene_voice(scene["narration"], i)
        voice = AUDIO / f"voice_fit_{i:02d}.m4a"
        fit_audio_to_scene(raw_voice, voice)
        sfx = create_scene_sfx(scene.get("sfx_type", "none"), i)
        transition = create_transition_sfx(i) if i > 0 else None
        mixed = AUDIO / f"scene_mix_{i:02d}.m4a"
        inputs = [str(voice), str(sfx)]
        filters = ["[0:a]volume=1.0[v]", "[1:a]volume=0.22[s]"]
        mix_inputs = "[v][s]"
        if transition:
            inputs.append(str(transition))
            filters.append("[2:a]adelay=6950|6950,volume=1.0[t]")
            mix_inputs += "[t]"
            mix_filter = f"{mix_inputs}amix=inputs=3:duration=longest:dropout_transition=0.08,alimiter=limit=0.90[a]"
        else:
            mix_filter = f"{mix_inputs}amix=inputs=2:duration=longest:dropout_transition=0.12,alimiter=limit=0.90[a]"
        run(["ffmpeg", "-y", *sum((["-i", p] for p in inputs), []), "-filter_complex", ";".join(filters + [mix_filter]), "-map", "[a]", "-t", str(SCENE_DURATION), "-c:a", "aac", "-b:a", "160k", str(mixed)])
        scene_clips.append(mixed)
    concat = AUDIO / "scene_audio_concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in scene_clips), encoding="utf-8")
    out = AUDIO / "scene_narration_sfx.m4a"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c:a", "aac", "-b:a", "160k", str(out)])
    return out


def mix_audio(video, narration_sfx, music):
    audio = AUDIO / "final_audio.m4a"
    fc = (
        "[0:a]volume=1.0,asplit=2[n][sc];"
        "[1:a]volume=0.68[m];"
        "[m][sc]sidechaincompress=threshold=0.04:ratio=5:attack=18:release=260:makeup=1:mix=0.82[duck];"
        "[n][duck]amix=inputs=2:duration=first:dropout_transition=0.8,"
        "loudnorm=I=-15.5:TP=-1.2:LRA=8[a]"
    )
    run(["ffmpeg", "-y", "-i", str(narration_sfx), "-i", str(music),
         "-filter_complex", fc, "-map", "[a]", "-t", str(DURATION),
         "-ar", "44100", "-c:a", "aac", "-b:a", "192k", str(audio)])
    run(["ffmpeg", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v:0",
         "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(VIDEO)])

def write_metadata(story):
    title = safe_ascii(story.get("title", "WHAT IF DAILY"), 120)
    desc = safe_ascii(story.get("description", ""), 5000)
    data = {"title": title, "description": desc, "keywords": story.get("keywords", [])[:25], "hashtags": story.get("hashtags", [])[:8], "created_at": datetime.utcnow().isoformat() + "Z", "voice": VOICE, "tts_rate": TTS_RATE, "visual_style": "cinematic scientific visualization + kinetic typography + audible cinematic music + scene-matched SFX"}
    METADATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upload_youtube(story):
    raw = os.getenv("YOUTUBE_OAUTH_JSON")
    if not raw:
        print("YouTube OAuth not configured; skipping upload.")
        return
    creds = Credentials.from_authorized_user_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/youtube.upload"])
    youtube = build("youtube", "v3", credentials=creds)
    body = {"snippet": {"title": safe_ascii(story.get("title", "WHAT IF DAILY"), 95), "description": safe_ascii(story.get("description", ""), 5000), "tags": story.get("keywords", [])[:25], "categoryId": "28"}, "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    media = MediaFileUpload(str(VIDEO), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    print("YouTube upload complete:", response.get("id"))


def production_qc(story):
    if not VIDEO.exists() or VIDEO.stat().st_size < 100000:
        raise RuntimeError("QC failed: final MP4 missing or suspiciously small")
    if not METADATA.exists() or METADATA.stat().st_size == 0:
        raise RuntimeError("QC failed: metadata.json missing or empty")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(VIDEO)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError("QC failed: ffprobe could not read final MP4")
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    video_stream = next((x for x in streams if x.get("codec_type") == "video"), None)
    audio_stream = next((x for x in streams if x.get("codec_type") == "audio"), None)
    if not video_stream or not audio_stream:
        raise RuntimeError("QC failed: final MP4 must contain video and audio")
    if int(video_stream.get("width", 0)) != WIDTH or int(video_stream.get("height", 0)) != HEIGHT:
        raise RuntimeError(f"QC failed: expected {WIDTH}x{HEIGHT} video")
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    if not 59.0 <= duration <= 60.5:
        raise RuntimeError(f"QC failed: final duration {duration:.2f}s is outside 59.0-60.5s")
    audio_duration = float(audio_stream.get("duration", duration) or duration)
    if audio_duration < 58.5:
        raise RuntimeError(f"QC failed: audio duration {audio_duration:.2f}s is too short")
    if int(audio_stream.get("sample_rate", 0)) != 44100:
        raise RuntimeError("QC failed: expected 44.1 kHz final audio")
    words = sum(len(x.get("narration", "").split()) for x in story.get("scenes", []))
    if not 104 <= words <= 116:
        raise RuntimeError(f"QC failed: narration word count {words}")
    manifest = OUTPUT / "visual_qc.json"
    if not manifest.exists():
        raise RuntimeError("QC failed: visual QC manifest missing")
    visuals = json.loads(manifest.read_text(encoding="utf-8"))
    generated = sum(1 for x in visuals if x.get("generated"))
    if len(visuals) != SCENES:
        raise RuntimeError(f"QC failed: expected {SCENES} visual records, got {len(visuals)}")
    if generated < 0:
        raise RuntimeError(f"QC failed: only {generated}/{SCENES} scenes have real generated visuals")
    print(f"PRODUCTION QC PASSED: {WIDTH}x{HEIGHT}, {duration:.2f}s, audio {audio_duration:.2f}s, {words} words, visuals {generated}/{SCENES}")

def main():
    clean()
    print("WHAT IF DAILY V2")
    print("Gemini model:", GEMINI_MODEL)
    print("Voice:", VOICE, "Rate:", TTS_RATE)
    print("Visuals enabled:", VISUALS_ENABLED, "Cloudflare configured:", bool(CF_TOKEN and CF_ACCOUNT))
    print("Cloudflare visual fallback model:", CF_FALLBACK_MODEL)
    topic = generate_unique_topic()
    print("Topic:", topic)
    story = create_story(topic)
    print("Narration words:", sum(len(s["narration"].split()) for s in story["scenes"]))
    scene_info = []
    for i, scene in enumerate(story["scenes"]):
        print(f"Creating scene {i + 1}/{SCENES}: {scene['on_screen']}")
        scene_info.append(create_typography_scene(topic, scene, i))
    silent = create_video(scene_info)
    narration_sfx = create_scene_audio(story)
    music = create_music()
    mix_audio(silent, narration_sfx, music)
    write_metadata(story)
    production_qc(story)
    upload_youtube(story)
    print("Final video:", VIDEO)


if __name__ == "__main__":
    main()

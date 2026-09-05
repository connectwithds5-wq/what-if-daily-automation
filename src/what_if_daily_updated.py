import os
import re
import json
import math
import random
import shutil
import subprocess
import io
import hashlib
from pathlib import Path
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VOICE = os.getenv("TTS_VOICE", "en-US-AndrewMultilingualNeural")
TTS_RATE = os.getenv("TTS_RATE", "-5%")
NASA_API = "https://images-api.nasa.gov/search"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "WHAT-IF-DAILY/3.0 educational cinematic Shorts generator"})

TOPICS = [
    "What If Earth Had Rings Like Saturn?",
    "What If Earth Suddenly Stopped Spinning?",
    "What If the Moon Came 10 Times Closer?",
    "What If Humans Could Breathe Underwater?",
    "What If Earth Had Two Moons?",
    "What If the Sun Disappeared for 24 Hours?",
    "What If Earth Lost Its Magnetic Field?",
    "What If Every Volcano Erupted at Once?",
    "What If Earth Became Twice as Large?",
    "What If Gravity Suddenly Became Half as Strong?",
    "What If Dinosaurs Returned Today?",
    "What If Humans Could Live on Mars?",
    "What If the Oceans Suddenly Froze?",
    "What If Earth Had No Moon?",
    "What If the Sky Turned Red?",
    "What If Time Suddenly Stopped for One Minute?",
    "What If Humans Never Needed Sleep?",
    "What If AI Controlled Every City?",
    "What If Earth Entered a New Ice Age?",
    "What If the Oceans Rose 100 Meters?",
    "What If Earth Started Moving Toward the Sun?",
    "What If We Found a Second Earth?",
    "What If Every Star Suddenly Disappeared?",
    "What If Earth Had the Gravity of the Moon?",
    "What If Humans Could See in Total Darkness?",
    "What If the Sahara Became a Giant Ocean?",
    "What If Earth Became 90 Percent Ocean?",
    "What If the Moon Broke Apart?",
    "What If Antarctica Suddenly Melted?",
    "What If Earth Had No Atmosphere?",
]

BAD_WORDS = [
    "diagram", "map", "chart", "graph", "plot", "scheme", "schematic", "logo",
    "icon", "symbol", "flag", "coat of arms", "illustration", "drawing", "sketch",
    "painting", "poster", "infographic", "cross section", "cutaway", "collage",
    "animation", "render", "computer generated", "concept art", "fictional", "model",
    "thumbnail", "screenshot", "watermark"
]


def run(cmd, check=True):
    print("RUN:", " ".join(map(str, cmd)))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout[-5000:])
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def clean():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    FRAMES.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)


def safe_ascii(text, max_len=120):
    text = str(text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9 .,!?':;()/%+\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:max_len]


def get_font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def wrap_text(text, font, max_width):
    words = safe_ascii(text).split()
    lines, current = [], ""
    dummy = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy)
    for word in words:
        test = word if not current else current + " " + word
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_crop(img, size=(WIDTH, HEIGHT), focus=(0.5, 0.5)):
    img = img.convert("RGB")
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    nw, nh = max(tw, int(img.width * scale)), max(th, int(img.height * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    fx, fy = focus
    left = max(0, min(nw - tw, int((nw - tw) * fx)))
    top = max(0, min(nh - th, int((nh - th) * fy)))
    return img.crop((left, top, left + tw, top + th))


def cinematic_grade(img, seed):
    random.seed(seed)
    img = ImageEnhance.Contrast(img).enhance(1.14)
    img = ImageEnhance.Color(img).enhance(1.10)
    img = ImageEnhance.Sharpness(img).enhance(1.16)
    # Subtle cinematic cool shadows / warm highlights.
    pix = img.load()
    for y in range(0, HEIGHT, 4):
        t = y / HEIGHT
        for x in range(0, WIDTH, 4):
            r, g, b = pix[x, y]
            if t < 0.55:
                pix[x, y] = (min(255, int(r * .97)), min(255, int(g * .98)), min(255, int(b * 1.04)))
            else:
                pix[x, y] = (min(255, int(r * 1.02)), min(255, int(g * 1.00)), min(255, int(b * .98)))
    # Vignette.
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    op = overlay.load()
    cx, cy = WIDTH / 2, HEIGHT / 2
    maxd = math.hypot(cx, cy)
    for y in range(0, HEIGHT, 8):
        for x in range(0, WIDTH, 8):
            d = math.hypot(x - cx, y - cy) / maxd
            a = int(max(0, min(125, (d ** 2.4) * 110)))
            for yy in range(y, min(y + 8, HEIGHT)):
                for xx in range(x, min(x + 8, WIDTH)):
                    op[xx, yy] = (0, 0, 0, a)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def add_atmosphere(img, seed, mode):
    random.seed(seed)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if mode in ("space", "earth", "moon", "rings", "mars"):
        for _ in range(75):
            x, y = random.randrange(WIDTH), random.randrange(HEIGHT)
            r = random.choice([1, 1, 1, 2])
            d.ellipse((x-r, y-r, x+r, y+r), fill=(255, 255, 255, random.randint(70, 170)))
    else:
        for _ in range(18):
            x, y = random.randrange(WIDTH), random.randrange(HEIGHT)
            r = random.randint(30, 100)
            d.ellipse((x-r, y-r, x+r, y+r), fill=(255, 255, 255, random.randint(3, 10)))
    return Image.alpha_composite(img.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(.5))).convert("RGB")


def is_bad_title(title):
    t = title.lower()
    return any(w in t for w in BAD_WORDS)


def score_candidate(item, query, source):
    title = str(item.get("title", "")).lower()
    w, h = int(item.get("width", 0) or 0), int(item.get("height", 0) or 0)
    if w < 1200 or h < 700:
        return -999
    if is_bad_title(title):
        return -500
    score = 0
    score += min(40, math.log(max(1, w * h), 10) * 4)
    ratio = w / max(1, h)
    if 1.15 <= ratio <= 2.2:
        score += 14
    if source == "NASA":
        score += 25
    photographic = ["photo", "photograph", "surface", "landscape", "clouds", "aerial", "satellite", "observatory", "image"]
    score += sum(4 for word in photographic if word in title)
    score -= sum(15 for word in BAD_WORDS if word in title)
    return score


def nasa_search(query, limit=12):
    try:
        r = SESSION.get(NASA_API, params={"q": query, "media_type": "image", "page_size": limit}, timeout=30)
        r.raise_for_status()
        data = r.json()
        out = []
        for item in data.get("collection", {}).get("items", []):
            data_item = item.get("data", [{}])[0]
            title = data_item.get("title", "")
            links = item.get("links", [])
            href = next((x.get("href") for x in links if x.get("render") == "image"), None)
            if not href or is_bad_title(title):
                continue
            out.append({"url": href, "title": title, "license": "NASA media - verify item notes", "source": "NASA", "width": 2000, "height": 1200})
        out.sort(key=lambda x: score_candidate(x, query, "NASA"), reverse=True)
        return out
    except Exception as e:
        print("NASA search failed:", e)
        return []


def wikimedia_search(query, limit=12):
    try:
        params = {
            "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|size|extmetadata", "format": "json"
        }
        r = SESSION.get(WIKIMEDIA_API, params=params, timeout=30)
        r.raise_for_status()
        pages = list(r.json().get("query", {}).get("pages", {}).values())
        out = []
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            url = info.get("url")
            w, h = int(info.get("width", 0) or 0), int(info.get("height", 0) or 0)
            title = p.get("title", "")
            if not url or w < 1200 or h < 700 or is_bad_title(title):
                continue
            meta = info.get("extmetadata") or {}
            license_name = str((meta.get("LicenseShortName") or {}).get("value", "Unknown"))
            out.append({"url": url, "width": w, "height": h, "title": title, "license": re.sub("<[^>]+>", "", license_name), "source": "Wikimedia Commons"})
        out.sort(key=lambda x: score_candidate(x, query, "Wikimedia"), reverse=True)
        return out
    except Exception as e:
        print("Wikimedia search failed:", e)
        return []


def download_image(source):
    try:
        r = SESSION.get(source["url"], timeout=45)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        if img.width < 1000 or img.height < 600:
            return None
        return img
    except Exception as e:
        print("Image download failed:", e)
        return None


def visual_mode(topic, scene):
    text = f"{topic} {scene.get('visual', '')}".lower()
    if "ring" in text or "saturn" in text: return "rings"
    if "moon" in text: return "moon"
    if "volcano" in text or "lava" in text: return "volcano"
    if "ocean" in text or "water" in text or "underwater" in text: return "ocean"
    if "ice" in text or "frozen" in text or "antarctica" in text: return "ice"
    if "sun" in text or "solar" in text: return "sun"
    if "mars" in text: return "mars"
    if "city" in text or "ai" in text or "human" in text or "people" in text: return "city"
    if "earth" in text or "planet" in text: return "earth"
    return "space"


def visual_queries(topic, scene, mode):
    text = f"{topic} {scene.get('visual', '')}".lower()
    if mode == "rings":
        return ["Earth full disk clouds space", "Earth from space blue planet", "Earth limb atmosphere space"]
    if mode == "moon":
        return ["Moon surface high resolution NASA", "Moon from space Earth NASA", "lunar landscape high resolution"]
    if mode == "volcano":
        return ["volcano eruption aerial photograph", "lava eruption volcano photograph", "volcano plume satellite NASA"]
    if mode == "ocean":
        return ["ocean aerial photograph waves", "deep ocean underwater photograph", "ocean storm satellite NASA"]
    if mode == "ice":
        return ["Antarctica ice satellite NASA", "glacier aerial photograph", "polar ice landscape photograph"]
    if mode == "sun":
        return ["Sun surface NASA", "solar flare NASA", "Sun space photograph NASA"]
    if mode == "mars":
        return ["Mars surface NASA", "Mars landscape NASA", "Mars planet space NASA"]
    if mode == "city":
        return ["modern city skyline aerial photograph", "city at night aerial photograph", "crowded city street photograph"]
    if "dinosaur" in text:
        return ["dinosaur fossil museum photograph", "dinosaur skeleton fossil photograph", "natural history museum dinosaur"]
    if "desert" in text or "sahara" in text:
        return ["Sahara desert aerial photograph", "Sahara landscape photograph", "desert satellite NASA"]
    if "earth" in text or "planet" in text:
        return ["Earth full disk NASA", "Earth clouds from space NASA", "Earth atmosphere limb NASA"]
    return ["deep space stars NASA", "Earth from space NASA", "planet Earth clouds NASA"]


def add_ring_overlay(canvas, seed, mode):
    if mode != "rings":
        return canvas
    ring = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    cx, cy = WIDTH // 2, int(HEIGHT * .50)
    rx, ry = int(WIDTH * .82), int(HEIGHT * .24)
    random.seed(seed)
    # Real-image Earth + subtle translucent Saturn-like ring treatment.
    for i in range(22):
        inset = i * 9
        alpha = max(18, 105 - i * 4)
        d.ellipse((cx-rx+inset, cy-ry+inset//2, cx+rx-inset, cy+ry-inset//2), outline=(215, 198, 168, alpha), width=random.choice([2, 3, 4]))
    return Image.alpha_composite(canvas.convert("RGBA"), ring.filter(ImageFilter.GaussianBlur(.45))).convert("RGB")


def find_best_image(topic, scene, mode, used_urls):
    candidates = []
    for q in visual_queries(topic, scene, mode):
        # NASA first for science/space/Earth imagery, then Wikimedia as photographic fallback.
        if mode in ("rings", "earth", "moon", "sun", "mars", "volcano", "ice", "ocean", "space"):
            candidates.extend(nasa_search(q, 10))
        candidates.extend(wikimedia_search(q, 10))
    ranked = sorted(candidates, key=lambda x: score_candidate(x, "", x.get("source", "Wikimedia")), reverse=True)
    for item in ranked:
        if item.get("url") in used_urls:
            continue
        img = download_image(item)
        if img is not None:
            return img, item
    return None, None


def create_realistic_scene(topic, scene, index, used_urls):
    seed = int(hashlib.sha256(f"{topic}:{index}".encode()).hexdigest()[:8], 16)
    mode = visual_mode(topic, scene)
    img, source = find_best_image(topic, scene, mode, used_urls)
    if img is None:
        print(f"Scene {index+1}: no external photo found; using cinematic emergency background")
        img = Image.new("RGB", (WIDTH, HEIGHT), (6, 10, 20))
        d = ImageDraw.Draw(img)
        random.seed(seed)
        for _ in range(280):
            x, y = random.randrange(WIDTH), random.randrange(HEIGHT)
            r = random.choice([1, 1, 1, 2])
            d.ellipse((x-r, y-r, x+r, y+r), fill=(190, 200, 220))
        source = {"title": "Emergency generated background", "license": "N/A", "url": "", "source": "generated"}
    else:
        used_urls.add(source.get("url", ""))

    focus_x = 0.42 + ((index % 3) - 1) * .08
    focus_y = 0.50 + ((index % 2) * .04)
    canvas = fit_crop(img, focus=(focus_x, focus_y))
    canvas = add_ring_overlay(canvas, seed, mode)
    canvas = cinematic_grade(canvas, seed)
    canvas = add_atmosphere(canvas, seed, mode)

    # Readability gradients without covering the photograph too much.
    grad = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for i in range(0, 430, 10):
        a = int(115 * (1 - i / 430))
        gd.rectangle((0, i, WIDTH, i + 10), fill=(0, 0, 0, a))
    for i in range(0, 420, 10):
        a = int(105 * (1 - i / 420))
        y = HEIGHT - i - 10
        gd.rectangle((0, y, WIDTH, y + 10), fill=(0, 0, 0, a))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), grad).convert("RGB")

    on_screen = safe_ascii(scene.get("on_screen", ""), 100)
    if on_screen:
        font = get_font(60, bold=True)
        lines = wrap_text(on_screen, font, 900)
        d = ImageDraw.Draw(canvas)
        y = 150
        for line in lines[:3]:
            box = d.textbbox((0, 0), line, font=font)
            x = (WIDTH - (box[2] - box[0])) // 2
            d.text((x+3, y+3), line, font=font, fill=(0,0,0), stroke_width=2, stroke_fill=(0,0,0))
            d.text((x, y), line, font=font, fill=(255,255,255), stroke_width=2, stroke_fill=(0,0,0))
            y += 76

    wm = get_font(27, bold=True)
    ImageDraw.Draw(canvas).text((42, HEIGHT-72), "WHAT IF DAILY", font=wm, fill=(255,255,255), stroke_width=1, stroke_fill=(0,0,0))
    path = FRAMES / f"scene_{index:02d}.png"
    canvas.save(path, format="PNG", optimize=True)
    return {"path": str(path), "source": source, "mode": mode}


def create_story(topic):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing")
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
You are the senior writer for WHAT IF DAILY, a cinematic science YouTube Shorts channel.
Topic: {topic}

Create one 60-second YouTube Short with exactly 8 scenes.
Use natural, confident American English. The first sentence must hook immediately.
Build consequences scene by scene, then reveal a surprising implication near scene 7.
Scene 8 should end with a strong question that encourages comments.
Target 115-135 spoken words total.

For every scene return:
- narration: spoken line only
- on_screen: short ASCII English text, max 55 characters
- visual: a detailed description of a REAL PHOTOGRAPHIC subject that can be found in NASA imagery or Wikimedia Commons. Do not ask for an illustration, cartoon, CGI render, diagram, map, infographic, poster, drawing, or concept art.

No emojis. No Hindi or Gujarati. ASCII characters only.
Return ONLY valid JSON in this schema:
{{
  "title": "...",
  "description": "...",
  "keywords": ["..."],
  "hashtags": ["#WhatIfDaily", "#WhatIf", "#Science"],
  "scenes": [
    {{"narration":"...", "on_screen":"...", "visual":"..."}}
  ]
}}
"""
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    story = json.loads(text)
    story["title"] = safe_ascii(story.get("title", topic), 100)
    story["description"] = safe_ascii(story.get("description", ""), 4500)
    story["keywords"] = [safe_ascii(x, 60) for x in story.get("keywords", [])][:20]
    story["hashtags"] = [safe_ascii(x, 40) for x in story.get("hashtags", [])][:12]
    scenes = story.get("scenes", [])[:SCENES]
    if len(scenes) != SCENES:
        raise RuntimeError(f"Gemini returned {len(scenes)} scenes; expected {SCENES}")
    for s in scenes:
        s["narration"] = safe_ascii(s.get("narration", ""), 600)
        s["on_screen"] = safe_ascii(s.get("on_screen", ""), 100)
        s["visual"] = safe_ascii(s.get("visual", ""), 500)
    story["scenes"] = scenes
    return story


def create_voice(text):
    out = AUDIO / "narration.mp3"
    txt = safe_ascii(text, 6000)
    # IMPORTANT: negative Edge-TTS rates must be one argument, e.g. --rate=-5%.
    run(["edge-tts", "--voice", VOICE, f"--rate={TTS_RATE}", "--volume=+0%", "--pitch=-2Hz", "--text", txt, "--write-media", str(out)])
    return out


def create_music():
    out = AUDIO / "music.wav"
    filt = (
        "sine=frequency=55:duration=60,volume=0.045[a];"
        "sine=frequency=110:duration=60,volume=0.020[b];"
        "anoisesrc=color=pink:duration=60,lowpass=f=800,volume=0.006[n];"
        "[a][b]amix=inputs=2:duration=longest[p];[p][n]amix=inputs=2:duration=longest,alimiter=limit=0.75"
    )
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", filt, "-t", "60", "-ar", "48000", "-ac", "2", str(out)])
    return out


def create_sound_design():
    out = AUDIO / "sfx.wav"
    # Gentle cinematic pulses at scene boundaries. Fully generated locally.
    sources = ["aevalsrc=0:d=60[s]"]
    labels = ["s"]
    for i, ms in enumerate(range(0, 60000, 7500), start=1):
        freq = 70 + (i % 4) * 18
        amp = 0.08 if i < 8 else 0.12
        label = f"i{i}"
        sources.append(f"aevalsrc={amp}*sin(2*PI*{freq}*t)*exp(-7*t):d=0.55,adelay={ms}|{ms}[{label}]")
        labels.append(label)
    filt = ";".join(sources) + ";" + "".join(f"[{x}]" for x in labels) + f"amix=inputs={len(labels)}:duration=longest,alimiter=limit=0.7"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", filt, "-t", "60", "-ar", "48000", "-ac", "2", str(out)])
    return out


def create_video(scene_info):
    clips = []
    for i, info in enumerate(scene_info):
        clip = FRAMES / f"clip_{i:02d}.mp4"
        # Each source image is already 1080x1920; zoompan adds motion instead of static posters.
        zoom = "1.0+0.055*on/225"
        xexpr = "iw/2-(iw/zoom/2)" if i % 2 == 0 else "iw/2-(iw/zoom/2)+12*on/225"
        yexpr = "ih/2-(ih/zoom/2)-10*on/225" if i % 2 == 0 else "ih/2-(ih/zoom/2)+10*on/225"
        vf = f"zoompan=z='{zoom}':x='{xexpr}':y='{yexpr}':d=225:s=1080x1920:fps=30,format=yuv420p"
        run(["ffmpeg", "-y", "-loop", "1", "-i", info["path"], "-vf", vf, "-t", str(SCENE_DURATION), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(clip)])
        clips.append(clip)
    concat = OUTPUT / "concat.txt"
    concat.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    silent = OUTPUT / "video_silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(silent)])
    return silent


def mix_audio(video_silent, narration, music, sfx):
    audio = AUDIO / "final_audio.m4a"
    run([
        "ffmpeg", "-y", "-i", str(narration), "-i", str(music), "-i", str(sfx),
        "-filter_complex",
        "[0:a]loudnorm=I=-15:TP=-1.5:LRA=8,volume=1.12[n];"
        "[1:a]volume=0.14[m];[2:a]volume=0.20[s];"
        "[n][m][s]amix=inputs=3:duration=longest:dropout_transition=2,alimiter=limit=0.9[a]",
        "-map", "[a]", "-t", "60", "-c:a", "aac", "-b:a", "192k", str(audio)
    ])
    run(["ffmpeg", "-y", "-i", str(video_silent), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(VIDEO)])
    return VIDEO


def save_metadata(story, sources):
    desc = safe_ascii(story.get("description", ""), 4500)
    desc += "\n\nWHAT IF DAILY - IMAGINE. WATCH. WONDER."
    desc += "\n\nVisual sources used in this video:\n"
    seen = set()
    for src in sources:
        url = src.get("url", "")
        title = safe_ascii(src.get("title", ""), 180)
        license_name = safe_ascii(src.get("license", "Unknown"), 100)
        source_name = safe_ascii(src.get("source", "Unknown"), 40)
        if url and url not in seen:
            seen.add(url)
            desc += f"- {source_name}: {title} | {license_name} | {url}\n"
    data = {
        "title": story.get("title", "WHAT IF DAILY"),
        "description": desc,
        "keywords": story.get("keywords", []),
        "hashtags": story.get("hashtags", []),
        "created_at": datetime.utcnow().isoformat() + "Z",
        "voice": VOICE,
        "tts_rate": TTS_RATE,
        "visual_style": "real high-resolution photographic imagery from NASA/Wikimedia Commons with cinematic crop, grade and motion",
        "source_count": len(seen),
    }
    METADATA.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def upload_youtube(metadata):
    raw = os.getenv("YOUTUBE_OAUTH_JSON")
    if not raw:
        print("YOUTUBE_OAUTH_JSON missing; skipping upload")
        return
    data = json.loads(raw)
    creds = Credentials(None, refresh_token=data["refresh_token"], token_uri="https://oauth2.googleapis.com/token", client_id=data["client_id"], client_secret=data["client_secret"], scopes=["https://www.googleapis.com/auth/youtube.upload"])
    youtube = build("youtube", "v3", credentials=creds)
    description = safe_ascii(metadata["description"], 4900)
    hashtags = " ".join(metadata.get("hashtags", [])[:8])
    if hashtags:
        description += "\n\n" + hashtags
    body = {
        "snippet": {"title": safe_ascii(metadata["title"], 95), "description": description, "tags": metadata.get("keywords", [])[:25], "categoryId": "28"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    print("Uploading to YouTube:", body["snippet"]["title"])
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(VIDEO), mimetype="video/mp4", resumable=True))
    result = req.execute()
    print("YouTube upload complete:", result.get("id"))


def main():
    clean()
    topic_index = int(os.getenv("TOPIC_INDEX", "0"))
    topic = os.getenv("WHAT_IF_TOPIC", "").strip() or TOPICS[topic_index % len(TOPICS)]
    print("Topic:", topic)
    print("Voice:", VOICE, "Rate:", TTS_RATE)

    story = create_story(topic)
    scene_info, sources, used_urls = [], [], set()
    for i, scene in enumerate(story["scenes"]):
        info = create_realistic_scene(topic, scene, i, used_urls)
        scene_info.append(info)
        sources.append(info["source"])
        print(f"Scene {i+1}: {info['mode']} | {info['source'].get('source','')} | {info['source'].get('title','')}")

    full_narration = " ".join(s["narration"] for s in story["scenes"])
    narration = create_voice(full_narration)
    music = create_music()
    sfx = create_sound_design()
    silent = create_video(scene_info)
    final_video = mix_audio(silent, narration, music, sfx)
    metadata = save_metadata(story, sources)
    upload_youtube(metadata)
    print("DONE:", final_video)


if __name__ == "__main__":
    main()

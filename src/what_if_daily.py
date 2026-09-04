import os
import re
import json
import math
import random
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
from google import genai


# ============================================================
# WHAT IF DAILY
# Free-first cinematic YouTube Shorts generator
# ============================================================

WIDTH = 1080
HEIGHT = 1920
FPS = 30
DURATION = 60
SCENES = 8
SCENE_DURATION = 7.5

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
FRAMES = OUTPUT / "frames"
AUDIO = OUTPUT / "audio"
VIDEO = OUTPUT / "what_if_daily.mp4"
METADATA = OUTPUT / "metadata.json"

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

VOICE = os.getenv("TTS_VOICE", "en-US-GuyNeural")

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
    "What If Earth Had No Atmosphere?"
]


def run(cmd, check=True):
    print("RUN:", " ".join(map(str, cmd)))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    print(result.stdout[-5000:])
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def clean():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)

    FRAMES.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)


def safe_ascii(text, max_len=90):
    """
    Strict ASCII only.
    This prevents Hindi/Gujarati/emoji/unicode rendering problems.
    """
    text = str(text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9 .,!?':;()/%+-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def get_font(size, bold=False):
    candidates = []

    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        ]

    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)

    return ImageFont.load_default()


def wrap_text(text, font, max_width):
    words = safe_ascii(text).split()
    lines = []
    current = ""

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


def gradient_background(seed, mode="space"):
    random.seed(seed)

    img = Image.new("RGB", (WIDTH, HEIGHT))
    px = img.load()

    if mode == "space":
        top = (5, 8, 28)
        bottom = (20, 5, 45)
    elif mode == "fire":
        top = (35, 5, 5)
        bottom = (65, 15, 5)
    elif mode == "ocean":
        top = (3, 18, 38)
        bottom = (2, 55, 75)
    elif mode == "ice":
        top = (8, 25, 42)
        bottom = (100, 155, 175)
    else:
        top = (10, 10, 18)
        bottom = (35, 20, 50)

    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)

        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)

        for x in range(WIDTH):
            noise = random.randint(-2, 2)
            px[x, y] = (
                max(0, min(255, r + noise)),
                max(0, min(255, g + noise)),
                max(0, min(255, b + noise))
            )

    return img


def add_stars(img, seed, count=180):
    random.seed(seed)
    draw = ImageDraw.Draw(img)

    for _ in range(count):
        x = random.randint(20, WIDTH - 20)
        y = random.randint(20, HEIGHT - 20)
        size = random.choice([1, 1, 1, 2, 2, 3])
        shade = random.randint(150, 255)

        draw.ellipse(
            (x, y, x + size, y + size),
            fill=(shade, shade, shade)
        )


def draw_glow_circle(img, center, radius, core, rings=8):
    draw = ImageDraw.Draw(img)

    cx, cy = center

    for i in range(rings, 0, -1):
        r = int(radius * (i / rings))

        alpha = int(25 + (rings - i) * 15)

        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)

        ld.ellipse(
            (cx-r, cy-r, cx+r, cy+r),
            fill=(
                core[0],
                core[1],
                core[2],
                min(255, alpha)
            )
        )

        img.paste(layer, (0, 0), layer)

    draw.ellipse(
        (cx-radius, cy-radius, cx+radius, cy+radius),
        fill=core
    )


def draw_earth(img, cx, cy, radius, ring=False):
    draw = ImageDraw.Draw(img)

    # glow
    draw_glow_circle(
        img,
        (cx, cy),
        int(radius * 1.25),
        (30, 100, 220),
        10
    )

    draw.ellipse(
        (cx-radius, cy-radius, cx+radius, cy+radius),
        fill=(25, 85, 180),
        outline=(120, 180, 255),
        width=4
    )

    # continents
    land = [
        (-0.45, -0.25, -0.05, 0.05),
        (0.05, -0.45, 0.38, -0.12),
        (0.18, 0.05, 0.48, 0.35),
        (-0.35, 0.22, -0.05, 0.55),
        (-0.65, -0.05, -0.45, 0.25)
    ]

    for x1, y1, x2, y2 in land:
        draw.ellipse(
            (
                cx + int(x1 * radius),
                cy + int(y1 * radius),
                cx + int(x2 * radius),
                cy + int(y2 * radius)
            ),
            fill=(50, 150, 85)
        )

    # atmosphere
    draw.ellipse(
        (cx-radius-8, cy-radius-8, cx+radius+8, cy+radius+8),
        outline=(100, 190, 255),
        width=5
    )

    if ring:
        # Saturn-like rings
        ring_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring_layer)

        bbox = (
            cx - int(radius * 1.9),
            cy - int(radius * 0.55),
            cx + int(radius * 1.9),
            cy + int(radius * 0.55)
        )

        for width, alpha in [
            (24, 80),
            (15, 120),
            (8, 190),
            (3, 230)
        ]:
            rd.ellipse(
                bbox,
                outline=(215, 190, 150, alpha),
                width=width
            )

        img.paste(ring_layer, (0, 0), ring_layer)

        # redraw planet center to make rings look behind it
        draw.ellipse(
            (cx-radius, cy-radius, cx+radius, cy+radius),
            fill=(25, 85, 180),
            outline=(120, 180, 255),
            width=4
        )

        for x1, y1, x2, y2 in land:
            draw.ellipse(
                (
                    cx + int(x1 * radius),
                    cy + int(y1 * radius),
                    cx + int(x2 * radius),
                    cy + int(y2 * radius)
                ),
                fill=(50, 150, 85)
            )


def draw_moon(img, cx, cy, radius):
    draw = ImageDraw.Draw(img)

    draw_glow_circle(
        img,
        (cx, cy),
        int(radius * 1.25),
        (150, 150, 160),
        6
    )

    draw.ellipse(
        (cx-radius, cy-radius, cx+radius, cy+radius),
        fill=(180, 180, 180)
    )

    random.seed(44)

    for _ in range(30):
        x = random.randint(cx-radius+5, cx+radius-5)
        y = random.randint(cy-radius+5, cy+radius-5)

        if (x-cx)**2 + (y-cy)**2 <= radius**2:
            rr = random.randint(3, 15)
            draw.ellipse(
                (x-rr, y-rr, x+rr, y+rr),
                fill=(145, 145, 145)
            )


def draw_sun(img, cx, cy, radius):
    draw_glow_circle(
        img,
        (cx, cy),
        int(radius * 1.5),
        (255, 120, 20),
        12
    )

    draw = ImageDraw.Draw(img)

    draw.ellipse(
        (cx-radius, cy-radius, cx+radius, cy+radius),
        fill=(255, 190, 40)
    )


def draw_city(img, seed):
    random.seed(seed)
    draw = ImageDraw.Draw(img)

    base_y = 1500

    for x in range(0, WIDTH, 80):
        h = random.randint(150, 650)
        w = random.randint(55, 100)

        draw.rectangle(
            (x, base_y-h, x+w, base_y),
            fill=(20, 22, 32)
        )

        for wy in range(base_y-h+30, base_y-20, 55):
            for wx in range(x+15, min(x+w-10, WIDTH), 35):
                if random.random() > 0.35:
                    draw.rectangle(
                        (wx, wy, wx+8, wy+12),
                        fill=(230, 180, 70)
                    )

    draw.rectangle(
        (0, base_y, WIDTH, HEIGHT),
        fill=(8, 9, 15)
    )


def draw_ocean(img):
    draw = ImageDraw.Draw(img)

    for y in range(900, HEIGHT, 45):
        points = []

        for x in range(0, WIDTH+40, 40):
            yy = y + int(15 * math.sin(x / 70))
            points.append((x, yy))

        draw.line(
            points,
            fill=(30, 150, 190),
            width=4
        )


def draw_volcano(img):
    draw = ImageDraw.Draw(img)

    # mountain
    draw.polygon(
        [
            (120, 1650),
            (540, 700),
            (960, 1650)
        ],
        fill=(35, 28, 30)
    )

    # lava
    draw.polygon(
        [
            (500, 800),
            (540, 700),
            (580, 800),
            (560, 1200),
            (520, 1400),
            (490, 1150)
        ],
        fill=(230, 55, 10)
    )

    draw_glow_circle(
        img,
        (540, 720),
        180,
        (255, 70, 10),
        10
    )


def choose_visual_mode(topic, scene_text):
    t = (topic + " " + scene_text).lower()

    if "ring" in t or "saturn" in t:
        return "rings"

    if "moon" in t:
        return "moon"

    if "sun" in t:
        return "sun"

    if "volcano" in t:
        return "volcano"

    if "ocean" in t or "water" in t or "underwater" in t:
        return "ocean"

    if "city" in t or "ai" in t or "human" in t:
        return "city"

    if "ice" in t or "frozen" in t:
        return "ice"

    return "space"


def create_scene_image(topic, scene, index):
    visual = choose_visual_mode(
        topic,
        scene.get("visual", "")
    )

    seed = abs(hash(topic + str(index))) % 100000

    img = gradient_background(seed, visual if visual in [
        "space", "fire", "ocean", "ice"
    ] else "space")

    add_stars(img, seed)

    # Main visual
    if visual == "rings":
        draw_earth(
            img,
            WIDTH // 2,
            780,
            350,
            ring=True
        )

    elif visual == "moon":
        draw_earth(
            img,
            420,
            820,
            280
        )

        draw_moon(
            img,
            760,
            500,
            210
        )

    elif visual == "sun":
        draw_sun(
            img,
            540,
            720,
            300
        )

    elif visual == "volcano":
        draw_volcano(img)

    elif visual == "ocean":
        draw_ocean(img)
        draw_earth(
            img,
            540,
            700,
            300
        )

    elif visual == "city":
        draw_city(img, seed)

    elif visual == "ice":
        draw_earth(
            img,
            540,
            700,
            320
        )

        draw.rectangle(
            (0, 1050, WIDTH, HEIGHT),
            fill=(150, 205, 225)
        )

    else:
        # generic Earth + space composition
        draw_earth(
            img,
            540,
            720,
            320
        )

        draw_moon(
            img,
            820,
            360,
            100
        )

    # cinematic dark overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    for y in range(HEIGHT):
        alpha = int(80 * (y / HEIGHT))
        od.line(
            (0, y, WIDTH, y),
            fill=(0, 0, 0, alpha)
        )

    img = Image.alpha_composite(
        img.convert("RGBA"),
        overlay
    ).convert("RGB")

    draw = ImageDraw.Draw(img)

    # Scene label
    label_font = get_font(42, bold=True)
    label = f"WHAT IF DAILY  |  {index + 1:02d}"

    draw.text(
        (60, 70),
        label,
        font=label_font,
        fill=(255, 255, 255)
    )

    # Main scene text
    text_font = get_font(68, bold=True)

    scene_text = safe_ascii(
        scene.get("on_screen", ""),
        120
    )

    lines = wrap_text(
        scene_text,
        text_font,
        WIDTH - 140
    )

    total_h = len(lines) * 82
    start_y = 1220 - total_h // 2

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=text_font
        )

        tw = bbox[2] - bbox[0]
        x = (WIDTH - tw) // 2

        # shadow
        draw.text(
            (x + 4, start_y + 4),
            line,
            font=text_font,
            fill=(0, 0, 0)
        )

        draw.text(
            (x, start_y),
            line,
            font=text_font,
            fill=(255, 255, 255)
        )

        start_y += 82

    # small topic
    topic_font = get_font(34)

    topic_short = safe_ascii(topic, 100)

    bbox = draw.textbbox(
        (0, 0),
        topic_short,
        font=topic_font
    )

    tw = bbox[2] - bbox[0]

    draw.text(
        ((WIDTH - tw) // 2, 1770),
        topic_short,
        font=topic_font,
        fill=(220, 220, 220)
    )

    path = FRAMES / f"scene_{index:02d}.png"
    img.save(path, quality=95)

    return path


def select_topic():
    now = datetime.utcnow()

    # First day:
    # 1st video = Earth rings
    # 2nd video = Earth stops spinning
    day_index = now.timetuple().tm_yday - 1

    slot = int(os.getenv("RUN_SLOT", "0"))

    index = (
        day_index * 2 + slot
    ) % len(TOPICS)

    override = os.getenv("TOPIC_OVERRIDE", "").strip()

    if override:
        return safe_ascii(override, 150)

    return TOPICS[index]


def generate_story(topic):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing.")

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    prompt = f"""
You are the senior writer for a viral YouTube Shorts channel called WHAT IF DAILY.

Channel tagline:
IMAGINE. WATCH. WONDER.

Topic:
{topic}

Create a 60-second cinematic science "What If?" Short.

Audience:
Global English-speaking audience.

Requirements:
- Exactly 8 scenes.
- Total narration approximately 125 to 145 English words.
- Strong hook in the first 2 seconds.
- Every scene must move the story forward.
- Explain realistic scientific consequences.
- Do not make unsupported claims sound certain.
- Use simple spoken English.
- Make it cinematic, mysterious and curiosity-driven.
- Scene 8 must end with a powerful question that encourages comments.
- No emojis.
- No hashtags inside narration.
- No Hindi.
- No Gujarati.
- ASCII English only.
- Avoid special Unicode characters.
- Do not use fancy quotation marks.
- Do not use bullet symbols.

Return ONLY valid JSON.

JSON format:
{{
  "title": "YouTube title under 70 characters",
  "description": "SEO friendly YouTube description",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "hashtags": ["#WhatIf", "#Science", "#Shorts"],
  "narration": "Complete narration in one paragraph",
  "scenes": [
    {{
      "visual": "visual description",
      "on_screen": "short ASCII text",
      "narration": "spoken narration for this scene"
    }}
  ]
}}

There must be exactly 8 scene objects.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )

    raw = response.text.strip()

    # Remove accidental markdown fences
    raw = re.sub(
        r"^```json\s*",
        "",
        raw,
        flags=re.IGNORECASE
    )

    raw = re.sub(
        r"\s*```$",
        "",
        raw
    )

    data = json.loads(raw)

    if len(data.get("scenes", [])) != SCENES:
        raise RuntimeError(
            f"Gemini returned {len(data.get('scenes', []))} scenes instead of 8."
        )

    return data


def build_narration(story):
    parts = []

    for scene in story["scenes"]:
        text = safe_ascii(
            scene.get("narration", ""),
            500
        )

        if text:
            parts.append(text)

    narration = " ".join(parts)

    # Safety cleanup
    narration = re.sub(
        r"\s+",
        " ",
        narration
    ).strip()

    return narration


def create_voice(narration):
    voice_file = AUDIO / "narration.mp3"

    text_file = AUDIO / "narration.txt"

    text_file.write_text(
        narration,
        encoding="utf-8"
    )

    # Edge TTS
    run([
        "edge-tts",
        "--voice",
        VOICE,
        "--rate=+3%",
        "--volume=+0%",
        "--text",
        narration,
        "--write-media",
        str(voice_file)
    ])

    return voice_file


def create_music():
    music = AUDIO / "music.mp3"

    # Original procedural ambient pulse.
    # No downloaded copyrighted music.
    filter_complex = (
        "[0:a]volume=0.06,"
        "lowpass=f=900,"
        "afade=t=in:st=0:d=2,"
        "afade=t=out:st=56:d=4"
        "[a];"
        "[1:a]volume=0.035,"
        "highpass=f=1800,"
        "afade=t=in:st=0:d=3,"
        "afade=t=out:st=55:d=5"
        "[b];"
        "[a][b]amix=inputs=2:duration=longest"
    )

    run([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=55:duration=60",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=110:duration=60",
        "-filter_complex",
        filter_complex,
        "-ar",
        "44100",
        "-ac",
        "2",
        str(music)
    ])

    return music


def create_scene_video(frame_path, output_path, seed):
    # Subtle cinematic zoom.
    frames = int(SCENE_DURATION * FPS)

    zoom_direction = 1 if seed % 2 == 0 else -1

    if zoom_direction == 1:
        zoom = "min(zoom+0.00055,1.16)"
    else:
        zoom = "if(lte(zoom,1.0),1.16,max(zoom-0.00055,1.0))"

    vf = (
        f"scale={WIDTH*2}:{HEIGHT*2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH*2}:{HEIGHT*2},"
        f"zoompan=z='{zoom}':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={frames}:"
        f"s={WIDTH}x{HEIGHT}:"
        f"fps={FPS},"
        f"format=yuv420p"
    )

    run([
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(frame_path),
        "-vf",
        vf,
        "-t",
        str(SCENE_DURATION),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        str(output_path)
    ])


def combine_scenes():
    concat_file = OUTPUT / "concat.txt"
    scene_files = []

    for i in range(SCENES):
        frame = FRAMES / f"scene_{i:02d}.png"
        scene_video = OUTPUT / f"part_{i:02d}.mp4"

        create_scene_video(
            frame,
            scene_video,
            i
        )

        scene_files.append(scene_video)

    with concat_file.open("w", encoding="utf-8") as f:
        for path in scene_files:
            f.write(
                f"file '{path.resolve()}'\n"
            )

    silent_video = OUTPUT / "silent.mp4"

    run([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(silent_video)
    ])

    return silent_video


def mix_audio(video_file, narration_file, music_file):
    run([
        "ffmpeg",
        "-y",
        "-i",
        str(video_file),
        "-i",
        str(narration_file),
        "-i",
        str(music_file),
        "-filter_complex",
        "[1:a]volume=1.0[n];"
        "[2:a]volume=0.30[m];"
        "[n][m]amix=inputs=2:duration=longest,"
        "apad,atrim=0:60[a]",
        "-map",
        "0:v:0",
        "-map",
        "[a]",
        "-t",
        "60",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(VIDEO)
    ])


def save_metadata(topic, story):
    title = safe_ascii(
        story.get("title", topic),
        70
    )

    description = safe_ascii(
        story.get("description", ""),
        4500
    )

    keywords = [
        safe_ascii(x, 60)
        for x in story.get("keywords", [])
    ]

    hashtags = [
        safe_ascii(x, 40)
        for x in story.get("hashtags", [])
    ]

    # Always keep core channel SEO.
    core_keywords = [
        "what if",
        "what if daily",
        "science",
        "science shorts",
        "science facts",
        "future",
        "space",
        "earth",
        "curiosity",
        "shorts"
    ]

    for item in core_keywords:
        if item not in keywords:
            keywords.append(item)

    if "#WhatIf" not in hashtags:
        hashtags.append("#WhatIf")

    if "#Shorts" not in hashtags:
        hashtags.append("#Shorts")

    if "#Science" not in hashtags:
        hashtags.append("#Science")

    metadata = {
        "channel": "WHAT IF DAILY",
        "tagline": "IMAGINE. WATCH. WONDER.",
        "topic": topic,
        "title": title,
        "description": description,
        "keywords": keywords[:30],
        "hashtags": hashtags[:12],
        "created_at_utc": datetime.utcnow().isoformat() + "Z"
    }

    METADATA.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=True
        ),
        encoding="utf-8"
    )


def main():
    print("=" * 60)
    print("WHAT IF DAILY AUTOMATION")
    print("=" * 60)

    clean()

    topic = select_topic()

    print("TOPIC:", topic)
    print("GEMINI MODEL:", GEMINI_MODEL)

    story = generate_story(topic)

    narration = build_narration(story)

    print("\nNARRATION:")
    print(narration)

    print("\nGenerating scenes...")

    for i, scene in enumerate(story["scenes"]):
        create_scene_image(
            topic,
            scene,
            i
        )

    print("\nGenerating narration...")
    narration_file = create_voice(narration)

    print("\nGenerating music...")
    music_file = create_music()

    print("\nRendering cinematic scenes...")
    silent_video = combine_scenes()

    print("\nMixing audio...")
    mix_audio(
        silent_video,
        narration_file,
        music_file
    )

    save_metadata(
        topic,
        story
    )

    if not VIDEO.exists():
        raise RuntimeError(
            "Final video was not created."
        )

    if VIDEO.stat().st_size < 100000:
        raise RuntimeError(
            "Final video file is suspiciously small."
        )

    print("\nSUCCESS")
    print("VIDEO:", VIDEO)
    print("METADATA:", METADATA)


if __name__ == "__main__":
    main()

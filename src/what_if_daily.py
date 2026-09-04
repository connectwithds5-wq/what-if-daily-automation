import os
import re
import json
import math
import random
import shutil
import subprocess
import io
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

import requests
from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageFilter,
    ImageEnhance,
    ImageOps
)
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ============================================================
# WHAT IF DAILY v2
# Real imagery + cinematic motion + documentary voice
# YouTube Shorts
# ============================================================

WIDTH = 1080
HEIGHT = 1920
FPS = 30
DURATION = 60
SCENES = 8
SCENE_DURATION = DURATION / SCENES

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
FRAMES = OUTPUT / "frames"
AUDIO = OUTPUT / "audio"
VIDEO = OUTPUT / "what_if_daily.mp4"
METADATA = OUTPUT / "metadata.json"

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Better documentary-style voice
VOICE = os.getenv(
    "TTS_VOICE",
    "en-US-AndrewMultilingualNeural"
)

TTS_RATE = os.getenv(
    "TTS_RATE",
    "-6%"
)

WIKIMEDIA_API = (
    "https://commons.wikimedia.org/w/api.php"
)

USER_AGENT = (
    "WHAT-IF-DAILY/2.0 "
    "(educational-video-generator)"
)


# ============================================================
# TOPICS
# ============================================================

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


# ============================================================
# COMMAND RUNNER
# ============================================================

def run(cmd, check=True):
    print("\nRUN:")
    print(" ".join(map(str, cmd)))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    print(result.stdout[-6000:])

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {cmd}"
        )

    return result


# ============================================================
# CLEAN OUTPUT
# ============================================================

def clean():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)

    FRAMES.mkdir(
        parents=True,
        exist_ok=True
    )

    AUDIO.mkdir(
        parents=True,
        exist_ok=True
    )


# ============================================================
# ASCII SAFETY
# ============================================================

def safe_ascii(text, max_len=160):
    text = str(text)

    text = (
        text
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    text = re.sub(
        r"[^A-Za-z0-9 .,!?':;()/%+\-&]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text[:max_len]


# ============================================================
# FONTS
# ============================================================

def get_font(size, bold=False):
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
            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


# ============================================================
# TEXT WRAPPING
# ============================================================

def wrap_text(text, font, max_width):
    words = safe_ascii(text).split()

    lines = []
    current = ""

    dummy = Image.new(
        "RGB",
        (10, 10)
    )

    draw = ImageDraw.Draw(dummy)

    for word in words:
        test = (
            word
            if not current
            else current + " " + word
        )

        bbox = draw.textbbox(
            (0, 0),
            test,
            font=font
        )

        if bbox[2] <= max_width:
            current = test

        else:
            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    return lines


# ============================================================
# WIKIMEDIA SEARCH
# ============================================================

def search_wikimedia(search_text):
    """
    Search Wikimedia Commons for a real photographic image.
    No API key required.
    """

    search_text = safe_ascii(
        search_text,
        180
    )

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": search_text,
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 2400,
        "format": "json"
    }

    try:
        response = requests.get(
            WIKIMEDIA_API,
            params=params,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        pages = (
            data
            .get("query", {})
            .get("pages", {})
        )

        results = []

        for page in pages.values():

            imageinfo = (
                page
                .get("imageinfo", [{}])[0]
            )

            url = imageinfo.get(
                "thumburl"
            ) or imageinfo.get("url")

            width = imageinfo.get(
                "width",
                0
            )

            height = imageinfo.get(
                "height",
                0
            )

            mime = imageinfo.get(
                "mime",
                ""
            )

            if not url:
                continue

            if not mime.startswith("image/"):
                continue

            if width < 1200 or height < 800:
                continue

            metadata = (
                imageinfo
                .get("extmetadata", {})
            )

            title = page.get(
                "title",
                ""
            )

            results.append({
                "title": title,
                "url": url,
                "width": width,
                "height": height,
                "license": metadata.get(
                    "LicenseShortName",
                    {}
                ).get("value", ""),
                "artist": metadata.get(
                    "Artist",
                    {}
                ).get("value", "")
            })

        results.sort(
            key=lambda x:
            x["width"] * x["height"],
            reverse=True
        )

        return results

    except Exception as e:
        print(
            "Wikimedia search failed:",
            e
        )

        return []


# ============================================================
# DOWNLOAD REAL IMAGE
# ============================================================

def download_image(url):
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT
            },
            timeout=60
        )

        response.raise_for_status()

        image = Image.open(
            io.BytesIO(response.content)
        ).convert("RGB")

        return image

    except Exception as e:
        print(
            "Image download failed:",
            e
        )

        return None


# ============================================================
# VISUAL SEARCH TERMS
# ============================================================

def get_search_terms(topic, scene):
    visual = safe_ascii(
        scene.get("visual", ""),
        300
    )

    text = (
        topic + " " +
        visual
    ).lower()

    terms = []

    if "earth" in text:
        terms.extend([
            "Earth from space",
            "Earth planet clouds space"
        ])

    if "ring" in text or "saturn" in text:
        terms.extend([
            "Saturn rings space",
            "planet rings space"
        ])

    if "moon" in text:
        terms.extend([
            "Moon surface space",
            "full moon space"
        ])

    if "sun" in text:
        terms.extend([
            "Sun space NASA",
            "solar flare Sun"
        ])

    if "ocean" in text or "water" in text:
        terms.extend([
            "ocean aerial",
            "deep ocean underwater"
        ])

    if "volcano" in text:
        terms.extend([
            "volcano eruption",
            "volcanic eruption lava"
        ])

    if "city" in text:
        terms.extend([
            "city skyline night",
            "modern city aerial"
        ])

    if "ice" in text or "frozen" in text:
        terms.extend([
            "ice glacier aerial",
            "Antarctica ice"
        ])

    if "mars" in text:
        terms.extend([
            "Mars planet surface",
            "Mars landscape"
        ])

    if "dinosaur" in text:
        terms.extend([
            "dinosaur fossil museum",
            "dinosaur landscape"
        ])

    if "star" in text:
        terms.extend([
            "Milky Way stars",
            "deep space stars"
        ])

    if not terms:
        terms = [
            "Earth from space",
            "deep space planet"
        ]

    # remove duplicates
    output = []

    for term in terms:
        if term not in output:
            output.append(term)

    return output[:5]


# ============================================================
# FALLBACK BACKGROUND
# ============================================================

def fallback_background(seed):
    random.seed(seed)

    img = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (5, 8, 20)
    )

    pixels = img.load()

    for y in range(HEIGHT):
        t = y / HEIGHT

        r = int(
            5 + 15 * t
        )

        g = int(
            8 + 8 * t
        )

        b = int(
            20 + 25 * t
        )

        for x in range(WIDTH):
            noise = random.randint(
                -2,
                2
            )

            pixels[x, y] = (
                max(0, r + noise),
                max(0, g + noise),
                max(0, b + noise)
            )

    draw = ImageDraw.Draw(img)

    for _ in range(250):
        x = random.randint(
            0,
            WIDTH - 1
        )

        y = random.randint(
            0,
            HEIGHT - 1
        )

        size = random.choice(
            [1, 1, 1, 2]
        )

        brightness = random.randint(
            130,
            255
        )

        draw.ellipse(
            (
                x,
                y,
                x + size,
                y + size
            ),
            fill=(
                brightness,
                brightness,
                brightness
            )
        )

    return img


# ============================================================
# COVER IMAGE
# ============================================================

def prepare_vertical_image(
    image,
    seed
):
    """
    Crop landscape/portrait source
    into cinematic 9:16 composition.
    """

    image = image.convert("RGB")

    target_ratio = WIDTH / HEIGHT
    source_ratio = (
        image.width /
        image.height
    )

    if source_ratio > target_ratio:

        # landscape
        new_height = HEIGHT

        new_width = int(
            new_height *
            source_ratio
        )

    else:

        new_width = WIDTH

        new_height = int(
            new_width /
            source_ratio
        )

    image = image.resize(
        (
            new_width,
            new_height
        ),
        Image.Resampling.LANCZOS
    )

    left = max(
        0,
        (new_width - WIDTH) // 2
    )

    top = max(
        0,
        (new_height - HEIGHT) // 2
    )

    image = image.crop(
        (
            left,
            top,
            left + WIDTH,
            top + HEIGHT
        )
    )

    # cinematic contrast
    image = ImageEnhance.Contrast(
        image
    ).enhance(1.12)

    image = ImageEnhance.Color(
        image
    ).enhance(1.08)

    image = ImageEnhance.Sharpness(
        image
    ).enhance(1.08)

    # subtle dark cinematic overlay
    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0)
    )

    od = ImageDraw.Draw(
        overlay
    )

    for i in range(12):

        alpha = int(
            2 + i * 2
        )

        od.rectangle(
            (
                0,
                i * 150,
                WIDTH,
                HEIGHT
            ),
            fill=(
                0,
                0,
                0,
                alpha
            )
        )

    image = Image.alpha_composite(
        image.convert("RGBA"),
        overlay
    ).convert("RGB")

    return image


# ============================================================
# VIGNETTE
# ============================================================

def add_vignette(image):
    width, height = image.size

    mask = Image.new(
        "L",
        (width, height),
        0
    )

    pixels = mask.load()

    cx = width / 2
    cy = height / 2

    max_dist = math.sqrt(
        cx * cx +
        cy * cy
    )

    for y in range(
        0,
        height,
        4
    ):
        for x in range(
            0,
            width,
            4
        ):

            dist = math.sqrt(
                (x - cx) ** 2 +
                (y - cy) ** 2
            )

            strength = min(
                180,
                int(
                    180 *
                    (dist / max_dist) ** 2
                )
            )

            pixels[x, y] = strength

    mask = mask.resize(
        (width, height),
        Image.Resampling.BILINEAR
    )

    black = Image.new(
        "RGB",
        image.size,
        (0, 0, 0)
    )

    return Image.composite(
        black,
        image,
        ImageOps.invert(mask)
    )


# ============================================================
# REALISTIC EARTH RINGS
# ============================================================

def add_ring_effect(
    image,
    seed
):
    """
    Adds a cinematic Saturn-like ring
    around Earth.

    The Earth remains photographic.
    """

    random.seed(seed)

    layer = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(layer)

    cx = WIDTH // 2
    cy = int(
        HEIGHT * 0.48
    )

    rx = 620
    ry = 170

    # soft ring glow
    for thickness, alpha in [
        (45, 25),
        (28, 45),
        (16, 75),
        (8, 120),
        (3, 190)
    ]:

        draw.ellipse(
            (
                cx - rx,
                cy - ry,
                cx + rx,
                cy + ry
            ),
            outline=(
                210,
                190,
                155,
                alpha
            ),
            width=thickness
        )

    # realistic ring breaks
    for i in range(18):

        x = random.randint(
            cx - rx,
            cx + rx
        )

        y = cy + random.randint(
            -ry,
            ry
        )

        draw.line(
            (
                x,
                y,
                x + random.randint(
                    10,
                    90
                ),
                y
            ),
            fill=(
                230,
                215,
                180,
                random.randint(
                    50,
                    130
                )
            ),
            width=random.randint(
                1,
                3
            )
        )

    layer = layer.filter(
        ImageFilter.GaussianBlur(0.7)
    )

    image = Image.alpha_composite(
        image.convert("RGBA"),
        layer
    )

    # Earth remains visible in front.
    # This creates a simple cinematic
    # planet/ring illusion.

    return image.convert("RGB")


# ============================================================
# TEXT OVERLAY
# ============================================================

def add_scene_text(
    image,
    scene,
    index
):
    image = image.convert(
        "RGBA"
    )

    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(
        overlay
    )

    # Top brand
    brand_font = get_font(
        34,
        bold=True
    )

    draw.text(
        (
            55,
            70
        ),
        "WHAT IF DAILY",
        font=brand_font,
        fill=(
            255,
            255,
            255,
            230
        )
    )

    # scene text
    scene_text = safe_ascii(
        scene.get(
            "on_screen",
            scene.get(
                "narration",
                ""
            )
        ),
        130
    )

    if not scene_text:
        return image.convert(
            "RGB"
        )

    font = get_font(
        58,
        bold=True
    )

    lines = wrap_text(
        scene_text,
        font,
        WIDTH - 150
    )

    if len(lines) > 4:
        lines = lines[:4]

    line_height = 72

    total_height = (
        len(lines) *
        line_height
    )

    box_top = int(
        HEIGHT * 0.69
    )

    box_bottom = (
        box_top +
        total_height +
        80
    )

    # dark transparent text box
    draw.rounded_rectangle(
        (
            45,
            box_top - 35,
            WIDTH - 45,
            box_bottom
        ),
        radius=28,
        fill=(
            0,
            0,
            0,
            135
        )
    )

    y = box_top

    for line in lines:

        bbox = draw.textbbox(
            (0, 0),
            line,
            font=font
        )

        text_width = (
            bbox[2] -
            bbox[0]
        )

        x = (
            WIDTH -
            text_width
        ) // 2

        # shadow
        draw.text(
            (
                x + 3,
                y + 4
            ),
            line,
            font=font,
            fill=(
                0,
                0,
                0,
                220
            )
        )

        draw.text(
            (
                x,
                y
            ),
            line,
            font=font,
            fill=(
                255,
                255,
                255,
                255
            )
        )

        y += line_height

    # scene counter
    small_font = get_font(
        28,
        bold=True
    )

    counter = (
        f"{index + 1:02d}/"
        f"{SCENES:02d}"
    )

    draw.text(
        (
            WIDTH - 145,
            75
        ),
        counter,
        font=small_font,
        fill=(
            255,
            255,
            255,
            190
        )
    )

    image = Image.alpha_composite(
        image,
        overlay
    )

    return image.convert(
        "RGB"
    )


# ============================================================
# SCENE IMAGE
# ============================================================

def create_scene_image(
    topic,
    scene,
    index
):

    seed = abs(
        hash(
            topic +
            str(index)
        )
    ) % 1000000

    search_terms = get_search_terms(
        topic,
        scene
    )

    print(
        f"\nScene {index + 1}:"
    )

    print(
        "Search terms:",
        search_terms
    )

    source = None
    source_info = None

    # Try several real image searches
    for term in search_terms:

        results = search_wikimedia(
            term
        )

        if not results:
            continue

        # choose best available
        for result in results[:5]:

            print(
                "Trying:",
                result["title"]
            )

            source = download_image(
                result["url"]
            )

            if source is not None:

                source_info = result

                print(
                    "Using:",
                    result["title"]
                )

                break

        if source is not None:
            break

    # fallback
    if source is None:

        print(
            "No photographic source found."
        )

        image = fallback_background(
            seed
        )

    else:

        image = prepare_vertical_image(
            source,
            seed
        )

    # Special Earth + rings treatment
    topic_lower = (
        topic +
        " " +
        scene.get(
            "visual",
            ""
        )
    ).lower()

    if (
        "ring" in topic_lower
        or "saturn" in topic_lower
    ):

        image = add_ring_effect(
            image,
            seed
        )

    # subtle cinematic processing
    image = ImageEnhance.Contrast(
        image
    ).enhance(1.08)

    image = ImageEnhance.Color(
        image
    ).enhance(1.05)

    image = add_vignette(
        image
    )

    image = add_scene_text(
        image,
        scene,
        index
    )

    output_path = (
        FRAMES /
        f"scene_{index:02d}.png"
    )

    image.save(
        output_path,
        quality=95
    )

    return {
        "path": output_path,
        "source": source_info
    }


# ============================================================
# SCENE VIDEO
# ============================================================

def create_scene_video(
    image_path,
    index
):

    output_path = (
        FRAMES /
        f"scene_{index:02d}.mp4"
    )

    # alternating zoom direction
    if index % 2 == 0:

        zoom_filter = (
            "zoompan="
            "z='min(zoom+0.0007,1.10)':"
            "d=225:"
            "s=1080x1920:"
            "fps=30"
        )

    else:

        zoom_filter = (
            "zoompan="
            "z='min(zoom+0.0005,1.08)':"
            "d=225:"
            "s=1080x1920:"
            "fps=30"
        )

    run([
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-vf",
        zoom_filter,
        "-t",
        str(SCENE_DURATION),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        str(output_path)
    ])

    return output_path


# ============================================================
# CONCAT VIDEO
# ============================================================

def concat_scenes(scene_videos):

    concat_file = (
        OUTPUT /
        "concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in scene_videos:

            f.write(
                f"file '{path.resolve()}'\n"
            )

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
        str(
            OUTPUT /
            "silent_video.mp4"
        )
    ])

    return (
        OUTPUT /
        "silent_video.mp4"
    )


# ============================================================
# GEMINI STORY
# ============================================================

def generate_story(topic):

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing."
        )

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

    prompt = f"""
You are the lead writer for a cinematic science
YouTube Shorts channel called WHAT IF DAILY.

Topic:
{topic}

Create a 60-second documentary-style YouTube Short.

Requirements:

- Exactly 8 scenes.
- Each scene is about 7.5 seconds.
- Natural spoken English.
- Strong hook in scene 1.
- Build curiosity continuously.
- Explain science clearly.
- Use realistic, believable scenarios.
- Scene 7 should contain the biggest twist.
- Scene 8 should end with a powerful question.
- Narration must sound natural for an English
  documentary voice.
- Do not use emojis.
- Do not use Hindi.
- Do not use Gujarati.
- Do not use Unicode symbols.
- All text must be plain ASCII English.

Return ONLY valid JSON.

Format:

{{
  "title": "YouTube title",
  "description": "YouTube description",
  "keywords": ["keyword1", "keyword2"],
  "hashtags": ["#WhatIfDaily", "#Science"],
  "scenes": [
    {{
      "narration": "spoken narration",
      "on_screen": "short text",
      "visual": "realistic photographic visual description"
    }}
  ]
}}

The visual field should describe what real stock,
NASA-style, scientific or documentary imagery would
best represent the scene.
"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt
    )

    text = response.text.strip()

    # remove markdown fences
    text = re.sub(
        r"^```json\s*",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    data = json.loads(
        text
    )

    scenes = data.get(
        "scenes",
        []
    )

    if len(scenes) < SCENES:

        raise RuntimeError(
            "Gemini returned fewer than "
            f"{SCENES} scenes."
        )

    data["scenes"] = scenes[:SCENES]

    return data


# ============================================================
# EDGE TTS
# ============================================================

def create_voice(
    narration,
    index
):

    output_path = (
        AUDIO /
        f"voice_{index:02d}.mp3"
    )

    narration = safe_ascii(
        narration,
        900
    )

    # Slow, documentary-style delivery
    run([
        "edge-tts",
        "--voice",
        VOICE,
        f"--rate={TTS_RATE}",
        "--text",
        narration,
        "--write-media",
        str(output_path)
    ])

    return output_path


# ============================================================
# CONCAT VOICES
# ============================================================

def concat_audio_files(
    audio_files,
    output_path
):

    concat_file = (
        AUDIO /
        "audio_concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as f:

        for path in audio_files:

            f.write(
                f"file '{path.resolve()}'\n"
            )

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
        str(output_path)
    ])

    return output_path


# ============================================================
# CINEMATIC AMBIENT MUSIC
# ============================================================

def create_music():

    music_path = (
        AUDIO /
        "ambient_music.wav"
    )

    # Low-volume cinematic drone.
    # Fully generated locally with FFmpeg.
    run([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            "sine=frequency=55:"
            "duration=60"
        ),
        "-f",
        "lavfi",
        "-i",
        (
            "sine=frequency=82.5:"
            "duration=60"
        ),
        "-filter_complex",
        (
            "[0:a]volume=0.10[a];"
            "[1:a]volume=0.045[b];"
            "[a][b]amix=inputs=2:"
            "duration=longest,"
            "lowpass=f=900,"
            "aformat=sample_fmts=fltp"
        ),
        "-ar",
        "44100",
        "-ac",
        "2",
        str(music_path)
    ])

    return music_path


# ============================================================
# SOUND EFFECTS
# ============================================================

def create_sfx():

    sfx_path = (
        AUDIO /
        "cinematic_sfx.wav"
    )

    # Deep impact + short high-frequency tone
    run([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        (
            "sine=frequency=48:"
            "duration=0.9"
        ),
        "-f",
        "lavfi",
        "-i",
        (
            "sine=frequency=900:"
            "duration=0.25"
        ),
        "-filter_complex",
        (
            "[0:a]afade=t=out:"
            "st=0.45:d=0.45,"
            "volume=0.28[a];"
            "[1:a]afade=t=out:"
            "st=0.05:d=0.20,"
            "volume=0.12[b];"
            "[a][b]amix=inputs=2:"
            "duration=longest"
        ),
        "-ar",
        "44100",
        "-ac",
        "2",
        str(sfx_path)
    ])

    return sfx_path


# ============================================================
# MIX AUDIO
# ============================================================

def mix_audio(
    narration_audio,
    music_audio,
    sfx_audio
):

    mixed_path = (
        AUDIO /
        "final_audio.m4a"
    )

    # Narration dominant.
    # Music stays subtle.
    # Impact SFX enters near the beginning.
    filter_complex = (
        "[0:a]volume=1.0[n];"
        "[1:a]volume=0.075[m];"
        "[2:a]adelay=120|120,"
        "volume=0.45[s];"
        "[n][m]amix=inputs=2:"
        "duration=longest,"
        "loudnorm=I=-15:TP=-1.5:LRA=11"
        "[base];"
        "[base][s]amix=inputs=2:"
        "duration=longest"
    )

    run([
        "ffmpeg",
        "-y",
        "-i",
        str(narration_audio),
        "-i",
        str(music_audio),
        "-i",
        str(sfx_audio),
        "-filter_complex",
        filter_complex,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(mixed_path)
    ])

    return mixed_path


# ============================================================
# FINAL VIDEO
# ============================================================

def render_final_video(
    silent_video,
    audio
):

    run([
        "ffmpeg",
        "-y",
        "-i",
        str(silent_video),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(VIDEO)
    ])

    return VIDEO


# ============================================================
# METADATA
# ============================================================

def save_metadata(
    topic,
    story,
    source_records
):

    title = safe_ascii(
        story.get(
            "title",
            topic
        ),
        100
    )

    description = safe_ascii(
        story.get(
            "description",
            ""
        ),
        4500
    )

    keywords = [
        safe_ascii(
            x,
            80
        )
        for x in story.get(
            "keywords",
            []
        )
    ]

    hashtags = [
        safe_ascii(
            x,
            60
        )
        for x in story.get(
            "hashtags",
            []
        )
    ]

    # Add source information.
    source_lines = []

    for source in source_records:

        if not source:
            continue

        source_lines.append(
            safe_ascii(
                source.get(
                    "title",
                    ""
                ),
                200
            )
        )

    final_description = (
        description +
        "\n\n"
        "WHAT IF DAILY explores science, "
        "space, Earth and impossible scenarios "
        "through cinematic storytelling.\n\n"
        "Visual sources: Wikimedia Commons."
    )

    metadata = {
        "title": title,
        "description": final_description,
        "keywords": keywords,
        "hashtags": hashtags,
        "topic": topic,
        "generated_at": datetime.utcnow().isoformat(),
        "voice": VOICE,
        "tts_rate": TTS_RATE,
        "visual_sources": source_lines
    }

    with open(
        METADATA,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            metadata,
            f,
            indent=2
        )

    return metadata


# ============================================================
# YOUTUBE UPLOAD
# ============================================================

def upload_youtube(metadata):

    oauth_json = os.getenv(
        "YOUTUBE_OAUTH_JSON"
    )

    if not oauth_json:
        raise RuntimeError(
            "YOUTUBE_OAUTH_JSON secret missing."
        )

    credentials_data = json.loads(
        oauth_json
    )

    credentials = Credentials(
        token=None,
        refresh_token=credentials_data[
            "refresh_token"
        ],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=credentials_data[
            "client_id"
        ],
        client_secret=credentials_data[
            "client_secret"
        ],
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload"
        ]
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials
    )

    title = safe_ascii(
        metadata["title"],
        100
    )

    description = safe_ascii(
        metadata["description"],
        4800
    )

    tags = [
        safe_ascii(
            tag,
            50
        )
        for tag in metadata.get(
            "keywords",
            []
        )
    ]

    hashtags = " ".join(
        metadata.get(
            "hashtags",
            []
        )
    )

    if hashtags:
        description += (
            "\n\n" +
            hashtags
        )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(VIDEO),
        chunksize=-1,
        resumable=True,
        mimetype="video/mp4"
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None

    while response is None:

        status, response = (
            request.next_chunk()
        )

        if status:
            print(
                "Upload progress:",
                int(
                    status.progress() * 100
                ),
                "%"
            )

    video_id = response.get(
        "id"
    )

    print(
        "YouTube upload complete:",
        video_id
    )

    print(
        "https://www.youtube.com/watch?v="
        + str(video_id)
    )

    return video_id


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "====================================\n"
        " WHAT IF DAILY v2\n"
        " Real Cinematic Edition\n"
        "====================================\n"
    )

    clean()

    # Rotate topic based on current date
    day_number = (
        datetime.utcnow()
        .timetuple()
        .tm_yday
    )

    topic = TOPICS[
        (day_number - 1) %
        len(TOPICS)
    ]

    print(
        "Today's topic:",
        topic
    )

    # --------------------------------------------------------
    # GEMINI STORY
    # --------------------------------------------------------

    story = generate_story(
        topic
    )

    scenes = story[
        "scenes"
    ][:SCENES]

    # --------------------------------------------------------
    # CREATE REAL VISUALS
    # --------------------------------------------------------

    scene_videos = []
    source_records = []

    for index, scene in enumerate(
        scenes
    ):

        result = create_scene_image(
            topic,
            scene,
            index
        )

        source_records.append(
            result.get("source")
        )

        scene_video = create_scene_video(
            result["path"],
            index
        )

        scene_videos.append(
            scene_video
        )

    # --------------------------------------------------------
    # CONCAT VIDEO
    # --------------------------------------------------------

    silent_video = concat_scenes(
        scene_videos
    )

    # --------------------------------------------------------
    # CREATE NARRATION
    # --------------------------------------------------------

    voice_files = []

    for index, scene in enumerate(
        scenes
    ):

        narration = scene.get(
            "narration",
            ""
        )

        voice = create_voice(
            narration,
            index
        )

        voice_files.append(
            voice
        )

    narration_audio = (
        AUDIO /
        "narration.mp3"
    )

    concat_audio_files(
        voice_files,
        narration_audio
    )

    # --------------------------------------------------------
    # MUSIC + SFX
    # --------------------------------------------------------

    music = create_music()

    sfx = create_sfx()

    # --------------------------------------------------------
    # MIX
    # --------------------------------------------------------

    final_audio = mix_audio(
        narration_audio,
        music,
        sfx
    )

    # --------------------------------------------------------
    # FINAL VIDEO
    # --------------------------------------------------------

    render_final_video(
        silent_video,
        final_audio
    )

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    metadata = save_metadata(
        topic,
        story,
        source_records
    )

    # --------------------------------------------------------
    # YOUTUBE
    # --------------------------------------------------------

    upload_youtube(
        metadata
    )

    print(
        "\n===================================="
    )

    print(
        " WHAT IF DAILY v2 COMPLETE"
    )

    print(
        " Video:",
        VIDEO
    )

    print(
        " Voice:",
        VOICE
    )

    print(
        "====================================\n"
    )


if __name__ == "__main__":
    main()

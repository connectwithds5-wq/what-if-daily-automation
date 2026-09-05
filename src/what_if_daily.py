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

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

GEMINI_CLIENT = genai.Client(
    api_key=GEMINI_API_KEY
)

VOICE = os.getenv(
    "TTS_VOICE",
    "en-US-AndrewMultilingualNeural"
)

TTS_RATE = os.getenv("TTS_RATE", "+5%")

TOPIC_HISTORY_FILE = ROOT / "topic_history.json"


def load_topic_history():
    history_file = TOPIC_HISTORY_FILE

    if not history_file.exists():
        return []

    try:
        data = json.loads(
            history_file.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            return data

    except Exception as e:
        print(
            "Topic history read failed:",
            e
        )

    return []


def save_topic_history(history):
    TOPIC_HISTORY_FILE.write_text(
        json.dumps(
            history[-1000:],
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )



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
        raise RuntimeError(
            f"Command failed: {cmd}"
        )

    return result


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


def safe_ascii(text, max_len=120):
    text = (
        str(text or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    text = re.sub(
        r"[^A-Za-z0-9 .,!?':;()/%+\-]",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()[:max_len]


def get_font(size, bold=False):

    names = [
        (
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf"
            if bold
            else
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf"
        ),
        (
            "/usr/share/fonts/truetype/liberation2/"
            "LiberationSans-Bold.ttf"
            if bold
            else
            "/usr/share/fonts/truetype/liberation2/"
            "LiberationSans-Regular.ttf"
        ),
    ]

    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(
                name,
                size
            )

    return ImageFont.load_default()


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

        if draw.textbbox(
            (0, 0),
            test,
            font=font
        )[2] <= max_width:

            current = test

        else:

            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    return lines


def fit_crop(
    img,
    size=(WIDTH, HEIGHT),
    focus=(0.5, 0.5)
):

    img = img.convert("RGB")

    tw, th = size

    scale = max(
        tw / img.width,
        th / img.height
    )

    nw = max(
        tw,
        int(img.width * scale)
    )

    nh = max(
        th,
        int(img.height * scale)
    )

    img = img.resize(
        (nw, nh),
        Image.Resampling.LANCZOS
    )

    fx, fy = focus

    left = max(
        0,
        min(
            nw - tw,
            int((nw - tw) * fx)
        )
    )

    top = max(
        0,
        min(
            nh - th,
            int((nh - th) * fy)
        )
    )

    return img.crop(
        (
            left,
            top,
            left + tw,
            top + th
        )
    )


def cinematic_grade(img, seed):

    random.seed(seed)

    img = ImageEnhance.Contrast(
        img
    ).enhance(1.14)

    img = ImageEnhance.Color(
        img
    ).enhance(1.10)

    img = ImageEnhance.Sharpness(
        img
    ).enhance(1.16)

    pix = img.load()

    for y in range(
        0,
        HEIGHT,
        4
    ):

        t = y / HEIGHT

        for x in range(
            0,
            WIDTH,
            4
        ):

            r, g, b = pix[x, y]

            if t < 0.55:

                pix[x, y] = (
                    min(
                        255,
                        int(r * .97)
                    ),
                    min(
                        255,
                        int(g * .98)
                    ),
                    min(
                        255,
                        int(b * 1.04)
                    )
                )

            else:

                pix[x, y] = (
                    min(
                        255,
                        int(r * 1.02)
                    ),
                    min(
                        255,
                        int(g * 1.00)
                    ),
                    min(
                        255,
                        int(b * .98)
                    )
                )

    overlay = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    op = overlay.load()

    cx = WIDTH / 2
    cy = HEIGHT / 2

    maxd = math.hypot(
        cx,
        cy
    )

    for y in range(
        0,
        HEIGHT,
        8
    ):

        for x in range(
            0,
            WIDTH,
            8
        ):

            d = (
                math.hypot(
                    x - cx,
                    y - cy
                ) / maxd
            )

            a = int(
                max(
                    0,
                    min(
                        125,
                        (d ** 2.4) * 110
                    )
                )
            )

            for yy in range(
                y,
                min(
                    y + 8,
                    HEIGHT
                )
            ):

                for xx in range(
                    x,
                    min(
                        x + 8,
                        WIDTH
                    )
                ):

                    op[xx, yy] = (
                        0,
                        0,
                        0,
                        a
                    )

    return Image.alpha_composite(
        img.convert("RGBA"),
        overlay
    ).convert("RGB")


def add_atmosphere(
    img,
    seed,
    mode
):

    random.seed(seed)

    layer = Image.new(
        "RGBA",
        img.size,
        (0, 0, 0, 0)
    )

    d = ImageDraw.Draw(layer)

    if mode in (
        "space",
        "earth",
        "moon",
        "rings",
        "mars"
    ):

        for _ in range(75):

            x = random.randrange(WIDTH)
            y = random.randrange(HEIGHT)

            r = random.choice(
                [1, 1, 1, 2]
            )

            d.ellipse(
                (
                    x - r,
                    y - r,
                    x + r,
                    y + r
                ),
                fill=(
                    255,
                    255,
                    255,
                    random.randint(
                        70,
                        170
                    )
                )
            )

    else:

        for _ in range(18):

            x = random.randrange(WIDTH)
            y = random.randrange(HEIGHT)

            r = random.randint(
                30,
                100
            )

            d.ellipse(
                (
                    x - r,
                    y - r,
                    x + r,
                    y + r
                ),
                fill=(
                    255,
                    255,
                    255,
                    random.randint(
                        3,
                        10
                    )
                )
            )

    return Image.alpha_composite(
        img.convert("RGBA"),
        layer.filter(
            ImageFilter.GaussianBlur(.5)
        )
    ).convert("RGB")


def create_typography_scene(topic, scene, index):
    """
    Create a cinematic kinetic-typography scene.

    No external images are downloaded. Every scene is generated locally
    from text, gradients, subtle geometric motion elements, and typography.
    """

    seed = int(
        hashlib.sha256(
            f"{topic}:{index}".encode("utf-8")
        ).hexdigest()[:8],
        16
    )

    random.seed(seed)

    scene_dir = FRAMES / f"scene_{index:02d}"
    scene_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    on_screen = safe_ascii(
        scene.get("on_screen", ""),
        55
    ).strip()

    narration = safe_ascii(
        scene.get("narration", ""),
        500
    ).strip()

    if not on_screen:
        on_screen = narration[:55]

    # ---------------------------------------------------------
    # Split the narration into short readable chunks.
    # These appear underneath the main kinetic text.
    # ---------------------------------------------------------

    words = narration.split()
    subtitle_chunks = []

    if words:
        chunk_size = max(
            5,
            math.ceil(len(words) / 2)
        )

        for i in range(
            0,
            len(words),
            chunk_size
        ):
            subtitle_chunks.append(
                " ".join(words[i:i + chunk_size])
            )

    if not subtitle_chunks:
        subtitle_chunks = [""]

    # ---------------------------------------------------------
    # Create a clean cinematic background.
    # ---------------------------------------------------------

    base = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (8, 10, 16)
    )

    px = base.load()

    # Vertical cinematic gradient.
    for y in range(HEIGHT):
        t = y / max(1, HEIGHT - 1)

        r = int(7 + 8 * t)
        g = int(9 + 10 * t)
        b = int(15 + 18 * t)

        for x in range(WIDTH):
            px[x, y] = (r, g, b)

    # ---------------------------------------------------------
    # Subtle abstract light shapes.
    # ---------------------------------------------------------

    bg = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    bd = ImageDraw.Draw(bg)

    accent_x = random.randint(
        100,
        WIDTH - 100
    )

    accent_y = random.randint(
        300,
        HEIGHT - 300
    )

    for radius, alpha in [
        (520, 8),
        (400, 10),
        (280, 13),
        (180, 16)
    ]:
        bd.ellipse(
            (
                accent_x - radius,
                accent_y - radius,
                accent_x + radius,
                accent_y + radius
            ),
            outline=(
                255,
                255,
                255,
                alpha
            ),
            width=3
        )

    # Diagonal cinematic lines.
    for i in range(5):
        y = 350 + i * 300
        bd.line(
            (
                -200,
                y,
                WIDTH + 200,
                y - 180
            ),
            fill=(
                255,
                255,
                255,
                10
            ),
            width=2
        )

    bg = bg.filter(
        ImageFilter.GaussianBlur(2)
    )

    base = Image.alpha_composite(
        base.convert("RGBA"),
        bg
    ).convert("RGB")

    # ---------------------------------------------------------
    # Dark readability area.
    # ---------------------------------------------------------

    overlay = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    od = ImageDraw.Draw(overlay)

    od.rectangle(
        (0, 0, WIDTH, 280),
        fill=(0, 0, 0, 95)
    )

    od.rectangle(
        (0, 1320, WIDTH, HEIGHT),
        fill=(0, 0, 0, 120)
    )

    base = Image.alpha_composite(
        base.convert("RGBA"),
        overlay
    ).convert("RGB")

    # ---------------------------------------------------------
    # Fonts.
    # ---------------------------------------------------------

    small_font = get_font(
        34,
        bold=True
    )

    main_font = get_font(
        104,
        bold=True
    )

    subtitle_font = get_font(
        42,
        bold=False
    )

    counter_font = get_font(
        28,
        bold=True
    )

    # ---------------------------------------------------------
    # Prepare main text lines.
    # ---------------------------------------------------------

    main_lines = wrap_text(
        on_screen.upper(),
        main_font,
        900
    )[:3]

    # Keep typewriter effect smooth and readable.
    total_chars = len(on_screen.upper())
    typing_frames = 18

    paths = []

    for frame_index in range(
        typing_frames
    ):

        canvas = base.copy()
        d = ImageDraw.Draw(canvas)

        # Header.
        header = "WHAT IF DAILY"
        d.text(
            (70, 72),
            header,
            font=small_font,
            fill=(255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0)
        )

        # Scene counter.
        counter = f"SCENE {index + 1}/{SCENES}"
        box = d.textbbox(
            (0, 0),
            counter,
            font=counter_font
        )

        d.text(
            (
                WIDTH - 70 - (box[2] - box[0]),
                78
            ),
            counter,
            font=counter_font,
            fill=(210, 210, 210)
        )

        # Current typewriter character count.
        if total_chars:
            visible_chars = int(
                total_chars *
                ((frame_index + 1) / typing_frames)
            )
        else:
            visible_chars = 0

        typed = on_screen.upper()[:visible_chars]

        typed_lines = wrap_text(
            typed,
            main_font,
            900
        )[:3]

        # If typing reaches a space, avoid a strange trailing blank.
        typed_lines = [
            line.rstrip()
            for line in typed_lines
        ]

        if not typed_lines:
            typed_lines = [""]

        # Main text block.
        line_height = 126
        block_height = len(typed_lines) * line_height
        y = 710 - block_height // 2

        for line in typed_lines:
            bbox = d.textbbox(
                (0, 0),
                line,
                font=main_font,
                stroke_width=2
            )

            tw = bbox[2] - bbox[0]
            x = (WIDTH - tw) // 2

            # Shadow.
            d.text(
                (x + 6, y + 7),
                line,
                font=main_font,
                fill=(0, 0, 0),
                stroke_width=4,
                stroke_fill=(0, 0, 0)
            )

            # Main white text.
            d.text(
                (x, y),
                line,
                font=main_font,
                fill=(255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0)
            )

            y += line_height

        # Blinking cursor during typing.
        if frame_index < typing_frames - 1:
            cursor_on = (
                frame_index % 4
            ) < 2

            if cursor_on and typed_lines:
                last_line = typed_lines[-1]
                bbox = d.textbbox(
                    (0, 0),
                    last_line,
                    font=main_font
                )

                tw = bbox[2] - bbox[0]
                cursor_x = (
                    (WIDTH - tw) // 2
                    + tw
                    + 8
                )

                cursor_y = y - line_height

                d.rectangle(
                    (
                        cursor_x,
                        cursor_y + 15,
                        cursor_x + 7,
                        cursor_y + 100
                    ),
                    fill=(255, 255, 255)
                )

        # Subtitle appears after the first part of typing.
        if frame_index >= 7:

            subtitle_index = min(
                1,
                (frame_index - 7) // 6
            )

            subtitle = subtitle_chunks[
                subtitle_index
            ]

            subtitle_lines = wrap_text(
                subtitle,
                subtitle_font,
                850
            )[:2]

            sy = 1420

            for line in subtitle_lines:

                bbox = d.textbbox(
                    (0, 0),
                    line,
                    font=subtitle_font
                )

                tw = bbox[2] - bbox[0]
                sx = (WIDTH - tw) // 2

                d.text(
                    (sx + 2, sy + 3),
                    line,
                    font=subtitle_font,
                    fill=(0, 0, 0),
                    stroke_width=2,
                    stroke_fill=(0, 0, 0)
                )

                d.text(
                    (sx, sy),
                    line,
                    font=subtitle_font,
                    fill=(225, 225, 225),
                    stroke_width=1,
                    stroke_fill=(0, 0, 0)
                )

                sy += 58

        # Bottom progress bar.
        progress = (
            (index + 1) / SCENES
        )

        d.rectangle(
            (70, 1818, WIDTH - 70, 1824),
            fill=(90, 90, 90)
        )

        d.rectangle(
            (
                70,
                1818,
                70 + int(
                    (WIDTH - 140) * progress
                ),
                1824
            ),
            fill=(255, 255, 255)
        )

        path = (
            scene_dir
            / f"typing_{frame_index:02d}.png"
        )

        canvas.save(
            path,
            format="PNG",
            optimize=True
        )

        paths.append(path)

    # Final fully typed frame.
    final_path = (
        scene_dir
        / "final.png"
    )

    paths[-1].replace(final_path)

    # Re-add final frame to the list for the hold section.
    paths[-1] = final_path

    return {
        "path": str(final_path),
        "frames": [str(p) for p in paths],
        "topic": topic,
        "scene": scene
    }


def create_video(scene_info):
    """
    Build the 60-second video from kinetic-typing frames.

    Each scene has a short typewriter reveal followed by a cinematic hold.
    No external image or image-search API is required.
    """

    clips = []

    for i, info in enumerate(scene_info):

        clip = FRAMES / f"clip_{i:02d}.mp4"

        frame_paths = info.get("frames", [])

        if not frame_paths:
            raise RuntimeError(
                f"Scene {i + 1}: typing frames are missing."
            )

        concat = OUTPUT / f"typing_concat_{i:02d}.txt"

        lines = []

        # Typewriter reveal: 18 frames x 0.10 sec = 1.8 sec.
        typing_duration = 0.10

        for frame_path in frame_paths:
            lines.append(
                f"file '{Path(frame_path).as_posix()}'"
            )
            lines.append(
                f"duration {typing_duration:.3f}"
            )

        # Hold the final typed frame for the remaining scene time.
        hold_duration = max(
            0.2,
            SCENE_DURATION - (
                len(frame_paths) * typing_duration
            )
        )

        lines.append(
            f"file '{Path(frame_paths[-1]).as_posix()}'"
        )
        lines.append(
            f"duration {hold_duration:.3f}"
        )

        # Repeat final frame so concat honors its duration.
        lines.append(
            f"file '{Path(frame_paths[-1]).as_posix()}'"
        )

        concat.write_text(
            "\n".join(lines),
            encoding="utf-8"
        )

        run([
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-vf",
            "fps=30,format=yuv420p",
            "-t",
            str(SCENE_DURATION),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(clip)
        ])

        clips.append(clip)

    concat_all = OUTPUT / "concat.txt"

    concat_all.write_text(
        "\n".join(
            f"file '{p.as_posix()}'"
            for p in clips
        ),
        encoding="utf-8"
    )

    silent = OUTPUT / "video_silent.mp4"

    run([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_all),
        "-c",
        "copy",
        str(silent)
    ])

    return silent


def mix_audio(
    video_silent,
    narration,
    music,
    sfx
):

    audio = (
        AUDIO
        / "final_audio.m4a"
    )

    run([
        "ffmpeg",
        "-y",
        "-i",
        str(narration),
        "-i",
        str(music),
        "-i",
        str(sfx),

        "-filter_complex",

        "[0:a]"
        "loudnorm="
        "I=-15:"
        "TP=-1.5:"
        "LRA=8,"
        "volume=1.12[n];"

        "[1:a]"
        "volume=0.14[m];"

        "[2:a]"
        "volume=0.20[s];"

        "[n][m][s]"
        "amix="
        "inputs=3:"
        "duration=longest:"
        "dropout_transition=2,"
        "alimiter=limit=0.9[a]",

        "-map",
        "[a]",

        "-t",
        "60",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        str(audio)
    ])

    run([
        "ffmpeg",
        "-y",
        "-i",
        str(video_silent),
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

        "-shortest",

        str(VIDEO)
    ])

    return VIDEO


def save_metadata(
    story,
    sources
):

    desc = safe_ascii(
        story.get(
            "description",
            ""
        ),
        4500
    )

    desc += (
        "\n\n"
        "WHAT IF DAILY - "
        "IMAGINE. WATCH. WONDER."
    )

    desc += (
        "\n\n"
        "Visual format: cinematic kinetic typography.\n"
        "No external image assets are used.\n"
    )

    seen = set()

    for src in sources:

        url = src.get(
            "url",
            ""
        )

        title = safe_ascii(
            src.get(
                "title",
                ""
            ),
            180
        )

        license_name = safe_ascii(
            src.get(
                "license",
                "Unknown"
            ),
            100
        )

        source_name = safe_ascii(
            src.get(
                "source",
                "Unknown"
            ),
            40
        )

        if (
            url
            and url not in seen
        ):

            seen.add(
                url
            )

            desc += (
                f"- {source_name}: "
                f"{title} | "
                f"{license_name} | "
                f"{url}\n"
            )

    data = {

        "title":
            story.get(
                "title",
                "WHAT IF DAILY"
            ),

        "description":
            desc,

        "keywords":
            story.get(
                "keywords",
                []
            ),

        "hashtags":
            story.get(
                "hashtags",
                []
            ),

        "created_at":
            datetime.utcnow().isoformat()
            + "Z",

        "voice":
            VOICE,

        "tts_rate":
            TTS_RATE,

        "visual_style":
            "real high-resolution "
            "photographic imagery from "
            "NASA/Wikimedia Commons with "
            "cinematic crop, grade and motion",

        "source_count":
            len(seen)
    }

    METADATA.write_text(
        json.dumps(
            data,
            indent=2
        ),
        encoding="utf-8"
    )

    return data


def upload_youtube(metadata):

    raw = os.getenv(
        "YOUTUBE_OAUTH_JSON"
    )

    if not raw:

        print(
            "YOUTUBE_OAUTH_JSON missing; "
            "skipping upload"
        )

        return

    data = json.loads(
        raw
    )

    creds = Credentials(
        None,
        refresh_token=data[
            "refresh_token"
        ],
        token_uri=
            "https://oauth2.googleapis.com/token",
        client_id=data[
            "client_id"
        ],
        client_secret=data[
            "client_secret"
        ],
        scopes=[
            "https://www.googleapis.com/auth/"
            "youtube.upload"
        ]
    )

    youtube = build(
        "youtube",
        "v3",
        credentials=creds
    )

    description = safe_ascii(
        metadata[
            "description"
        ],
        4900
    )

    hashtags = " ".join(
        metadata.get(
            "hashtags",
            []
        )[:8]
    )

    if hashtags:

        description += (
            "\n\n"
            + hashtags
        )

    body = {

        "snippet": {

            "title":
                safe_ascii(
                    metadata[
                        "title"
                    ],
                    95
                ),

            "description":
                description,

            "tags":
                metadata.get(
                    "keywords",
                    []
                )[:25],

            "categoryId":
                "28"
        },

        "status": {

            "privacyStatus":
                "public",

            "selfDeclaredMadeForKids":
                False
        }
    }

    print(
        "Uploading to YouTube:",
        body["snippet"]["title"]
    )

    req = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(
            str(VIDEO),
            mimetype="video/mp4",
            resumable=True
        )
    )

    result = req.execute()

    print(
        "YouTube upload complete:",
        result.get("id")
    )


def main():

    clean()

    manual_topic = os.getenv("WHAT_IF_TOPIC", "").strip()

    if manual_topic:
        topic = manual_topic
    else:
        topic = generate_unique_topic()
    print(
        "Topic:",
        topic
    )

    print(
        "Voice:",
        VOICE,
        "Rate:",
        TTS_RATE
    )

    story = create_story(
        topic
    )

    scene_info = []
    sources = []

    for i, scene in enumerate(
        story["scenes"]
    ):

        info = create_typography_scene(
            topic,
            scene,
            i
        )

        scene_info.append(
            info
        )

    full_narration = " ".join(
        s["narration"]
        for s in story["scenes"]
    )

    narration = create_voice(
        full_narration
    )

    music = create_music()

    sfx = create_sound_design()

    silent = create_video(
        scene_info
    )

    final_video = mix_audio(
        silent,
        narration,
        music,
        sfx
    )

    metadata = save_metadata(
        story,
        sources
    )

    upload_youtube(
        metadata
    )

    print(
        "DONE:",
        final_video
    )


if __name__ == "__main__":
    main()

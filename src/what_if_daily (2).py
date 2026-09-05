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
import time
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

NASA_API = "https://images-api.nasa.gov/search"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":
        "WHAT-IF-DAILY/3.0 educational cinematic Shorts generator"
})


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
# =========================================================
# IMAGE SEARCH FILTER
# =========================================================

BAD_WORDS = {
    "diagram",
    "illustration",
    "drawing",
    "cartoon",
    "animation",
    "animated",
    "cgi",
    "render",
    "rendering",
    "3d render",
    "concept art",
    "digital art",
    "artwork",
    "painting",
    "sketch",
    "graphic",
    "infographic",
    "logo",
    "map",
    "flag",
    "poster",
    "icon",
    "screenshot",
    "computer generated",
    "generated image",
    "ai generated",
    "ai art"
}

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
    """
    Keep real photographs looking REAL.

    - No fake stars on Earth/Moon/Mars photographs.
    - No artificial particles over documentary photographs.
    - Only very subtle cinematic atmospheric enhancement.
    """

    random.seed(seed)

    # ---------------------------------------------------------
    # REAL PHOTOGRAPH PROTECTION
    # ---------------------------------------------------------
    # Do NOT place artificial stars/dots over real photographs.
    # This keeps NASA/Wikimedia imagery natural.
    if mode in (
        "earth",
        "moon",
        "mars",
        "ocean",
        "volcano",
        "ice",
        "city"
    ):
        return img.convert("RGB")

    # ---------------------------------------------------------
    # SPACE MODE
    # ---------------------------------------------------------
    # Only deep-space scenes may receive extremely subtle
    # atmospheric particles.
    if mode == "space":
        layer = Image.new(
            "RGBA",
            img.size,
            (0, 0, 0, 0)
        )

        d = ImageDraw.Draw(layer)

        for _ in range(18):
            x = random.randrange(WIDTH)
            y = random.randrange(HEIGHT)

            r = random.choice(
                [1, 1, 1, 2]
            )

            alpha = random.randint(
                18,
                55
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
                    alpha
                )
            )

        return Image.alpha_composite(
            img.convert("RGBA"),
            layer.filter(
                ImageFilter.GaussianBlur(0.7)
            )
        ).convert("RGB")

    # ---------------------------------------------------------
    # ALL OTHER MODES
    # ---------------------------------------------------------
    # Preserve original photograph completely.
    return img.convert("RGB")

def is_bad_title(title):

    t = title.lower()

    return any(
        w in t
        for w in BAD_WORDS
    )


def score_candidate(item, query, source):

    title = str(
        item.get(
            "title",
            ""
        )
    ).lower()

    query_text = str(
        query or ""
    ).lower()

    w = int(
        item.get(
            "width",
            0
        ) or 0
    )

    h = int(
        item.get(
            "height",
            0
        ) or 0
    )

    if w < 1000 or h < 600:
        return -999

    if is_bad_title(title):
        return -500

    score = 0

    # IMAGE QUALITY
    megapixels = (
        w * h
    ) / 1_000_000

    score += min(
        40,
        megapixels * 4
    )

    ratio = w / max(
        1,
        h
    )

    if 1.15 <= ratio <= 2.3:
        score += 12

    # SOURCE QUALITY
    if source == "NASA":
        score += 30

    elif source == "Wikimedia Commons":
        score += 12

    # QUERY RELEVANCE
    query_words = set(
        re.findall(
            r"[a-z0-9]{3,}",
            query_text
        )
    )

    title_words = set(
        re.findall(
            r"[a-z0-9]{3,}",
            title
        )
    )

    ignored = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "photo",
        "photograph",
        "image",
        "real",
        "nasa",
        "commons",
        "wikimedia",
        "high",
        "resolution"
    }

    useful = (
        query_words
        - ignored
    )

    matches = len(
        useful & title_words
    )

    score += min(
        50,
        matches * 8
    )

    # Photographic keywords
    photographic = [
        "earth",
        "moon",
        "planet",
        "space",
        "astronaut",
        "ocean",
        "volcano",
        "lava",
        "cloud",
        "surface",
        "landscape",
        "mountain",
        "glacier",
        "city",
        "forest",
        "desert",
        "satellite"
    ]

    score += sum(
        4
        for word in photographic
        if word in title
    )

    return score


def nasa_search(
    query,
    limit=12
):

    try:

        r = SESSION.get(
            NASA_API,
            params={
                "q": query,
                "media_type": "image",
                "page_size": limit
            },
            timeout=30
        )

        r.raise_for_status()

        data = r.json()

        out = []

        for item in data.get(
            "collection",
            {}
        ).get(
            "items",
            []
        ):

            data_item = item.get(
                "data",
                [{}]
            )[0]

            title = data_item.get(
                "title",
                ""
            )

            links = item.get(
                "links",
                []
            )

            href = next(
                (
                    x.get("href")
                    for x in links
                    if x.get("render") == "image"
                ),
                None
            )

            if not href:
                continue

            if is_bad_title(title):
                continue

            out.append({
                "url": href,
                "title": title,
                "license":
                    "NASA media - verify item notes",
                "source": "NASA",
                "width": 2000,
                "height": 1200
            })

        out.sort(
            key=lambda x:
                score_candidate(
                    x,
                    query,
                    "NASA"
                ),
            reverse=True
        )

        return out

    except Exception as e:

        print(
            "NASA search failed:",
            e
        )

        return []



def wikimedia_search(
    query,
    limit=8
):
    """
    Search Wikimedia Commons safely.

    Includes:
    - rate-limit protection
    - retry for HTTP 429
    - real-photo filtering
    - high-resolution filtering
    """

    for attempt in range(3):

        try:

            # -------------------------------------------------
            # Small delay to avoid Wikimedia rate limiting
            # -------------------------------------------------

            delay = 1.5 + random.uniform(
                0.5,
                1.5
            )

            time.sleep(delay)

            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch":
                    f"filetype:bitmap {query}",
                "gsrnamespace": 6,
                "gsrlimit": limit,
                "prop": "imageinfo",
                "iiprop":
                    "url|size|extmetadata",
                "format": "json"
            }

            r = SESSION.get(
                WIKIMEDIA_API,
                params=params,
                timeout=30
            )

            # -------------------------------------------------
            # RATE LIMIT
            # -------------------------------------------------

            if r.status_code == 429:

                wait = 5 * (
                    attempt + 1
                )

                print(
                    f"Wikimedia rate limited "
                    f"(429). Waiting {wait}s..."
                )

                time.sleep(wait)

                continue

            r.raise_for_status()

            data = r.json()

            pages = list(
                data.get(
                    "query",
                    {}
                ).get(
                    "pages",
                    {}
                ).values()
            )

            out = []

            for p in pages:

                info = (
                    p.get(
                        "imageinfo"
                    )
                    or [{}]
                )[0]

                url = info.get(
                    "url"
                )

                w = int(
                    info.get(
                        "width",
                        0
                    ) or 0
                )

                h = int(
                    info.get(
                        "height",
                        0
                    ) or 0
                )

                title = p.get(
                    "title",
                    ""
                )

                # -------------------------------------------------
                # Reject weak images
                # -------------------------------------------------

                if (
                    not url
                    or w < 1200
                    or h < 700
                    or is_bad_title(title)
                ):
                    continue

                meta = (
                    info.get(
                        "extmetadata"
                    )
                    or {}
                )

                license_name = str(
                    (
                        meta.get(
                            "LicenseShortName"
                        )
                        or {}
                    ).get(
                        "value",
                        "Unknown"
                    )
                )

                out.append({
                    "url": url,
                    "width": w,
                    "height": h,
                    "title": title,
                    "license":
                        re.sub(
                            r"<[^>]+>",
                            "",
                            license_name
                        ),
                    "source":
                        "Wikimedia Commons"
                })

            # -------------------------------------------------
            # Correct Wikimedia scoring
            # -------------------------------------------------

            out.sort(
                key=lambda x:
                    score_candidate(
                        x,
                        query,
                        "Wikimedia Commons"
                    ),
                reverse=True
            )

            return out

        except Exception as e:

            print(
                "Wikimedia search failed:",
                e
            )

            if attempt < 2:

                wait = 3 * (
                    attempt + 1
                )

                print(
                    f"Retrying Wikimedia "
                    f"in {wait}s..."
                )

                time.sleep(wait)

            else:
                return []

    return []

def download_image(source):

    try:

        url = source.get("url", "").strip()

        if not url:
            return None

        print("Downloading image:")
        print(url)

        r = SESSION.get(
            url,
            timeout=45,
            allow_redirects=True
        )

        r.raise_for_status()

        content_type = (
            r.headers.get(
                "Content-Type",
                ""
            )
            .lower()
        )

        # Reject HTML pages and other non-image responses.
        if (
            "image/" not in content_type
            and not url.lower().endswith(
                (
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".tif",
                    ".tiff"
                )
            )
        ):
            print(
                "Rejected non-image response:",
                content_type
            )
            return None

        img = Image.open(
            io.BytesIO(r.content)
        )

        img.load()

        img = img.convert("RGB")

        print(
            f"Downloaded image: "
            f"{img.width}x{img.height}"
        )

        if (
            img.width < 1000
            or img.height < 600
        ):
            print(
                "Rejected image: resolution too low"
            )
            return None

        return img

    except Exception as e:

        print(
            "Image download failed:",
            e
        )

        return None

def visual_mode(
    topic,
    scene
):

    text = (
        f"{topic} "
        f"{scene.get('visual', '')}"
    ).lower()

    if (
        "ring" in text
        or "saturn" in text
    ):
        return "rings"

    if "moon" in text:
        return "moon"

    if (
        "volcano" in text
        or "lava" in text
    ):
        return "volcano"

    if (
        "ocean" in text
        or "water" in text
        or "underwater" in text
    ):
        return "ocean"

    if (
        "ice" in text
        or "frozen" in text
        or "antarctica" in text
    ):
        return "ice"

    if (
        "sun" in text
        or "solar" in text
    ):
        return "sun"

    if "mars" in text:
        return "mars"

    if (
        "city" in text
        or "ai" in text
        or "human" in text
        or "people" in text
    ):
        return "city"

    if (
        "earth" in text
        or "planet" in text
    ):
        return "earth"

    return "space"


def visual_queries(topic, scene, mode):

    visual = safe_ascii(
        scene.get("visual", ""),
        500
    )

    narration = safe_ascii(
        scene.get("narration", ""),
        500
    )

    text = (
        f"{topic} "
        f"{visual} "
        f"{narration}"
    ).lower()

    queries = []

    # --------------------------------------------------
    # 1. EXACT SCENE SEARCH
    # --------------------------------------------------

    if visual:

        queries.append(
            f"{visual} NASA photograph"
        )

        queries.append(
            f"{visual} real photograph"
        )

        queries.append(
            f"{visual} Wikimedia Commons"
        )

    # --------------------------------------------------
    # 2. TOPIC + SCENE
    # --------------------------------------------------

    queries.append(
        f"{safe_ascii(topic, 120)} "
        f"{visual}"
    )

    # --------------------------------------------------
    # 3. SCIENCE-SPECIFIC SEARCHES
    # --------------------------------------------------

    if "earth" in text:

        queries.extend([
            "Earth from space NASA",
            "Earth atmosphere NASA photograph",
            "Earth clouds satellite NASA",
            "Earth horizon space NASA"
        ])

    if "moon" in text:

        queries.extend([
            "Moon surface NASA photograph",
            "Moon Earth from space NASA",
            "lunar landscape NASA",
            "Moon horizon NASA"
        ])

    if (
        "ocean" in text
        or "sea" in text
        or "underwater" in text
    ):

        queries.extend([
            "ocean aerial photograph",
            "deep ocean underwater photograph",
            "ocean satellite NASA",
            "sea waves aerial photograph"
        ])

    if (
        "volcano" in text
        or "lava" in text
    ):

        queries.extend([
            "volcano eruption NASA",
            "volcano lava photograph",
            "volcano plume satellite NASA",
            "active volcano aerial photograph"
        ])

    if (
        "ice" in text
        or "frozen" in text
        or "glacier" in text
        or "antarctica" in text
    ):

        queries.extend([
            "Antarctica NASA photograph",
            "glacier aerial photograph",
            "polar ice NASA",
            "frozen landscape photograph"
        ])

    if (
        "mars" in text
    ):

        queries.extend([
            "Mars surface NASA",
            "Mars landscape NASA",
            "Mars rover photograph",
            "Mars planet NASA"
        ])

    if (
        "sun" in text
        or "solar" in text
    ):

        queries.extend([
            "Sun NASA photograph",
            "solar flare NASA",
            "solar surface NASA",
            "Sun from space NASA"
        ])

    if (
        "astronaut" in text
        or "spacewalk" in text
    ):

        queries.extend([
            "astronaut spacewalk NASA",
            "astronaut Earth background NASA",
            "space station astronaut NASA",
            "extravehicular activity NASA"
        ])

    if (
        "city" in text
        or "cities" in text
        or "urban" in text
    ):

        queries.extend([
            "city skyline aerial photograph",
            "city street documentary photograph",
            "city night aerial photograph",
            "urban landscape photograph"
        ])

    if (
        "desert" in text
        or "sahara" in text
    ):

        queries.extend([
            "Sahara desert NASA",
            "Sahara aerial photograph",
            "desert landscape photograph",
            "desert satellite NASA"
        ])

    if (
        "forest" in text
        or "jungle" in text
    ):

        queries.extend([
            "forest aerial photograph",
            "forest landscape photograph",
            "forest satellite NASA",
            "dense jungle photograph"
        ])

    if (
        "dinosaur" in text
        or "prehistoric" in text
    ):

        queries.extend([
            "dinosaur fossil photograph",
            "dinosaur skeleton museum photograph",
            "prehistoric fossil photograph",
            "natural history museum dinosaur"
        ])

    # --------------------------------------------------
    # 4. MODE FALLBACK
    # --------------------------------------------------

    mode_queries = {

        "rings": [
            "Earth full disk NASA",
            "Earth from space NASA",
            "Earth atmosphere NASA"
        ],

        "earth": [
            "Earth full disk NASA",
            "Earth clouds NASA",
            "Earth horizon NASA"
        ],

        "moon": [
            "Moon surface NASA",
            "Moon Earth NASA",
            "lunar landscape NASA"
        ],

        "volcano": [
            "volcano eruption photograph",
            "lava eruption photograph"
        ],

        "ocean": [
            "ocean aerial photograph",
            "ocean satellite NASA"
        ],

        "ice": [
            "Antarctica NASA",
            "glacier photograph"
        ],

        "sun": [
            "Sun NASA",
            "solar flare NASA"
        ],

        "mars": [
            "Mars NASA",
            "Mars surface NASA"
        ],

        "city": [
            "city skyline photograph",
            "city aerial photograph"
        ],

        "space": [
            "deep space NASA",
            "Earth from space NASA"
        ]
    }

    queries.extend(
        mode_queries.get(
            mode,
            []
        )
    )

    # --------------------------------------------------
    # REMOVE DUPLICATES
    # --------------------------------------------------

    final_queries = []
    seen = set()

    for q in queries:

        q = re.sub(
            r"\s+",
            " ",
            q
        ).strip()

        if not q:
            continue

        key = q.lower()

        if key not in seen:

            seen.add(key)
            final_queries.append(q)

    print("")
    print("IMAGE SEARCH QUERIES:")

    for q in final_queries[:15]:
        print(" -", q)

    return final_queries[:15]


def add_ring_overlay(
    canvas,
    seed,
    mode
):

    if mode != "rings":
        return canvas

    ring = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    d = ImageDraw.Draw(ring)

    cx = WIDTH // 2
    cy = int(
        HEIGHT * .50
    )

    rx = int(
        WIDTH * .82
    )

    ry = int(
        HEIGHT * .24
    )

    random.seed(seed)

    for i in range(22):

        inset = i * 9

        alpha = max(
            18,
            105 - i * 4
        )

        d.ellipse(
            (
                cx - rx + inset,
                cy - ry + inset // 2,
                cx + rx - inset,
                cy + ry - inset // 2
            ),
            outline=(
                215,
                198,
                168,
                alpha
            ),
            width=random.choice(
                [2, 3, 4]
            )
        )

    return Image.alpha_composite(
        canvas.convert("RGBA"),
        ring.filter(
            ImageFilter.GaussianBlur(.45)
        )
    ).convert("RGB")


def find_best_image(
    topic,
    scene,
    mode,
    used_urls
):

    candidates = []

    queries = visual_queries(
        topic,
        scene,
        mode
    )

    for q in queries:

        print("")
        print(
            "Searching:",
            q
        )

        # NASA
        nasa_results = nasa_search(
            q,
            8
        )

        for item in nasa_results:
            item["_query"] = q

        candidates.extend(
            nasa_results
        )

        # Wikimedia
        wiki_results = wikimedia_search(
            q,
            6
        )

        for item in wiki_results:
            item["_query"] = q

        candidates.extend(
            wiki_results
        )

    # Remove duplicate URLs
    unique = {}
    
    for item in candidates:

        url = item.get(
            "url",
            ""
        )

        if not url:
            continue

        if url in used_urls:
            continue

        if url not in unique:
            unique[url] = item

    candidates = list(
        unique.values()
    )

    print(
        f"Found {len(candidates)} "
        "unique image candidates."
    )

    # IMPORTANT:
    # Score using the ACTUAL search query.
    ranked = sorted(
        candidates,
        key=lambda item:
            score_candidate(
                item,
                item.get(
                    "_query",
                    ""
                ),
                item.get(
                    "source",
                    "Wikimedia Commons"
                )
            ),
        reverse=True
    )

    # Try best candidates
    for rank, item in enumerate(
        ranked[:40],
        start=1
    ):

        print(
            f"IMAGE CANDIDATE {rank}: "
            f"{item.get('title', '')}"
        )

        img = download_image(
            item
        )

        if img is None:
            continue

        print(
            "SELECTED IMAGE:",
            item.get(
                "title",
                ""
            )
        )

        print(
            "SOURCE:",
            item.get(
                "source",
                ""
            )
        )

        print(
            "QUERY:",
            item.get(
                "_query",
                ""
            )
        )

        return img, item

    return None, None

def create_realistic_scene(topic, scene, index, used_urls):
    """
    Create one cinematic 9:16 scene using ONLY a real,
    topic-matched NASA/Wikimedia image.

    IMPORTANT:
    - No generated/emergency background.
    - No random star background.
    - If a suitable real image cannot be found, STOP the video.
    - Every scene must use a different image URL whenever possible.
    """

    # ---------------------------------------------------------
    # Deterministic seed for consistent cinematic movement
    # ---------------------------------------------------------
    seed = int(
        hashlib.sha256(
            f"{topic}:{index}".encode("utf-8")
        ).hexdigest()[:8],
        16
    )

    random.seed(seed)

    # ---------------------------------------------------------
    # Decide visual category
    # ---------------------------------------------------------
    mode = visual_mode(topic, scene)

    print()
    print("=" * 70)
    print(f"SCENE {index + 1}/{SCENES}")
    print(f"TOPIC : {topic}")
    print(f"MODE  : {mode}")
    print(f"VISUAL: {scene.get('visual', '')}")
    print(f"NARR  : {scene.get('narration', '')}")
    print("=" * 70)

    # ---------------------------------------------------------
    # Find REAL topic-matched image
    # ---------------------------------------------------------
    img, source = find_best_image(
        topic,
        scene,
        mode,
        used_urls
    )

    # ---------------------------------------------------------
    # NEVER generate a fake fallback image
    # ---------------------------------------------------------
    if img is None:
        raise RuntimeError(
            f"\n"
            f"SCENE {index + 1}: NO REAL TOPIC-MATCHED IMAGE FOUND.\n"
            f"Topic : {topic}\n"
            f"Visual: {scene.get('visual', '')}\n"
            f"Mode  : {mode}\n"
            f"\n"
            f"Video generation stopped intentionally.\n"
            f"Please improve the image search/query logic instead of "
            f"publishing an unrelated background."
        )

    # ---------------------------------------------------------
    # Validate source
    # ---------------------------------------------------------
    if not source:
        raise RuntimeError(
            f"Scene {index + 1}: image downloaded but source metadata is missing."
        )

    source_url = str(source.get("url", "")).strip()

    if not source_url:
        raise RuntimeError(
            f"Scene {index + 1}: image source URL is missing."
        )

    # Prevent duplicate images between scenes
    if source_url in used_urls:
        raise RuntimeError(
            f"Scene {index + 1}: duplicate image selected:\n"
            f"{source_url}"
        )

    used_urls.add(source_url)

    print()
    print("REAL IMAGE SELECTED")
    print("Source :", source.get("source", "Unknown"))
    print("Title  :", source.get("title", "Unknown"))
    print("URL    :", source_url)
    print("License:", source.get("license", "Unknown"))
    print()

    # ---------------------------------------------------------
    # Validate downloaded image
    # ---------------------------------------------------------
    if img.width < 1000 or img.height < 600:
        raise RuntimeError(
            f"Scene {index + 1}: selected image resolution is too low: "
            f"{img.width}x{img.height}"
        )

    # ---------------------------------------------------------
    # Cinematic focus position
    #
    # Slightly different framing per scene prevents the
    # Shorts video from looking like repeated static photos.
    # ---------------------------------------------------------
    focus_positions = [
        (0.40, 0.48),
        (0.58, 0.50),
        (0.45, 0.42),
        (0.55, 0.58),
        (0.38, 0.52),
        (0.62, 0.46),
        (0.48, 0.55),
        (0.52, 0.48),
    ]

    focus_x, focus_y = focus_positions[
        index % len(focus_positions)
    ]

    # ---------------------------------------------------------
    # Convert real image into 1080x1920 cinematic frame
    # ---------------------------------------------------------
    canvas = fit_crop(
        img,
        focus=(focus_x, focus_y)
    )

    # ---------------------------------------------------------
    # Cinematic visual treatment
    # ---------------------------------------------------------
    canvas = add_ring_overlay(
        canvas,
        seed,
        mode
    )

    canvas = cinematic_grade(
        canvas,
        seed
    )

    canvas = add_atmosphere(
        canvas,
        seed,
        mode
    )

    # ---------------------------------------------------------
    # Dark readability gradients
    #
    # Keeps text readable while preserving most of the
    # original photograph.
    # ---------------------------------------------------------
    grad = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0)
    )

    gd = ImageDraw.Draw(grad)

    # Top gradient
    for i in range(0, 430, 10):
        alpha = int(
            115 * (1 - i / 430)
        )

        gd.rectangle(
            (0, i, WIDTH, i + 10),
            fill=(0, 0, 0, alpha)
        )

    # Bottom gradient
    for i in range(0, 420, 10):
        alpha = int(
            105 * (1 - i / 420)
        )

        y = HEIGHT - i - 10

        gd.rectangle(
            (0, y, WIDTH, y + 10),
            fill=(0, 0, 0, alpha)
        )

    canvas = Image.alpha_composite(
        canvas.convert("RGBA"),
        grad
    ).convert("RGB")

    # ---------------------------------------------------------
    # On-screen text
    # ---------------------------------------------------------
    on_screen = safe_ascii(
        scene.get("on_screen", ""),
        100
    )

    if on_screen:
        font = get_font(
            60,
            bold=True
        )

        lines = wrap_text(
            on_screen,
            font,
            900
        )

        d = ImageDraw.Draw(canvas)

        y = 150

        for line in lines[:3]:

            box = d.textbbox(
                (0, 0),
                line,
                font=font
            )

            text_width = box[2] - box[0]

            x = (
                WIDTH - text_width
            ) // 2

            # Shadow
            d.text(
                (x + 3, y + 3),
                line,
                font=font,
                fill=(0, 0, 0),
                stroke_width=2,
                stroke_fill=(0, 0, 0)
            )

            # Main text
            d.text(
                (x, y),
                line,
                font=font,
                fill=(255, 255, 255),
                stroke_width=2,
                stroke_fill=(0, 0, 0)
            )

            y += 76

    # ---------------------------------------------------------
    # Channel watermark
    # ---------------------------------------------------------
    wm = get_font(
        27,
        bold=True
    )

    ImageDraw.Draw(canvas).text(
        (42, HEIGHT - 72),
        "WHAT IF DAILY",
        font=wm,
        fill=(255, 255, 255),
        stroke_width=1,
        stroke_fill=(0, 0, 0)
    )

    # ---------------------------------------------------------
    # Save final scene frame
    # ---------------------------------------------------------
    path = FRAMES / f"scene_{index:02d}.png"

    canvas.save(
        path,
        format="PNG",
        optimize=True
    )

    print(
        f"Scene {index + 1} frame saved: "
        f"{path}"
    )

    print(
        f"Scene {index + 1} image source: "
        f"{source.get('source', 'Unknown')}"
    )

    return {
        "path": str(path),
        "source": source,
        "mode": mode
    }
def generate_unique_topic():

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing"
        )

    history_file = ROOT / "topic_history.json"

    if history_file.exists():
        try:
            history = json.loads(
                history_file.read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(history, list):
                history = []

        except Exception as e:
            print(
                "Topic history read failed:",
                e
            )
            history = []
    else:
        history = []

    prompt = f"""
Generate ONE completely new and highly interesting "What If" topic
for a YouTube Shorts channel called WHAT IF DAILY.

The topic must be:
- scientifically interesting
- visually spectacular
- easy to understand
- suitable for a 60-second video
- different from all previous topics
- NOT a rewording of an old topic
- NOT the same basic scenario as an old topic

Previous topics:
{json.dumps(history[-200:], ensure_ascii=False)}

Return ONLY the topic.
Start with "What If".
Do not explain anything.
"""

    for attempt in range(1, 6):

        try:

            print("")
            print("======================================")
            print(
                f"TOPIC GENERATION ATTEMPT "
                f"{attempt}/5"
            )
            print("======================================")

            response = GEMINI_CLIENT.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )

            if (
                not response
                or not getattr(
                    response,
                    "text",
                    None
                )
            ):
                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            topic = response.text.strip()

            if topic.startswith("```"):
                topic = re.sub(
                    r"^```(?:json)?",
                    "",
                    topic
                ).strip()

                topic = re.sub(
                    r"```$",
                    "",
                    topic
                ).strip()

            topic = safe_ascii(
                topic,
                180
            ).strip()

            if not topic:
                raise RuntimeError(
                    "Gemini returned an empty topic."
                )

            if not topic.lower().startswith(
                "what if"
            ):
                topic = "What If " + topic

            normalized = re.sub(
                r"[^a-z0-9]+",
                " ",
                topic.lower()
            ).strip()

            duplicate = False

            for old_topic in history:

                old_normalized = re.sub(
                    r"[^a-z0-9]+",
                    " ",
                    str(old_topic).lower()
                ).strip()

                if normalized == old_normalized:
                    duplicate = True
                    break

            if duplicate:

                print(
                    "Duplicate topic detected."
                )

                continue

            history.append(topic)

            history_file.write_text(
                json.dumps(
                    history[-1000:],
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )

            print("")
            print("======================================")
            print("NEW UNIQUE TOPIC")
            print(topic)
            print("======================================")

            return topic

        except Exception as e:

            error_text = str(e)
            error_upper = error_text.upper()

            print("")
            print("TOPIC GENERATION ERROR:")
            print(error_text)

            transient_error = any(
                code in error_upper
                for code in [
                    "500",
                    "INTERNAL",
                    "503",
                    "UNAVAILABLE",
                    "429",
                    "RESOURCE_EXHAUSTED",
                    "502",
                    "504",
                    "OVERLOADED",
                    "HIGH DEMAND",
                    "RATE LIMIT",
                    "TEMPORAR"
                ]
            )

            if not transient_error:
                raise

            if attempt >= 5:
                raise RuntimeError(
                    "Gemini topic generation failed "
                    f"after {attempt} attempts: "
                    f"{error_text}"
                )

            wait_seconds = 10 * (
                2 ** (attempt - 1)
            )

            jitter = random.randint(0, 5)

            total_wait = (
                wait_seconds + jitter
            )

            print(
                "Temporary Gemini error detected."
            )

            print(
                f"Retrying in "
                f"{total_wait} seconds..."
            )

            import time
            time.sleep(total_wait)

    raise RuntimeError(
        "Could not generate a unique topic "
        "after 5 attempts."
        )

def create_story(topic):

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing"
        )

    client = genai.Client(
        api_key=GEMINI_API_KEY
    )

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

    # =========================================================
    # GEMINI RETRY PROTECTION
    # =========================================================
    #
    # Temporary Gemini 503 / 429 / 500 / 502 / 504
    # errors will be retried automatically.
    #
    # Attempts:
    # 1 -> wait about 10 sec if failed
    # 2 -> wait about 20 sec
    # 3 -> wait about 40 sec
    # 4 -> wait about 80 sec
    # 5 -> fail if still unavailable
    #
    # This prevents temporary Gemini high-demand errors
    # from immediately killing the GitHub Actions workflow.
    # =========================================================

    import time

    max_attempts = 5
    response = None

    for attempt in range(
        1,
        max_attempts + 1
    ):

        try:

            print("")
            print(
                "======================================"
            )

            print(
                "GEMINI GENERATION ATTEMPT "
                f"{attempt}/{max_attempts}"
            )

            print(
                "Model:",
                GEMINI_MODEL
            )

            print(
                "Topic:",
                topic
            )

            print(
                "======================================"
            )

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )

            if (
                not response
                or not getattr(
                    response,
                    "text",
                    None
                )
            ):

                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            print(
                "Gemini generation successful."
            )

            break

        except Exception as e:

            error_text = str(e)
            error_upper = error_text.upper()

            print("")
            print(
                "GEMINI ERROR:"
            )
            print(
                error_text
            )

            transient_error = any(
                code in error_upper
                for code in [
                    "503",
                    "UNAVAILABLE",
                    "429",
                    "RESOURCE_EXHAUSTED",
                    "500",
                    "502",
                    "504",
                    "INTERNAL",
                    "HIGH DEMAND",
                    "OVERLOADED",
                    "RATE LIMIT",
                    "TEMPOR"
                ]
            )

            if not transient_error:

                print(
                    "Non-transient Gemini error detected."
                )

                print(
                    "Stopping without retry."
                )

                raise

            if attempt >= max_attempts:

                print("")
                print(
                    "======================================"
                )

                print(
                    "GEMINI FAILED AFTER ALL RETRIES"
                )

                print(
                    "======================================"
                )

                raise RuntimeError(
                    "Gemini failed after "
                    f"{max_attempts} attempts: "
                    f"{error_text}"
                )

            wait_seconds = (
                10
                * (
                    2
                    ** (
                        attempt - 1
                    )
                )
            )

            jitter = random.randint(
                0,
                5
            )

            total_wait = (
                wait_seconds
                + jitter
            )

            print("")
            print(
                "Temporary Gemini error detected."
            )

            print(
                f"Waiting {total_wait} seconds "
                "before retry..."
            )

            time.sleep(
                total_wait
            )

    if response is None:

        raise RuntimeError(
            "Gemini did not return a response."
        )

    # =========================================================
    # PARSE GEMINI RESPONSE
    # =========================================================

    text = response.text.strip()

    if text.startswith("```"):

        text = re.sub(
            r"^```(?:json)?",
            "",
            text
        ).strip()

        text = re.sub(
            r"```$",
            "",
            text
        ).strip()

    try:

        story = json.loads(
            text
        )

    except json.JSONDecodeError as e:

        print("")
        print(
            "======================================"
        )

        print(
            "GEMINI RETURNED INVALID JSON"
        )

        print(
            "======================================"
        )

        print(
            text[:5000]
        )

        raise RuntimeError(
            f"Gemini returned invalid JSON: {e}"
        )

    # =========================================================
    # CLEAN METADATA
    # =========================================================

    story["title"] = safe_ascii(
        story.get(
            "title",
            topic
        ),
        100
    )

    story["description"] = safe_ascii(
        story.get(
            "description",
            ""
        ),
        4500
    )

    story["keywords"] = [
        safe_ascii(
            x,
            60
        )
        for x in story.get(
            "keywords",
            []
        )
    ][:20]

    story["hashtags"] = [
        safe_ascii(
            x,
            40
        )
        for x in story.get(
            "hashtags",
            []
        )
    ][:12]

    # =========================================================
    # VALIDATE EXACTLY 8 SCENES
    # =========================================================

    scenes = story.get(
        "scenes",
        []
    )[:SCENES]

    if len(scenes) != SCENES:

        raise RuntimeError(
            "Gemini returned "
            f"{len(scenes)} scenes; "
            f"expected {SCENES}"
        )

    for s in scenes:

        s["narration"] = safe_ascii(
            s.get(
                "narration",
                ""
            ),
            600
        )

        s["on_screen"] = safe_ascii(
            s.get(
                "on_screen",
                ""
            ),
            100
        )

        s["visual"] = safe_ascii(
            s.get(
                "visual",
                ""
            ),
            500
        )

    story["scenes"] = scenes

    print("")
    print(
        "======================================"
    )

    print(
        "GEMINI STORY READY"
    )

    print(
        "Title:",
        story["title"]
    )

    print(
        "Scenes:",
        len(
            story["scenes"]
        )
    )

    print(
        "======================================"
    )

    return story


def create_voice(text):

    out = AUDIO / "narration.mp3"

    txt = safe_ascii(
        text,
        6000
    )

    # IMPORTANT:
    # negative Edge-TTS rates must be one argument,
    # e.g. --rate=-5%

    run([
        "edge-tts",
        "--voice",
        VOICE,
        f"--rate={TTS_RATE}",
        "--volume=+0%",
        "--pitch=-2Hz",
        "--text",
        txt,
        "--write-media",
        str(out)
    ])

    return out


def create_music():

    out = AUDIO / "music.wav"

    filt = (
        "sine=frequency=55:duration=60,"
        "volume=0.045[a];"

        "sine=frequency=110:duration=60,"
        "volume=0.020[b];"

        "anoisesrc=color=pink:duration=60,"
        "lowpass=f=800,volume=0.006[n];"

        "[a][b]amix=inputs=2:duration=longest[p];"

        "[p][n]amix=inputs=2:"
        "duration=longest,"
        "alimiter=limit=0.75"
    )

    run([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        filt,
        "-t",
        "60",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(out)
    ])

    return out


def create_sound_design():

    out = AUDIO / "sfx.wav"

    # Gentle cinematic pulses at scene boundaries.
    # Fully generated locally.

    sources = [
        "aevalsrc=0:d=60[s]"
    ]

    labels = [
        "s"
    ]

    for i, ms in enumerate(
        range(
            0,
            60000,
            7500
        ),
        start=1
    ):

        freq = (
            70
            + (
                i % 4
            ) * 18
        )

        amp = (
            0.08
            if i < 8
            else 0.12
        )

        label = f"i{i}"

        sources.append(
            "aevalsrc="
            f"{amp}*sin(2*PI*{freq}*t)"
            "*exp(-7*t):d=0.55,"
            f"adelay={ms}|{ms}"
            f"[{label}]"
        )

        labels.append(
            label
        )

    filt = (
        ";".join(sources)
        + ";"
        + "".join(
            f"[{x}]"
            for x in labels
        )
        + f"amix=inputs={len(labels)}:"
        "duration=longest,"
        "alimiter=limit=0.7"
    )

    run([
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        filt,
        "-t",
        "60",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(out)
    ])

    return out


def create_video(scene_info):

    clips = []

    for i, info in enumerate(
        scene_info
    ):

        clip = (
            FRAMES
            / f"clip_{i:02d}.mp4"
        )

        # Each source image is already 1080x1920.
        # zoompan adds motion instead of static posters.

        zoom = (
            "1.0+0.055*on/225"
        )

        if i % 2 == 0:

            xexpr = (
                "iw/2-(iw/zoom/2)"
            )

            yexpr = (
                "ih/2-(ih/zoom/2)"
                "-10*on/225"
            )

        else:

            xexpr = (
                "iw/2-(iw/zoom/2)"
                "+12*on/225"
            )

            yexpr = (
                "ih/2-(ih/zoom/2)"
                "+10*on/225"
            )

        vf = (
            f"zoompan="
            f"z='{zoom}':"
            f"x='{xexpr}':"
            f"y='{yexpr}':"
            "d=225:"
            "s=1080x1920:"
            "fps=30,"
            "format=yuv420p"
        )

        run([
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            info["path"],
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
            "18",
            "-pix_fmt",
            "yuv420p",
            str(clip)
        ])

        clips.append(
            clip
        )

    concat = (
        OUTPUT
        / "concat.txt"
    )

    concat.write_text(
        "\n".join(
            f"file '{p.as_posix()}'"
            for p in clips
        ),
        encoding="utf-8"
    )

    silent = (
        OUTPUT
        / "video_silent.mp4"
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
        "Visual sources used in this video:\n"
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
    used_urls = set()

    for i, scene in enumerate(
        story["scenes"]
    ):

        info = create_realistic_scene(
            topic,
            scene,
            i,
            used_urls
        )

        scene_info.append(
            info
        )

        sources.append(
            info["source"]
        )

        print(
            f"Scene {i+1}: "
            f"{info['mode']} | "
            f"{info['source'].get('source', '')} | "
            f"{info['source'].get('title', '')}"
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

import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "pixazo_test"
OUT.mkdir(parents=True, exist_ok=True)

# Exact Pixazo LTX endpoint/parameter style used by toon_kids_automation.
PIXAZO_KEY = os.getenv("PIXAZO_API_KEY", "").strip()
API_BASE = "https://gateway.pixazo.ai"

if not PIXAZO_KEY:
    raise RuntimeError("PIXAZO_API_KEY is missing")

PROMPT = """
Premium cinematic scientific visualization for a YouTube What-If Short: What If Earth Suddenly Stopped Spinning?
Vertical 9:16, photorealistic CGI, realistic Earth viewed from near-space.
The camera starts in a dramatic high-altitude orbital drone-like wide shot and makes a slow, smooth,
controlled cinematic push toward Earth. Earth is visibly rotating, then the rotation smoothly decelerates
and stops, creating a clear visual cause-and-effect moment. Realistic white cloud systems drift over blue oceans
and detailed continents. Natural atmospheric haze, warm sunlight, subtle rim lighting, realistic shadows,
deep cinematic contrast, polished premium science-documentary look. Stable camera motion, strong depth,
clean composition, highly detailed planet surface, professional YouTube Shorts visual quality.
No text, no letters, no numbers, no subtitles, no logos, no watermark.
""".strip()

NEGATIVE = (
    "boring static shot, frantic motion, rapid cuts, time lapse, speed ramp, camera shake, extreme zoom, "
    "fisheye, blurry, low quality, distorted Earth, warped continents, duplicated clouds, melting planet, "
    "flicker, jitter, unstable colors, cartoon, anime, text, letters, numbers, subtitles, logo, watermark, UI, border"
)


def submit(prompt):
    url = f"{API_BASE}/ltx-video/v1/text-to-video"
    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": PIXAZO_KEY,
    }
    payload = {
        "prompt": prompt,
        "negative": NEGATIVE,
        "aspect": "9:16",
        "num_frames": 121,
        "frame_rate": 24,
        "steps": 8,
        "cfg": 3.0,
    }
    print("🎬 Pixazo LTX What-If test: submitting...")
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"Pixazo HTTP {r.status_code}: {r.text[:2000]}")
    data = r.json()
    request_id = data.get("request_id")
    polling_url = data.get("polling_url")
    if not request_id:
        raise RuntimeError(f"Pixazo did not return request_id: {data}")
    print("request_id:", request_id)
    return request_id, polling_url


def poll(request_id, polling_url=None):
    url = polling_url or f"{API_BASE}/v2/requests/status/{request_id}"
    headers = {"Ocp-Apim-Subscription-Key": PIXAZO_KEY}
    for poll_no in range(1, 301):
        time.sleep(5)
        r = requests.get(url, headers=headers, timeout=45)
        if r.status_code >= 400:
            raise RuntimeError(f"Pixazo status HTTP {r.status_code}: {r.text[:2000]}")
        data = r.json()
        status = str(data.get("status", "")).upper()
        print(f"Status: {status} | {poll_no * 5}s")
        if status == "COMPLETED":
            output = data.get("output") or {}
            media = output.get("media_url")
            if isinstance(media, list) and media:
                return media[0]
            if isinstance(media, str):
                return media
            raise RuntimeError(f"Completed but no media URL: {data}")
        if status in {"FAILED", "ERROR", "CANCELLED"}:
            raise RuntimeError(f"Pixazo generation failed: {data}")
    raise TimeoutError("Pixazo job did not finish within 25 minutes")


def download(url, path):
    print("⬇️ Downloading generated video...")
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    print("Saved:", path)


def main():
    request_id, polling_url = submit(PROMPT)
    media_url = poll(request_id, polling_url)
    final = OUT / "pixazo_ltx_what_if_test.mp4"
    download(media_url, final)
    print(f"\nFINAL_VIDEO={final}")


if __name__ == "__main__":
    main()

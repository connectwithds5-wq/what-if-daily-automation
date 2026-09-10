import os
import time
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "pixazo_test"
OUT.mkdir(parents=True, exist_ok=True)

# EXACT Pixazo model/API used by toon_kids_automation:
# https://github.com/connectwithds5-wq/toon_kids_automation/blob/main/src/pixazo_ltx_10sec_story_av.py
PIXAZO_KEY = os.getenv("PIXAZO_API_KEY", "").strip()
API_BASE = "https://gateway.pixazo.ai"

if not PIXAZO_KEY:
    raise RuntimeError("PIXAZO_API_KEY is missing")

# One What-If test clip. Same LTX endpoint and parameter style as Toon Kids.
PROMPT = """
Premium cinematic scientific visualization for a YouTube Short.
Vertical 9:16. Photorealistic Earth seen from a high aerial / near-space camera.
The camera starts with a dramatic wide aerial view and slowly pushes toward the planet.
White clouds move naturally over blue oceans and continents, with warm sunlight breaking through.
Smooth controlled drone-like camera movement, realistic atmospheric depth, cinematic lighting,
high detail, physically believable motion, strong visual impact, polished professional video.
No text, no letters, no numbers, no subtitles, no logos, no watermark.
""".strip()

NEGATIVE = (
    "boring static shot, frantic motion, rapid cuts, time lapse, speed ramp, camera shake, extreme zoom, "
    "fisheye, blurry, low quality, distorted objects, flicker, jitter, unstable colors, text, letters, "
    "numbers, subtitles, logo, watermark"
)


def submit(prompt):
    # Exact endpoint/headers/parameter names from Toon Kids Pixazo LTX implementation.
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
    print("🎬 Pixazo LTX test: submitting...")
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

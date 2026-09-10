import os
import time
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "pixazo_test"
OUT.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("PIXAZO_API_KEY")
MODEL = os.getenv("PIXAZO_MODEL", "ltx")
DURATION = int(os.getenv("PIXAZO_DURATION", "6"))
RESOLUTION = os.getenv("PIXAZO_RESOLUTION", "720p")
ASPECT_RATIO = os.getenv("PIXAZO_ASPECT_RATIO", "9:16")

if not API_KEY:
    raise RuntimeError("PIXAZO_API_KEY is missing")

PROMPTS = [
    "Cinematic aerial drone view of Earth from the upper atmosphere, clouds moving rapidly over continents, dramatic sunlight, photorealistic scientific visualization, vertical 9:16, smooth camera push-in, no text, no logos",
    "Cinematic close aerial view of a massive tropical storm forming over a deep blue ocean, spiraling clouds and powerful waves, realistic physics, photorealistic, vertical 9:16, slow orbit camera, no text, no logos",
]


def submit(prompt):
    url = f"https://gateway.pixazo.ai/{MODEL}/text-to-video"
    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": API_KEY,
    }
    payload = {
        "prompt": prompt,
        "resolution": RESOLUTION,
        "duration": DURATION,
        "aspect_ratio": ASPECT_RATIO,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    request_id = data.get("request_id")
    if not request_id:
        raise RuntimeError(f"Pixazo did not return request_id: {data}")
    print("Submitted:", request_id)
    return request_id


def poll(request_id):
    url = f"https://gateway.pixazo.ai/v2/requests/status/{request_id}"
    headers = {"Ocp-Apim-Subscription-Key": API_KEY}
    for _ in range(120):
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = str(data.get("status", "")).upper()
        print("Status:", status)
        if status == "COMPLETED":
            output = data.get("output") or {}
            urls = output.get("media_url") or []
            if isinstance(urls, str):
                urls = [urls]
            if not urls:
                raise RuntimeError(f"Completed but no media URL: {data}")
            return urls[0]
        if status in {"FAILED", "ERROR"}:
            raise RuntimeError(f"Pixazo generation failed: {data}")
        time.sleep(5)
    raise TimeoutError("Pixazo job did not finish within 10 minutes")


def download(url, path):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)


def concat(clips):
    concat_file = OUT / "concat.txt"
    concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    final = OUT / "pixazo_test.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(final)
    ], check=True)
    return final


def main():
    clips = []
    for i, prompt in enumerate(PROMPTS, 1):
        request_id = submit(prompt)
        media_url = poll(request_id)
        clip = OUT / f"scene_{i}.mp4"
        download(media_url, clip)
        clips.append(clip)
        print("Saved:", clip)
    final = concat(clips)
    print(f"FINAL_VIDEO={final}")


if __name__ == "__main__":
    main()

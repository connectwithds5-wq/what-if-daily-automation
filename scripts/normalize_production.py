from pathlib import Path
import re
import subprocess

SOURCE = Path("src/what_if_daily_v2.py")


def replace_function(source: str, name: str, replacement: str, next_name: str) -> str:
    start = source.find(f"def {name}(")
    end = source.find(f"\ndef {next_name}", start)
    if start < 0 or end < 0:
        raise RuntimeError(f"Could not locate {name}() boundaries")
    return source[:start] + replacement.rstrip() + "\n\n" + source[end + 1:]


def main() -> None:
    s = SOURCE.read_text(encoding="utf-8")

    mix = '''def mix_audio(video, narration_sfx, music):
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
         "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(VIDEO)])'''
    s = replace_function(s, "mix_audio", mix, "write_metadata")

    # Replace the visual provider with a real multi-provider chain:
    # Gemini native image generation -> Cloudflare FLUX -> Cloudflare SDXL -> visual fallback.
    visual_start = s.find("def cloudflare_image(")
    visual_end = s.find("\ndef overlay_frame", visual_start)
    if visual_start < 0 or visual_end < 0:
        raise RuntimeError("Could not locate visual generation function boundaries")

    visual_block = r'''def visual_is_valid(path):
    try:
        with Image.open(path) as im:
            im.verify()
        return path.exists() and path.stat().st_size >= 50000
    except Exception as exc:
        print(f"Visual validation failed for {path}: {exc}")
        return False


def gemini_image(prompt, output_path):
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
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    payload = {
        "model": model,
        "input": request_prompt,
        "response_format": {"type": "image", "aspect_ratio": "9:16", "image_size": "1K"},
    }
    for attempt in range(2):
        try:
            print(f"Gemini image generation: {model} | attempt {attempt + 1}/2")
            response = requests.post(url, headers=headers, json=payload, timeout=180)
            if response.status_code in (429, 500, 502, 503, 504):
                print(f"Gemini image temporary error {response.status_code}; retry/fallback.")
                if attempt == 0:
                    time.sleep(2)
                    continue
                return False
            response.raise_for_status()
            data = response.json()
            image_b64 = None
            output_image = data.get("output_image")
            if isinstance(output_image, dict):
                image_b64 = output_image.get("data")
            if not image_b64:
                for step in data.get("steps", []):
                    if step.get("type") == "model_output":
                        for block in step.get("content", []):
                            if block.get("type") == "image" and block.get("data"):
                                image_b64 = block["data"]
                                break
                    if image_b64:
                        break
            if not image_b64:
                raise RuntimeError(f"Gemini image response contained no image: {str(data)[:1000]}")
            if image_b64.startswith("data:image"):
                image_b64 = image_b64.split(",", 1)[1]
            output_path.write_bytes(base64.b64decode(image_b64))
            if visual_is_valid(output_path):
                print(f"Gemini image saved: {output_path}")
                return True
        except Exception as exc:
            print(f"Gemini image error: {exc}")
            if attempt == 0:
                time.sleep(2)
    return False


def cloudflare_image(prompt, output_path):
    if not (VISUALS_ENABLED and CF_TOKEN and CF_ACCOUNT):
        return False
    request_prompt = f"""Cinematic scientific visualization for a premium YouTube Shorts video, vertical 9:16.
Photorealistic, dramatic but scientifically grounded, realistic scale, strong depth, clear foreground/midground/background, one obvious visual event.
No text, letters, numbers, logos, labels, UI, watermark, captions or infographic elements. Original visual only.
Never return an abstract geometric background. Show concrete real-world objects and environments.
SCENE: {prompt}""".strip()[:3500]
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}

    def request_model(model, label, steps=4):
        url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{model}"
        payload = {"prompt": request_prompt, "steps": steps}
        try:
            print(f"{label} visual generation: {model}")
            response = requests.post(url, headers=headers, json=payload, timeout=180)
            if response.status_code in (429, 500, 502, 503, 504):
                print(f"{label} temporary error {response.status_code}; switching immediately.")
                return False
            response.raise_for_status()
            data = response.json()
            result = data.get("result", data)
            image_b64 = result.get("image") if isinstance(result, dict) else None
            if not image_b64 and isinstance(result, dict):
                images = result.get("images")
                if isinstance(images, list) and images:
                    image_b64 = images[0]
            if not image_b64:
                raise RuntimeError(f"{label} returned no image: {str(data)[:800]}")
            if image_b64.startswith("data:image"):
                image_b64 = image_b64.split(",", 1)[1]
            output_path.write_bytes(base64.b64decode(image_b64))
            if visual_is_valid(output_path):
                print(f"{label} visual saved: {output_path}")
                return True
        except Exception as exc:
            print(f"{label} visual error: {exc}")
        return False

    if request_model(CF_MODEL, "Cloudflare primary", steps=4):
        return True
    if CF_FALLBACK_MODEL and CF_FALLBACK_MODEL != CF_MODEL:
        if request_model(CF_FALLBACK_MODEL, "Cloudflare fallback", steps=4):
            return True
    return False


def write_visual_qc(index, provider):
    manifest = OUTPUT / "visual_qc.json"
    data = []
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            data = []
    data = [x for x in data if x.get("scene") != index + 1]
    data.append({"scene": index + 1, "provider": provider, "generated": provider != "fallback"})
    data.sort(key=lambda x: x["scene"])
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


def make_base_visual(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.png"

    if gemini_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, "gemini")
            return path
        except Exception as exc:
            print("Gemini visual decode/resize failed:", exc)

    if cloudflare_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, "cloudflare")
            return path
        except Exception as exc:
            print("Cloudflare visual decode/resize failed:", exc)

    print(f"All image providers failed for scene {index + 1}; using cinematic fallback.")
    seed = int(hashlib.sha256(f"{topic}:{index}".encode()).hexdigest()[:8], 16)
    random.seed(seed)
    base = Image.new("RGB", (WIDTH, HEIGHT), (6, 8, 16))
    draw = ImageDraw.Draw(base)
    cx, cy = random.randint(150, 930), random.randint(450, 1450)
    for radius in (600, 450, 300, 180):
        draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=(35, 38, 55), width=3)
    base.save(path)
    write_visual_qc(index, "fallback")
    return path
'''
    s = s[:visual_start] + visual_block + s[visual_end + 1:]

    # Ensure the fast hook timing and transition SFX timing remain fixed.
    s = s.replace(
        'filters.append("[2:a]volume=1.0[t]")',
        'filters.append("[2:a]adelay=6950|6950,volume=1.0[t]")',
        1,
    )
    s = s.replace(
        'overlay_frame(frame, scene["on_screen"], scene["narration"], index, (fi + 1) / 18)',
        'overlay_frame(frame, scene["on_screen"], scene["narration"], index, min(1.0, (fi + 1) / 5.0))',
        1,
    )

    validation = re.compile(
        r'    total_words = sum\(len\(s\["narration"\]\.split\(\)\) for s in scenes\)\n'
        r'    if not 104 <= total_words <= 116:\n'
        r'        raise RuntimeError\(f"Narration word count \{total_words\}; expected 104-116"\)'
    )
    normalized_validation = '''    total_words = sum(len(s["narration"].split()) for s in scenes)
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
        raise RuntimeError(f"Narration word count {total_words}; expected 104-116")'''
    s, count = validation.subn(normalized_validation, s, count=1)
    if count != 1:
        raise RuntimeError("Could not locate narration word-count validation")

    if "def production_qc(" not in s:
        qc = '''def production_qc(story):
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
    if generated < 6:
        raise RuntimeError(f"QC failed: only {generated}/{SCENES} scenes have real generated visuals")
    print(f"PRODUCTION QC PASSED: {WIDTH}x{HEIGHT}, {duration:.2f}s, audio {audio_duration:.2f}s, {words} words, visuals {generated}/{SCENES}")

'''
        s = s.replace("\ndef main():\n", "\n" + qc + "def main():\n", 1)

    upload_pattern = "    write_metadata(story)\n    upload_youtube(story)"
    if upload_pattern in s:
        s = s.replace(upload_pattern, "    write_metadata(story)\n    production_qc(story)\n    upload_youtube(story)", 1)
    elif "    production_qc(story)\n    upload_youtube(story)" not in s:
        raise RuntimeError("Could not locate YouTube upload call")

    SOURCE.write_text(s, encoding="utf-8")
    subprocess.run(["python", "-m", "py_compile", str(SOURCE)], check=True)
    print("Production source normalized, Gemini image chain installed, and py_compile passed.")


if __name__ == "__main__":
    main()

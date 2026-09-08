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


def patch_narration_validation(s: str) -> str:
    start = s.find('    total_words = sum(len(s["narration"].split()) for s in scenes)')
    if start < 0:
        raise RuntimeError("Could not locate narration word-count validation")
    end = s.find("    return data", start)
    if end < 0:
        raise RuntimeError("Could not locate end of narration validation")
    block = '''    total_words = sum(len(s["narration"].split()) for s in scenes)
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
'''
    return s[:start] + block + s[end:]


MIX = '''def mix_audio(video, narration_sfx, music):
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

VISUALS = r'''def visual_is_valid(path):
    try:
        with Image.open(path) as im:
            im.verify()
        return path.exists() and path.stat().st_size >= 30000
    except Exception as exc:
        print(f"Visual validation failed for {path}: {exc}")
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


def gemini_image(prompt, output_path):
    if not (VISUALS_ENABLED and GEMINI_API_KEY):
        return False
    models = []
    for env_name, default in (
        ("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"),
        ("GEMINI_IMAGE_FALLBACK_MODEL", "gemini-3-pro-image"),
        ("GEMINI_IMAGE_FALLBACK_2_MODEL", "gemini-2.5-flash-image"),
    ):
        model = os.getenv(env_name, default).strip()
        if model and model not in models:
            models.append(model)

    request_prompt = f"""Create a premium cinematic scientific visualization for a YouTube Shorts video.
Vertical 9:16 composition. Photorealistic, dramatic, physically believable, realistic lighting, strong depth, clear foreground, midground and background, and one unmistakable visual event.
Show EXACTLY the phenomenon described below. Make the transformation or consequence visually obvious.
Use concrete real-world environments, objects, materials, atmosphere, scale cues and motion.
Do NOT create a generic abstract background, gradient, circles, geometric pattern, poster, infographic or text card.
No text, letters, numbers, logos, labels, UI, watermark, captions, diagrams or infographic elements.
The image must look like a frame from a high-budget science documentary or cinematic film.
SCENE: {prompt}""".strip()[:12000]

    for model in models:
        for attempt in range(2):
            try:
                print(f"Gemini image generation: {model} | attempt {attempt + 1}/2")
                kwargs = {
                    "model": model,
                    "input": request_prompt,
                    "response_format": {
                        "type": "image",
                        "aspect_ratio": "9:16",
                        "image_size": "1K",
                    },
                }
                if model == "gemini-3.1-flash-image":
                    kwargs["generation_config"] = {"thinking_level": "high"}
                interaction = CLIENT.interactions.create(**kwargs)

                image_b64 = None
                output_image = getattr(interaction, "output_image", None)
                if output_image is not None:
                    image_b64 = getattr(output_image, "data", None)
                if not image_b64:
                    for step in getattr(interaction, "steps", []) or []:
                        if getattr(step, "type", None) != "model_output":
                            continue
                        for block in getattr(step, "content", []) or []:
                            if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                                image_b64 = block.data
                                break
                        if image_b64:
                            break
                if not image_b64:
                    raise RuntimeError("Gemini returned no image output")
                output_path.write_bytes(base64.b64decode(image_b64))
                if visual_is_valid(output_path):
                    print(f"Gemini image saved: {output_path} using {model}")
                    return model
                raise RuntimeError("Gemini image failed local validation")
            except Exception as exc:
                print(f"Gemini image error ({model}, attempt {attempt + 1}): {exc}")
                if attempt == 0:
                    time.sleep(2)
    return False


def make_base_visual(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.png"
    provider = gemini_image(scene["visual_prompt"], path)
    if provider:
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, provider)
            print(f"Gemini visual ready for scene {index + 1}: {provider}")
            return path
        except Exception as exc:
            print("Gemini visual decode/resize failed:", exc)

    print(f"All Gemini image models failed for scene {index + 1}; using cinematic fallback.")
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

QC = r'''def production_qc(story):
    if not VIDEO.exists() or VIDEO.stat().st_size < 100000:
        raise RuntimeError("QC failed: final MP4 missing or suspiciously small")
    if not METADATA.exists() or METADATA.stat().st_size == 0:
        raise RuntimeError("QC failed: metadata.json missing or empty")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(VIDEO)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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
        raise RuntimeError(f"QC failed: final duration {duration:.2f}s")
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
        raise RuntimeError(f"QC failed: Gemini generated visuals only {generated}/{SCENES}; minimum is 6/8")
    print(f"PRODUCTION QC PASSED: {WIDTH}x{HEIGHT}, {duration:.2f}s, 44.1kHz, {words} words, Gemini visuals {generated}/{SCENES}")
'''


def main() -> None:
    s = SOURCE.read_text(encoding="utf-8")
    # Strip all legacy Cloudflare environment constants/import references from source.
    s = re.sub(r"^.*CF_TOKEN.*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"^.*CF_ACCOUNT.*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"^.*CF_MODEL.*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"^.*CF_FALLBACK_MODEL.*\n", "", s, flags=re.MULTILINE)

    s = replace_function(s, "mix_audio", MIX, "write_metadata")

    # Replace the entire legacy visual-provider region with Gemini-only generation.
    visual_start = s.find("def gemini_image(")
    if visual_start < 0:
        visual_start = s.find("def cloudflare_image(")
    visual_end = s.find("\ndef overlay_frame", visual_start)
    if visual_start < 0 or visual_end < 0:
        raise RuntimeError("Could not locate visual generation boundaries")
    s = s[:visual_start] + VISUALS.rstrip() + "\n\n" + s[visual_end + 1:]

    # Ensure production QC runs immediately before YouTube upload.
    qc_start = s.find("def production_qc(")
    if qc_start >= 0:
        qc_end = s.find("\ndef main", qc_start)
        s = s[:qc_start] + s[qc_end + 1:]
    marker = "def main():"
    s = s.replace(marker, QC + "\n\n" + marker, 1)
    s = re.sub(r"\n\s*production_qc\(story\)\n", "\n", s)
    s = s.replace("    write_metadata(story)\n    upload_youtube(story)", "    write_metadata(story)\n    production_qc(story)\n    upload_youtube(story)", 1)

    s = s.replace('filters.append("[2:a]volume=1.0[t]")', 'filters.append("[2:a]adelay=6950|6950,volume=1.0[t]")', 1)
    s = s.replace('overlay_frame(frame, scene["on_screen"], scene["narration"], index, (fi + 1) / 18)', 'overlay_frame(frame, scene["on_screen"], scene["narration"], index, min(1.0, (fi + 1) / 5.0))', 1)
    s = patch_narration_validation(s)

    SOURCE.write_text(s, encoding="utf-8")
    subprocess.run(["python", "-m", "py_compile", str(SOURCE)], check=True)
    print("Production source normalized: Gemini-only image generation, Gemini fallbacks, audio fix and pre-upload QC installed.")


if __name__ == "__main__":
    main()

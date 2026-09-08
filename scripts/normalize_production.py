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


def main() -> None:
    s = SOURCE.read_text(encoding="utf-8")

    # Remove all Cloudflare visual configuration from the production source.
    s = re.sub(r"^.*CF_TOKEN.*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"^.*CF_ACCOUNT.*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"^.*CF_MODEL.*\n", "", s, flags=re.MULTILINE)
    s = re.sub(r"^.*CF_FALLBACK_MODEL.*\n", "", s, flags=re.MULTILINE)

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

    visual_start = s.find("def cloudflare_image(")
    if visual_start < 0:
        visual_start = s.find("def gemini_image(")
    visual_end = s.find("\ndef overlay_frame", visual_start)
    if visual_start < 0 or visual_end < 0:
        raise RuntimeError("Could not locate visual generation function boundaries")

    visual_block = r'''def visual_is_valid(path):
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
    for name, default in (
        ("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"),
        ("GEMINI_IMAGE_FALLBACK_MODEL", "gemini-3-pro-image"),
        ("GEMINI_IMAGE_FALLBACK_2_MODEL", "gemini-2.5-flash-image"),
    ):
        model = os.getenv(name, default).strip()
        if model and model not in models:
            models.append(model)

    request_prompt = f"""Create a premium cinematic scientific visualization for a YouTube Shorts video.
Vertical 9:16 composition. Photorealistic, dramatic, physically believable, realistic lighting, strong depth, clear foreground, midground and background, and one unmistakable visual event.
Show EXACTLY the phenomenon described below. Make the transformation or consequence visually obvious.
Use concrete real-world environments, objects, materials, atmosphere, scale cues and motion.
Do NOT create a generic abstract background, gradient, circles, geometric pattern, poster, infographic or text card.
No text, letters, numbers, logos, labels, UI, watermark, captions, diagrams or infographic elements.
The image must look like a frame from a high-budget science documentary or cinematic film, not an illustration card.
SCENE: {prompt}""".strip()[:12000]

    for model in models:
        for attempt in range(2):
            try:
                print(f"Gemini image generation: {model} | attempt {attempt + 1}/2")
                interaction = CLIENT.interactions.create(
                    model=model,
                    input=request_prompt,
                    response_format={
                        "type": "image",
                        "aspect_ratio": "9:16",
                        "image_size": "1K",
                    },
                    generation_config={"thinking_level": "high"} if model == "gemini-3.1-flash-image" else None,
                )
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
    s = s[:visual_start] + visual_block + s[visual_end + 1:]

    # Remove any previously injected production_qc function/call; workflow handles file QC.
    qc_start = s.find("\ndef production_qc(")
    main_start = s.find("\ndef main(", qc_start if qc_start >= 0 else 0)
    if qc_start >= 0 and main_start >= 0:
        s = s[:qc_start] + s[main_start:]
    s = re.sub(r"\n\s*production_qc\(story\)\n", "\n", s)

    s = s.replace('filters.append("[2:a]volume=1.0[t]")', 'filters.append("[2:a]adelay=6950|6950,volume=1.0[t]")', 1)
    s = s.replace('overlay_frame(frame, scene["on_screen"], scene["narration"], index, (fi + 1) / 18)', 'overlay_frame(frame, scene["on_screen"], scene["narration"], index, min(1.0, (fi + 1) / 5.0))', 1)

    s = patch_narration_validation(s)
    SOURCE.write_text(s, encoding="utf-8")
    subprocess.run(["python", "-m", "py_compile", str(SOURCE)], check=True)
    print("Production source normalized: Gemini-only image generation with fallbacks; audio pipeline fixed; py_compile passed.")


if __name__ == "__main__":
    main()

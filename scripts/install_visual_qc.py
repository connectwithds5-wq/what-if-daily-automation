from pathlib import Path

SOURCE = Path("src/what_if_daily_v2.py")

WRITE_VISUAL_QC = r'''def write_visual_qc(index, provider):
    manifest = OUTPUT / "visual_qc.json"
    data = []
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            data = []
    data = [x for x in data if x.get("scene") != index + 1]
    data.append({
        "scene": index + 1,
        "provider": provider,
        "generated": provider != "fallback",
    })
    data.sort(key=lambda x: x["scene"])
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")

'''

QC = '''def production_qc(story):
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

GEMINI_IMAGE = r'''def gemini_image(prompt, output_path):
    # Gemini native image generation is paid-only through the Gemini Developer API.
    # Keep disabled by default so the automation never creates unexpected image charges.
    if os.getenv("GEMINI_IMAGE_ENABLED", "false").lower() != "true":
        return False
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
    try:
        from google.genai import types
        response = CLIENT.models.generate_content(
            model=model,
            contents=request_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio="9:16", image_size="1K"),
            ),
        )
        for candidate in getattr(response, "candidates", []) or []:
            for part in getattr(getattr(candidate, "content", None), "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    output_path.write_bytes(base64.b64decode(data) if isinstance(data, str) else data)
                    if visual_is_valid(output_path):
                        print(f"Gemini image saved: {output_path}")
                        return True
    except Exception as exc:
        print(f"Gemini image unavailable: {exc}")
    return False
'''

CLOUDFLARE_IMAGE = r'''def cloudflare_image(prompt, output_path):
    if not (VISUALS_ENABLED and CF_TOKEN and CF_ACCOUNT):
        return False

    request_prompt = f"""Cinematic scientific visualization for a premium YouTube Shorts video, vertical 9:16.
Photorealistic, dramatic, scientifically grounded, realistic scale, strong depth, clear foreground/midground/background, one obvious real-world visual event.
Show concrete environments, objects, atmosphere, materials and motion relevant to the scene.
No text, letters, numbers, logos, labels, UI, watermark, captions, infographic panels, diagrams or abstract geometric backgrounds.
SCENE: {prompt}""".strip()[:2048]
    negative_prompt = "text, letters, numbers, logo, watermark, UI, infographic, poster, diagram, abstract circles, geometric background, blank dark background"
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}

    models = [
        (CF_MODEL, "Cloudflare FLUX", {"prompt": request_prompt, "steps": 4}),
        (os.getenv("CLOUDFLARE_IMAGE_FALLBACK_MODEL", "@cf/bytedance/stable-diffusion-xl-lightning"), "Cloudflare SDXL Lightning", {
            "prompt": request_prompt, "negative_prompt": negative_prompt, "width": 576, "height": 1024, "num_steps": 4, "guidance": 7.0,
        }),
        (os.getenv("CLOUDFLARE_IMAGE_FALLBACK_2_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0"), "Cloudflare SDXL Base", {
            "prompt": request_prompt, "negative_prompt": negative_prompt, "width": 576, "height": 1024, "num_steps": 8, "guidance": 7.5,
        }),
        (os.getenv("CLOUDFLARE_IMAGE_FALLBACK_3_MODEL", "@cf/lykon/dreamshaper-8-lcm"), "Cloudflare DreamShaper", {
            "prompt": request_prompt, "negative_prompt": negative_prompt, "width": 576, "height": 1024, "num_steps": 4, "guidance": 6.5,
        }),
    ]

    seen = set()
    for model, label, payload in models:
        if not model or model in seen:
            continue
        seen.add(model)
        for attempt, delay in enumerate((0, 8, 20), start=1):
            if delay:
                time.sleep(delay)
            try:
                print(f"{label}: {model} | attempt {attempt}/3")
                response = requests.post(
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{model}",
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
                if response.status_code == 429:
                    try:
                        detail = response.json().get("errors", [{}])[0].get("message", "429")
                    except Exception:
                        detail = "429"
                    print(f"{label} HTTP 429: {detail}")
                    if "daily free allocation" in detail.lower() or "account limited" in detail.lower():
                        print("Cloudflare daily free allocation is exhausted; stop image retries for this model.")
                        break
                    continue
                if response.status_code in (500, 502, 503, 504):
                    print(f"{label} temporary server error {response.status_code}")
                    continue
                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if content_type.startswith("image/"):
                    output_path.write_bytes(response.content)
                else:
                    data = response.json()
                    result = data.get("result", data)
                    image_b64 = result.get("image") if isinstance(result, dict) else None
                    if not image_b64 and isinstance(result, dict):
                        image_b64 = result.get("image_b64")
                    if not image_b64 and isinstance(result, dict):
                        images = result.get("images")
                        if isinstance(images, list) and images:
                            image_b64 = images[0]
                    if not image_b64:
                        raise RuntimeError(f"{label} returned no image: {str(data)[:700]}")
                    if image_b64.startswith("data:image"):
                        image_b64 = image_b64.split(",", 1)[1]
                    output_path.write_bytes(base64.b64decode(image_b64))

                if visual_is_valid(output_path):
                    print(f"{label} visual saved: {output_path}")
                    return True
            except Exception as exc:
                print(f"{label} error: {exc}")
    return False
'''

OVERLAY = r'''def overlay_frame(background, on_screen, narration, index, progress):
    image = background.copy()
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(0, 430):
        alpha = int(190 * (1 - y / 430))
        draw.rectangle((0, y, WIDTH, y + 1), fill=(0, 0, 0, alpha))
    for y in range(1450, HEIGHT):
        alpha = int(210 * ((y - 1450) / (HEIGHT - 1450)))
        draw.rectangle((0, y, WIDTH, y + 1), fill=(0, 0, 0, alpha))
    small = font(30, True)
    main = font(94, True)
    sub = font(38)
    draw.text((60, 60), "WHAT IF DAILY", font=small, fill=(255, 255, 255, 235))
    on = safe_ascii(on_screen, 55).upper()
    typed = on[:max(1, int(len(on) * progress))]
    lines = wrap(typed, main, 900)[:3] or [""]
    y = 560 - (len(lines) * 115) // 2
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=main, stroke_width=3)
        x = (WIDTH - (bb[2] - bb[0])) // 2
        draw.text((x + 5, y + 7), line, font=main, fill=(0, 0, 0, 220), stroke_width=7, stroke_fill=(0, 0, 0, 230))
        draw.text((x, y), line, font=main, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += 115
    if progress > 0.45:
        narration_lines = wrap(narration, sub, 900)[:3]
        block_height = len(narration_lines) * 52
        narration_y = 1020 - block_height // 2
        for j, line in enumerate(narration_lines):
            bb = draw.textbbox((0, 0), line, font=sub)
            x = (WIDTH - (bb[2] - bb[0])) // 2
            draw.text((x, narration_y + j * 52), line, font=sub, fill=(245, 245, 245, 240), stroke_width=2, stroke_fill=(0, 0, 0, 200))
    draw.rectangle((60, 1815, WIDTH - 60, 1822), fill=(90, 90, 90, 170))
    draw.rectangle((60, 1815, 60 + int((WIDTH - 120) * ((index + progress) / SCENES)), 1822), fill=(255, 255, 255, 240))
    return image
'''

s = SOURCE.read_text(encoding="utf-8")

# Ensure the manifest writer exists before make_base_visual.
if "def write_visual_qc(" not in s:
    marker = "def make_base_visual(topic, scene, index):"
    if marker not in s:
        raise RuntimeError("Could not locate make_base_visual()")
    s = s.replace(marker, WRITE_VISUAL_QC + marker, 1)

for name, replacement, next_name in [
    ("gemini_image", GEMINI_IMAGE, "cloudflare_image"),
    ("cloudflare_image", CLOUDFLARE_IMAGE, "overlay_frame"),
    ("overlay_frame", OVERLAY, "create_typography_scene"),
]:
    start = s.find(f"def {name}(")
    end = s.find(f"\ndef {next_name}", start)
    if start < 0 or end < 0:
        raise RuntimeError(f"Could not locate {name}() boundaries")
    s = s[:start] + replacement.rstrip() + "\n\n" + s[end + 1:]

start = s.find("def production_qc(")
if start >= 0:
    end = s.find("\ndef main", start)
    if end < 0:
        raise RuntimeError("Could not locate main() after production_qc()")
    s = s[:start] + QC + s[end + 1:]
else:
    marker = "def main():"
    if marker not in s:
        raise RuntimeError("Could not locate main()")
    s = s.replace(marker, QC + marker, 1)

if "    production_qc(story)\n    upload_youtube(story)" not in s:
    if "    write_metadata(story)\n    upload_youtube(story)" in s:
        s = s.replace("    write_metadata(story)\n    upload_youtube(story)", "    write_metadata(story)\n    production_qc(story)\n    upload_youtube(story)", 1)

SOURCE.write_text(s, encoding="utf-8")
print("Installed visual QC writer and production image pipeline.")

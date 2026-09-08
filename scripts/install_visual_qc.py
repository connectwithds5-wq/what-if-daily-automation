from pathlib import Path
import re

SOURCE = Path("src/what_if_daily_v2.py")

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
    if not (VISUALS_ENABLED and GEMINI_API_KEY):
        return False
    models = []
    for name in [
        os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"),
        os.getenv("GEMINI_IMAGE_FALLBACK_MODEL", "gemini-3.1-flash-lite-image"),
    ]:
        if name and name not in models:
            models.append(name)
    request_prompt = f"""Create a premium cinematic scientific visualization for a YouTube Shorts video.
Vertical 9:16 composition. Photorealistic, dramatic, physically believable, strong depth, clear foreground/midground/background, realistic lighting, one unmistakable visual event.
Show the exact phenomenon described below. Make the transformation or consequence visually obvious without any text.
No text, letters, numbers, logos, labels, UI, watermark, captions, infographic panels, diagrams, or poster design.
Do not make a generic abstract background. Do not make a simple gradient or geometric pattern.
Use concrete environments, objects, atmosphere, scale cues and realistic materials.
SCENE: {prompt}""".strip()[:12000]
    try:
        from google.genai import types
    except Exception as exc:
        print(f"Gemini image SDK types unavailable: {exc}")
        return False

    for model in models:
        for attempt in range(2):
            try:
                print(f"Gemini image generation: {model} | attempt {attempt + 1}/2")
                response = CLIENT.models.generate_content(
                    model=model,
                    contents=request_prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(
                            aspect_ratio="9:16",
                            image_size="1K",
                        ),
                    ),
                )
                image_bytes = None
                for candidate in getattr(response, "candidates", []) or []:
                    content = getattr(candidate, "content", None)
                    for part in getattr(content, "parts", []) or []:
                        inline = getattr(part, "inline_data", None)
                        data = getattr(inline, "data", None) if inline else None
                        if data:
                            image_bytes = data
                            break
                    if image_bytes:
                        break
                if not image_bytes:
                    raise RuntimeError("Gemini image response contained no inline image")
                if isinstance(image_bytes, str):
                    image_bytes = base64.b64decode(image_bytes.split(",", 1)[-1])
                output_path.write_bytes(image_bytes)
                if visual_is_valid(output_path):
                    print(f"Gemini image saved: {output_path}")
                    return True
            except Exception as exc:
                print(f"Gemini image error ({model}): {exc}")
                upper = str(exc).upper()
                if "429" in upper or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper or "503" in upper or "UNAVAILABLE" in upper:
                    if attempt == 0:
                        time.sleep(5)
                        continue
                    print(f"Switching away from Gemini image model {model}.")
                    break
                break
    return False
'''

s = SOURCE.read_text(encoding="utf-8")

# Keep the existing production audio/visual normalizer changes, but replace the Gemini image implementation with the supported SDK call.
start = s.find("def gemini_image(")
end = s.find("\ndef cloudflare_image", start)
if start < 0 or end < 0:
    raise RuntimeError("Could not locate gemini_image() boundaries")
s = s[:start] + GEMINI_IMAGE.rstrip() + "\n\n" + s[end + 1:]

# Add a second image-model fallback before Cloudflare by retaining the model loop above.
# Ensure Cloudflare has a short retry window for transient 429s instead of immediately exhausting both providers.
s = s.replace(
    'if response.status_code in (429, 500, 502, 503, 504):\n                print(f"{label} temporary error {response.status_code}; switching immediately.")\n                return False',
    'if response.status_code in (429, 500, 502, 503, 504):\n                print(f"{label} temporary error {response.status_code}; retrying after backoff.")\n                for delay in (8, 16):\n                    time.sleep(delay)\n                    retry = requests.post(url, headers=headers, json=payload, timeout=180)\n                    if retry.status_code < 400:\n                        response = retry\n                        break\n                    response = retry\n                if response.status_code >= 400:\n                    print(f"{label} remained unavailable after backoff: {response.status_code}")\n                    return False',
    1,
)

# Replace production QC installer and upload gate.
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

upload = "    write_metadata(story)\n    upload_youtube(story)"
if upload in s:
    s = s.replace(upload, "    write_metadata(story)\n    production_qc(story)\n    upload_youtube(story)", 1)
elif "    production_qc(story)\n    upload_youtube(story)" not in s:
    raise RuntimeError("Could not locate upload_youtube() call")

SOURCE.write_text(s, encoding="utf-8")
print("Gemini image API and visual production QC gate installed.")

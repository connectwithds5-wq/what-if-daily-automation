from pathlib import Path

SOURCE = Path("src/what_if_daily_v2.py")

MAKE_BASE_VISUAL = r'''def make_base_visual(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.png"

    # Primary: Gemini native image generation when explicitly enabled.
    if "gemini_image" in globals() and gemini_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, "gemini")
            return path
        except Exception as exc:
            print(f"Gemini visual decode/resize failed: {exc}")

    # Free Cloudflare chain: FLUX -> SDXL Lightning -> SDXL Base -> DreamShaper.
    if cloudflare_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, "cloudflare")
            return path
        except Exception as exc:
            print(f"Cloudflare visual decode/resize failed: {exc}")

    # Last-resort visual only; it is explicitly marked as fallback in visual_qc.json.
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

s = SOURCE.read_text(encoding="utf-8")
if "def make_base_visual(" not in s:
    marker = "\ndef overlay_frame"
    pos = s.find(marker)
    if pos < 0:
        raise RuntimeError("Could not locate overlay_frame() insertion point")
    s = s[:pos] + "\n" + MAKE_BASE_VISUAL.rstrip() + "\n" + s[pos:]
    SOURCE.write_text(s, encoding="utf-8")
    print("Inserted missing make_base_visual() entry point.")
else:
    print("make_base_visual() already present; no change needed.")

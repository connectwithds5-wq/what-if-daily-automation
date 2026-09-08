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
    data.append({"scene": index + 1, "provider": provider, "generated": provider != "fallback"})
    data.sort(key=lambda x: x["scene"])
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")

'''

MAKE_BASE_VISUAL = r'''def make_base_visual(topic, scene, index):
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source.png"

    if "gemini_image" in globals() and gemini_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, "gemini")
            return path
        except Exception as exc:
            print(f"Gemini visual decode/resize failed: {exc}")

    if cloudflare_image(scene["visual_prompt"], path):
        try:
            image = Image.open(path).convert("RGB")
            image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            image.save(path)
            write_visual_qc(index, "cloudflare")
            return path
        except Exception as exc:
            print(f"Cloudflare visual decode/resize failed: {exc}")

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

# Ensure the visual QC manifest writer exists.
if "def write_visual_qc(" not in s:
    marker = "def make_base_visual(topic, scene, index):"
    if marker not in s:
        marker = "\ndef overlay_frame"
        pos = s.find(marker)
        if pos < 0:
            raise RuntimeError("Could not locate visual insertion point")
        s = s[:pos] + "\n" + WRITE_VISUAL_QC.rstrip() + "\n" + s[pos:]
    else:
        s = s.replace(marker, WRITE_VISUAL_QC + marker, 1)

# Replace the visual entry point with the hardened provider chain.
start = s.find("def make_base_visual(")
end = s.find("\ndef overlay_frame", start)
if start < 0 or end < 0:
    raise RuntimeError("Could not locate make_base_visual() boundaries")
s = s[:start] + MAKE_BASE_VISUAL.rstrip() + "\n\n" + s[end + 1:]

# Protect the real 6/8 visual QC gate from the legacy fallback-upload patch in the workflow.
legacy = '    if generated < 6:\n        raise RuntimeError(f"QC failed: only {generated}/{SCENES} scenes have real generated visuals")'
protected = '    minimum_generated_visuals = 6\n    if generated < minimum_generated_visuals:\n        raise RuntimeError(f"QC failed: only {generated}/{SCENES} scenes have real generated visuals")'
if legacy in s:
    s = s.replace(legacy, protected, 1)

SOURCE.write_text(s, encoding="utf-8")
print("Visual pipeline hardened: manifest writer present, fallback chain installed, 6/8 visual QC gate protected.")

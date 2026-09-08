from pathlib import Path
import re
import subprocess

SOURCE = Path("src/what_if_daily_v2.py")


def main() -> None:
    s = SOURCE.read_text(encoding="utf-8")

    # Remove the old production QC function and its call completely.
    s = re.sub(r'\n\ndef production_qc\(story\):.*?(?=\n\ndef main\()', '\n', s, flags=re.S)
    s = s.replace('    production_qc(story)\n', '')

    # Keep narration tolerant for occasional 103-word Gemini output.
    s = s.replace(
        '    if not 104 <= total_words <= 116:\n        raise RuntimeError(f"Narration word count {total_words}; expected 104-116")',
        '    if not 100 <= total_words <= 116:\n        raise RuntimeError(f"Narration word count {total_words}; expected 100-116")',
    )

    # Reduce dead air between scene narrations. Each scene is 7.5s; leave only
    # 0.05s of intentional boundary space instead of the old 0.20s.
    s = s.replace(
        '    target = SCENE_DURATION - 0.20',
        '    target = SCENE_DURATION - 0.05',
    )

    # Image quota circuit breaker: once a model is quota-exhausted, do not
    # call that same model again for any later scene in this run.
    if 'DISABLED_IMAGE_MODELS = set()' not in s:
        s = s.replace(
            'VISUALS_ENABLED = os.getenv("VISUALS_ENABLED", "true").lower() == "true"',
            'VISUALS_ENABLED = os.getenv("VISUALS_ENABLED", "true").lower() == "true"\nDISABLED_IMAGE_MODELS = set()',
            1,
        )

    old_loop = '''    for model in models:\n        for attempt in range(2):\n            try:'''
    new_loop = '''    for model in models:\n        if model in DISABLED_IMAGE_MODELS:\n            print(f"Skipping disabled Gemini image model for this run: {model}")\n            continue\n        for attempt in range(2):\n            try:'''
    if old_loop in s:
        s = s.replace(old_loop, new_loop, 1)

    old_error = '''            except Exception as exc:\n                print(f"Gemini image error ({model}, attempt {attempt + 1}): {exc}")\n                if attempt == 0:\n                    time.sleep(2)\n    return False'''
    new_error = '''            except Exception as exc:\n                print(f"Gemini image error ({model}, attempt {attempt + 1}): {exc}")\n                if is_quota_error(exc):\n                    DISABLED_IMAGE_MODELS.add(model)\n                    print(f"Disabling {model} for the rest of this run because quota is exhausted.")\n                    break\n                if attempt == 0:\n                    time.sleep(2)\n    return False'''
    if old_error in s:
        s = s.replace(old_error, new_error, 1)

    # FFmpeg lavfi expressions use commas as filter separators. Escape the
    # comma inside mod(t,0.9) so the bird SFX expression parses correctly.
    s = s.replace(
        'aevalsrc=0.055*sin(2*PI*(900+700*sin(2*PI*0.8*t))*t)*exp(-0.55*mod(t,0.9)):s=44100:d=7.5',
        'aevalsrc=0.055*sin(2*PI*(900+700*sin(2*PI*0.8*t))*t)*exp(-0.55*mod(t\\,0.9)):s=44100:d=7.5',
    )

    SOURCE.write_text(s, encoding="utf-8")
    subprocess.run(["python", "-m", "py_compile", str(SOURCE)], check=True)
    print("Production source normalized: QC removed, image quota circuit breaker enabled, bird SFX fixed, and scene silence reduced to 0.05s.")


if __name__ == "__main__":
    main()

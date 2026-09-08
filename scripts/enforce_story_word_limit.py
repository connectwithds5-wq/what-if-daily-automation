from pathlib import Path

SOURCE = Path("src/what_if_daily_v2.py")

old = '''    total_words = sum(len(s["narration"].split()) for s in scenes)\n    if 117 <= total_words <= 120:\n        excess = total_words - 116\n        for scene in reversed(scenes):\n            words = scene["narration"].split()\n            removable = max(0, len(words) - 12)\n            take = min(removable, excess)\n            if take:\n                scene["narration"] = " ".join(words[:-take])\n                excess -= take\n            if excess == 0:\n                break\n        total_words = sum(len(s["narration"].split()) for s in scenes)\n        print(f"Normalized narration word count to {total_words} words.")\n    if not 100 <= total_words <= 116:\n        raise RuntimeError(f"Narration word count {total_words}; expected 100-116")\n'''

new = '''    total_words = sum(len(s["narration"].split()) for s in scenes)\n    if total_words > 116:\n        excess = total_words - 116\n        # Trim from the end while keeping every scene at least 12 words.\n        # This protects the run even when Gemini returns more than 120 words.\n        for scene in reversed(scenes):\n            words = scene["narration"].split()\n            removable = max(0, len(words) - 12)\n            take = min(removable, excess)\n            if take:\n                scene["narration"] = " ".join(words[:-take])\n                excess -= take\n            if excess == 0:\n                break\n        total_words = sum(len(s["narration"].split()) for s in scenes)\n        print(f"Normalized narration word count to {total_words} words.")\n    if not 100 <= total_words <= 116:\n        raise RuntimeError(f"Narration word count {total_words}; expected 100-116")\n'''

text = SOURCE.read_text(encoding="utf-8")
if new in text:
    print("Narration word-limit safety patch already present.")
elif old in text:
    SOURCE.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("Applied narration word-limit safety patch.")
else:
    raise SystemExit("Expected narration word-count block was not found; refusing to modify source.")

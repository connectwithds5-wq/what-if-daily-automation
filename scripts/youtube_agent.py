"""WHAT IF DAILY YouTube AI Growth Agent v2.

A dependency-light packaging layer inspired by proven YouTube-agent workflows:
- hook scoring
- title + thumbnail pairing lint
- SEO package generation
- channel audit from the repository's analytics strategy
- retention/action report
- Shorts/chapters helpers for future long-form inputs

This module does not require another API key. It works from the story and
analytics already produced by the existing pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "if", "what", "why", "how",
    "is", "are", "was", "were", "on", "in", "for", "with", "your", "you",
    "this", "that", "it", "will", "would", "could", "we", "our", "from",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", str(text or "").lower())


def hook_score(hook: str) -> dict[str, Any]:
    """Score a short-form hook using transparent heuristics, not prediction."""
    text = str(hook or "").strip()
    words = _words(text)
    n = len(words)
    score = 0
    checks: dict[str, int] = {}

    curiosity = int(any(x in text.lower() for x in ("what if", "imagine", "what happens", "if "))
                    or "?" in text)
    specificity = int(any(ch.isdigit() for ch in text) or any(
        x in text.lower() for x in ("seconds", "hours", "days", "earth", "moon", "sun", "human")
    ))
    consequence = int(any(x in text.lower() for x in (
        "die", "survive", "disappear", "collapse", "change", "happen", "lose", "destroy", "freeze", "burn"
    )))
    brevity = int(6 <= n <= 18)
    spoken = int(not any(x in text for x in (";", ":", "—", "•")))

    checks.update(
        curiosity=curiosity,
        specificity=specificity,
        consequence=consequence,
        brevity=brevity,
        spoken=spoken,
    )
    score = sum(checks.values()) * 20
    if n > 24:
        score -= 10
    if n < 4:
        score -= 10

    return {
        "score": max(0, min(100, score)),
        "word_count": n,
        "checks": checks,
        "note": "Heuristic quality gate; not a view predictor.",
    }


def lint_title_thumbnail(title: str, thumbnail_text: str) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", str(title or "").strip())
    thumb = re.sub(r"\s+", " ", str(thumbnail_text or "").strip())
    title_words = set(_words(title)) - STOPWORDS
    thumb_words = set(_words(thumb)) - STOPWORDS
    overlap = sorted(title_words & thumb_words)

    issues: list[str] = []
    if len(title) > 90:
        issues.append("Title may truncate on some surfaces.")
    if len(title) > 100:
        issues.append("Title is over the recommended 100-character ceiling.")
    if not title:
        issues.append("Missing title.")
    if not thumb:
        issues.append("Missing thumbnail text.")
    if overlap and len(overlap) >= max(2, min(5, len(thumb_words))):
        issues.append("Title and thumbnail repeat too much of the same wording.")
    if title.lower().startswith(("video about", "interesting facts", "science facts")):
        issues.append("Title is generic; lead with the consequence or curiosity gap.")
    if "?" not in title and not title.lower().startswith("what if"):
        issues.append("Consider a stronger curiosity framing.")
    return {
        "title": title,
        "thumbnail_text": thumb,
        "title_length": len(title),
        "thumbnail_length": len(thumb),
        "overlap_words": overlap,
        "issues": issues,
        "pass": not issues,
    }


def seo_package(story: dict[str, Any], hook: str) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", str(story.get("title", "WHAT IF DAILY")).strip())
    if len(title) > 95:
        title = title[:92].rsplit(" ", 1)[0] + "..."
    if not title.lower().startswith("what if") and "what if" not in title.lower():
        title = f"What If {title}"

    keywords = []
    for value in story.get("keywords", []) or []:
        value = re.sub(r"[^A-Za-z0-9 ]+", " ", str(value)).strip().lower()
        if value and value not in keywords:
            keywords.append(value)
    for value in _words(title):
        if value not in STOPWORDS and value not in keywords:
            keywords.append(value)
    keywords = keywords[:25]

    base_description = re.sub(r"\s+", " ", str(story.get("description", "")).strip())
    lead = f"{title}. A fast, cinematic science What If that explores the real consequences step by step."
    description = lead
    if base_description:
        description += " " + base_description
    description += "\n\nFollow WHAT IF DAILY for more science, space and curiosity Shorts."
    description += "\n\n" + " ".join("#" + x.replace(" ", "") for x in keywords[:5])

    return {
        "title": title,
        "description": description[:5000],
        "keywords": keywords,
        "hook": hook,
    }


def _thumbnail_text(story: dict[str, Any]) -> str:
    scenes = story.get("scenes") or []
    if scenes:
        text = str(scenes[0].get("on_screen") or scenes[0].get("narration") or "")
    else:
        text = str(story.get("title") or "WHAT IF?")
    text = re.sub(r"[^A-Za-z0-9?! ]+", " ", text).strip()
    words = text.split()
    return " ".join(words[:7]).upper()[:42]


def build_youtube_package(
    story: dict[str, Any],
    scene_info: list[dict[str, Any]] | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Build the final title/thumbnail/SEO/hook package and write JSON."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenes = story.get("scenes") or []
    hook = str(scenes[0].get("narration", "") if scenes else story.get("title", ""))
    thumb = _thumbnail_text(story)

    seo = seo_package(story, hook)
    lint = lint_title_thumbnail(seo["title"], thumb)
    hook_report = hook_score(hook)

    # Reuse the first generated cinematic frame as a real thumbnail base.
    thumbnail_path = output_dir / "thumbnail.jpg"
    first_frame = None
    if scene_info:
        frames = scene_info[0].get("frames") or []
        if frames:
            first_frame = Path(frames[0])
    if first_frame and first_frame.exists():
        try:
            from PIL import Image, ImageDraw, ImageFont
            image = Image.open(first_frame).convert("RGB")
            image = image.resize((1080, 1920))
            # YouTube thumbnail is landscape; crop the strongest central region.
            image = image.crop((0, 330, 1080, 1410)).resize((1280, 720))
            draw = ImageDraw.Draw(image, "RGBA")
            draw.rectangle((0, 0, 1280, 720), fill=(0, 0, 0, 55))
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            font = ImageFont.truetype(font_path, 78) if Path(font_path).exists() else ImageFont.load_default()
            draw.text((55, 55), "WHAT IF DAILY", font=font, fill="white")
            draw.text((55, 535), thumb, font=font, fill="white", stroke_width=3, stroke_fill="black")
            image.save(thumbnail_path, quality=92, optimize=True)
        except Exception as exc:
            print(f"Thumbnail render skipped: {exc}")

    package = {
        "version": 2,
        "agent": "WHAT IF DAILY YouTube AI Growth Agent",
        "hook": hook_report,
        "title_thumbnail": lint,
        "seo": seo,
        "thumbnail_path": str(thumbnail_path) if thumbnail_path.exists() else None,
        "workflow": [
            "analytics",
            "strategy",
            "topic",
            "hook",
            "script",
            "video",
            "title_thumbnail",
            "seo",
            "upload",
            "analytics_feedback",
        ],
    }
    (output_dir / "youtube_package.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return package


def build_audit(root: Path) -> dict[str, Any]:
    root = Path(root)
    strategy_path = root / "growth_strategy.json"
    data = {}
    if strategy_path.exists():
        try:
            data = json.loads(strategy_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    report = {
        "channel": "WHAT IF DAILY",
        "confidence": data.get("confidence", "unknown"),
        "analytics_source": data.get("analytics_source", "unknown"),
        "top_actions": [],
        "retention_insights": data.get("retention_insights", []),
        "next_best_topics": data.get("next_best_topics", [])[:5],
        "posting_windows": data.get("best_posting_windows", [])[:2],
    }
    for item in data.get("what_to_improve", [])[:5]:
        if isinstance(item, dict):
            report["top_actions"].append(item)
    if not report["top_actions"]:
        report["top_actions"].append({
            "pattern": "Collect more owner Analytics",
            "evidence": "No improvement recommendations were present.",
            "action": "Run the analytics strategy job again before changing the content format.",
        })

    out = root / "output" / "youtube_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="WHAT IF DAILY YouTube AI Growth Agent")
    parser.add_argument("command", choices=("audit",))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    if args.command == "audit":
        report = build_audit(Path(args.root))
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

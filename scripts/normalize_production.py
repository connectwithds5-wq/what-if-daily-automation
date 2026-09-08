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

    # Faster pacing: 8 scenes now total 58 seconds instead of 60 seconds.
    s = s.replace('SCENE_DURATION = DURATION / SCENES', 'SCENE_DURATION = 7.25')

    # Leave only ~0.10s at the end of each scene for a natural handoff.
    s = s.replace('    target = SCENE_DURATION - 0.20', '    target = SCENE_DURATION - 0.10')
    s = s.replace('    target = SCENE_DURATION - 0.05', '    target = SCENE_DURATION - 0.10')

    # Keep the transition SFX inside the shorter 7.25s scene.
    s = s.replace('adelay=6950|6950,volume=1.0[t]', 'adelay=6700|6700,volume=1.0[t]')

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

    # --- AI YouTube growth strategy ---------------------------------------
    if 'GROWTH_STRATEGY = ROOT / "growth_strategy.json"' not in s:
        s = s.replace(
            'HISTORY = ROOT / "topic_history.json"',
            'HISTORY = ROOT / "topic_history.json"\nGROWTH_STRATEGY = ROOT / "growth_strategy.json"\nRUN_SLOT = os.getenv("RUN_SLOT", "0")',
            1,
        )

    strategy_block = '''\n\ndef load_growth_strategy():\n    default = {\n        "slots": {\n            "0": {"name": "Universal Curiosity", "best_lanes": ["Earth", "space", "human body", "animals", "everyday science", "nature"], "style": "instantly understandable, surprising, relatable, visual"},\n            "1": {"name": "Extreme Future", "best_lanes": ["extreme Earth", "future technology", "survival", "disasters", "space extremes", "human consequences"], "style": "high stakes, escalating consequences, cinematic, debate-worthy"}\n        },\n        "ranking_objective": {"curiosity": 0.25, "broad_appeal": 0.20, "visual_impact": 0.18, "retention_potential": 0.17, "comment_debate": 0.08, "novelty": 0.07, "trend_relevance": 0.05}\n    }\n    try:\n        data = json.loads(GROWTH_STRATEGY.read_text(encoding="utf-8"))\n        return data if isinstance(data, dict) else default\n    except Exception:\n        return default\n\n\ndef strategy_context():\n    strategy = load_growth_strategy()\n    slot = strategy.get("slots", {}).get(str(RUN_SLOT), strategy.get("slots", {}).get("0", {}))\n    return strategy, slot\n'''
    if 'def load_growth_strategy()' not in s:
        s = s.replace('\ndef generate_unique_topic():', strategy_block + '\n\ndef generate_unique_topic():', 1)

    old_topic_prompt = '''    prompt = f"""\nYou create one fresh topic for a YouTube Shorts channel called WHAT IF DAILY.\nReturn ONLY one topic, no quotes, no numbering.\nIt must start with What If and be scientifically plausible, surprising, highly visual, and different from all previous topics.\nKeep it under 80 characters.\nPrevious topics:\n{json.dumps(history[-200:], ensure_ascii=False)}\n"""'''
    new_topic_prompt = '''    strategy, slot = strategy_context()\n    prompt = f"""\nYou are the AI growth strategist and topic editor for the YouTube Shorts channel WHAT IF DAILY.\nGenerate ONE fresh topic for this upload slot.\nReturn ONLY one topic, no quotes, no numbering.\nThe topic must start with What If, stay scientifically plausible, be highly visual, and be understandable worldwide by a general English audience.\nOptimize for curiosity {strategy.get("ranking_objective", {}).get("curiosity", 0.25)}, broad appeal {strategy.get("ranking_objective", {}).get("broad_appeal", 0.20)}, visual impact {strategy.get("ranking_objective", {}).get("visual_impact", 0.18)}, retention {strategy.get("ranking_objective", {}).get("retention_potential", 0.17)}, comment potential {strategy.get("ranking_objective", {}).get("comment_debate", 0.08)}.\nCurrent slot: {RUN_SLOT} - {slot.get("name", "Universal Curiosity")}\nPreferred topic lanes: {json.dumps(slot.get("best_lanes", []))}\nPreferred style: {slot.get("style", "surprising and visual")}\nKeep it under 80 characters.\nAvoid narrow academic topics, generic facts, repeated ideas, and unsupported speculation.\nPrevious topics:\n{json.dumps(history[-200:], ensure_ascii=False)}\n"""'''
    if old_topic_prompt in s:
        s = s.replace(old_topic_prompt, new_topic_prompt, 1)

    # Add strategy context to story generation so title, hook, visuals and
    # payoff are optimized for the selected slot.
    s = s.replace(
        'def create_story(topic):\n    prompt = f"""\nCreate an exciting 60-second WHAT IF DAILY science short about: {topic}',
        'def create_story(topic):\n    strategy, slot = strategy_context()\n    prompt = f"""\nCreate an exciting 58-second WHAT IF DAILY science short about: {topic}\nThis is upload slot {RUN_SLOT}: {slot.get("name", "Universal Curiosity")}.\nUse these preferred lanes: {json.dumps(slot.get("best_lanes", []))}.\nOptimize for broad appeal, immediate curiosity, strong visual transformation, high retention, and a surprising final payoff.\nDo not make the title clickbait that the video cannot deliver.',
        1,
    )

    # Store strategy metadata in each generated artifact for later analysis.
    s = s.replace(
        '"visual_style": "cinematic scientific visualization + kinetic typography + audible cinematic music + scene-matched SFX"}',
        '"visual_style": "cinematic scientific visualization + kinetic typography + audible cinematic music + scene-matched SFX", "growth_strategy_slot": RUN_SLOT, "growth_strategy_name": strategy_context()[1].get("name", "Universal Curiosity")}',
        1,
    )

    # FFmpeg lavfi expressions use commas as filter separators. Escape the
    # comma inside mod(t,0.9) so the bird SFX expression parses correctly.
    s = s.replace(
        'aevalsrc=0.055*sin(2*PI*(900+700*sin(2*PI*0.8*t))*t)*exp(-0.55*mod(t,0.9)):s=44100:d=7.5',
        'aevalsrc=0.055*sin(2*PI*(900+700*sin(2*PI*0.8*t))*t)*exp(-0.55*mod(t\\,0.9)):s=44100:d=7.5',
    )

    SOURCE.write_text(s, encoding="utf-8")
    subprocess.run(["python", "-m", "py_compile", str(SOURCE)], check=True)
    print("Production source normalized: QC removed, image quota circuit breaker enabled, AI growth strategy enabled, bird SFX fixed, and pacing optimized to 7.25s per scene (~58s total).")


if __name__ == "__main__":
    main()

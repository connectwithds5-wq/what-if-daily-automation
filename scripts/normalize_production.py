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


def main() -> None:
    s = SOURCE.read_text(encoding="utf-8")

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

    s = s.replace(
        'filters.append("[2:a]volume=1.0[t]")',
        'filters.append("[2:a]adelay=6950|6950,volume=1.0[t]")',
        1,
    )
    s = s.replace(
        'overlay_frame(frame, scene["on_screen"], scene["narration"], index, (fi + 1) / 18)',
        'overlay_frame(frame, scene["on_screen"], scene["narration"], index, min(1.0, (fi + 1) / 5.0))',
        1,
    )

    validation = re.compile(
        r'    total_words = sum\(len\(s\["narration"\]\.split\(\)\) for s in scenes\)\n'
        r'    if not 104 <= total_words <= 116:\n'
        r'        raise RuntimeError\(f"Narration word count \{total_words\}; expected 104-116"\)'
    )
    normalized_validation = '''    total_words = sum(len(s["narration"].split()) for s in scenes)
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
        raise RuntimeError(f"Narration word count {total_words}; expected 104-116")'''
    s, count = validation.subn(normalized_validation, s, count=1)
    if count != 1:
        raise RuntimeError("Could not locate narration word-count validation")

    if "def production_qc(" not in s:
        qc = '''def production_qc(story):
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
    print(f"PRODUCTION QC PASSED: {WIDTH}x{HEIGHT}, {duration:.2f}s, audio {audio_duration:.2f}s, {words} words")

'''
        s = s.replace("\ndef main():\n", "\n" + qc + "def main():\n", 1)

    upload_pattern = "    write_metadata(story)\n    upload_youtube(story)"
    if upload_pattern in s:
        s = s.replace(upload_pattern, "    write_metadata(story)\n    production_qc(story)\n    upload_youtube(story)", 1)
    elif "    production_qc(story)\n    upload_youtube(story)" not in s:
        raise RuntimeError("Could not locate YouTube upload call")

    SOURCE.write_text(s, encoding="utf-8")
    subprocess.run(["python", "-m", "py_compile", str(SOURCE)], check=True)
    print("Production source normalized and py_compile passed.")


if __name__ == "__main__":
    main()

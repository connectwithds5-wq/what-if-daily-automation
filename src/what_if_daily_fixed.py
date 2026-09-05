import os
import re
import json
import math
import random
import shutil
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont
from google import genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

WIDTH, HEIGHT, FPS, DURATION = 1080, 1920, 30, 60
SCENES = 8
SCENE_DURATION = DURATION / SCENES
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
FRAMES = OUTPUT / "frames"
AUDIO = OUTPUT / "audio"
VIDEO = OUTPUT / "what_if_daily.mp4"
METADATA = OUTPUT / "metadata.json"
HISTORY = ROOT / "topic_history.json"

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
VOICE = os.getenv("TTS_VOICE", "en-US-AndrewMultilingualNeural")
TTS_RATE = os.getenv("TTS_RATE", "+5%")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")
CLIENT = genai.Client(api_key=GEMINI_API_KEY)


def run(cmd):
    print("RUN:", " ".join(map(str, cmd)))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(p.stdout[-5000:])
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return p


def clean():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    FRAMES.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)


def safe_ascii(text, max_len=5000):
    text = str(text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9 .,!?':;()/%+\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:max_len]


def font(size, bold=False):
    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for n in names:
        if Path(n).exists():
            return ImageFont.truetype(n, size)
    return ImageFont.load_default()


def wrap(text, f, max_width):
    words = safe_ascii(text, 1000).split()
    lines, cur = [], ""
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for w in words:
        test = w if not cur else cur + " " + w
        if d.textbbox((0, 0), test, font=f)[2] <= max_width:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines


def load_history():
    if not HISTORY.exists(): return []
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(items):
    HISTORY.write_text(json.dumps(items[-1000:], ensure_ascii=False, indent=2), encoding="utf-8")


def transient(exc):
    s = str(exc).upper()
    return any(x in s for x in ["500", "502", "503", "504", "429", "INTERNAL", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED", "HIGH DEMAND", "RATE LIMIT", "TEMPORARY"])


def gemini_text(prompt, attempts=5):
    for i in range(attempts):
        try:
            r = CLIENT.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = (getattr(r, "text", "") or "").strip()
            if text:
                return text
            raise RuntimeError("Gemini returned an empty response")
        except Exception as e:
            print(f"Gemini attempt {i + 1}/{attempts} failed: {e}")
            if not transient(e) or i == attempts - 1:
                raise
            wait = 2 ** i
            print(f"Retrying in {wait}s...")
            import time
            time.sleep(wait)
    raise RuntimeError("Gemini failed")


def generate_unique_topic():
    history = load_history()
    prompt = f"""
You create one fresh topic for a YouTube Shorts channel called WHAT IF DAILY.
Return ONLY one topic, no quotes, no numbering.
It must start with What If and be scientifically plausible, surprising, highly visual, and different from all previous topics.
Keep it under 80 characters.
Previous topics:
{json.dumps(history[-200:], ensure_ascii=False)}
"""
    for _ in range(5):
        topic = safe_ascii(gemini_text(prompt), 100).strip().strip('"')
        norm = re.sub(r"[^a-z0-9]+", " ", topic.lower()).strip()
        old = {re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip() for x in history}
        if topic and norm not in old:
            history.append(topic)
            save_history(history)
            return topic
        prompt += "\nGenerate a completely different topic."
    raise RuntimeError("Could not generate a unique topic")


def create_story(topic):
    prompt = f"""
Create an exciting 60-second WHAT IF DAILY science short about: {topic}
Return ONLY valid JSON with exactly this structure:
{{"title":"...","description":"...","keywords":["..."],"hashtags":["#..."],"scenes":[{{"narration":"...","on_screen":"..."}}]}}
Rules:
- Exactly 8 scenes.
- Total narration should fit about 60 seconds.
- Each narration is 18-35 words, natural spoken English, factual but entertaining.
- on_screen is punchy English text, maximum 55 characters, for kinetic typing.
- Build curiosity: hook, escalation, consequences, surprising fact, ending.
- No image URLs, no visual prompts, no markdown.
"""
    raw = gemini_text(prompt, 5)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Story JSON parse failed: {e}\n{raw[:1000]}")
    scenes = data.get("scenes", [])
    if len(scenes) != 8:
        raise RuntimeError(f"Gemini returned {len(scenes)} scenes; expected 8")
    for s in scenes:
        s["narration"] = safe_ascii(s.get("narration", ""), 700)
        s["on_screen"] = safe_ascii(s.get("on_screen", ""), 55)
        if not s["narration"]: raise RuntimeError("Empty narration scene")
        if not s["on_screen"]: s["on_screen"] = s["narration"][:55]
    return data


def create_typography_scene(topic, scene, index):
    seed = int(hashlib.sha256(f"{topic}:{index}".encode()).hexdigest()[:8], 16)
    random.seed(seed)
    folder = FRAMES / f"scene_{index:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    on = safe_ascii(scene.get("on_screen", ""), 55).upper()
    narration = safe_ascii(scene.get("narration", ""), 600)
    main_f = font(104, True); sub_f = font(40); small_f = font(30, True)
    base = Image.new("RGB", (WIDTH, HEIGHT))
    p = base.load()
    r0,g0,b0 = 6,8,16
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        c = (int(r0+10*t), int(g0+12*t), int(b0+22*t))
        for x in range(WIDTH): p[x,y] = c
    d = ImageDraw.Draw(base)
    cx, cy = random.randint(150,930), random.randint(450,1450)
    for rad in (600,450,300,180): d.ellipse((cx-rad,cy-rad,cx+rad,cy+rad), outline=(35,38,55), width=3)
    d.rectangle((0,0,WIDTH,270), fill=(0,0,0))
    d.rectangle((0,1300,WIDTH,HEIGHT), fill=(0,0,0))
    lines = wrap(on, main_f, 900)[:3]
    paths=[]
    total=max(1,len(on))
    for fi in range(18):
        c=base.copy(); dd=ImageDraw.Draw(c)
        dd.text((65,65),"WHAT IF DAILY",font=small_f,fill=(255,255,255))
        counter=f"SCENE {index+1}/{SCENES}"; bb=dd.textbbox((0,0),counter,font=small_f)
        dd.text((WIDTH-65-(bb[2]-bb[0]),68),counter,font=small_f,fill=(210,210,210))
        n=int(total*(fi+1)/18); typed=on[:n]; tl=wrap(typed,main_f,900)[:3] or [""]
        y=650-(len(tl)*125)//2
        for line in tl:
            bb=dd.textbbox((0,0),line,font=main_f,stroke_width=3); x=(WIDTH-(bb[2]-bb[0]))//2
            dd.text((x+5,y+7),line,font=main_f,fill=(0,0,0),stroke_width=6,stroke_fill=(0,0,0))
            dd.text((x,y),line,font=main_f,fill=(255,255,255),stroke_width=3,stroke_fill=(0,0,0)); y+=125
        if fi>=7:
            chunks=wrap(narration,sub_f,850)[:2]
            sy=1410
            for line in chunks:
                bb=dd.textbbox((0,0),line,font=sub_f); x=(WIDTH-(bb[2]-bb[0]))//2
                dd.text((x,sy),line,font=sub_f,fill=(225,225,225),stroke_width=1,stroke_fill=(0,0,0)); sy+=56
        dd.rectangle((65,1815,WIDTH-65,1822),fill=(70,70,70)); dd.rectangle((65,1815,65+int((WIDTH-130)*(index+1)/SCENES),1822),fill=(255,255,255))
        path=folder/f"frame_{fi:02d}.png"; c.save(path,format="PNG",optimize=True); paths.append(path)
    return {"frames":[str(x) for x in paths]}


def create_video(scene_info):
    clips=[]
    for i,info in enumerate(scene_info):
        clip=OUTPUT/f"clip_{i:02d}.mp4"; txt=OUTPUT/f"typing_{i:02d}.txt"; lines=[]
        for p in info["frames"]:
            lines += [f"file '{Path(p).as_posix()}'", "duration 0.10"]
        lines += [f"file '{Path(info['frames'][-1]).as_posix()}'", f"duration {SCENE_DURATION-1.8:.3f}", f"file '{Path(info['frames'][-1]).as_posix()}'"]
        txt.write_text("\n".join(lines),encoding="utf-8")
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(txt),"-vf","fps=30,format=yuv420p","-t",str(SCENE_DURATION),"-an","-c:v","libx264","-preset","veryfast","-crf","20","-pix_fmt","yuv420p",str(clip)])
        clips.append(clip)
    alltxt=OUTPUT/"concat.txt"; alltxt.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips),encoding="utf-8")
    silent=OUTPUT/"video_silent.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(alltxt),"-c","copy",str(silent)])
    return silent


def create_voice(text):
    out=AUDIO/"narration.mp3"
    run(["edge-tts","--voice",VOICE,"--rate",TTS_RATE,"--text",safe_ascii(text,5000),"--write-media",str(out)])
    return out


def create_music():
    out=AUDIO/"music.m4a"
    run(["ffmpeg","-y","-f","lavfi","-i","sine=frequency=110:sample_rate=44100","-t","60","-af","volume=0.035","-c:a","aac","-b:a","128k",str(out)])
    return out


def create_sound_design():
    out=AUDIO/"sfx.m4a"
    run(["ffmpeg","-y","-f","lavfi","-i","anoisesrc=color=white:amplitude=0.008:sample_rate=44100","-t","60","-c:a","aac","-b:a","96k",str(out)])
    return out


def mix_audio(video, narration, music, sfx):
    audio=AUDIO/"final_audio.m4a"
    run(["ffmpeg","-y","-i",str(narration),"-i",str(music),"-i",str(sfx),"-filter_complex","[0:a]volume=1.12[n];[1:a]volume=0.7[m];[2:a]volume=0.5[s];[n][m][s]amix=inputs=3:duration=longest:dropout_transition=2,alimiter=limit=0.9[a]","-map","[a]","-t","60","-c:a","aac","-b:a","192k",str(audio)])
    run(["ffmpeg","-y","-i",str(video),"-i",str(audio),"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-shortest",str(VIDEO)])
    return VIDEO


def save_metadata(story):
    title=safe_ascii(story.get("title", "WHAT IF DAILY"),95)
    desc=safe_ascii(story.get("description", ""),4500)+"\n\nWHAT IF DAILY - IMAGINE. WATCH. WONDER.\n\nVisual format: cinematic kinetic typography. No external image assets are used."
    data={"title":title,"description":desc,"keywords":story.get("keywords",[])[:25],"hashtags":story.get("hashtags",[])[:8],"created_at":datetime.utcnow().isoformat()+"Z","voice":VOICE,"tts_rate":TTS_RATE,"visual_style":"cinematic kinetic typography; no external images"}
    METADATA.write_text(json.dumps(data,indent=2),encoding="utf-8")
    return data


def upload_youtube(meta):
    raw=os.getenv("YOUTUBE_OAUTH_JSON")
    if not raw: print("YOUTUBE_OAUTH_JSON missing; skipping upload"); return
    d=json.loads(raw)
    creds=Credentials(None,refresh_token=d["refresh_token"],token_uri="https://oauth2.googleapis.com/token",client_id=d["client_id"],client_secret=d["client_secret"],scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt=build("youtube","v3",credentials=creds)
    desc=safe_ascii(meta["description"],4900); tags=meta.get("hashtags",[])
    if tags: desc += "\n\n"+" ".join(tags)
    body={"snippet":{"title":meta["title"],"description":desc,"tags":meta.get("keywords",[])[:25],"categoryId":"28"},"status":{"privacyStatus":"public","selfDeclaredMadeForKids":False}}
    print("Uploading to YouTube:",body["snippet"]["title"])
    req=yt.videos().insert(part="snippet,status",body=body,media_body=MediaFileUpload(str(VIDEO),mimetype="video/mp4",resumable=True))
    result=req.execute(); print("YouTube upload complete:",result.get("id"))


def main():
    clean()
    manual=os.getenv("WHAT_IF_TOPIC","").strip()
    topic=manual or generate_unique_topic()
    print("Topic:",topic); print("Voice:",VOICE,"Rate:",TTS_RATE)
    story=create_story(topic)
    infos=[create_typography_scene(topic,s,i) for i,s in enumerate(story["scenes"])]
    narration=create_voice(" ".join(s["narration"] for s in story["scenes"]))
    music=create_music(); sfx=create_sound_design()
    silent=create_video(infos)
    mix_audio(silent,narration,music,sfx)
    meta=save_metadata(story); upload_youtube(meta)
    print("DONE:",VIDEO)

if __name__=="__main__": main()

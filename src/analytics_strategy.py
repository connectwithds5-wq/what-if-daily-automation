"""Refresh WHAT IF DAILY content strategy from public YouTube performance."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import requests
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "analytics_history.json"
STRATEGY_FILE = ROOT / "growth_strategy.json"
API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
MAX_VIDEOS = 50


def get_json(url, params):
    r = requests.get(url, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f"YouTube API {r.status_code}: {r.text[:500]}")
    return r.json()


def discover_ids():
    channel = get_json("https://www.googleapis.com/youtube/v3/channels", {"part": "contentDetails", "id": CHANNEL_ID, "key": API_KEY})
    items = channel.get("items", [])
    if not items:
        raise RuntimeError("YouTube channel not found. Check YOUTUBE_CHANNEL_ID.")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    data = get_json("https://www.googleapis.com/youtube/v3/playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": MAX_VIDEOS, "key": API_KEY})
    return [x["contentDetails"]["videoId"] for x in data.get("items", []) if x.get("contentDetails", {}).get("videoId")]


def collect(ids):
    data = get_json("https://www.googleapis.com/youtube/v3/videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[:50]), "key": API_KEY})
    videos = []
    for item in data.get("items", []):
        s = item.get("statistics", {})
        sn = item.get("snippet", {})
        videos.append({
            "video_id": item.get("id"), "title": sn.get("title", ""), "published_at": sn.get("publishedAt"),
            "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0)), "duration": item.get("contentDetails", {}).get("duration")
        })
    return sorted(videos, key=lambda x: x.get("published_at") or "", reverse=True)


def main():
    if not API_KEY or not CHANNEL_ID:
        print("Analytics refresh skipped: YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID is not configured.")
        return
    videos = collect(discover_ids())
    if not videos:
        print("Analytics refresh skipped: no public videos found.")
        return
    history = {"updated_at": datetime.now(timezone.utc).isoformat(), "videos": videos[:MAX_VIDEOS]}
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    compact = [{k: v[k] for k in ("title", "published_at", "views", "likes", "comments")} for v in videos[:MAX_VIDEOS]]
    prompt = f"""
You are the growth strategist for WHAT IF DAILY, a science/curiosity YouTube Shorts channel.
Analyze ONLY these YouTube performance records. Do not invent watch time or retention metrics.
Data: {json.dumps(compact, ensure_ascii=False)}

Return ONLY valid JSON with this structure:
{{
  "version": 3,
  "channel": "WHAT IF DAILY",
  "generated_at": "",
  "confidence": "low|medium|high",
  "data_points": 0,
  "overall_summary": "",
  "what_is_working": [{{"pattern":"","evidence":"","action":""}}],
  "what_to_improve": [{{"pattern":"","evidence":"","action":""}}],
  "best_posting_windows": [
    {{"hour_utc":0,"minute_utc":0,"window":"","reason":"","confidence":"low|medium|high"}},
    {{"hour_utc":0,"minute_utc":0,"window":"","reason":"","confidence":"low|medium|high"}}
  ],
  "next_best_topics": [{{"priority":1,"topic":"","hook":"","reason":"","confidence":"low|medium|high"}}],
  "ranking_objective": {{"curiosity":0.25,"broad_appeal":0.20,"visual_impact":0.18,"retention_potential":0.17,"comment_debate":0.08,"novelty":0.07,"trend_relevance":0.05}},
  "slots": {{
    "0": {{"name":"Universal Curiosity","best_lanes":[],"style":""}},
    "1": {{"name":"Extreme Future","best_lanes":[],"style":""}}
  }}
}}

Choose TWO distinct best posting windows based only on the observed published_at timestamps and performance patterns. Return UTC hour/minute as integers so automation can schedule the two daily uploads. Keep at least 30 minutes between windows. Recommendations must be fresh What If questions, scientifically plausible, visually strong, and broad-audience friendly. Use repeated performance patterns, not one-off outliers. Keep controlled experiments in the strategy.
"""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "").strip())
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json"))
    text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
    strategy = json.loads(text)
    strategy["generated_at"] = datetime.now(timezone.utc).isoformat()
    strategy["data_points"] = len(videos)
    STRATEGY_FILE.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WHAT IF DAILY analytics strategy refreshed from", len(videos), "YouTube videos.")


if __name__ == "__main__":
    main()

"""Build WHAT IF DAILY strategy from public YouTube data and owner Analytics."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from google import genai
from google.genai import types
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "analytics_history.json"
STRATEGY_FILE = ROOT / "growth_strategy.json"
API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash").strip()
MAX_VIDEOS = 50
ANALYTICS_VIDEO_LIMIT = 20

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def get_json(url, params):
    r = requests.get(url, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f"YouTube API {r.status_code}: {r.text[:500]}")
    return r.json()


def discover_ids():
    channel = get_json("https://www.googleapis.com/youtube/v3/channels", {
        "part": "contentDetails", "id": CHANNEL_ID, "key": API_KEY
    })
    items = channel.get("items", [])
    if not items:
        raise RuntimeError("YouTube channel not found. Check YOUTUBE_CHANNEL_ID.")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    data = get_json("https://www.googleapis.com/youtube/v3/playlistItems", {
        "part": "contentDetails", "playlistId": uploads, "maxResults": MAX_VIDEOS, "key": API_KEY
    })
    return [x["contentDetails"]["videoId"] for x in data.get("items", []) if x.get("contentDetails", {}).get("videoId")]


def collect(ids):
    data = get_json("https://www.googleapis.com/youtube/v3/videos", {
        "part": "snippet,statistics,contentDetails", "id": ",".join(ids[:50]), "key": API_KEY
    })
    videos = []
    for item in data.get("items", []):
        s = item.get("statistics", {})
        sn = item.get("snippet", {})
        videos.append({
            "video_id": item.get("id"),
            "title": sn.get("title", ""),
            "published_at": sn.get("publishedAt"),
            "views": int(s.get("viewCount", 0)),
            "likes": int(s.get("likeCount", 0)),
            "comments": int(s.get("commentCount", 0)),
            "duration": item.get("contentDetails", {}).get("duration"),
        })
    return sorted(videos, key=lambda x: x.get("published_at") or "", reverse=True)


def load_oauth_credentials():
    raw = os.environ.get("YOUTUBE_OAUTH_JSON", "").strip()
    if not raw:
        raise RuntimeError("YOUTUBE_OAUTH_JSON GitHub Secret is missing.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("YOUTUBE_OAUTH_JSON is not valid JSON.") from exc
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not data.get(k)]
    if missing:
        raise RuntimeError("YOUTUBE_OAUTH_JSON is missing: " + ", ".join(missing))
    credentials = Credentials.from_authorized_user_info(data, YOUTUBE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError("YouTube OAuth credentials are invalid.")
    return credentials


def youtube_owner_analytics(videos):
    credentials = load_oauth_credentials()
    api = build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=365)

    report = api.reports().query(
        ids="channel==MINE",
        startDate=start.isoformat(),
        endDate=today.isoformat(),
        dimensions="video",
        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost",
        sort="-views",
        maxResults=200,
    ).execute()
    headers = [h["name"] for h in report.get("columnHeaders", [])]
    analytics = {}
    for row in report.get("rows", []):
        item = dict(zip(headers, row))
        video_id = item.pop("video", None)
        if video_id:
            analytics[video_id] = item

    retention = {}
    candidates = videos[:ANALYTICS_VIDEO_LIMIT]
    for video in candidates:
        video_id = video.get("video_id")
        published = (video.get("published_at") or "")[:10]
        if not video_id or not published:
            continue
        try:
            rr = api.reports().query(
                ids="channel==MINE",
                startDate=published,
                endDate=today.isoformat(),
                dimensions="elapsedVideoTimeRatio",
                metrics="audienceWatchRatio,relativeRetentionPerformance",
                filters=f"video=={video_id}",
                sort="elapsedVideoTimeRatio",
                maxResults=200,
            ).execute()
            rh = [h["name"] for h in rr.get("columnHeaders", [])]
            points = [dict(zip(rh, row)) for row in rr.get("rows", [])]
            if points:
                retention[video_id] = points
        except Exception as exc:
            print(f"Retention unavailable for {video_id}: {exc}")

    return analytics, retention


def summarize_retention(points):
    if not points:
        return None
    sampled = []
    for point in points:
        try:
            ratio = float(point.get("elapsedVideoTimeRatio", 0))
            watch = float(point.get("audienceWatchRatio", 0))
            relative = float(point.get("relativeRetentionPerformance", 0))
        except (TypeError, ValueError):
            continue
        if ratio <= 0.10 or abs(ratio - 0.25) < 0.02 or abs(ratio - 0.50) < 0.02 or abs(ratio - 0.75) < 0.02 or ratio >= 0.90:
            sampled.append({
                "video_progress": round(ratio, 3),
                "audience_watch_ratio": round(watch, 4),
                "relative_retention": round(relative, 4),
            })
    return sampled[:15]


def build_prompt(videos, analytics, retention):
    compact = []
    for video in videos:
        row = {
            "title": video["title"],
            "published_at": video["published_at"],
            "views": video["views"],
            "likes": video["likes"],
            "comments": video["comments"],
        }
        owner = analytics.get(video["video_id"])
        if owner:
            row["youtube_studio"] = owner
        rp = summarize_retention(retention.get(video["video_id"], []))
        if rp:
            row["retention_curve"] = rp
        compact.append(row)

    return f"""
You are the growth strategist for WHAT IF DAILY, a science/curiosity YouTube Shorts channel.
Analyze ONLY the supplied YouTube data. Never invent metrics.
Use owner Analytics when present: average view duration, average percentage watched,
estimated watch time, engagement, subscriber conversion and retention curves.
Use retention curves to identify early drop-offs, strong hold zones and payoff timing.
For short videos, convert evidence into concrete guidance on hook, pacing, visual transformation,
payoff timing, topic and publishing time. Prefer repeated patterns over outliers.
Recommend fresh scientifically plausible What If questions; never copy existing titles.
Choose TWO distinct best posting windows from observed published_at timestamps and performance.
Return exact UTC hour/minute integers at least 30 minutes apart.

Return ONLY valid JSON:
{{
  "version": 3,
  "channel": "WHAT IF DAILY",
  "generated_at": "",
  "confidence": "low|medium|high",
  "data_points": 0,
  "analytics_source": "youtube_analytics_api",
  "overall_summary": "",
  "what_is_working": [{{"pattern":"","evidence":"","action":""}}],
  "what_to_improve": [{{"pattern":"","evidence":"","action":""}}],
  "retention_insights": [{{"video_pattern":"","drop_or_strength":"","evidence":"","action":""}}],
  "best_posting_windows": [
    {{"hour_utc":0,"minute_utc":0,"window":"","reason":"","confidence":"low|medium|high"}},
    {{"hour_utc":0,"minute_utc":0,"window":"","reason":"","confidence":"low|medium|high"}}
  ],
  "next_best_topics": [{{"priority":1,"topic":"","hook":"","reason":"","confidence":"low|medium|high"}}],
  "ranking_objective": {{"curiosity":0.25,"broad_appeal":0.20,"visual_impact":0.18,"retention_potential":0.17,"comment_debate":0.08,"novelty":0.07,"trend_relevance":0.05}},
  "slots": {{"0": {{"name":"Universal Curiosity","best_lanes":[],"style":""}}, "1": {{"name":"Extreme Future","best_lanes":[],"style":""}}}},
  "avoid_or_limit": [{{"item":"","reason":""}}]
}}

YOUTUBE DATA:
{json.dumps(compact, ensure_ascii=False, indent=2)}
"""


def main():
    if not API_KEY or not CHANNEL_ID:
        raise RuntimeError("YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID is missing.")
    videos = collect(discover_ids())
    if not videos:
        raise RuntimeError("No public videos found.")

    analytics, retention = youtube_owner_analytics(videos)
    for video in videos:
        if video["video_id"] in analytics:
            video["owner_analytics"] = analytics[video["video_id"]]
        rp = summarize_retention(retention.get(video["video_id"], []))
        if rp:
            video["retention_curve"] = rp

    HISTORY_FILE.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "analytics_source": "youtube_analytics_api",
        "videos": videos[:MAX_VIDEOS],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing.")
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_prompt(videos[:MAX_VIDEOS], analytics, retention),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    text = (response.text or "").strip().removeprefix("```json").removesuffix("```").strip()
    strategy = json.loads(text)
    strategy["generated_at"] = datetime.now(timezone.utc).isoformat()
    strategy["data_points"] = len(videos)
    strategy["analytics_source"] = "youtube_analytics_api"
    strategy["owner_analytics_videos"] = len(analytics)
    strategy["retention_videos"] = len(retention)
    STRATEGY_FILE.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WHAT IF DAILY analytics strategy refreshed")
    print("Videos analyzed:", len(videos))
    print("Analytics source: youtube_analytics_api")
    print("Owner analytics videos:", len(analytics))
    print("Retention reports:", len(retention))


if __name__ == "__main__":
    main()

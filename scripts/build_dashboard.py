"""
Weekly Social Media Dashboard Builder for The Autism Helper.

Pulls fresh engagement data from Apify scrapers, builds a branded HTML
dashboard, writes it to index.html (which GitHub Pages publishes), and
sends a Slack notification with a summary.

Designed to run inside GitHub Actions. Reads APIFY_TOKEN and
SLACK_WEBHOOK_URL from the environment.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()

DASHBOARD_URL = "https://sashatheautismhelper.github.io/social-media-dashboard/"
PRIMARY = "#148496"
ACCENT = "#F1592E"

ACTORS = {
    "instagram": {
        "id": "shu8hvrXbJbY3Eb9W",
        "input": {
            "directUrls": ["https://www.instagram.com/theautismhelper/"],
            "resultsLimit": 30,
        },
    },
    "tiktok": {
        "id": "h7sDV53CddomktSi5",
        "input": {
            "profiles": ["theautismhelper"],
            "resultsPerPage": 30,
        },
    },
    "youtube": {
        "id": "KoJrdxJCTtpon81KY",
        "input": {
            "startUrls": [{"url": "https://www.youtube.com/@TheAutismHelper"}],
            "maxResults": 20,
        },
    },
    "facebook": {
        "id": "apify~facebook-posts-scraper",
        "input": {
            "startUrls": [{"url": "https://www.facebook.com/theautismhelper"}],
            "resultsLimit": 30,
        },
    },
}

POLL_INTERVAL_SECONDS = 30
MAX_WAIT_SECONDS = 15 * 60  # 15 minutes per actor

# ---------------------------------------------------------------------------
# Apify helpers
# ---------------------------------------------------------------------------


def start_actor(actor_id: str, input_payload: dict[str, Any]) -> str:
    """Start an Apify actor run, return the run ID."""
    url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={APIFY_TOKEN}"
    response = requests.post(url, json=input_payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"]["id"]


def wait_for_run(run_id: str) -> dict[str, Any] | None:
    """Poll until the run finishes. Return the run record or None on failure."""
    url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        run = response.json()["data"]
        status = run["status"]
        if status == "SUCCEEDED":
            return run
        if status in {"FAILED", "ABORTED", "TIMED-OUT"}:
            print(f"  Run {run_id} ended with status {status}", file=sys.stderr)
            return None
        time.sleep(POLL_INTERVAL_SECONDS)
    print(f"  Run {run_id} exceeded max wait time", file=sys.stderr)
    return None


def fetch_dataset(dataset_id: str) -> list[dict[str, Any]]:
    """Fetch all items from an Apify dataset."""
    url = (
        f"https://api.apify.com/v2/datasets/{dataset_id}/items"
        f"?token={APIFY_TOKEN}&format=json&limit=100"
    )
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def scrape_platform(platform: str) -> list[dict[str, Any]]:
    """Run a scraper end-to-end. Returns dataset items, or empty list on failure."""
    config = ACTORS[platform]
    print(f"Starting {platform} scraper ({config['id']})...")
    try:
        run_id = start_actor(config["id"], config["input"])
    except requests.HTTPError as exc:
        print(f"  Failed to start: {exc}", file=sys.stderr)
        return []

    run = wait_for_run(run_id)
    if not run:
        return []

    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        return []

    items = fetch_dataset(dataset_id)
    print(f"  {platform}: {len(items)} items fetched")
    return items


# ---------------------------------------------------------------------------
# Metric aggregation
# ---------------------------------------------------------------------------


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def aggregate(platform: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute totals and pick top posts. Schema varies by platform."""
    if not items:
        return {
            "platform": platform,
            "post_count": 0,
            "followers": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "views": 0,
            "engagement_rate": 0.0,
            "top_posts": [],
        }

    likes = comments = shares = views = followers = 0
    scored: list[tuple[int, dict[str, Any]]] = []

    for item in items:
        if platform == "instagram":
            l = safe_int(item.get("likesCount"))
            c = safe_int(item.get("commentsCount"))
            s = safe_int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)
            v = safe_int(item.get("videoViewCount"))
            followers = max(followers, safe_int(item.get("ownerFollowersCount")))
            caption = (item.get("caption") or "")[:80]
            post_type = item.get("type", "Post")
        elif platform == "tiktok":
            l = safe_int(item.get("diggCount"))
            c = safe_int(item.get("commentCount"))
            s = safe_int(item.get("shareCount"))
            v = safe_int(item.get("playCount"))
            author = item.get("authorMeta") or {}
            followers = max(followers, safe_int(author.get("fans")))
            caption = (item.get("text") or "")[:80]
            post_type = "Video"
        elif platform == "youtube":
            l = safe_int(item.get("likes"))
            c = safe_int(item.get("commentsCount"))
            s = 0
            v = safe_int(item.get("viewCount"))
            followers = max(followers, safe_int(item.get("numberOfSubscribers")))
            caption = (item.get("title") or "")[:80]
            post_type = "Video"
        elif platform == "facebook":
            l = safe_int(item.get("likes") or item.get("likesCount"))
            c = safe_int(item.get("comments") or item.get("commentsCount"))
            s = safe_int(item.get("shares") or item.get("sharesCount"))
            v = 0
            page = item.get("pageInfo") or {}
            followers = max(followers, safe_int(page.get("followers") or page.get("likes")))
            caption = (item.get("text") or "")[:80]
            post_type = "Post"
        else:
            continue

        likes += l
        comments += c
        shares += s
        views += v
        engagement_score = l + c * 3 + s * 5  # weight engagement signals
        scored.append((engagement_score, {
            "caption": caption.strip() or "(no caption)",
            "type": post_type,
            "likes": l,
            "comments": c,
            "shares": s,
            "views": v,
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_posts = [post for _, post in scored[:4]]
    total_engagement = likes + comments + shares
    rate = (total_engagement / followers * 100) if followers else 0.0

    return {
        "platform": platform,
        "post_count": len(items),
        "followers": followers,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "views": views,
        "engagement_rate": round(rate, 2),
        "top_posts": top_posts,
    }


# ---------------------------------------------------------------------------
# HTML rendering (ASCII-safe)
# ---------------------------------------------------------------------------


EMOJI = {
    "chart": "&#128202;",
    "trend": "&#128200;",
    "bulb": "&#128161;",
    "target": "&#127919;",
    "loop": "&#128260;",
    "rocket": "&#128640;",
    "calendar": "&#128197;",
    "trophy": "&#127942;",
}


def fmt(n: int) -> str:
    """Format a number with thousand separators."""
    return f"{n:,}"


def metric_card(label: str, value: str, accent: bool = False, sub: str = "") -> str:
    cls = "metric-card accent" if accent else "metric-card"
    return f"""
        <div class="{cls}">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-change">{sub}</div>
        </div>"""


def platform_section(name: str, label: str, data: dict[str, Any]) -> str:
    rows = ""
    for post in data["top_posts"]:
        rows += f"""
                        <tr>
                            <td>{post['caption']}</td>
                            <td>{post['type']}</td>
                            <td>{fmt(post['likes'])}</td>
                            <td>{fmt(post['comments'])}</td>
                            <td>{fmt(post['shares'])}</td>
                            <td>{fmt(post['views'])}</td>
                        </tr>"""
    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;color:#999">No data available this run</td></tr>'

    note = ""
    if data["post_count"] == 0:
        note = '<p style="color:#999;font-style:italic;margin-bottom:1rem">Scraper returned no data for this platform on this run.</p>'

    return f"""
        <div id="{name}" class="tab-content">
            {note}
            <div class="metrics-grid">
                {metric_card("Followers", fmt(data["followers"]) if data["followers"] else "&mdash;")}
                {metric_card("Total Engagements", fmt(data["likes"] + data["comments"] + data["shares"]), accent=True)}
                {metric_card("Posts Analyzed", str(data["post_count"]))}
                {metric_card("Engagement Rate", f"{data['engagement_rate']}%" if data["followers"] else "&mdash;", accent=True)}
            </div>
            <div class="top-posts-table">
                <h3>{EMOJI["trophy"]} Top Performing Posts</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Content</th><th>Type</th><th>Likes</th>
                            <th>Comments</th><th>Shares</th><th>Views</th>
                        </tr>
                    </thead>
                    <tbody>{rows}
                    </tbody>
                </table>
            </div>
        </div>"""


def build_html(metrics: dict[str, dict[str, Any]], date_range: str) -> str:
    overview_total_engagement = sum(
        m["likes"] + m["comments"] + m["shares"] for m in metrics.values()
    )
    overview_total_followers = sum(m["followers"] for m in metrics.values())
    overview_total_posts = sum(m["post_count"] for m in metrics.values())

    # Bar chart data: engagements by platform
    chart_labels = json.dumps([p.title() for p in metrics])
    chart_values = json.dumps([
        m["likes"] + m["comments"] + m["shares"] for m in metrics.values()
    ])

    platform_tabs = "".join(
        platform_section(name, name.title(), data) for name, data in metrics.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The Autism Helper &mdash; Weekly Social Media Dashboard</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background: #f5f7fa; color: #333; line-height: 1.6; }}
        header {{ background: linear-gradient(135deg, {PRIMARY} 0%, #0f5a6a 100%); color: white; padding: 2rem; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        header h1 {{ font-size: 2.5rem; margin-bottom: 0.5rem; }}
        header p {{ font-size: 1.1rem; opacity: 0.9; }}
        .container {{ max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }}
        .tabs {{ display: flex; gap: 0.5rem; margin-bottom: 2rem; border-bottom: 2px solid #e0e0e0; flex-wrap: wrap; }}
        .tab-button {{ padding: 1rem 1.5rem; background: white; border: none; cursor: pointer; font-size: 1rem; font-weight: 500; color: #666; border-bottom: 3px solid transparent; margin-bottom: -2px; }}
        .tab-button:hover {{ color: {PRIMARY}; }}
        .tab-button.active {{ color: white; background: {PRIMARY}; border-bottom-color: {ACCENT}; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem; margin-bottom: 2rem; }}
        .metric-card {{ background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-left: 4px solid {PRIMARY}; }}
        .metric-card.accent {{ border-left-color: {ACCENT}; }}
        .metric-label {{ font-size: 0.9rem; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 0.5rem; }}
        .metric-value {{ font-size: 2rem; font-weight: bold; color: {PRIMARY}; }}
        .metric-card.accent .metric-value {{ color: {ACCENT}; }}
        .metric-change {{ font-size: 0.85rem; color: #666; margin-top: 0.5rem; }}
        .chart-container {{ background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 2rem; height: 350px; }}
        .chart-container h3 {{ margin-bottom: 1rem; color: {PRIMARY}; font-size: 1.2rem; }}
        .chart-wrapper {{ position: relative; height: 280px; }}
        .top-posts-table {{ background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 2rem; overflow-x: auto; }}
        .top-posts-table h3 {{ margin-bottom: 1rem; color: {PRIMARY}; font-size: 1.2rem; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ background: #f5f5f5; padding: 0.75rem; text-align: left; border-bottom: 2px solid #e0e0e0; }}
        td {{ padding: 0.75rem; border-bottom: 1px solid #e0e0e0; }}
        tr:hover {{ background: #fafafa; }}
        footer {{ text-align: center; padding: 2rem; color: #999; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <header>
        <h1>The Autism Helper</h1>
        <p>Weekly Social Media Dashboard &mdash; {date_range}</p>
    </header>
    <div class="container">
        <div class="tabs">
            <button class="tab-button active" onclick="switchTab(event, 'overview')">{EMOJI["trend"]} Overview</button>
            <button class="tab-button" onclick="switchTab(event, 'instagram')">Instagram</button>
            <button class="tab-button" onclick="switchTab(event, 'tiktok')">TikTok</button>
            <button class="tab-button" onclick="switchTab(event, 'youtube')">YouTube</button>
            <button class="tab-button" onclick="switchTab(event, 'facebook')">Facebook</button>
        </div>
        <div id="overview" class="tab-content active">
            <div class="metrics-grid">
                {metric_card("Total Followers", fmt(overview_total_followers))}
                {metric_card("Total Engagements", fmt(overview_total_engagement), accent=True)}
                {metric_card("Posts Analyzed", str(overview_total_posts))}
                {metric_card("Platforms Covered", str(sum(1 for m in metrics.values() if m["post_count"] > 0)), accent=True)}
            </div>
            <div class="chart-container">
                <h3>{EMOJI["chart"]} Engagement by Platform</h3>
                <div class="chart-wrapper"><canvas id="platformChart"></canvas></div>
            </div>
        </div>
        {platform_tabs}
    </div>
    <footer>
        <p>Dashboard generated {datetime.utcnow().strftime("%B %d, %Y at %H:%M UTC")} &mdash; Live data from Apify</p>
    </footer>
    <script>
        function switchTab(evt, name) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-button').forEach(el => el.classList.remove('active'));
            document.getElementById(name).classList.add('active');
            evt.currentTarget.classList.add('active');
        }}
        new Chart(document.getElementById('platformChart'), {{
            type: 'bar',
            data: {{
                labels: {chart_labels},
                datasets: [{{
                    label: 'Engagements',
                    data: {chart_values},
                    backgroundColor: ['{PRIMARY}', '{ACCENT}', '{PRIMARY}', '{ACCENT}']
                }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ beginAtZero: true }} }} }}
        }});
    </script>
</body>
</html>"""

    return html


def sanitize_to_ascii(html: str) -> str:
    """Replace any non-ASCII char with its numeric HTML entity."""
    out = []
    for ch in html:
        code = ord(ch)
        if code < 128:
            out.append(ch)
        else:
            out.append(f"&#{code};")
    return "".join(out)


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------


def send_slack(metrics: dict[str, dict[str, Any]], date_range: str) -> None:
    if not SLACK_WEBHOOK_URL:
        print("No SLACK_WEBHOOK_URL set; skipping Slack notification")
        return

    top_platform = max(
        metrics.items(),
        key=lambda kv: kv[1]["likes"] + kv[1]["comments"] + kv[1]["shares"],
    )
    top_name = top_platform[0].title()
    top_engagement = (
        top_platform[1]["likes"]
        + top_platform[1]["comments"]
        + top_platform[1]["shares"]
    )

    text = (
        f":bar_chart: *Weekly Social Media Dashboard - {date_range}*\n\n"
        f"Highlights:\n"
        f"- {top_name} led the week with {top_engagement:,} total engagements\n"
        f"- Total posts analyzed across platforms: "
        f"{sum(m['post_count'] for m in metrics.values())}\n"
        f"- Total followers across platforms: "
        f"{sum(m['followers'] for m in metrics.values()):,}\n\n"
        f":chart_with_upwards_trend: View the full dashboard: {DASHBOARD_URL}"
    )

    try:
        requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
        print("Slack notification sent")
    except requests.RequestException as exc:
        print(f"Slack notification failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not APIFY_TOKEN:
        print("ERROR: APIFY_TOKEN environment variable is required", file=sys.stderr)
        return 1

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=6)
    date_range = f"{week_start.strftime('%B %d')} - {today.strftime('%B %d, %Y')}"

    metrics: dict[str, dict[str, Any]] = {}
    for platform in ACTORS:
        items = scrape_platform(platform)
        metrics[platform] = aggregate(platform, items)

    html = build_html(metrics, date_range)
    html = sanitize_to_ascii(html)

    # Verify zero non-ASCII bytes
    non_ascii = sum(1 for ch in html if ord(ch) > 127)
    if non_ascii:
        print(f"ERROR: {non_ascii} non-ASCII chars remain", file=sys.stderr)
        return 1

    with open("index.html", "w", encoding="ascii") as fh:
        fh.write(html)
    print(f"Wrote index.html ({len(html):,} bytes, ASCII-clean)")

    send_slack(metrics, date_range)
    return 0


if __name__ == "__main__":
    sys.exit(main())

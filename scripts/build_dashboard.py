#!/usr/bin/env python3
"""
Build a social media dashboard for The Autism Helper.
 
This script:
1. Calls Apify API to scrape Instagram, TikTok, YouTube, and Facebook data
2. Aggregates metrics
3. Generates an HTML dashboard
4. Sends a Slack notification
"""
 
import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from html import escape as html_escape
 
 
# ============================================================================
# CONFIGURATION
# ============================================================================
 
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
 
APIFY_API_BASE = "https://api.apify.com/v2"
 
PLATFORMS = {
    "instagram": {
        "actor_id": "shu8hvrXbJbY3Eb9W",
        "input": {
            "directUrls": ["https://www.instagram.com/theautismhelper/"],
            "resultsLimit": 30,
        },
        "color": "#E1306C",
    },
    "tiktok": {
        "actor_id": "clockworks~free-tiktok-scraper",
        "input": {
            "profiles": ["https://www.tiktok.com/@theautismhelper"],
            "resultsPerPage": 30,
        },
        "color": "#000000",
    },
    "youtube": {
        "actor_id": "bernardo~youtube-scraper",
        "input": {
            "startUrls": [{"url": "https://www.youtube.com/@theautismhelper"}],
            "maxResults": 20,
        },
        "color": "#FF0000",
    },
    "facebook": {
        "actor_id": "apify~facebook-posts-scraper",
        "input": {
            "startUrls": [{"url": "https://www.facebook.com/theautismhelper"}],
            "resultsLimit": 30,
        },
        "color": "#1877F2",
    },
}
 
POLL_INTERVAL = 30  # seconds
MAX_WAIT_TIME = 15 * 60  # 15 minutes
 
BRAND_PRIMARY = "#148496"
BRAND_ACCENT = "#F1592E"
 
 
# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
 
 
def find_value(item: Dict[str, Any], *keys: str) -> int:
    """
    Try multiple dot-notation keys and return the first non-zero int.
 
    Example:
        find_value(item, "likesCount", "likes", "engagement.likes")
    """
    for key in keys:
        try:
            parts = key.split(".")
            value = item
            for part in parts:
                value = value[part]
 
            if isinstance(value, (int, float)) and value != 0:
                return int(value)
        except (KeyError, TypeError, ValueError):
            continue
 
    return 0
 
 
def filter_valid_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter out items with error keys."""
    return [
        item
        for item in items
        if "error" not in item and "errorDescription" not in item
    ]
 
 
def sanitize_to_ascii(html: str) -> str:
    """Replace every char with ord > 127 with &#NNNNN; entity."""
    result = []
    for char in html:
        if ord(char) > 127:
            result.append(f"&#{ord(char)};")
        else:
            result.append(char)
    return "".join(result)
 
 
def log(message: str):
    """Print a timestamped log message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")
 
 
# ============================================================================
# APIFY API FUNCTIONS
# ============================================================================
 
 
def call_apify_actor(actor_id: str, input_data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """
    Call an Apify actor and poll for results.
    Returns the items list or None if failed.
    """
    headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}
 
    # Start the actor run
    run_url = f"{APIFY_API_BASE}/actors/{actor_id}/runs"
    start_response = requests.post(
        run_url,
        json={"input": input_data},
        headers=headers,
        timeout=30,
    )
 
    if start_response.status_code not in (200, 201):
        log(f"Failed to start actor {actor_id}: {start_response.status_code}")
        return None
 
    run_data = start_response.json()
    run_id = run_data.get("data", {}).get("id")
 
    if not run_id:
        log(f"No run ID returned for actor {actor_id}")
        return None
 
    log(f"Run started: {run_id}")
 
    # Poll for completion
    status_url = f"{APIFY_API_BASE}/actor-runs/{run_id}"
    start_time = time.time()
 
    while True:
        elapsed = time.time() - start_time
 
        if elapsed > MAX_WAIT_TIME:
            log(f"Timeout waiting for actor {actor_id}")
            return None
 
        status_response = requests.get(status_url, headers=headers, timeout=30)
 
        if status_response.status_code != 200:
            log(f"Failed to check status for {run_id}: {status_response.status_code}")
            return None
 
        status_data = status_response.json()
        status = status_data.get("data", {}).get("status")
 
        if status == "SUCCEEDED":
            break
        elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
            log(f"Actor {actor_id} run {run_id} ended with status: {status}")
            return None
 
        # Still running, wait and retry
        time.sleep(POLL_INTERVAL)
 
    # Get the results
    dataset_id = status_data.get("data", {}).get("defaultDatasetId")
 
    if not dataset_id:
        log(f"No dataset ID returned for {run_id}")
        return None
 
    items_url = f"{APIFY_API_BASE}/datasets/{dataset_id}/items"
    items_response = requests.get(items_url, headers=headers, timeout=30)
 
    if items_response.status_code != 200:
        log(f"Failed to fetch items from {dataset_id}: {items_response.status_code}")
        return None
 
    items = items_response.json()
    return items if isinstance(items, list) else []
 
 
def scrape_all_platforms() -> Dict[str, List[Dict[str, Any]]]:
    """Scrape all platforms, continuing even if some fail."""
    results = {}
 
    for platform, config in PLATFORMS.items():
        log(f"Starting {platform} scraper... (Actor: {config['actor_id']})")
 
        items = call_apify_actor(config["actor_id"], config["input"])
 
        if items is None:
            log(f"{platform}: FAILED")
            results[platform] = []
            continue
 
        # Filter valid items
        valid_items = filter_valid_items(items)
        log(f"{platform}: {len(valid_items)} items fetched")
 
        # Log sample keys
        if valid_items:
            sample_keys = sorted(valid_items[0].keys())[:25]
            log(f"{platform} sample keys: {sample_keys}")
 
        results[platform] = valid_items
 
    return results
 
 
# ============================================================================
# DATA AGGREGATION
# ============================================================================
 
 
def aggregate_instagram(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate Instagram data."""
    posts = []
 
    for item in items:
        likes = find_value(item, "likesCount", "likes")
        comments = find_value(item, "commentsCount", "comments")
        views = find_value(item, "videoViewCount", "videoPlayCount")
        caption = item.get("caption") or item.get("text") or ""
 
        engagement = likes + (comments * 3) + (views * 5)
 
        posts.append({
            "url": item.get("url") or item.get("postUrl") or "",
            "caption": caption[:100] + "..." if len(caption) > 100 else caption,
            "likes": likes,
            "comments": comments,
            "shares": 0,
            "views": views,
            "engagement": engagement,
            "timestamp": item.get("timestamp") or datetime.now().isoformat(),
        })
 
    # Top 5 by engagement
    top_posts = sorted(posts, key=lambda x: x["engagement"], reverse=True)[:5]
 
    total_likes = sum(p["likes"] for p in posts)
    total_comments = sum(p["comments"] for p in posts)
    total_shares = sum(p["shares"] for p in posts)
    total_views = sum(p["views"] for p in posts)
    total_engagement = total_likes + (total_comments * 3) + (total_shares * 5)
 
    return {
        "platform": "Instagram",
        "posts_analyzed": len(posts),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "total_views": total_views,
        "total_engagement": total_engagement,
        "top_posts": top_posts,
    }
 
 
def aggregate_tiktok(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate TikTok data."""
    posts = []
 
    for item in items:
        likes = find_value(item, "diggCount", "likesCount", "likes")
        comments = find_value(item, "commentCount", "commentsCount")
        shares = find_value(item, "shareCount", "sharesCount")
        views = find_value(item, "playCount", "plays")
        caption = item.get("text") or item.get("desc") or ""
 
        engagement = likes + (comments * 3) + (shares * 5)
 
        posts.append({
            "url": item.get("url") or item.get("videoUrl") or "",
            "caption": caption[:100] + "..." if len(caption) > 100 else caption,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "views": views,
            "engagement": engagement,
            "timestamp": item.get("createTime") or item.get("timestamp") or datetime.now().isoformat(),
        })
 
    # Top 5 by engagement
    top_posts = sorted(posts, key=lambda x: x["engagement"], reverse=True)[:5]
 
    total_likes = sum(p["likes"] for p in posts)
    total_comments = sum(p["comments"] for p in posts)
    total_shares = sum(p["shares"] for p in posts)
    total_views = sum(p["views"] for p in posts)
    total_engagement = total_likes + (total_comments * 3) + (total_shares * 5)
 
    return {
        "platform": "TikTok",
        "posts_analyzed": len(posts),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "total_views": total_views,
        "total_engagement": total_engagement,
        "top_posts": top_posts,
    }
 
 
def aggregate_facebook(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate Facebook data."""
    posts = []
 
    for item in items:
        likes = find_value(item, "likes", "likesCount", "topReactionsCount")
        comments = find_value(item, "comments", "commentsCount")
        shares = find_value(item, "shares", "sharesCount")
        caption = item.get("text") or item.get("message") or ""
 
        engagement = likes + (comments * 3) + (shares * 5)
 
        posts.append({
            "url": item.get("url") or item.get("postUrl") or "",
            "caption": caption[:100] + "..." if len(caption) > 100 else caption,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "views": 0,  # Facebook typically doesn't expose view counts via scraping
            "engagement": engagement,
            "timestamp": item.get("timestamp") or item.get("createdTime") or datetime.now().isoformat(),
        })
 
    # Top 5 by engagement
    top_posts = sorted(posts, key=lambda x: x["engagement"], reverse=True)[:5]
 
    total_likes = sum(p["likes"] for p in posts)
    total_comments = sum(p["comments"] for p in posts)
    total_shares = sum(p["shares"] for p in posts)
    total_views = sum(p["views"] for p in posts)
    total_engagement = total_likes + (total_comments * 3) + (total_shares * 5)
 
    return {
        "platform": "Facebook",
        "posts_analyzed": len(posts),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "total_views": total_views,
        "total_engagement": total_engagement,
        "top_posts": top_posts,
    }
 
 
def aggregate_youtube(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate YouTube data."""
    posts = []
 
    for item in items:
        likes = find_value(item, "likes", "likesCount", "likeCount")
        comments = find_value(item, "commentsCount", "commentCount", "numberOfComments", "comments")
        shares = 0
        views = find_value(item, "viewCount", "views", "viewsCount")
        caption = item.get("title") or item.get("text") or ""
 
        engagement = likes + (comments * 3)
 
        posts.append({
            "url": item.get("url") or item.get("videoUrl") or "",
            "caption": caption[:100] + "..." if len(caption) > 100 else caption,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "views": views,
            "engagement": engagement,
            "timestamp": item.get("date") or item.get("uploadDate") or datetime.now().isoformat(),
        })
 
    top_posts = sorted(posts, key=lambda x: x["engagement"], reverse=True)[:5]
 
    total_likes = sum(p["likes"] for p in posts)
    total_comments = sum(p["comments"] for p in posts)
    total_shares = sum(p["shares"] for p in posts)
    total_views = sum(p["views"] for p in posts)
    total_engagement = total_likes + (total_comments * 3)
 
    return {
        "platform": "YouTube",
        "posts_analyzed": len(posts),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "total_views": total_views,
        "total_engagement": total_engagement,
        "top_posts": top_posts,
    }
 
 
def aggregate_data(raw_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate all platform data."""
    aggregated = {}
 
    if raw_data.get("instagram"):
        aggregated["instagram"] = aggregate_instagram(raw_data["instagram"])
 
    if raw_data.get("tiktok"):
        aggregated["tiktok"] = aggregate_tiktok(raw_data["tiktok"])
 
    if raw_data.get("youtube"):
        aggregated["youtube"] = aggregate_youtube(raw_data["youtube"])
 
    if raw_data.get("facebook"):
        aggregated["facebook"] = aggregate_facebook(raw_data["facebook"])
 
    return aggregated
 
 
# ============================================================================
# INSIGHTS AND ACTION ITEMS
# ============================================================================
 
 
def generate_insights(aggregated: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Generate insight cards based on data."""
    insights = {}
 
    # Find platforms by engagement
    engagement_by_platform = {
        k: v["total_engagement"] for k, v in aggregated.items()
    }
 
    if not engagement_by_platform:
        insights["whats_working"] = "Collect data from platforms to see what's working."
        insights["opportunities"] = "Analyze your content strategy across platforms."
        insights["watch_list"] = "Monitor your top-performing posts."
        return insights
 
    # What's Working
    top_platform = max(engagement_by_platform, key=engagement_by_platform.get)
    top_platform_data = aggregated[top_platform]
    top_post = (
        top_platform_data["top_posts"][0]
        if top_platform_data["top_posts"]
        else {"caption": "content"}
    )
    platform_display = top_platform.capitalize()
    insights[
        "whats_working"
    ] = f"{platform_display} is driving the highest engagement with {top_platform_data['total_engagement']} total interactions. Your audience is particularly responsive to this platform. Keep leveraging '{top_post['caption']}' style content."
 
    # Opportunities
    bottom_platform = min(engagement_by_platform, key=engagement_by_platform.get)
    bottom_platform_data = aggregated[bottom_platform]
    platform_display = bottom_platform.capitalize()
    insights[
        "opportunities"
    ] = f"{platform_display} has room for growth with {bottom_platform_data['total_engagement']} interactions. Consider experimenting with different content formats or posting schedules to increase engagement on this platform."
 
    # Watch List
    all_posts = []
    for platform_data in aggregated.values():
        all_posts.extend(platform_data["top_posts"])
 
    if all_posts:
        watch_post = max(all_posts, key=lambda x: x["engagement"])
        total_reach = sum(
            p["total_engagement"] for p in aggregated.values()
        )
        insights[
            "watch_list"
        ] = f"Your top-performing post '{watch_post['caption']}' generated {watch_post['engagement']} interactions. Total reach across all platforms: {total_reach} engagements. This represents strong community connection."
    else:
        insights[
            "watch_list"
        ] = "Monitor your top-performing posts across all platforms to identify content patterns."
 
    return insights
 
 
def generate_action_items(platform: str) -> List[str]:
    """Generate contextual action items for each platform."""
    if platform.lower() == "instagram":
        return [
            "Create more Reels with educational autism strategies and sensory tips",
            "Develop carousel posts for classroom teachers with step-by-step implementation guides",
            "Collaborate with special education professionals for partnership content",
            "Post behind-the-scenes content showing curriculum development process",
        ]
    elif platform.lower() == "tiktok":
        return [
            "Produce short-form educational videos using trending sounds and hashtags",
            "Create autism awareness content and myth-busting videos",
            "Participate in duets with special education creators to expand reach",
            "Share quick sensory activity ideas in under 60 seconds",
        ]
    elif platform.lower() == "youtube":
        return [
            "Create longer-form video tutorials on autism strategies and classroom setups",
            "Add YouTube Shorts to capture TikTok-style quick tips for teachers",
            "Optimize video titles and thumbnails for search discoverability",
            "Build playlists by topic (sensory, communication, behavior) to increase watch time",
        ]
    elif platform.lower() == "facebook":
        return [
            "Host live Q&A sessions answering parent and teacher questions",
            "Encourage discussion in comments to build community engagement",
            "Share longer-form educational articles and case studies",
            "Create a Facebook Group for community discussion and resource sharing",
        ]
 
    return ["Continue monitoring and optimizing content performance"]
 
 
# ============================================================================
# HTML GENERATION
# ============================================================================
 
 
def generate_html(aggregated: Dict[str, Dict[str, Any]]) -> str:
    """Generate the complete HTML dashboard."""
 
    # Generate insights
    insights = generate_insights(aggregated)
 
    # Calculate overview metrics
    total_posts = sum(p["posts_analyzed"] for p in aggregated.values())
    total_engagements = sum(p["total_engagement"] for p in aggregated.values())
    total_views = sum(p["total_views"] for p in aggregated.values())
    platforms_reporting = len([p for p in aggregated.values() if p["posts_analyzed"] > 0])
 
    # Date range
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    date_range = f"{week_ago.strftime('%b %d')} - {today.strftime('%b %d, %Y')}"
 
    # Build platform engagement data for overview chart
    platform_colors = {
        "instagram": "#E1306C",
        "tiktok": "#000000",
        "youtube": "#FF0000",
        "facebook": "#1877F2",
    }
 
    engagement_by_platform = {
        k: v["total_engagement"] for k, v in aggregated.items() if v["total_engagement"] > 0
    }
 
    max_engagement = max(engagement_by_platform.values()) if engagement_by_platform else 1
 
    # HTML Structure
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>The Autism Helper - Social Media Dashboard</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
 
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background-color: #f8f9fa;
            color: #333;
        }}
 
        header {{
            background: linear-gradient(135deg, #148496 0%, #0f5a6a 100%);
            color: white;
            padding: 2rem;
            text-align: center;
        }}
 
        header h1 {{
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
        }}
 
        header p {{
            font-size: 1.1rem;
            opacity: 0.95;
        }}
 
        .container {{
            max-width: 1200px;
            margin: 2rem auto;
            padding: 0 1rem;
        }}
 
        /* TABS */
        .tabs {{
            display: flex;
            gap: 0;
            margin-bottom: 2rem;
            border-bottom: 2px solid #e0e0e0;
            background: white;
            border-radius: 8px 8px 0 0;
        }}
 
        .tab-button {{
            padding: 1rem 1.5rem;
            border: none;
            background: white;
            color: #666;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.3s ease;
            border-bottom: 3px solid transparent;
        }}
 
        .tab-button.active {{
            color: white;
            background: #148496;
            border-bottom-color: #F1592E;
        }}
 
        .tab-button:hover {{
            background: #f5f5f5;
        }}
 
        .tab-button.active:hover {{
            background: #148496;
        }}
 
        .tab-content {{
            display: none;
            animation: fadeIn 0.3s ease;
        }}
 
        .tab-content.active {{
            display: block;
        }}
 
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
        }}
 
        /* METRIC CARDS */
        .metrics-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
 
        .metric-card {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            border-left: 4px solid #148496;
        }}
 
        .metric-card.accent {{
            border-left-color: #F1592E;
        }}
 
        .metric-label {{
            font-size: 0.9rem;
            color: #666;
            margin-bottom: 0.5rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
 
        .metric-value {{
            font-size: 2rem;
            font-weight: bold;
            color: #148496;
        }}
 
        .metric-card.accent .metric-value {{
            color: #F1592E;
        }}
 
        /* INSIGHTS */
        .insights-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
 
        .insight-card {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
        }}
 
        .insight-card h4 {{
            color: #148496;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 1.1rem;
        }}
 
        .insight-card p {{
            color: #666;
            font-size: 0.95rem;
            line-height: 1.6;
        }}
 
        /* CHARTS */
        .chart-container {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            margin-bottom: 2rem;
        }}
 
        .chart-container h3 {{
            margin-bottom: 1.5rem;
            color: #148496;
            font-size: 1.2rem;
        }}
 
        /* BAR CHART */
        .bar-chart {{
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }}
 
        .bar-item {{
            display: flex;
            align-items: center;
            gap: 1rem;
        }}
 
        .bar-label {{
            width: 100px;
            font-weight: 600;
            color: #333;
            text-align: right;
        }}
 
        .bar-background {{
            flex: 1;
            background: #f0f0f0;
            border-radius: 4px;
            height: 30px;
            position: relative;
            overflow: hidden;
        }}
 
        .bar-fill {{
            height: 100%;
            border-radius: 4px;
            display: flex;
            align-items: center;
            padding-right: 0.75rem;
            color: white;
            font-weight: 600;
            font-size: 0.9rem;
            justify-content: flex-end;
        }}
 
        .bar-value {{
            width: 60px;
            text-align: right;
            color: #333;
            font-weight: 600;
        }}
 
        /* DOUGHNUT CHART */
        .doughnut-container {{
            display: flex;
            flex-direction: column;
            align-items: center;
            margin: 2rem 0;
        }}
 
        .doughnut-chart {{
            width: 200px;
            height: 200px;
            border-radius: 50%;
            position: relative;
        }}
 
        .doughnut-chart::after {{
            content: '';
            width: 120px;
            height: 120px;
            background: white;
            border-radius: 50%;
            position: absolute;
            top: 40px;
            left: 40px;
        }}
 
        .doughnut-legend {{
            display: flex;
            justify-content: center;
            gap: 1.5rem;
            margin-top: 1rem;
            flex-wrap: wrap;
        }}
 
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.85rem;
            color: #666;
        }}
 
        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}
 
        /* ACTION ITEMS */
        .action-items {{
            background: linear-gradient(135deg, #148496 0%, #0f5a6a 100%);
            color: white;
            padding: 1.5rem;
            border-radius: 8px;
            margin-bottom: 2rem;
        }}
 
        .action-items h3 {{
            margin-bottom: 1rem;
            font-size: 1.2rem;
        }}
 
        .action-items ul {{
            list-style: none;
        }}
 
        .action-items li {{
            margin-bottom: 0.75rem;
            padding-left: 1.5rem;
            position: relative;
        }}
 
        .action-items li:before {{
            content: '&#9654;';
            color: #F1592E;
            position: absolute;
            left: 0;
        }}
 
        /* TOP POSTS TABLE */
        .top-posts-table {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            margin-bottom: 2rem;
            overflow-x: auto;
        }}
 
        .top-posts-table h3 {{
            margin-bottom: 1rem;
            color: #148496;
        }}
 
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
 
        table th {{
            background: #f5f5f5;
            padding: 0.75rem;
            border-bottom: 2px solid #e0e0e0;
            text-align: left;
            font-weight: 600;
            color: #333;
            font-size: 0.9rem;
        }}
 
        table td {{
            padding: 0.75rem;
            border-bottom: 1px solid #e0e0e0;
            color: #666;
            font-size: 0.9rem;
        }}
 
        table tr:hover {{
            background: #f9f9f9;
        }}
 
        .no-data {{
            text-align: center;
            padding: 2rem;
            color: #999;
        }}
 
        footer {{
            text-align: center;
            padding: 2rem;
            color: #999;
            font-size: 0.9rem;
        }}
 
        @media (max-width: 768px) {{
            header h1 {{
                font-size: 1.8rem;
            }}
 
            .metrics-row {{
                grid-template-columns: 1fr;
            }}
 
            .insights-grid {{
                grid-template-columns: 1fr;
            }}
 
            .tabs {{
                flex-wrap: wrap;
            }}
 
            .tab-button {{
                flex: 1;
                min-width: 100px;
            }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>The Autism Helper</h1>
        <p>Weekly Social Media Dashboard &#8212; {html_escape(date_range)}</p>
    </header>
 
    <div class="container">
        <!-- TABS -->
        <div class="tabs">
            <button class="tab-button active" data-tab="overview">Overview</button>
            <button class="tab-button" data-tab="instagram">Instagram</button>
            <button class="tab-button" data-tab="tiktok">TikTok</button>
            <button class="tab-button" data-tab="youtube">YouTube</button>
            <button class="tab-button" data-tab="facebook">Facebook</button>
        </div>
 
        <!-- OVERVIEW TAB -->
        <div id="overview" class="tab-content active">
            <!-- Metric Cards -->
            <div class="metrics-row">
                <div class="metric-card accent">
                    <div class="metric-label">Total Engagements</div>
                    <div class="metric-value">{total_engagements:,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Posts Analyzed</div>
                    <div class="metric-value">{total_posts}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Views</div>
                    <div class="metric-value">{total_views:,}</div>
                </div>
                <div class="metric-card accent">
                    <div class="metric-label">Platforms Reporting</div>
                    <div class="metric-value">{platforms_reporting}</div>
                </div>
            </div>
 
            <!-- Engagement by Platform Chart -->
            <div class="chart-container">
                <h3>Engagement by Platform</h3>
                <div class="bar-chart">
"""
 
    # Add bars for each platform with engagement
    for platform, engagement in sorted(engagement_by_platform.items(), key=lambda x: x[1], reverse=True):
        bar_width = (engagement / max_engagement * 100) if max_engagement > 0 else 0
        color = platform_colors.get(platform, "#148496")
        platform_name = platform.capitalize()
        html += f"""                    <div class="bar-item">
                        <div class="bar-label">{platform_name}</div>
                        <div class="bar-background">
                            <div class="bar-fill" style="width: {bar_width}%; background-color: {color};">
                            </div>
                        </div>
                        <div class="bar-value">{engagement:,}</div>
                    </div>
"""
 
    html += """                </div>
            </div>
 
            <!-- Insights -->
            <div class="insights-grid">
                <div class="insight-card">
                    <h4>&#128161; What's Working</h4>
                    <p>"""
 
    html += html_escape(insights.get("whats_working", ""))
    html += """</p>
                </div>
                <div class="insight-card">
                    <h4>&#127919; Opportunities</h4>
                    <p>"""
 
    html += html_escape(insights.get("opportunities", ""))
    html += """</p>
                </div>
                <div class="insight-card">
                    <h4>&#128260; Watch List</h4>
                    <p>"""
 
    html += html_escape(insights.get("watch_list", ""))
    html += """</p>
                </div>
            </div>
        </div>
 
"""
 
    # PLATFORM TABS
    for platform_key, platform_data in aggregated.items():
        platform_name = platform_data["platform"]
        tab_id = platform_key
 
        # Calculate likes/comments/shares distribution
        total_interactions = (
            platform_data["total_likes"]
            + platform_data["total_comments"]
            + platform_data["total_shares"]
        )
 
        if total_interactions > 0:
            likes_pct = (platform_data["total_likes"] / total_interactions) * 100
            comments_pct = (platform_data["total_comments"] / total_interactions) * 100
            shares_pct = (platform_data["total_shares"] / total_interactions) * 100
        else:
            likes_pct = comments_pct = shares_pct = 0
 
        # Determine doughnut colors
        likes_color = "#148496"
        comments_color = "#F1592E"
        shares_color = "#66BB6A"
 
        # Conic gradient for doughnut
        conic_gradient = f"conic-gradient({likes_color} 0% {likes_pct}%, {comments_color} {likes_pct}% {likes_pct + comments_pct}%, {shares_color} {likes_pct + comments_pct}% 100%)"
 
        html += f"""        <!-- {platform_name.upper()} TAB -->
        <div id="{tab_id}" class="tab-content">
            <!-- Metric Cards -->
            <div class="metrics-row">
                <div class="metric-card">
                    <div class="metric-label">Total Likes</div>
                    <div class="metric-value">{platform_data['total_likes']:,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Comments</div>
                    <div class="metric-value">{platform_data['total_comments']:,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Total Shares/Views</div>
                    <div class="metric-value">{max(platform_data['total_shares'], platform_data['total_views']):,}</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Posts Analyzed</div>
                    <div class="metric-value">{platform_data['posts_analyzed']}</div>
                </div>
            </div>
 
            <!-- Doughnut Chart -->
            <div class="chart-container">
                <h3>Engagement Distribution</h3>
                <div class="doughnut-container">
                    <div class="doughnut-chart" style="background: {conic_gradient};"></div>
                    <div class="doughnut-legend">
                        <div class="legend-item">
                            <div class="legend-dot" style="background: {likes_color};"></div>
                            <span>Likes ({likes_pct:.0f}%)</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-dot" style="background: {comments_color};"></div>
                            <span>Comments ({comments_pct:.0f}%)</span>
                        </div>
                        <div class="legend-item">
                            <div class="legend-dot" style="background: {shares_color};"></div>
                            <span>Shares ({shares_pct:.0f}%)</span>
                        </div>
                    </div>
                </div>
            </div>
 
            <!-- Top Posts Table -->
            <div class="top-posts-table">
                <h3>Top Performing Posts</h3>
"""
 
        if platform_data["top_posts"]:
            html += """                <table>
                    <thead>
                        <tr>
                            <th>Content</th>
                            <th>Likes</th>
                            <th>Comments</th>
                            <th>Shares</th>
                            <th>Views</th>
                        </tr>
                    </thead>
                    <tbody>
"""
 
            for post in platform_data["top_posts"]:
                caption = html_escape(post["caption"])
                html += f"""                        <tr>
                            <td>{caption}</td>
                            <td>{post['likes']:,}</td>
                            <td>{post['comments']:,}</td>
                            <td>{post['shares']:,}</td>
                            <td>{post['views']:,}</td>
                        </tr>
"""
 
            html += """                    </tbody>
                </table>
"""
        else:
            html += """                <div class="no-data">No posts data available</div>
"""
 
        html += """            </div>
 
            <!-- Action Items -->
            <div class="action-items">
                <h3>Action Items</h3>
                <ul>
"""
 
        action_items = generate_action_items(platform_name)
        for item in action_items:
            html += f"""                    <li>{html_escape(item)}</li>
"""
 
        html += """                </ul>
            </div>
        </div>
 
"""
 
    # Footer
    html += """    </div>
 
    <footer>
        <p>Generated on """
 
    html += datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html += """ UTC</p>
    </footer>
 
    <script>
        // Tab switching
        document.querySelectorAll('.tab-button').forEach(button => {
            button.addEventListener('click', function() {
                // Remove active class from all buttons and content
                document.querySelectorAll('.tab-button').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
 
                // Add active class to clicked button and corresponding content
                this.classList.add('active');
                const tabId = this.getAttribute('data-tab');
                document.getElementById(tabId).classList.add('active');
            });
        });
    </script>
</body>
</html>
"""
 
    return html
 
 
# ============================================================================
# SLACK NOTIFICATION
# ============================================================================
 
 
def send_slack_notification(aggregated: Dict[str, Dict[str, Any]]):
    """Send a Slack webhook notification with summary."""
 
    if not SLACK_WEBHOOK_URL:
        log("SLACK_WEBHOOK_URL not set, skipping notification")
        return
 
    # Calculate summary stats
    total_posts = sum(p["posts_analyzed"] for p in aggregated.values())
    total_engagements = sum(p["total_engagement"] for p in aggregated.values())
 
    # Find platform with highest engagement
    engagement_by_platform = {
        k: v["total_engagement"] for k, v in aggregated.items()
    }
 
    if engagement_by_platform:
        top_platform = max(engagement_by_platform, key=engagement_by_platform.get)
        top_platform_display = top_platform.capitalize()
    else:
        top_platform_display = "Unknown"
 
    # Date range
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    date_range = f"{week_ago.strftime('%b %d')} - {today.strftime('%b %d, %Y')}"
 
    # Build message
    message = {
        "text": "The Autism Helper - Weekly Social Media Dashboard",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "The Autism Helper - Weekly Social Media Dashboard",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Date Range*\n{date_range}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Top Platform*\n{top_platform_display}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Posts Analyzed*\n{total_posts}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Total Engagements*\n{total_engagements:,}",
                    },
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "View Dashboard",
                            "emoji": True,
                        },
                        "url": "https://sashatheautismhelper.github.io/social-media-dashboard/",
                    },
                ],
            },
        ],
    }
 
    try:
        response = requests.post(
            SLACK_WEBHOOK_URL,
            json=message,
            timeout=30,
        )
 
        if response.status_code == 200:
            log("Slack notification sent successfully")
        else:
            log(f"Failed to send Slack notification: {response.status_code}")
    except Exception as e:
        log(f"Error sending Slack notification: {e}")
 
 
# ============================================================================
# MAIN
# ============================================================================
 
 
def main():
    """Main function."""
 
    if not APIFY_TOKEN:
        log("ERROR: APIFY_TOKEN environment variable not set")
        sys.exit(1)
 
    log("=" * 80)
    log("Starting Social Media Dashboard Build")
    log("=" * 80)
 
    # Scrape all platforms
    raw_data = scrape_all_platforms()
 
    # Aggregate data
    aggregated = aggregate_data(raw_data)
 
    # Print summary table
    log("")
    log("SUMMARY")
    log("-" * 80)
    log(f"{'Platform':<15} {'Posts':<10} {'Engagements':<15} {'Views':<15}")
    log("-" * 80)
 
    for platform, data in aggregated.items():
        log(
            f"{platform.capitalize():<15} {data['posts_analyzed']:<10} "
            f"{data['total_engagement']:<15} {data['total_views']:<15}"
        )
 
    log("-" * 80)
    log("")
 
    # Generate HTML
    html = generate_html(aggregated)
 
    # Sanitize to ASCII
    html = sanitize_to_ascii(html)
 
    # Verify no non-ASCII characters remain
    non_ascii = [ord(c) for c in html if ord(c) > 127]
    if non_ascii:
        log(f"WARNING: Found {len(non_ascii)} non-ASCII characters")
    else:
        log("ASCII sanitization: VERIFIED (zero non-ASCII characters)")
 
    # Write HTML file
    output_path = "index.html"
 
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
 
    file_size = len(html)
    log(f"Wrote index.html ({file_size} bytes, ASCII-clean)")
 
    # Send Slack notification
    send_slack_notification(aggregated)
 
    log("=" * 80)
    log("Dashboard build complete!")
    log("=" * 80)
 
 
if __name__ == "__main__":
    main()
 

#!/usr/bin/env python3
import os
import random
import time
import requests
from datetime import datetime, timezone

API_BASE = "https://api.twitterapi.io"

# ── Pagination / volume knobs ─────────────────────────────────
MAX_PAGES_TOP     = int(os.getenv('DISCOVER_MAX_PAGES_TOP', '3'))
MAX_PAGES_LATEST  = int(os.getenv('DISCOVER_MAX_PAGES_LATEST', '4'))
MAX_ITEMS_PER_QUERY = int(os.getenv('DISCOVER_MAX_ITEMS', '120'))
STOP_IF_OLDER_HOURS = int(os.getenv('DISCOVER_STOP_OLDER_H', '48'))

# ── Category queries ──────────────────────────────────────────
# Each category now has TWO queries: Top (high-engagement) and Latest (fresh)
CATEGORY_QUERIES = {
    "AI Marketing": {
        "Top":    '("ai marketing" OR "vibe marketing" OR "marketing automation" OR "growth automation" OR "content engine") min_faves:10 lang:en -is:retweet -is:reply',
        "Latest": '("ai marketing" OR "vibe marketing" OR "marketing automation" OR "growth automation" OR "content engine") min_faves:3 lang:en -is:retweet -is:reply',
    },
    "AI Coding": {
        "Top":    '("claude code" OR "cursor" OR mcp OR "agentic coding" OR "ai coding" OR "dev workflow") min_faves:10 lang:en -is:retweet -is:reply',
        "Latest": '("claude code" OR "cursor" OR mcp OR "agentic coding" OR "ai coding" OR "dev workflow") min_faves:3 lang:en -is:retweet -is:reply',
    },
    "AI Design": {
        "Top":    '("ai design" OR "ux" OR "ui" OR figma OR "design system" OR "prototype") min_faves:10 lang:en -is:retweet -is:reply',
        "Latest": '("ai design" OR "ux" OR "ui" OR figma OR "design system" OR "prototype") min_faves:3 lang:en -is:retweet -is:reply',
    },
    "General AI": {
        "Top":    '("new ai tool" OR "ai release" OR "open source ai" OR "ai agents" OR llm OR "paper") min_faves:10 lang:en -is:retweet -is:reply',
        "Latest": '("new ai tool" OR "ai release" OR "open source ai" OR "ai agents" OR llm OR "paper") min_faves:3 lang:en -is:retweet -is:reply',
    },
    "OpenClaw": {
        "Top":    '(openclaw OR "open claw") (marketing OR growth OR automation OR mcp OR workflow OR agent) lang:en -is:retweet',
        "Latest": '(openclaw OR "open claw") (marketing OR growth OR automation OR mcp OR workflow OR agent) lang:en -is:retweet',
    },
}


def _headers():
    key = os.getenv("TWITTERAPI_IO_KEY")
    if not key:
        raise RuntimeError("TWITTERAPI_IO_KEY missing")
    return {"X-API-Key": key, "Accept": "application/json"}


def _parse_created_at(s: str):
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _hours_ago(created_at: str):
    dt = _parse_created_at(created_at)
    if not dt:
        return 999
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _in_window(created_at: str):
    return _hours_ago(created_at) <= STOP_IF_OLDER_HOURS


def _sleep(base=1.8, jitter=1.2):
    time.sleep(base + random.random() * jitter)


def _request_with_backoff(url, headers, params, timeout=40, retries=3):
    for attempt in range(retries + 1):
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        if r.status_code != 429:
            r.raise_for_status()
            return r
        wait_s = (2 ** attempt) * 1.5 + random.random()
        print(f"[x-trend][discover] 429 rate-limited, waiting {wait_s:.1f}s (attempt {attempt})")
        time.sleep(wait_s)
    r.raise_for_status()
    return r


def _paginated_search(category: str, query: str, query_type: str, max_pages: int):
    """
    Paginate through twitterapi.io advanced_search.
    Returns (all_tweets_raw, stats_dict).
    """
    url = f"{API_BASE}/twitter/tweet/advanced_search"
    headers = _headers()
    cursor = ""
    all_tweets = []
    pages_fetched = 0

    for page_idx in range(max_pages):
        params = {"query": query, "queryType": query_type, "cursor": cursor}
        r = _request_with_backoff(url, headers, params, timeout=40, retries=3)
        j = r.json()
        tweets = j.get("tweets", []) or []
        has_next = bool(j.get("has_next_page"))
        next_cursor = j.get("next_cursor") or ""
        pages_fetched += 1

        all_tweets.extend(tweets)

        print(
            f"[x-trend][discover] cat={category} type={query_type} "
            f"page={page_idx + 1} items={len(tweets)} total={len(all_tweets)} "
            f"has_next={'1' if has_next else '0'} cursor_len={len(next_cursor)}"
        )

        # ── stop conditions ──
        if not has_next:
            break
        if not next_cursor:
            break
        if len(all_tweets) >= MAX_ITEMS_PER_QUERY:
            print(f"[x-trend][discover]   → stop: max_items ({MAX_ITEMS_PER_QUERY}) reached")
            break

        # stop if most tweets on this page are outside the time window
        in_window_count = sum(1 for t in tweets if _in_window(t.get("createdAt", "")))
        if tweets and in_window_count / len(tweets) < 0.3:
            print(f"[x-trend][discover]   → stop: most tweets older than {STOP_IF_OLDER_HOURS}h "
                  f"({in_window_count}/{len(tweets)} in window)")
            break

        cursor = next_cursor
        if page_idx < max_pages - 1:
            _sleep(base=2.2, jitter=1.6)

    # filter to time window
    kept = [t for t in all_tweets if _in_window(t.get("createdAt", ""))]
    print(
        f"[x-trend][discover] DONE cat={category} type={query_type} "
        f"pages={pages_fetched} total_items={len(all_tweets)} kept_in_window={len(kept)}"
    )
    return kept


def _to_candidate(category: str, tw: dict):
    author = tw.get("author") or {}
    entities = tw.get("entities") or {}
    return {
        "category": category,
        "id": str(tw.get("id") or ""),
        "url": tw.get("url") or "",
        "createdAt": tw.get("createdAt") or "",
        "text": tw.get("text") or "",
        "lang": tw.get("lang") or "",
        "metrics": {
            "bookmark": tw.get("bookmarkCount") or 0,
            "retweet": tw.get("retweetCount") or 0,
            "reply": tw.get("replyCount") or 0,
            "like": tw.get("likeCount") or 0,
            "view": tw.get("viewCount") or 0,
            "quote": tw.get("quoteCount") or 0,
        },
        "author": {
            "userName": author.get("userName") or "",
            "name": author.get("name") or "",
            "followers": author.get("followers") or author.get("followersCount") or 0,
            "verified": bool(author.get("isBlueVerified") or author.get("verified")),
        },
        "entities": entities,
        "source": "twitterapi",
    }


def _last_tweets(username: str = "GithubProjects", limit: int = 60):
    url = f"{API_BASE}/twitter/user/last_tweets"
    headers = _headers()
    params = {"userName": username}
    r = _request_with_backoff(url, headers, params, timeout=40, retries=3)
    j = r.json()
    tweets = j.get("tweets") or j.get("data") or []
    if isinstance(tweets, dict):
        tweets = tweets.get("tweets", [])
    return tweets[:limit]


def run(max_pages: int = 2, only_category=None):
    """
    Discover candidate tweets.

    For each category we run TWO queries: Top (high engagement) and Latest (fresh).
    Results are merged and returned as blocks.
    """
    out = []

    categories = list(CATEGORY_QUERIES.items())
    if only_category:
        categories = [(c, qs) for (c, qs) in categories if c == only_category]

    for idx, (category, queries) in enumerate(categories):
        all_items = []
        seen_ids = set()

        for query_type in ("Top", "Latest"):
            query = queries.get(query_type)
            if not query:
                continue
            max_pg = MAX_PAGES_TOP if query_type == "Top" else MAX_PAGES_LATEST
            try:
                tweets = _paginated_search(category, query, query_type, max_pages=max_pg)
                for tw in tweets:
                    tid = str(tw.get("id") or "")
                    if tid and tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    all_items.append(_to_candidate(category, tw))
            except Exception as e:
                print(f"[x-trend][discover] ERROR cat={category} type={query_type}: {e}")

            # pause between Top→Latest
            _sleep(base=2.0, jitter=1.5)

        out.append({"category": category, "items": all_items, "error": None})

        # pause between categories
        if idx < len(categories) - 1:
            _sleep(base=2.5, jitter=2.0)

    # GitHubProjects from user timeline
    if not only_category or only_category == "GitHubProjects":
        try:
            _sleep(base=2.8, jitter=1.8)
            tweets = _last_tweets("GithubProjects", limit=60)
            items = []
            for tw in tweets:
                if not _in_window(tw.get("createdAt", "")):
                    continue
                items.append(_to_candidate("GitHubProjects", tw))
            out.append({"category": "GitHubProjects", "items": items, "error": None})
        except Exception as e:
            out.append({"category": "GitHubProjects", "items": [], "error": str(e)})

    return out

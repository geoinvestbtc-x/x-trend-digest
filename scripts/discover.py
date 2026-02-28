#!/usr/bin/env python3
import json
import os
import random
import time
import requests
import yaml
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://api.twitterapi.io"

# ── Pagination / volume knobs ─────────────────────────────────
MAX_PAGES_TOP     = int(os.getenv('DISCOVER_MAX_PAGES_TOP', '3'))
MAX_PAGES_LATEST  = int(os.getenv('DISCOVER_MAX_PAGES_LATEST', '4'))
MAX_ITEMS_PER_QUERY = int(os.getenv('DISCOVER_MAX_ITEMS', '120'))
STOP_IF_OLDER_HOURS = int(os.getenv('DISCOVER_STOP_OLDER_H', '48'))

# ── Trends knobs ──────────────────────────────────────────────
TRENDS_ENABLED    = os.getenv('DISCOVER_TRENDS_ENABLED', '1') == '1'
TRENDS_WOEID      = int(os.getenv('DISCOVER_TRENDS_WOEID', '1'))  # 1 = worldwide
TRENDS_MAX_PER_CAT = int(os.getenv('DISCOVER_TRENDS_MAX_PER_CAT', '3'))

# ── Dynamic author discovery knobs ────────────────────────────
DYN_AUTHORS_ENABLED   = os.getenv('DISCOVER_DYN_AUTHORS_ENABLED', '1') == '1'
DYN_AUTHORS_PER_CAT   = int(os.getenv('DISCOVER_DYN_AUTHORS_PER_CAT', '3'))
DYN_AUTHORS_CACHE_H   = int(os.getenv('DISCOVER_DYN_AUTHORS_CACHE_H', '24'))

# ── Quote tweets knobs ────────────────────────────────────────
QUOTES_ENABLED     = os.getenv('DISCOVER_QUOTES_ENABLED', '1') == '1'
QUOTES_TOP_N       = int(os.getenv('DISCOVER_QUOTES_TOP_N', '5'))   # top N tweets per category to expand
QUOTES_MAX_PER_TWEET = int(os.getenv('DISCOVER_QUOTES_MAX', '20'))  # max quote tweets to fetch per tweet

# ── Category queries ──────────────────────────────────────────
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
    "AI Business": {
        "Top":    '("made" OR "earned" OR "built" OR "revenue" OR "profit" OR "MRR" OR "ARR" OR "side hustle" OR "monetize" OR "income" OR "saas") ("with ai" OR "using ai" OR "ai tool" OR "gpt" OR "claude" OR "chatgpt" OR "automation") min_faves:15 lang:en -is:retweet -is:reply',
        "Latest": '("made" OR "earned" OR "built" OR "revenue" OR "launched" OR "customers" OR "paying" OR "business" OR "freelance") ("with ai" OR "using ai" OR "ai tool" OR "ai agent" OR "ai workflow" OR "ai automation") min_faves:5 lang:en -is:retweet -is:reply',
    },
    "OpenClaw": {
        "Top":    '(openclaw OR "open claw") (marketing OR growth OR automation OR mcp OR workflow OR agent) lang:en -is:retweet',
        "Latest": '(openclaw OR "open claw") (marketing OR growth OR automation OR mcp OR workflow OR agent) lang:en -is:retweet',
    },
}

# Keywords used to map trending topics → categories
CATEGORY_TREND_KEYWORDS = {
    "AI Marketing": [
        "marketing", "growth", "seo", "content", "ads", "brand", "campaign",
        "audience", "funnel", "copywriting", "virality", "tiktok", "instagram",
    ],
    "AI Coding": [
        "code", "coding", "developer", "programming", "python", "javascript",
        "typescript", "github", "claude", "cursor", "copilot", "mcp", "llm",
        "api", "open source", "framework", "devtools",
    ],
    "AI Design": [
        "design", "figma", "ui", "ux", "prototype", "css", "frontend",
        "typography", "animation", "creative", "midjourney", "stable diffusion",
    ],
    "General AI": [
        "ai", "artificial intelligence", "gpt", "openai", "anthropic", "gemini",
        "llama", "model", "agent", "inference", "benchmark", "research", "paper",
    ],
    "AI Business": [
        "saas", "startup", "revenue", "mrr", "arr", "monetize", "business",
        "entrepreneur", "product", "launch", "funding", "vc", "indie hacker",
    ],
}

# Search terms used to find dynamic authors per category
CATEGORY_AUTHOR_SEARCH_TERMS = {
    "AI Marketing": "AI marketing expert",
    "AI Coding": "AI coding developer tools",
    "AI Design": "AI design UX tools",
    "General AI": "AI researcher LLM",
    "AI Business": "AI startup founder SaaS",
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

        if not has_next or not next_cursor:
            break
        if len(all_tweets) >= MAX_ITEMS_PER_QUERY:
            print(f"[x-trend][discover]   → stop: max_items ({MAX_ITEMS_PER_QUERY}) reached")
            break

        in_window_count = sum(1 for t in tweets if _in_window(t.get("createdAt", "")))
        if tweets and in_window_count / len(tweets) < 0.3:
            print(f"[x-trend][discover]   → stop: most tweets older than {STOP_IF_OLDER_HOURS}h "
                  f"({in_window_count}/{len(tweets)} in window)")
            break

        cursor = next_cursor
        if page_idx < max_pages - 1:
            _sleep(base=2.2, jitter=1.6)

    kept = [t for t in all_tweets if _in_window(t.get("createdAt", ""))]
    print(
        f"[x-trend][discover] DONE cat={category} type={query_type} "
        f"pages={pages_fetched} total_items={len(all_tweets)} kept_in_window={len(kept)}"
    )
    return kept


def _to_candidate(category: str, tw: dict, source: str = "keyword"):
    author = tw.get("author") or {}
    entities = tw.get("entities") or {}

    # Extract quoted tweet info if present
    quoted_id = ""
    quoted_text = ""
    if tw.get("quoted_status_id_str"):
        quoted_id = str(tw["quoted_status_id_str"])
    elif tw.get("quotedTweet"):
        qt = tw["quotedTweet"]
        quoted_id = str(qt.get("id") or "")
        quoted_text = qt.get("text") or ""

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
        "source": source,
        "quoted_id": quoted_id,
        "quoted_text": quoted_text,
    }


# ── Trends discovery ─────────────────────────────────────────

def _fetch_trends(woeid: int = 1) -> list[dict]:
    """Fetch trending topics from twitterapi.io.

    Returns list of dicts: {name, query, rank, description}
    """
    url = f"{API_BASE}/twitter/trends"
    headers = _headers()
    try:
        r = _request_with_backoff(url, headers, {"woeid": woeid}, timeout=30, retries=2)
        data = r.json()
        trends = data.get("trends") or []
        result = []
        for t in trends:
            name = t.get("name") or ""
            query = (t.get("target") or {}).get("query") or name
            rank = t.get("rank") or 999
            desc = t.get("meta_description") or ""
            if name:
                result.append({"name": name, "query": query, "rank": rank, "description": desc})
        print(f"[x-trend][trends] fetched {len(result)} trends (woeid={woeid})")
        return result
    except Exception as e:
        print(f"[x-trend][trends] ERROR fetching trends: {e}")
        return []


def _match_trends_to_categories(trends: list[dict]) -> dict[str, list[dict]]:
    """Match trending topics to categories by keyword overlap.

    Returns {category: [trend, ...]} keeping at most TRENDS_MAX_PER_CAT per category.
    """
    matched: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_TREND_KEYWORDS}
    for trend in trends:
        name_lower = trend["name"].lower()
        desc_lower = trend["description"].lower()
        text = f"{name_lower} {desc_lower}"
        for cat, keywords in CATEGORY_TREND_KEYWORDS.items():
            if len(matched[cat]) >= TRENDS_MAX_PER_CAT:
                continue
            if any(kw in text for kw in keywords):
                matched[cat].append(trend)
    for cat, hits in matched.items():
        if hits:
            print(f"[x-trend][trends] cat={cat} matched {len(hits)} trends: "
                  f"{', '.join(t['name'] for t in hits)}")
    return matched


def _search_trends_for_category(category: str, trend: dict, seen_ids: set) -> list[dict]:
    """Run a Top search for a single matched trend and return new candidates."""
    query = trend["query"]
    # Add lang and engagement filters since trend queries are bare
    full_query = f"({query}) min_faves:5 lang:en -is:retweet -is:reply"
    print(f"[x-trend][trends] searching trend '{trend['name']}' for cat={category}")
    try:
        tweets = _paginated_search(category, full_query, "Top", max_pages=2)
        items = []
        for tw in tweets:
            tid = str(tw.get("id") or "")
            if tid and tid in seen_ids:
                continue
            seen_ids.add(tid)
            c = _to_candidate(category, tw, source="trend")
            c["trend_name"] = trend["name"]
            items.append(c)
        print(f"[x-trend][trends] trend '{trend['name']}' → {len(items)} new candidates")
        return items
    except Exception as e:
        print(f"[x-trend][trends] ERROR searching trend '{trend['name']}': {e}")
        return []


# ── Dynamic author search ─────────────────────────────────────

def _dyn_authors_cache_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "data" / "dynamic_authors_cache.json"


def _load_dyn_authors_cache() -> dict:
    """Load cached dynamic authors. Returns {category: {usernames: [...], cached_at: iso}}."""
    path = _dyn_authors_cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_dyn_authors_cache(cache: dict):
    path = _dyn_authors_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _search_users_by_keyword(keyword: str, limit: int = 20) -> list[str]:
    """Search Twitter users by keyword, return list of usernames."""
    url = f"{API_BASE}/twitter/user/search"
    headers = _headers()
    try:
        r = _request_with_backoff(url, headers, {"query": keyword}, timeout=30, retries=2)
        data = r.json()
        # Response may be {"users": [...]} or {"data": [...]}
        users = data.get("users") or data.get("data") or []
        if isinstance(users, dict):
            users = users.get("users", [])
        usernames = []
        for u in users[:limit]:
            uname = u.get("userName") or u.get("username") or u.get("screen_name") or ""
            followers = u.get("followers") or u.get("followersCount") or 0
            # Only include accounts with meaningful following (avoid bots)
            if uname and followers >= 500:
                usernames.append(uname.lstrip("@"))
        print(f"[x-trend][dyn_authors] keyword='{keyword}' → {len(usernames)} users found")
        return usernames
    except Exception as e:
        print(f"[x-trend][dyn_authors] ERROR searching users for '{keyword}': {e}")
        return []


def _get_dynamic_authors(category: str) -> list[str]:
    """Return dynamic authors for a category, using cache if fresh enough."""
    cache = _load_dyn_authors_cache()
    now = datetime.now(timezone.utc)
    entry = cache.get(category)
    if entry:
        cached_at_str = entry.get("cached_at", "")
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            if (now - cached_at).total_seconds() / 3600 < DYN_AUTHORS_CACHE_H:
                usernames = entry.get("usernames", [])
                print(f"[x-trend][dyn_authors] cat={category} cache hit → {len(usernames)} authors")
                return usernames
        except Exception:
            pass

    keyword = CATEGORY_AUTHOR_SEARCH_TERMS.get(category)
    if not keyword:
        return []

    usernames = _search_users_by_keyword(keyword, limit=30)
    cache[category] = {
        "usernames": usernames,
        "cached_at": now.isoformat(),
    }
    _save_dyn_authors_cache(cache)
    return usernames[:DYN_AUTHORS_PER_CAT * 3]  # keep buffer, caller slices


# ── Quote tweets ──────────────────────────────────────────────

def _fetch_quotations(tweet_id: str, max_items: int = 20) -> list[dict]:
    """Fetch quote tweets for a given tweet_id."""
    url = f"{API_BASE}/twitter/tweet/quotations"
    headers = _headers()
    try:
        r = _request_with_backoff(
            url, headers, {"tweet_id": tweet_id, "count": max_items}, timeout=30, retries=2
        )
        data = r.json()
        tweets = data.get("tweets") or data.get("data") or []
        if isinstance(tweets, dict):
            tweets = tweets.get("tweets", [])
        return tweets
    except Exception as e:
        print(f"[x-trend][quotes] ERROR fetching quotations for {tweet_id}: {e}")
        return []


def _expand_with_quotations(category: str, candidates: list[dict], seen_ids: set) -> list[dict]:
    """For top-N candidates by engagement, fetch their quote tweets and add as new candidates."""
    if not QUOTES_ENABLED or not candidates:
        return []

    # Pick top N by like+bookmark engagement to expand
    def _eng(c):
        m = c.get("metrics", {})
        return m.get("like", 0) + m.get("bookmark", 0) * 2 + m.get("retweet", 0)

    top = sorted(candidates, key=_eng, reverse=True)[:QUOTES_TOP_N]
    new_items = []
    for c in top:
        tid = c.get("id", "")
        if not tid:
            continue
        print(f"[x-trend][quotes] expanding tweet {tid} (cat={category})")
        raw_quotes = _fetch_quotations(tid, max_items=QUOTES_MAX_PER_TWEET)
        added = 0
        for tw in raw_quotes:
            if not _in_window(tw.get("createdAt", "")):
                continue
            qid = str(tw.get("id") or "")
            if qid and qid in seen_ids:
                continue
            seen_ids.add(qid)
            qt_candidate = _to_candidate(category, tw, source="quote")
            qt_candidate["quoted_id"] = tid
            new_items.append(qt_candidate)
            added += 1
        _sleep(base=1.2, jitter=0.8)
        if added:
            print(f"[x-trend][quotes]   → {added} quote tweets added")
    return new_items


# ── Author-based discovery ──────────────────────────────────

def _load_authors() -> dict:
    """Load data/authors.yaml → {category: [username, ...]}"""
    root = Path(__file__).resolve().parent.parent
    path = root / "data" / "authors.yaml"
    if not path.exists():
        print(f"[x-trend][discover] authors.yaml not found at {path}")
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    # deduplicate usernames per category
    out = {}
    for cat, usernames in raw.items():
        if isinstance(usernames, list):
            out[cat] = list(dict.fromkeys(u.lstrip('@') for u in usernames if u))
    return out


def _last_tweets(username: str, limit: int = 60):
    url = f"{API_BASE}/twitter/user/last_tweets"
    headers = _headers()
    params = {"userName": username}
    r = _request_with_backoff(url, headers, params, timeout=40, retries=3)
    j = r.json()
    tweets = j.get("tweets") or j.get("data") or []
    if isinstance(tweets, dict):
        tweets = tweets.get("tweets", [])
    return tweets[:limit]


def _discover_authors(category: str, usernames: list, seen_ids: set):
    """Fetch recent tweets from author list, return candidates in window."""
    items = []
    for username in usernames:
        try:
            tweets = _last_tweets(username, limit=30)
            found = 0
            for tw in tweets:
                if not _in_window(tw.get("createdAt", "")):
                    continue
                tid = str(tw.get("id") or "")
                if tid and tid in seen_ids:
                    continue
                seen_ids.add(tid)
                items.append(_to_candidate(category, tw, source="author"))
                found += 1
            if found:
                print(f"[x-trend][discover] author @{username} → {found} tweets in window")
        except Exception as e:
            print(f"[x-trend][discover] author @{username} ERROR: {e}")
        _sleep(base=1.5, jitter=1.0)
    return items


# ── Main entry point ─────────────────────────────────────────

def run(max_pages: int = 2, only_category=None):
    out = []
    authors_map = _load_authors()

    # ── Fetch trending topics once for all categories ──
    trends_by_category: dict[str, list[dict]] = {}
    if TRENDS_ENABLED:
        _sleep(base=1.0, jitter=0.5)
        all_trends = _fetch_trends(woeid=TRENDS_WOEID)
        if all_trends:
            trends_by_category = _match_trends_to_categories(all_trends)
    else:
        print("[x-trend][discover] Trends disabled (DISCOVER_TRENDS_ENABLED=0)")

    categories = list(CATEGORY_QUERIES.items())
    if only_category:
        categories = [(c, qs) for (c, qs) in categories if c == only_category]

    for idx, (category, queries) in enumerate(categories):
        all_items = []
        seen_ids = set()
        keyword_found = 0
        author_found = 0
        trend_found = 0
        quote_found = 0
        dyn_author_found = 0

        # ── Keyword discovery ──
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
                    all_items.append(_to_candidate(category, tw, source="keyword"))
                    keyword_found += 1
            except Exception as e:
                print(f"[x-trend][discover] ERROR cat={category} type={query_type}: {e}")
            _sleep(base=2.0, jitter=1.5)

        # ── Trend-based discovery ──
        matched_trends = trends_by_category.get(category, [])
        for trend in matched_trends:
            trend_items = _search_trends_for_category(category, trend, seen_ids)
            all_items.extend(trend_items)
            trend_found += len(trend_items)
            _sleep(base=2.0, jitter=1.0)

        # ── Quote tweet expansion (on keyword + trend candidates) ──
        if QUOTES_ENABLED and all_items:
            quote_items = _expand_with_quotations(category, all_items, seen_ids)
            all_items.extend(quote_items)
            quote_found = len(quote_items)

        # ── Static author discovery ──
        cat_authors = authors_map.get(category, [])
        if cat_authors:
            author_items = _discover_authors(category, cat_authors, seen_ids)
            all_items.extend(author_items)
            author_found = len(author_items)

        # ── Dynamic author discovery ──
        if DYN_AUTHORS_ENABLED:
            try:
                dyn_usernames = _get_dynamic_authors(category)
                # Exclude usernames already in the static list
                static_set = {u.lower() for u in cat_authors}
                dyn_usernames = [u for u in dyn_usernames if u.lower() not in static_set]
                dyn_usernames = dyn_usernames[:DYN_AUTHORS_PER_CAT]
                if dyn_usernames:
                    dyn_items = _discover_authors(category, dyn_usernames, seen_ids)
                    all_items.extend(dyn_items)
                    dyn_author_found = len(dyn_items)
            except Exception as e:
                print(f"[x-trend][dyn_authors] ERROR cat={category}: {e}")
            _sleep(base=1.5, jitter=1.0)

        merged = len(all_items)
        print(
            f"[x-trend][discover] cat={category} "
            f"keyword={keyword_found} trend={trend_found} quotes={quote_found} "
            f"authors_static={author_found} authors_dyn={dyn_author_found} "
            f"total={merged}"
        )

        out.append({"category": category, "items": all_items, "error": None})

        if idx < len(categories) - 1:
            _sleep(base=2.5, jitter=2.0)

    # ── GitHubProjects from user timeline ──
    if not only_category or only_category == "GitHubProjects":
        try:
            _sleep(base=2.8, jitter=1.8)
            tweets = _last_tweets("GithubProjects", limit=60)
            items = []
            for tw in tweets:
                if not _in_window(tw.get("createdAt", "")):
                    continue
                items.append(_to_candidate("GitHubProjects", tw, source="author"))
            out.append({"category": "GitHubProjects", "items": items, "error": None})
        except Exception as e:
            out.append({"category": "GitHubProjects", "items": [], "error": str(e)})

    return out

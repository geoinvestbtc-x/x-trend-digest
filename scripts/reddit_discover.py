#!/usr/bin/env python3
"""
Reddit discovery using Reddit's public JSON endpoint — no API key required.

Any Reddit URL + ".json" returns the page data as JSON.
  https://www.reddit.com/r/{sub}/hot.json?limit=50
  https://www.reddit.com/r/{sub}/top.json?limit=25&t=day

Rate limit: ~1 req/sec unauthenticated. We sleep 1.1s between calls.
The pipeline fetches hot + top/day per subreddit, filters to the 48h window,
and outputs the SAME candidate format as discover.py so rank/summarize/publish
need zero changes.
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# ── Root detection ────────────────────────────────────────────

def _detect_root() -> Path:
    env_root = os.getenv('X_TREND_ROOT')
    if env_root:
        return Path(env_root).expanduser()
    server_root = Path('/home/geo/.openclaw/workspace')
    if server_root.exists():
        return server_root
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()

# ── Config ────────────────────────────────────────────────────

STOP_IF_OLDER_HOURS = int(os.getenv('DISCOVER_STOP_OLDER_H', '48'))
MIN_SCORE           = int(os.getenv('REDDIT_MIN_SCORE', '10'))
HOT_LIMIT           = int(os.getenv('REDDIT_HOT_LIMIT', '50'))
TOP_LIMIT           = int(os.getenv('REDDIT_TOP_LIMIT', '25'))
SLEEP_BETWEEN       = float(os.getenv('REDDIT_SLEEP', '1.1'))  # seconds between requests

_HEADERS = {
    'User-Agent': os.getenv('REDDIT_USER_AGENT', 'x-trend-digest/1.0 (personal digest bot)'),
    'Accept': 'application/json',
}


def _load_subreddits() -> dict:
    path = ROOT / 'data' / 'subreddits.yaml'
    if not path.exists():
        return {}
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


# ── Helpers ───────────────────────────────────────────────────

def _created_str(created_utc: float) -> str:
    """Convert Unix timestamp to the pipeline's standard date string."""
    dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
    return dt.strftime("%a %b %d %H:%M:%S +0000 %Y")


def _hours_ago(created_utc: float) -> float:
    dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _fetch_json(url: str, params: dict = None, retries: int = 3) -> dict:
    """GET a Reddit JSON endpoint with retry + backoff."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_HEADERS, params=params, timeout=20)
            if r.status_code == 429:
                wait = 2 ** attempt * 2
                print(f"[reddit] rate limited, sleeping {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code == 403:
                print(f"[reddit] 403 on {url} — subreddit may be private/banned")
                return {}
            if r.status_code == 404:
                print(f"[reddit] 404 on {url} — subreddit not found")
                return {}
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return {}


def _post_to_candidate(post: dict, category: str) -> dict:
    """
    Convert a Reddit post data dict (from JSON API) to the pipeline candidate format.
    Returns None if the post should be skipped.
    """
    body = post.get('selftext', '') or ''
    title = post.get('title', '') or ''

    if not title:
        return None
    if body in ('[removed]', '[deleted]'):
        body = ''

    created_utc = post.get('created_utc', 0)
    if _hours_ago(created_utc) > STOP_IF_OLDER_HOURS:
        return None
    if (post.get('score') or 0) < MIN_SCORE:
        return None

    # Text: title + body excerpt
    full_text = title
    if body:
        full_text += '\n\n' + body[:400]

    # External URL for link posts
    post_url = post.get('url', '') or ''
    permalink = post.get('permalink', '') or ''
    is_self = post.get('is_self', True)
    external_url = ''
    if not is_self and post_url:
        if 'reddit.com' not in post_url and 'redd.it' not in post_url:
            external_url = post_url

    # Scale comments down to normalize vs tweet reply volumes
    num_comments = post.get('num_comments') or 0
    scaled_comments = max(0, num_comments // 5)

    author = post.get('author') or '[deleted]'
    subreddit = post.get('subreddit') or post.get('subreddit_display_name', '')

    return {
        'category': category,
        'id': post['id'],
        'url': f'https://reddit.com{permalink}',
        'createdAt': _created_str(created_utc),
        'text': full_text,
        'lang': 'en',
        'metrics': {
            'bookmark': post.get('total_awards_received') or 0,
            'retweet': 0,
            'reply': scaled_comments,
            'like': post.get('score') or 0,
            'view': 0,
            'quote': 0,
        },
        'author': {
            'userName': author,
            'name': author,
            'followers': 0,   # not available without auth
            'verified': False,
        },
        'entities': {
            'subreddit': subreddit,
            'flair': post.get('link_flair_text') or '',
            'external_url': external_url,
        },
        'source': 'subreddit',
        'quoted_id': '',
        'quoted_text': '',
        'platform': 'reddit',
    }


def fetch_top_comments(post_id: str, subreddit: str, limit: int = 5) -> list:
    """
    Fetch top-level comments for a Reddit post, sorted by score.
    Returns list of {author, score, text} dicts.
    Uses public JSON endpoint — no API key needed.
    """
    url = f'https://www.reddit.com/r/{subreddit}/comments/{post_id}.json'
    params = {'sort': 'top', 'limit': limit + 5, 'depth': 1, 'raw_json': 1}
    try:
        data = _fetch_json(url, params=params)
        if not data or not isinstance(data, list) or len(data) < 2:
            return []
        children = (data[1].get('data') or {}).get('children') or []
        result = []
        for c in children:
            if c.get('kind') != 't1':
                continue
            cd = c.get('data') or {}
            body = (cd.get('body') or '').strip()
            if not body or body in ('[removed]', '[deleted]'):
                continue
            score = cd.get('score') or 0
            if score < 1:
                continue
            author = cd.get('author') or '[deleted]'
            result.append({'author': author, 'score': score, 'text': body[:400]})
        # Sort by score descending (API may return in insertion order)
        result.sort(key=lambda x: x['score'], reverse=True)
        return result[:limit]
    except Exception as e:
        return []


def _fetch_subreddit(sr_name: str, sort: str, limit: int, time_filter: str = None) -> list:
    """Fetch posts from one subreddit endpoint. Returns list of raw post dicts."""
    url = f'https://www.reddit.com/r/{sr_name}/{sort}.json'
    params = {'limit': limit, 'raw_json': 1}
    if time_filter:
        params['t'] = time_filter

    data = _fetch_json(url, params=params)
    if not data:
        return []

    children = (data.get('data') or {}).get('children') or []
    return [c['data'] for c in children if c.get('kind') == 't3' and c.get('data')]


# ── Main discovery ────────────────────────────────────────────

def run(only_category: str = None) -> list:
    """
    Discover Reddit posts per category from subreddits.yaml.
    Returns list of {category, items[], error} blocks — same shape as discover.py.
    """
    subreddits_by_cat = _load_subreddits()
    if not subreddits_by_cat:
        print("[reddit][discover] No subreddits.yaml found — skipping")
        return []

    results = []

    for category, subreddit_list in subreddits_by_cat.items():
        if only_category and only_category != category:
            continue

        seen_ids: set = set()
        items: list = []
        errors: list = []

        for sr_name in (subreddit_list or []):
            batch_count = 0
            try:
                # hot — best for trending right now
                for post in _fetch_subreddit(sr_name, 'hot', HOT_LIMIT):
                    if post['id'] in seen_ids:
                        continue
                    c = _post_to_candidate(post, category)
                    if c:
                        items.append(c)
                        seen_ids.add(post['id'])
                        batch_count += 1
                time.sleep(SLEEP_BETWEEN)

                # top/day — catches posts that peaked earlier today
                for post in _fetch_subreddit(sr_name, 'top', TOP_LIMIT, time_filter='day'):
                    if post['id'] in seen_ids:
                        continue
                    c = _post_to_candidate(post, category)
                    if c:
                        items.append(c)
                        seen_ids.add(post['id'])
                        batch_count += 1
                time.sleep(SLEEP_BETWEEN)

                print(f"[reddit][discover] r/{sr_name} → {batch_count} posts (cat={category})")

            except Exception as e:
                msg = f"r/{sr_name}: {e}"
                print(f"[reddit][discover] ❌ {msg}")
                errors.append(msg)

        print(f"[reddit][discover] cat={category} subreddits={len(subreddit_list or [])} total={len(items)}")
        results.append({
            'category': category,
            'items': items,
            'error': '; '.join(errors) if errors else None,
        })

    return results


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT / 'scripts'))

    def _load_env():
        p = ROOT / '.env'
        if not p.exists():
            return
        for line in p.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

    _load_env()
    blocks = run()
    total = sum(len(b['items']) for b in blocks)
    print(f"\nTotal Reddit posts discovered: {total}")
    for b in blocks:
        print(f"  [{b['category']}] {len(b['items'])} posts" +
              (f" (errors: {b['error']})" if b.get('error') else ''))
        for it in b['items'][:2]:
            print(f"    r/{it['entities']['subreddit']} ⬆️{it['metrics']['like']} "
                  f"💬{it['metrics']['reply']*5} — {it['text'][:70]}…")

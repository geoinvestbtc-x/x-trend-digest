#!/usr/bin/env python3
"""
Simple JSONL store for 🔥 Interesting bookmarks.
Storage: data/bookmarks.jsonl
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _detect_root() -> Path:
    env_root = os.getenv('X_TREND_ROOT')
    if env_root:
        return Path(env_root).expanduser()
    server_root = Path('/home/geo/.openclaw/workspace')
    if server_root.exists():
        return server_root
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()
BOOKMARKS_FILE = ROOT / 'data' / 'bookmarks.jsonl'


def _load_all():
    """Load all bookmark records."""
    records = []
    if not BOOKMARKS_FILE.exists():
        return records
    for line in BOOKMARKS_FILE.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def exists(tweet_id: str) -> bool:
    """Check if tweet_id is already bookmarked."""
    for rec in _load_all():
        if rec.get('tweet_id') == tweet_id:
            return True
    return False


def save(tweet_id: str, url: str = '', title: str = '', category: str = '',
         deep_read_sent: bool = False):
    """Save a bookmark entry."""
    BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        'tweet_id': tweet_id,
        'url': url,
        'title': title,
        'category': category,
        'saved_at': datetime.now(timezone.utc).isoformat(),
        'deep_read_sent': deep_read_sent,
    }
    with BOOKMARKS_FILE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return rec


def remove(tweet_id: str) -> bool:
    """Remove a bookmark entry."""
    records = _load_all()
    filtered = [r for r in records if r.get('tweet_id') != tweet_id]
    if len(filtered) == len(records):
        return False
    BOOKMARKS_FILE.write_text(
        '\n'.join(json.dumps(x, ensure_ascii=False) for x in filtered) + ('\n' if filtered else ''),
        encoding='utf-8',
    )
    return True


def mark_deep_read_sent(tweet_id: str):
    """Mark a bookmark as having had its deep-read sent."""
    records = _load_all()
    updated = False
    for rec in records:
        if rec.get('tweet_id') == tweet_id:
            rec['deep_read_sent'] = True
            updated = True
    if updated:
        BOOKMARKS_FILE.write_text(
            '\n'.join(json.dumps(x, ensure_ascii=False) for x in records) + ('\n' if records else ''),
            encoding='utf-8',
        )


def get_all():
    """Get all bookmarks."""
    return _load_all()


def stats():
    """Return bookmark stats."""
    records = _load_all()
    deep_read = sum(1 for r in records if r.get('deep_read_sent'))
    return {'total': len(records), 'deep_read': deep_read}

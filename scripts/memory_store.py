#!/usr/bin/env python3
"""
Two-tier memory for deduplication across pipeline runs.

Tiers:
  - "pick"   → sent to Telegram, TTL = PICK_TTL_DAYS (30d default)
  - "ranked" → seen by LLM but not picked, TTL = RANKED_TTL_DAYS (3d default)

Storage: memory/trend-radar.jsonl  (one JSON object per line)
Each record: {"key": "tweet:123", "category": "AI Coding", "tier": "pick", "seen_at": "..."}
"""
import json
import os
import fcntl
from datetime import datetime, timezone, timedelta
from pathlib import Path

PICK_TTL_DAYS = int(os.getenv('TREND_MEMORY_DAYS', '30'))
RANKED_TTL_DAYS = int(os.getenv('TREND_RANKED_TTL_DAYS', '3'))


def _detect_root() -> Path:
    env_root = os.getenv('X_TREND_ROOT')
    if env_root:
        return Path(env_root).expanduser()
    server_root = Path('/home/geo/.openclaw/workspace')
    if server_root.exists():
        return server_root
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()
MEM = ROOT / 'memory' / 'trend-radar.jsonl'


def _load_all():
    """Load all records from JSONL."""
    records = []
    if not MEM.exists():
        return records
    for line in MEM.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records


def load_recent(days=None):
    """
    Load keys that should be filtered out.
    - "pick" tier: filtered for PICK_TTL_DAYS (default 30d)
    - "ranked" tier: filtered for RANKED_TTL_DAYS (default 3d)
    Returns a set of keys to skip.
    """
    now = datetime.now(timezone.utc)
    pick_cutoff = now - timedelta(days=days if days is not None else PICK_TTL_DAYS)
    ranked_cutoff = now - timedelta(days=RANKED_TTL_DAYS)

    keys = set()
    for rec in _load_all():
        try:
            ts = datetime.fromisoformat(rec.get('seen_at'))
        except Exception:
            continue

        tier = rec.get('tier', 'pick')  # legacy records default to "pick"
        key = rec.get('key')
        if not key:
            continue

        if tier == 'pick' and ts >= pick_cutoff:
            keys.add(key)
        elif tier == 'ranked' and ts >= ranked_cutoff:
            keys.add(key)

    return keys


def filter_new(candidates, days=None):
    """Filter out candidates whose key is already in memory."""
    seen = load_recent(days)
    out = []
    for c in candidates:
        if c.get('key') in seen:
            continue
        out.append(c)
    return out


def append(items, tier='pick'):
    """
    Append items to memory with given tier.
    tier should be "pick" or "ranked".
    Uses exclusive file lock to prevent concurrent write corruption.
    """
    MEM.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with MEM.open('a', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            for p in items:
                key = p.get('key') or (f"tweet:{p.get('id')}" if p.get('id') else '')
                if not key:
                    continue
                rec = {
                    'key': key,
                    'category': p.get('category', ''),
                    'tier': tier,
                    'seen_at': now,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def cleanup(days=None):
    """Remove records older than their tier's TTL."""
    if not MEM.exists():
        return

    now = datetime.now(timezone.utc)
    pick_cutoff = now - timedelta(days=days if days is not None else PICK_TTL_DAYS)
    ranked_cutoff = now - timedelta(days=RANKED_TTL_DAYS)

    kept = []
    removed = 0
    for rec in _load_all():
        try:
            ts = datetime.fromisoformat(rec.get('seen_at'))
        except Exception:
            continue

        tier = rec.get('tier', 'pick')
        if tier == 'pick' and ts >= pick_cutoff:
            kept.append(rec)
        elif tier == 'ranked' and ts >= ranked_cutoff:
            kept.append(rec)
        else:
            removed += 1

    content = '\n'.join(json.dumps(x, ensure_ascii=False) for x in kept) + ('\n' if kept else '')
    with MEM.open('w', encoding='utf-8') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(content)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return removed


def stats():
    """Return memory stats for logging."""
    records = _load_all()
    picks = sum(1 for r in records if r.get('tier', 'pick') == 'pick')
    ranked = sum(1 for r in records if r.get('tier') == 'ranked')
    return {'total': len(records), 'picks': picks, 'ranked': ranked}

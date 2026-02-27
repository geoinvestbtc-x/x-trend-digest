#!/usr/bin/env python3
"""
Trend-aware ranker: velocity × relative engagement × virality.
Not a "popularity" ranker — a "what's trending NOW" ranker.
"""
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

REDDIT_MIN_RANK_SCORE = int(os.getenv('REDDIT_MIN_RANK_SCORE', '50'))

BLACKLIST = {
    'airdrop', 'giveaway', 'copytrade',
    'i am building an ai applied to marketing tech startup',
    'outside consultant',
    # AI business spam
    'dm me', 'link in bio', 'free course', 'free ebook',
    'get rich', 'passive income guaranteed', '10x your income',
    'drop a', 'comment below', 'retweet to win',
    'crypto trading bot', 'forex', 'binary option',
    'dropshipping', 'print on demand',
    'follow me for', 'follow for follow',
    'limited spots', 'spots remaining', 'enroll now',
}


def _parse_created(s: str):
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
    except Exception:
        return None


def _hours_since(created_at: str) -> float:
    dt = _parse_created(created_at)
    if not dt:
        return 48.0
    h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return max(0.1, h)


def _raw_engagement(m: dict) -> float:
    """Weighted engagement: bookmarks are most valuable (intent to return > passive like)."""
    b = float(m.get('bookmark', 0) or 0)
    rt = float(m.get('retweet', 0) or 0)
    rp = float(m.get('reply', 0) or 0)
    lk = float(m.get('like', 0) or 0)
    return 10*b + 3*rt + 2*rp + 1*lk


def score(it: dict) -> dict:
    """
    Returns (total_score, components_dict) for logging.

    Components:
    - velocity:  engagement / (hours + 2)  — how fast is it growing
    - relative:  engagement / log10(followers + 10)  — engagement rate
    - virality:  retweets / (likes + 1)  — share ratio
    - freshness: time decay multiplier
    """
    m = it.get('metrics', {})
    followers = float((it.get('author') or {}).get('followers', 0) or 0)
    hours = _hours_since(it.get('createdAt', ''))

    eng = _raw_engagement(m)

    # Velocity: engagement per hour (floor +2h to avoid division spikes)
    velocity = eng / (hours + 2)

    # Relative: how impressive is this engagement for the author's audience size
    relative = eng / math.log10(followers + 10)

    # Virality: retweet-to-like ratio (high = people share, not just heart)
    likes = float(m.get('like', 0) or 0)
    retweets = float(m.get('retweet', 0) or 0)
    virality = retweets / (likes + 1)

    # Freshness: smooth exponential decay (1.0 at 0h → ~0.86 at 12h → ~0.74 at 24h → ~0.64 at 36h → ~0.57 at 48h)
    freshness = math.exp(-0.0125 * hours)

    # Author boost for author-sourced tweets
    source_boost = 1.15 if it.get('source') == 'author' else 1.0

    # Combined score: quality-biased blend (reduced virality weight to avoid low-substance viral bait)
    total = (velocity * 2.5 + relative * 2.0 + virality * 1.5) * freshness * source_boost

    components = {
        'velocity': round(velocity, 3),
        'relative': round(relative, 3),
        'virality': round(virality, 3),
        'freshness': freshness,
        'raw_eng': round(eng, 1),
        'hours': round(hours, 1),
    }

    return round(total, 5), components


def _classify_reject(it):
    text = (it.get('text') or '').strip()
    t_low = text.lower()

    if not text:
        return False, 'empty'
    if len(text.split()) < 8:
        return False, 'short'
    if any(b in t_low for b in BLACKLIST):
        return False, 'blacklisted'

    mention_count = t_low.count('@')
    if mention_count >= 5:
        return False, 'too_many_mentions'

    m = it.get('metrics', {}) or {}
    bookmarks = int(m.get('bookmark', 0) or 0)
    retweets = int(m.get('retweet', 0) or 0)

    # Reddit posts: gate on upvotes (no bookmarks/retweets on Reddit)
    if it.get('platform') == 'reddit':
        likes = int(m.get('like', 0) or 0)
        if likes < REDDIT_MIN_RANK_SCORE:
            return False, 'low_eng'
    # Softer gate: author-sourced tweets get a pass even with low engagement
    elif it.get('source') == 'author':
        # author tweets only need minimal engagement
        if bookmarks < 1 and retweets < 1 and int(m.get('like', 0) or 0) < 3:
            return False, 'low_eng'
    else:
        # keyword tweets need more proof
        if bookmarks < 2 and retweets < 1:
            return False, 'low_eng'

    return True, 'ok'


def run(items, max_candidates_per_category=25):
    by_cat = defaultdict(list)
    reasons = defaultdict(lambda: defaultdict(int))
    rejected_examples = defaultdict(list)
    cat_in = defaultdict(int)

    # Aggregate score components for logging
    cat_components = defaultdict(lambda: {'velocity': [], 'relative': [], 'virality': []})

    for it in items:
        cat = it.get('category', '?')
        cat_in[cat] += 1

        valid, reason = _classify_reject(it)
        if not valid:
            reasons[cat][reason] += 1
            if len(rejected_examples[cat]) < 5:
                rejected_examples[cat].append(
                    f"id={str(it.get('id',''))[:12]} reason={reason} "
                    f"\"{(it.get('text','')[:50]).replace(chr(10),' ')}...\""
                )
            continue

        it = dict(it)
        total_score, components = score(it)
        it['score'] = total_score
        it['score_components'] = components
        by_cat[cat].append(it)

        cat_components[cat]['velocity'].append(components['velocity'])
        cat_components[cat]['relative'].append(components['relative'])
        cat_components[cat]['virality'].append(components['virality'])

    # ── LOG ──
    all_cats = set(list(cat_in.keys()) + list(by_cat.keys()))
    for cat in sorted(all_cats):
        total_in = cat_in.get(cat, 0)
        passed = len(by_cat.get(cat, []))
        rejected = total_in - passed
        reason_str = " ".join(f"{k}={v}" for k, v in sorted(reasons.get(cat, {}).items()))
        print(
            f"[x-trend][rank] cat={cat} in={total_in} passed={passed} "
            f"rejected={rejected}"
            + (f" reasons: {reason_str}" if reason_str else "")
        )
        for ex in rejected_examples.get(cat, []):
            print(f"[x-trend][rank]   example: {ex}")

        # Average score components for passed items
        cc = cat_components.get(cat)
        if cc and cc['velocity']:
            n = len(cc['velocity'])
            avg_v = sum(cc['velocity']) / n
            avg_r = sum(cc['relative']) / n
            avg_vir = sum(cc['virality']) / n
            print(f"[x-trend][rank] cat={cat} avg_velocity={avg_v:.2f} "
                  f"avg_relative={avg_r:.2f} avg_virality={avg_vir:.3f}")

    out = []
    for cat, arr in by_cat.items():
        arr.sort(key=lambda x: x['score'], reverse=True)
        top = arr[:max_candidates_per_category]
        if top:
            scores = [x['score'] for x in top[:5]]
            print(f"[x-trend][rank] cat={cat} top_scores={scores}")
        out.extend(top)

    return out

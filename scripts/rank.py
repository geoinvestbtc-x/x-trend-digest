#!/usr/bin/env python3
import math
from collections import defaultdict
from datetime import datetime, timezone

BLACKLIST = {
    'airdrop', 'giveaway', 'copytrade',
    'i am building an ai applied to marketing tech startup',
    'outside consultant',
}


def _parse_created(s: str):
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
    except Exception:
        return None


def _freshness(created_at: str):
    dt = _parse_created(created_at)
    if not dt:
        return 0.65
    h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if h <= 24:
        return 1.0
    if h <= 48:
        return 0.85
    return 0.65


def _eng(m):
    b = float(m.get('bookmark', 0) or 0)
    rt = float(m.get('retweet', 0) or 0)
    rp = float(m.get('reply', 0) or 0)
    lk = float(m.get('like', 0) or 0)
    vw = float(m.get('view', 0) or 0)
    return 6*b + 3*rt + 2*rp + 1*lk + 0.25*math.log10(vw + 1)


def score(it):
    m = it.get('metrics', {})
    followers = float((it.get('author') or {}).get('followers', 0) or 0)
    auth = 0.5 * math.log10(followers + 1)
    return round((_eng(m) + auth) * _freshness(it.get('createdAt', '')), 5)


def _classify_reject(it):
    """
    Returns (is_valid: bool, reason: str).
    """
    text = (it.get('text') or '').strip()
    t_low = text.lower()

    if not text:
        return False, 'empty'
    if len(text.split()) < 8:
        return False, 'short'
    if any(b in t_low for b in BLACKLIST):
        return False, 'blacklisted'

    # too many @mentions = likely spam/engagement farming
    mention_count = t_low.count('@')
    if mention_count >= 5:
        return False, 'too_many_mentions'

    m = it.get('metrics', {}) or {}
    bookmarks = int(m.get('bookmark', 0) or 0)
    retweets = int(m.get('retweet', 0) or 0)
    if bookmarks < 2 and retweets < 1:
        return False, 'low_eng'

    return True, 'ok'


def run(items, max_candidates_per_category=25):
    by_cat = defaultdict(list)
    reasons = defaultdict(lambda: defaultdict(int))
    rejected_examples = defaultdict(list)  # keep top 5 examples per cat
    cat_in = defaultdict(int)

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
        it['score'] = score(it)
        by_cat[cat].append(it)

    # ── LOG: aggregated ──
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

    out = []
    for cat, arr in by_cat.items():
        arr.sort(key=lambda x: x['score'], reverse=True)
        top = arr[:max_candidates_per_category]
        if top:
            scores = [x['score'] for x in top[:5]]
            print(f"[x-trend][rank] cat={cat} top_scores={scores}")
        out.extend(top)

    return out

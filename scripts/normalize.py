#!/usr/bin/env python3
import hashlib
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

KEEP_PARAMS = {"id"}


def canonical_url(url: str) -> str:
    if not url:
        return ""
    p = urlparse(url.strip())
    host = p.netloc.lower().replace("www.", "")
    path = re.sub(r"/+$", "", p.path)

    # x/twitter canonicalization
    if host in {"x.com", "twitter.com"}:
        host = "x.com"

    q = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        kl = k.lower()
        if kl.startswith("utm_") or kl in {"ref", "ref_src", "s", "t"}:
            continue
        if kl in KEEP_PARAMS:
            q.append((k, v))
    query = urlencode(q)
    return urlunparse((p.scheme or "https", host, path, "", query, ""))


def _normalize_text(text: str) -> str:
    """Normalize text for dedup: lowercase, strip whitespace/mentions/links, collapse spaces."""
    t = (text or "").lower()
    t = re.sub(r"@\w+", "", t)                      # remove @mentions
    t = re.sub(r"https?://\S+", "", t)               # remove URLs
    t = re.sub(r"[^\w\s]", "", t)                    # remove punctuation
    t = unicodedata.normalize("NFKD", t)             # normalize unicode
    t = re.sub(r"\s+", " ", t).strip()               # collapse whitespace
    return t


def text_hash(text: str) -> str:
    """SHA-1 hash of normalized text for content dedup."""
    return hashlib.sha1(_normalize_text(text).encode()).hexdigest()[:16]


def key_for(item: dict) -> str:
    # X tweet id is strongest key
    tid = item.get("id")
    if tid:
        return f"tweet:{tid}"
    u = canonical_url(item.get("url", ""))
    return "url:" + hashlib.sha256(u.encode()).hexdigest()[:16]


def run(discovered_blocks):
    """
    Normalize and deduplicate discovered items.
    Returns (items_list, stats_dict).
    stats_dict has per-category counters for the funnel.
    """
    out, seen_keys, seen_urls, seen_text = [], set(), set(), set()

    # per-category stats
    stats = defaultdict(lambda: {"in": 0, "url_dup": 0, "id_dup": 0, "text_dup": 0, "out": 0})

    for block in discovered_blocks:
        category = block.get("category")
        for it in block.get("items", []):
            it = dict(it)
            st = stats[category]
            st["in"] += 1

            it["url"] = canonical_url(it.get("url", ""))
            it["key"] = key_for(it)
            it["text_hash"] = text_hash(it.get("text", ""))

            if not it["url"] and not it.get("id"):
                continue

            # dedup by key (tweet id / url hash)
            if it["key"] in seen_keys:
                st["id_dup"] += 1
                continue
            seen_keys.add(it["key"])

            # dedup by canonical URL
            if it["url"] and it["url"] in seen_urls:
                st["url_dup"] += 1
                continue
            if it["url"]:
                seen_urls.add(it["url"])

            # dedup by text content
            if it["text_hash"] in seen_text:
                st["text_dup"] += 1
                continue
            seen_text.add(it["text_hash"])

            it["category"] = category
            st["out"] += 1
            out.append(it)

    # ── LOG ──
    for cat, st in stats.items():
        print(
            f"[x-trend][norm] cat={cat} "
            f"in={st['in']} url_dup={st['url_dup']} id_dup={st['id_dup']} "
            f"text_dup={st['text_dup']} out={st['out']}"
        )

    return out

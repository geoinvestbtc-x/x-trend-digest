#!/usr/bin/env python3
"""
External URL extraction — DISABLED in v1.2.
Passes items through unchanged. HTTP fetching of external links was adding
~360 requests per run with zero downstream usage.

Can be re-enabled selectively (e.g. for top-1 pick) in v1.3+.
"""


def run(items):
    print("[x-trend][extract] extract_external_disabled=1 (v1.2: pass-through)")
    return list(items)

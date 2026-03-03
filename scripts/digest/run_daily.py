#!/usr/bin/env python3
"""
Daily Digest Orchestrator — runs all category digests then the Business Idea Radar.

Flow:
  1. Collect from ALL sources (X, Reddit, HN, ProductHunt, IndieHackers, Habr, VC.ru)
  2. For each category: merge, deduplicate, rank, send top 5-10 to Telegram
  3. Run the Business Idea Radar → send top 20 ideas

Usage:
    python3 scripts/digest/run_daily.py
    python3 scripts/digest/run_daily.py --dry-run    # print only, no Telegram
"""
import sys
import os
import argparse
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Root & env setup ──────────────────────────────────────────
def _detect_root() -> Path:
    env_root = os.getenv("AI_DIGEST_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    server_root = Path("/home/geo/.openclaw/workspace")
    if server_root.exists():
        return server_root
    return Path(__file__).resolve().parent.parent.parent

ROOT = _detect_root()

def _load_env():
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── Path setup ────────────────────────────────────────────────
_digest_dir  = Path(__file__).resolve().parent
_scripts_dir = _digest_dir.parent
_radar_dir   = _scripts_dir / "radar"

for p in [str(_digest_dir), str(_scripts_dir), str(_radar_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Source fetchers ───────────────────────────────────────────
from sources.hn_fetcher          import fetch_hn_by_category
from sources.reddit_fetcher      import fetch_reddit_by_category
from sources.producthunt_fetcher import fetch_ph_by_category
from sources.indiehackers_fetcher import fetch_ih_by_category
from sources.ru_fetcher          import fetch_ru_by_category
from sources.x_fetcher           import fetch_x_by_category
from category_publisher          import send_category, CATEGORY_EMOJIS, format_category_message
from rank_digest                 import LLMRanker

# ── Category order ────────────────────────────────────────────
CATEGORIES = [
    "AI Marketing",
    "AI Coding",
    "General AI",
    "AI Design",
    "OpenClaw",
    "GitHub Projects",
]

MIN_POSTS_TO_SEND = 3
MAX_POSTS_TO_SEND = 10

def _print_banner(text: str, char="═", width=60):
    print(f"\n{char * width}")
    print(f"{text.center(width)}")
    print(f"{char * width}\n")

def _print_dry_run_msg(msg: str):
    print(f"DEBUG: [DRY-RUN] {msg}")
    """Merge results from all sources, dedup by URL, rank by engagement."""
    merged: dict[str, list[dict]] = defaultdict(list)
    seen_per_cat: dict[str, set] = defaultdict(set)

    for source_results in all_by_cat:
        for cat, posts in source_results.items():
            for post in posts:
                url_key = post.get("url", "").split("?")[0].rstrip("/")
                if url_key and url_key not in seen_per_cat[cat]:
                    seen_per_cat[cat].add(url_key)
                    merged[cat].append(post)

    # Sort each category's posts by engagement descending
    for cat in merged:
        merged[cat].sort(key=lambda x: x.get("engagement", 0), reverse=True)

    return dict(merged)


def run_category_digests(dry_run: bool = False):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ── Category Digests started ──")

    # ── 1. Collect from all sources ──
    print("[digest] Fetching HN...")
    hn_results = fetch_hn_by_category()

    print("[digest] Fetching Reddit...")
    reddit_results = fetch_reddit_by_category()

    print("[digest] Fetching Product Hunt...")
    ph_results = fetch_ph_by_category()

    print("[digest] Fetching IndieHackers...")
    ih_results = fetch_ih_by_category()

    print("[digest] Fetching Habr & VC.ru...")
    ru_results = fetch_ru_by_category()

    print("[digest] Fetching X/Twitter...")
    x_results = fetch_x_by_category()

    # ── 2. Merge and sort by engagement (as pool of candidates) ──
    all_sources = [hn_results, reddit_results, ph_results, ih_results, ru_results, x_results]
    merged = _merge_and_rank(all_sources)

    # ── 3. LLM Rank & Send per category ──
    ranker = LLMRanker()
    total_tokens_used = 0

    for cat in CATEGORIES:
        candidates = merged.get(cat, [])
        emoji = CATEGORY_EMOJIS.get(cat, "📌")
        
        print(f"[digest] {emoji} {cat}: {len(candidates)} total candidates collected")
        
        if len(candidates) > 0:
            # Pass candidates to LLM
            posts = ranker.filter_category(cat, candidates)
        else:
            posts = []

        if dry_run:
            _print_banner(f"DRY RUN: {emoji} {cat}", char="─")
            rendered = format_category_message(cat, posts, max_posts=MAX_POSTS_TO_SEND)
            # Indent for better readability
            for line in rendered.splitlines():
                print(f"  {line}")
            print("\n" + "·" * 40)
        else:
            if send_category(cat, posts, min_posts=MIN_POSTS_TO_SEND, max_posts=MAX_POSTS_TO_SEND):
                time.sleep(1)  # small delay between category messages

    total_prompt = ranker.total_prompt_tokens
    total_comp = ranker.total_completion_tokens
    total_tok = total_prompt + total_comp
    cost = (total_prompt * 0.075 / 1_000_000) + (total_comp * 0.3 / 1_000_000)
    print(f"[digest] LLM usage: {total_prompt} prompt + {total_comp} completion = {total_tok} tokens.")
    print(f"[digest] Estimated cost: ${cost:.6f}")

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ── Category Digests complete ──\n")
    return total_prompt, total_comp

def run_idea_radar(dry_run: bool = False):
    """Run the Business Idea Radar pipeline."""
    # ... existing ...
    # Wait to avoid passing BOT_TOKEN/CHAT_ID manually into radar if we can avoid it.
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ── Business Idea Radar started ──")
    try:
        import collect
        import process
        import memory
        import publish

        raw_items = collect.run_collection()
        if not raw_items:
            print("[radar] No items collected.")
            return 0, 0

        generated_ideas, token_usage = process.process_items(raw_items)
        if not generated_ideas:
            print("[radar] No ideas generated.")
            return 0, 0

        print(f"[radar] Generated {len(generated_ideas)} raw ideas.")

        memory.cleanup()
        pool = []
        for idea in generated_ideas:
            processed = memory.match_and_merge(idea)
            if processed.get("status") in ("new", "growing", "reframed"):
                pool.append(processed)

        pool.sort(key=lambda x: x.get("rating", 0), reverse=True)

        from config import config as radar_config
        final_ideas = pool[:radar_config.IDEA_MAX_PER_DAY]

        print(f"[radar] Sending top {len(final_ideas)} ideas.")

        if not dry_run:
            publish.save_payload(final_ideas)
            publish.publish_to_telegram(final_ideas)  # Don't pass token_usage so it doesn't send its own msg
        else:
            _print_banner("DRY RUN: BUSINESS IDEA RADAR", char="★")
            for i, idea in enumerate(final_ideas, 1):
                title = idea.get('idea_title', 'Untitled')
                score = idea.get('rating', 0)
                print(f" {i}. [{score}/100] 🔥 {title}")
                print(f"    Problem: {idea.get('problem_description', '')[:200]}...")
                print(f"    Solution: {idea.get('proposed_solution', '')[:200]}...")
                print(f"    ICP: {idea.get('icp', '')}")
                print(f"    Sources: {idea.get('sources', '')}")
                print(f"    " + "-" * 30)
                
        p_tokens = token_usage.get('prompt_tokens', 0)
        c_tokens = token_usage.get('completion_tokens', 0)
        
    except Exception as e:
        import traceback
        print(f"[radar] ERROR: {e}")
        traceback.print_exc()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ── Business Idea Radar complete ──")
    return p_tokens, c_tokens


def main():
    parser = argparse.ArgumentParser(description="Daily digest: categories + idea radar")
    parser.add_argument("--dry-run",       action="store_true", help="Print results, skip Telegram")
    parser.add_argument("--only-radar",    action="store_true", help="Skip category digests, run only radar")
    parser.add_argument("--only-digest",   action="store_true", help="Skip radar, run only category digests")
    args = parser.parse_args()

    total_prompt_tokens = 0
    total_completion_tokens = 0

    if not args.only_radar:
        p_tok, c_tok = run_category_digests(dry_run=args.dry_run)
        total_prompt_tokens += p_tok
        total_completion_tokens += c_tok

    if not args.only_digest:
        p_tok, c_tok = run_idea_radar(dry_run=args.dry_run)
        total_prompt_tokens += p_tok
        total_completion_tokens += c_tok
        
    # Send Final Combined Cost Summary
    total_tok = total_prompt_tokens + total_completion_tokens
    # gemini-3-flash-preview pricing via OpenRouter: ~$0.075/1M input, ~$0.30/1M output
    cost = (total_prompt_tokens * 0.075 / 1_000_000) + (total_completion_tokens * 0.3 / 1_000_000)
    
    if args.dry_run:
        _print_banner("TOTAL PIPELINE SUMMARY (DRY RUN)", char="█")
        print(f"  Tokens Used: {total_tok:,}")
        print(f"  - Prompt:    {total_prompt_tokens:,}")
        print(f"  - Completion: {total_completion_tokens:,}")
        print(f"  Estimated Cost: ${cost:.6f}")
        print(f"  Pipelines run: {'Digests ' if not args.only_radar else ''}{'Radar ' if not args.only_digest else ''}")
        print("█" * 60 + "\n")
    else:
        print(f"\n[pipeline] Total Pipeline LLM usage: {total_prompt_tokens} prompt + {total_completion_tokens} completion = {total_tok} tokens.")
        print(f"[pipeline] Total Pipeline Estimated cost: ${cost:.6f}")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL")
    
    if not args.dry_run and bot_token and chat_id and total_tok > 0:
        import requests
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"📊 <b>Total Daily Pipeline Cost</b>\n\nTokens Used: {total_tok:,}\nEstimated Cost: ${cost:.6f}\n\n<i>Pipelines run: {'Digests ' if not args.only_radar else ''}{'Radar ' if not args.only_digest else ''}</i>",
                    "parse_mode": "HTML",
                }, timeout=5
            )
        except Exception as e:
            print(f"[pipeline] Could not send final cost message: {e}")


if __name__ == "__main__":
    main()

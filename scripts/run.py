#!/usr/bin/env python3
import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from discover import run as discover_run
from normalize import run as normalize_run
from extract import run as extract_run
from rank import run as rank_run
from summarize import run as summarize_run
from memory_store import filter_new, append as mem_append, cleanup as mem_cleanup, stats as mem_stats
from publish_telegram import group_picks, render_messages, send_messages


def _detect_root() -> Path:
    env_root = os.getenv('X_TREND_ROOT')
    if env_root:
        return Path(env_root).expanduser()
    server_root = Path('/home/geo/.openclaw/workspace')
    if server_root.exists():
        return server_root
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()
OUT = ROOT / 'out_trends'
DATA = ROOT / 'data'


def load_env():
    p = ROOT / '.env'
    if not p.exists():
        return
    for line in p.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _mask(val: str, show_start=4, show_end=4) -> str:
    if not val or len(val) < show_start + show_end + 4:
        return '***'
    return val[:show_start] + '...' + val[-show_end:]


def main():
    parser = argparse.ArgumentParser(description='X Trend Digest pipeline')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run full pipeline (discover→rank→summarize) but skip Telegram send and memory writes')
    parser.add_argument('--no-reddit', action='store_true',
                        help='Skip Reddit discovery even if REDDIT_DISCOVER_ENABLED=1')
    args = parser.parse_args()
    dry_run = args.dry_run
    skip_reddit = args.no_reddit

    load_env()
    ts = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')
    day = datetime.now(timezone.utc).strftime('%Y%m%d')
    picks_n = int(os.getenv('DIGEST_MAX_PER_TOPIC', '5'))
    memory_days = int(os.getenv('TREND_MEMORY_DAYS', '30'))

    # ── funnel counters per category ──
    funnel = defaultdict(lambda: {
        'discovered': 0, 'in_window': 0, 'after_norm': 0,
        'after_ttl': 0, 'after_dedup': 0, 'after_rank': 0,
        'picks': 0, 'sent': 0,
    })

    # ══════════════════════════════════════════════════════════
    # STAGE 0: CONFIG
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"[STAGE 0] CONFIG")
    print(f"{'='*60}")
    print(f"  ts             : {ts}")
    if dry_run:
        print(f"  *** DRY RUN — no Telegram send, no memory writes ***")
    print(f"  ROOT           : {ROOT}")
    print(f"  picks_n        : {picks_n}")
    print(f"  memory_days    : {memory_days}")
    print(f"  DIGEST_MODEL   : {os.getenv('DIGEST_MODEL', 'openai/gpt-5-mini')}")
    print(f"  ONLY_CATEGORY  : {os.getenv('DIGEST_ONLY_CATEGORY', '(all)')}")
    print(f"  SEND_TELEGRAM  : {os.getenv('SEND_TELEGRAM', '0')}")
    print(f"  TELEGRAM_TARGET: {os.getenv('TELEGRAM_TARGET', '(not set)')}")
    print(f"  OPENROUTER_KEY : {_mask(os.getenv('OPENROUTER_API_KEY', ''))}")
    print(f"  TWITTERAPI_KEY : {_mask(os.getenv('TWITTERAPI_IO_KEY', ''))}")
    print(f"  TELEGRAM_TOKEN : {_mask(os.getenv('TELEGRAM_BOT_TOKEN', ''))}")
    reddit_enabled = os.getenv('REDDIT_DISCOVER_ENABLED', '0') == '1' and not skip_reddit
    print(f"  REDDIT         : {'enabled' if reddit_enabled else 'disabled (set REDDIT_DISCOVER_ENABLED=1 to enable)'}")
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 1: DISCOVER
    # ══════════════════════════════════════════════════════════
    only_cat = os.getenv('DIGEST_ONLY_CATEGORY', '').strip()
    print(f"{'='*60}")
    print(f"[STAGE 1] DISCOVER")
    print(f"{'='*60}")
    discovered_blocks = discover_run(max_pages=2, only_category=only_cat or None)
    for b in discovered_blocks:
        cat = b.get('category', '?')
        items_count = len(b.get('items', []))
        err = b.get('error')
        status = f"✅ {items_count} items" if not err else f"❌ error: {err}"
        print(f"  [{cat}] {status}")
        funnel[cat]['discovered'] = items_count
        funnel[cat]['in_window'] = items_count  # discover already filters by window
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 2: NORMALIZE (logging inside normalize.py)
    # ══════════════════════════════════════════════════════════
    print(f"{'='*60}")
    print(f"[STAGE 2] NORMALIZE + TEXT DEDUP")
    print(f"{'='*60}")
    normalized = normalize_run(discovered_blocks)
    for it in normalized:
        funnel[it.get('category', '?')]['after_norm'] += 1
    print(f"  total after normalize: {len(normalized)}")
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 2b: EXTRACT (external URLs)
    # ══════════════════════════════════════════════════════════
    extracted = extract_run(normalized)

    # ══════════════════════════════════════════════════════════
    # STAGE 2c: MEMORY TTL DEDUP
    # ══════════════════════════════════════════════════════════
    print(f"{'='*60}")
    print(f"[STAGE 2c] MEMORY TTL DEDUP (picks={os.getenv('TREND_MEMORY_DAYS', '30')}d, ranked={os.getenv('TREND_RANKED_TTL_DAYS', '3')}d)")
    print(f"{'='*60}")
    mstats = mem_stats()
    print(f"  memory: {mstats['total']} records ({mstats['picks']} picks, {mstats['ranked']} ranked)")
    fresh = filter_new(extracted, days=memory_days)
    ttl_removed = len(extracted) - len(fresh)
    print(f"  before TTL: {len(extracted)}, after TTL: {len(fresh)}, removed: {ttl_removed}")

    # cross-category dedup by tweet id/url
    dedup = {}
    for it in fresh:
        k = it.get('id') or it.get('url')
        if not k:
            continue
        if k not in dedup:
            dedup[k] = it
        else:
            if len((it.get('text') or '')) > len((dedup[k].get('text') or '')):
                dedup[k] = it
    cross_removed = len(fresh) - len(dedup)
    fresh = list(dedup.values())
    print(f"  cross-category dedup removed: {cross_removed}, final fresh: {len(fresh)}")

    for it in fresh:
        funnel[it.get('category', '?')]['after_ttl'] += 1
        funnel[it.get('category', '?')]['after_dedup'] += 1
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 3: RANK (logging inside rank.py)
    # ══════════════════════════════════════════════════════════
    print(f"{'='*60}")
    print(f"[STAGE 3] RANK & FILTER")
    print(f"{'='*60}")
    ranked = rank_run(fresh, max_candidates_per_category=25)

    # NO FALLBACK: if rank produces 0, we don't send junk to LLM
    if not ranked:
        print(f"  ⚠ rank produced 0 candidates. No LLM call, no picks.")
    else:
        for it in ranked:
            funnel[it.get('category', '?')]['after_rank'] += 1
    print(f"  total ranked: {len(ranked)}")
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 4: SUMMARIZE / LLM (logging inside summarize.py)
    # ══════════════════════════════════════════════════════════
    picks = []
    usage_stats = {}
    if ranked:
        print(f"{'='*60}")
        print(f"[STAGE 4] SUMMARIZE / LLM")
        print(f"{'='*60}")
        picks, usage_stats = summarize_run(ranked, picks_n=picks_n)
        for p in picks:
            funnel[p.get('category', '?')]['picks'] += 1
        print(f"  total picks: {len(picks)}")
        print(f"  LLM usage: {usage_stats}")
        print(f"{'='*60}\n")
    else:
        print(f"[STAGE 4] SKIPPED — no ranked candidates\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 5: MEMORY APPEND (two-tier)
    # ══════════════════════════════════════════════════════════
    pick_ids = {str(p.get('id')) for p in picks if p.get('id')}

    # Assign keys to picks
    ranked_idx = {str(x.get('id')): x for x in fresh if x.get('id')}
    for p in picks:
        base = ranked_idx.get(str(p.get('id')), {})
        p['key'] = base.get('key') or (f"tweet:{p.get('id')}" if p.get('id') else '')

    # Build ranked-but-not-picked list
    ranked_not_picked = []
    for r in ranked:
        rid = str(r.get('id', ''))
        if rid not in pick_ids:
            r_copy = dict(r)
            r_copy['key'] = r_copy.get('key') or (f"tweet:{rid}" if rid else '')
            ranked_not_picked.append(r_copy)

    # Save to memory (skipped in dry-run)
    if dry_run:
        print(f"  [STAGE 5] Memory: DRY RUN — skipping write ({len(picks)} picks + {len(ranked_not_picked)} ranked)")
    else:
        mem_append(picks, tier='pick')
        mem_append(ranked_not_picked, tier='ranked')
        mem_cleanup(memory_days)
        print(f"  [STAGE 5] Memory: saved {len(picks)} picks + {len(ranked_not_picked)} ranked")

    # ══════════════════════════════════════════════════════════
    # STAGE 5b: REDDIT PIPELINE (optional, parallel data flow)
    # ══════════════════════════════════════════════════════════
    reddit_picks = []
    reddit_usage_stats = {}

    if reddit_enabled:
        print(f"{'='*60}")
        print(f"[STAGE 5b] REDDIT PIPELINE")
        print(f"{'='*60}")
        try:
            import reddit_discover
            from reddit_discover import run as reddit_discover_run

            r_blocks = reddit_discover_run(only_category=only_cat or None)
            for b in r_blocks:
                cat = b.get('category', '?')
                status = f"✅ {len(b.get('items', []))} posts" if not b.get('error') else f"❌ {b['error']}"
                print(f"  [Reddit/{cat}] {status}")

            r_normalized = normalize_run(r_blocks)
            print(f"  Reddit normalized: {len(r_normalized)}")

            r_fresh = filter_new(r_normalized, days=memory_days)
            print(f"  Reddit after TTL dedup: {len(r_fresh)}")

            # cross-dedup within Reddit batch
            r_dedup = {}
            for it in r_fresh:
                k = it.get('key') or it.get('id')
                if k and k not in r_dedup:
                    r_dedup[k] = it
            r_fresh = list(r_dedup.values())

            r_ranked = rank_run(r_fresh, max_candidates_per_category=25)
            print(f"  Reddit ranked: {len(r_ranked)}")

            # ── Enrich top candidates with top comments before LLM ──
            fetch_comments = os.getenv('REDDIT_FETCH_COMMENTS', '1') == '1'
            n_top_comments = int(os.getenv('REDDIT_TOP_COMMENTS', '5'))
            if fetch_comments and r_ranked:
                from concurrent.futures import ThreadPoolExecutor
                import threading
                _rate_lock = threading.Semaphore(3)  # max 3 concurrent requests

                # Only enrich top 10 per category (those most likely to reach LLM)
                from collections import defaultdict as _dd
                _cat_seen = _dd(int)
                _to_enrich, _skip_enrich = [], []
                for _item in r_ranked:
                    _cat = _item.get('category', '?')
                    if _cat_seen[_cat] < 10:
                        _to_enrich.append(_item)
                        _cat_seen[_cat] += 1
                    else:
                        _skip_enrich.append(_item)

                def _enrich_with_comments(item):
                    pid = str(item.get('id', ''))
                    sr = (item.get('entities') or {}).get('subreddit', '')
                    if not pid or not sr:
                        return item
                    with _rate_lock:
                        comments = reddit_discover.fetch_top_comments(pid, sr, limit=n_top_comments)
                        time.sleep(0.4)
                    if not comments:
                        return item
                    item = dict(item)
                    comments_block = '\n\nTop comments:\n' + '\n'.join(
                        f"[⬆️{c['score']}] {c['text']}" for c in comments
                    )
                    item['text'] = (item.get('text') or '') + comments_block
                    return item

                print(f"  Fetching comments for {len(_to_enrich)} Reddit candidates...")
                with ThreadPoolExecutor(max_workers=3) as _pool:
                    _enriched = list(_pool.map(_enrich_with_comments, _to_enrich))
                r_ranked = _enriched + _skip_enrich
                print(f"  Comment enrichment done")

            if r_ranked:
                reddit_picks, reddit_usage_stats = summarize_run(r_ranked, picks_n=picks_n)
                print(f"  Reddit picks: {len(reddit_picks)}")
                print(f"  Reddit LLM: {reddit_usage_stats}")

                # Enrich Reddit picks with display metadata (subreddit, upvotes, comment count)
                r_ranked_idx = {str(r.get('id')): r for r in r_ranked}
                for p in reddit_picks:
                    base = r_ranked_idx.get(str(p.get('id')), {})
                    p['platform'] = 'reddit'
                    p['entities'] = base.get('entities', {})
                    raw_comments = base.get('metrics', {}).get('reply', 0)
                    p['display_metrics'] = {
                        'upvotes': base.get('metrics', {}).get('like', 0),
                        'comments': raw_comments * 5,  # restore actual count (was scaled /5)
                    }

                if not dry_run:
                    r_pick_ids = {str(p.get('id')) for p in reddit_picks}
                    mem_append(reddit_picks, tier='pick')
                    mem_append(
                        [r for r in r_ranked if str(r.get('id')) not in r_pick_ids],
                        tier='ranked'
                    )
                    print(f"  Reddit memory: saved {len(reddit_picks)} picks")
            else:
                print(f"  Reddit: 0 ranked candidates, skipping LLM")

        except Exception as e:
            import traceback
            print(f"  [Reddit] ❌ Pipeline error: {e}")
            traceback.print_exc()

        print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 6: PUBLISH
    # ══════════════════════════════════════════════════════════
    print(f"{'='*60}")
    print(f"[STAGE 6] PUBLISH")
    print(f"{'='*60}")

    # Twitter messages first, then Reddit messages
    twitter_by_cat = group_picks(picks)
    reddit_by_cat = group_picks(reddit_picks)
    twitter_messages = render_messages(twitter_by_cat, ts, max_picks=min(7, picks_n), source='twitter')
    reddit_messages = render_messages(reddit_by_cat, ts, max_picks=min(7, picks_n), source='reddit') if reddit_picks else []
    messages = twitter_messages + reddit_messages
    print(f"  X picks        : {len(picks)}")
    print(f"  Reddit picks   : {len(reddit_picks)}")
    print(f"  messages built : {len(messages)} ({len(twitter_messages)} X + {len(reddit_messages)} Reddit)")
    for m in messages:
        cat = m.get('category', '')
        print(f"  [{cat}] {len(m.get('text',''))} chars")
        for line in m.get('text', '').split('\n'):
            print(f"    | {line}")

    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    payload_path = OUT / f'payload-{ts}.json'
    digest_md = OUT / f'digest-{ts}.md'
    tg_json = OUT / f'telegram-messages-{ts}.json'
    tg_txt = OUT / f'telegram-ready-{ts}.txt'
    run_json = DATA / f'run-{day}.json'

    save_json(payload_path, picks)
    save_json(tg_json, messages)

    md_lines = [f'# X Trend Digest ({ts} UTC)', '']
    for m in messages:
        md_lines.append(m['text'])
        md_lines.append('')
    digest_md.write_text('\n'.join(md_lines), encoding='utf-8')
    tg_txt.write_text('\n\n'.join([m['text'] for m in messages]), encoding='utf-8')

    sent_count = 0
    if dry_run:
        print(f"  Telegram skipped (DRY RUN)")
    elif os.getenv('SEND_TELEGRAM', '0') == '1' and os.getenv('TELEGRAM_TARGET'):
        if not messages:
            print(f"  ⚠ SEND_TELEGRAM=1, но 0 сообщений.")
        else:
            print(f"  → sending {len(messages)} message(s) to Telegram...")
            sent_count = send_messages(
                messages,
                target=os.getenv('TELEGRAM_TARGET'),
                channel=os.getenv('TELEGRAM_CHANNEL', 'telegram'),
            )
            print(f"  → sent: {sent_count}/{len(messages)}")
            for m in messages:
                funnel[m.get('category', '?')]['sent'] += 1 if sent_count else 0
    else:
        print(f"  Telegram skipped (SEND_TELEGRAM={os.getenv('SEND_TELEGRAM', '0')})")
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # FUNNEL SUMMARY
    # ══════════════════════════════════════════════════════════
    print(f"{'='*60}")
    print(f"[FUNNEL SUMMARY]")
    print(f"{'='*60}")
    totals = defaultdict(int)
    for cat in sorted(funnel.keys()):
        f = funnel[cat]
        print(
            f"[x-trend][summary] {cat}: "
            f"discovered={f['discovered']} in_window={f['in_window']} "
            f"after_dedup={f['after_dedup']} after_rank={f['after_rank']} "
            f"picks={f['picks']} sent={f['sent']}"
        )
        for k, v in f.items():
            totals[k] += v

    print(
        f"[x-trend][summary] TOTAL: "
        f"discovered={totals['discovered']} in_window={totals['in_window']} "
        f"after_dedup={totals['after_dedup']} after_rank={totals['after_rank']} "
        f"picks={totals['picks']} msgs={len(messages)} sent={sent_count}"
    )
    if usage_stats:
        print(
            f"[x-trend][summary] LLM: "
            f"calls={usage_stats.get('llm_calls', 0)} "
            f"prompt={usage_stats.get('prompt_tokens', 0):,} "
            f"completion={usage_stats.get('completion_tokens', 0):,} "
            f"(reasoning={usage_stats.get('reasoning_tokens', 0):,}) "
            f"total={usage_stats.get('total_tokens', 0):,} "
            f"cost=${usage_stats.get('cost_usd', 0):.4f}"
        )
    print(f"{'='*60}\n")

    # ══════════════════════════════════════════════════════════
    # STAGE 7: PIPELINE SUMMARY → Telegram
    # ══════════════════════════════════════════════════════════
    if not dry_run and os.getenv('SEND_TELEGRAM', '0') == '1' and os.getenv('TELEGRAM_TARGET') and sent_count > 0:
        cost = usage_stats.get('cost_usd', 0)
        total_tok = usage_stats.get('total_tokens', 0)
        llm_calls = usage_stats.get('llm_calls', 0)
        reasoning_pct = 0
        if usage_stats.get('completion_tokens', 0) > 0:
            reasoning_pct = int(usage_stats.get('reasoning_tokens', 0) / usage_stats['completion_tokens'] * 100)

        reddit_line = f"🟠 Reddit: {len(reddit_picks)} picks\n" if reddit_picks else ""
        summary_text = (
            f"✅ <b>Pipeline complete</b>\n"
            f"\n"
            f"𝕏 {totals['discovered']} discovered → {totals['after_rank']} ranked → {totals['picks']} picks\n"
            f"{reddit_line}"
            f"📨 {sent_count} messages sent\n"
            f"🤖 {llm_calls} LLM calls · {total_tok:,} tokens (reasoning {reasoning_pct}%)\n"
            f"💰 ${cost:.4f}"
        )
        print(f"  → Sending pipeline summary to Telegram...")
        send_messages(
            [{'category': '__pipeline_summary__', 'text': summary_text}],
            target=os.getenv('TELEGRAM_TARGET'),
            channel=os.getenv('TELEGRAM_CHANNEL', 'telegram'),
        )

    # ── JSON output ──
    run_payload = {
        'ts': ts,
        'counts': {
            'discovered_blocks': len(discovered_blocks),
            'normalized': len(normalized),
            'fresh_after_ttl': len(fresh),
            'ranked': len(ranked),
            'picks': len(picks),
            'messages': len(messages),
            'telegram_sent': sent_count,
        },
        'usage': usage_stats,
        'errors': [{'category': b.get('category'), 'error': b.get('error')} for b in discovered_blocks if b.get('error')],
        'files': {
            'payload': str(payload_path),
            'digest': str(digest_md),
            'telegram_json': str(tg_json),
            'telegram_ready': str(tg_txt),
        }
    }
    save_json(run_json, run_payload)
    print(json.dumps(run_payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

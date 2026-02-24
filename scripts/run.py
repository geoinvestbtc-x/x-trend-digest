#!/usr/bin/env python3
import json
import os
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

    # Save to memory
    mem_append(picks, tier='pick')
    mem_append(ranked_not_picked, tier='ranked')
    mem_cleanup(memory_days)
    print(f"  [STAGE 5] Memory: saved {len(picks)} picks + {len(ranked_not_picked)} ranked")

    # ══════════════════════════════════════════════════════════
    # STAGE 6: PUBLISH
    # ══════════════════════════════════════════════════════════
    print(f"{'='*60}")
    print(f"[STAGE 6] PUBLISH")
    print(f"{'='*60}")

    by_cat = group_picks(picks)
    messages = render_messages(by_cat, ts, max_picks=min(7, picks_n))
    print(f"  picks total    : {len(picks)}")
    print(f"  messages built : {len(messages)}")
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
    if os.getenv('SEND_TELEGRAM', '0') == '1' and os.getenv('TELEGRAM_TARGET'):
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
    if os.getenv('SEND_TELEGRAM', '0') == '1' and os.getenv('TELEGRAM_TARGET') and sent_count > 0:
        from publish_telegram import _send_via_telegram_http
        cost = usage_stats.get('cost_usd', 0)
        total_tok = usage_stats.get('total_tokens', 0)
        llm_calls = usage_stats.get('llm_calls', 0)
        reasoning_pct = 0
        if usage_stats.get('completion_tokens', 0) > 0:
            reasoning_pct = int(usage_stats.get('reasoning_tokens', 0) / usage_stats['completion_tokens'] * 100)

        summary_text = (
            f"✅ <b>Pipeline complete</b>\n"
            f"\n"
            f"📊 {totals['discovered']} discovered → {totals['after_rank']} ranked → {totals['picks']} picks → {sent_count} sent\n"
            f"🤖 {llm_calls} LLM calls · {total_tok:,} tokens (reasoning {reasoning_pct}%)\n"
            f"💰 ${cost:.4f}"
        )
        print(f"  → Sending pipeline summary to Telegram...")
        _send_via_telegram_http(summary_text, target=os.getenv('TELEGRAM_TARGET'))

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

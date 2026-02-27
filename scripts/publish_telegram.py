#!/usr/bin/env python3
from collections import defaultdict
import html
import json
import os
import re
import subprocess

import requests

ORDER = [
    'AI Marketing',
    'AI Coding',
    'AI Design',
    'General AI',
    'AI Business',
    'OpenClaw',
    'GitHubProjects',
]

CAT_EMOJI = {
    'AI Marketing':   '📣',
    'AI Coding':      '⚡',
    'AI Design':      '🎨',
    'General AI':     '🧠',
    'AI Business':    '💰',
    'OpenClaw':       '🦞',
    'GitHubProjects': '🐙',
}


def _html_esc(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _strip_html(text: str) -> str:
    """Convert simple HTML-formatted Telegram text to plain text for channels that don't parse HTML."""
    no_tags = re.sub(r'<[^>]+>', '', text)
    return html.unescape(no_tags)


def _fmt_number(n: int) -> str:
    """Format a large number compactly: 4500 → 4.5k."""
    if n >= 1000:
        return f'{n / 1000:.1f}'.rstrip('0').rstrip('.') + 'k'
    return str(n)


def render_messages(picks_by_category, ts, max_picks=10, source='twitter'):
    """Render one message per category with numbered picks.

    source: 'twitter' or 'reddit' — controls header badge and item format.
    Each message carries a `picks_data` list so that send_messages
    can attach inline 🪨 buttons.
    """
    source_badge = '🟠 Reddit' if source == 'reddit' else '𝕏'
    messages = []

    for cat in ORDER:
        picks = (picks_by_category.get(cat) or [])[:max(1, min(10, max_picks))]
        if not picks:
            continue

        emoji = CAT_EMOJI.get(cat, '🔹')
        lines = [f'{emoji} <b>{_html_esc(cat)}</b> · {source_badge} — last 48h']

        picks_data = []
        for i, p in enumerate(picks):
            title = _html_esc((p.get('title', '').strip() or '(no title)')[:300])
            why = _html_esc(p.get('why_interesting', '').strip() or '')
            url = p.get('url', '').strip()
            item_id = str(p.get('id', ''))

            lines.append('')
            lines.append(f'<b>{i + 1}. {title}</b>')
            if why:
                lines.append(f'Why: {why}')

            if source == 'reddit':
                # Stats line: upvotes · comments · subreddit
                dm = p.get('display_metrics', {})
                upvotes = dm.get('upvotes', 0)
                comments = dm.get('comments', 0)
                subreddit = (p.get('entities') or {}).get('subreddit', '')
                stats_parts = []
                if upvotes:
                    stats_parts.append(f'⬆️ {_fmt_number(upvotes)}')
                if comments:
                    stats_parts.append(f'💬 {_fmt_number(comments)}')
                if subreddit:
                    stats_parts.append(f'r/{subreddit}')
                if stats_parts:
                    lines.append(' · '.join(stats_parts))

                if url:
                    lines.append(url)

                # External article link for link posts
                ext_url = (p.get('entities') or {}).get('external_url', '')
                if ext_url:
                    lines.append(f'🔗 {ext_url}')

                # Button callback uses reddit: prefix
                callback_key = f'reddit:{item_id}'
            else:
                if url:
                    lines.append(url)
                callback_key = item_id

            lines.append('')

            picks_data.append({'index': i + 1, 'tweet_id': callback_key})

        messages.append({
            'category': cat,
            'source': source,
            'text': '\n'.join(lines),
            'picks_data': picks_data,
        })
    return messages


def _build_interesting_keyboard(picks_data, activated=None):
    """Build InlineKeyboardMarkup with 🪨/🔥 buttons.

    picks_data: [{'index': 1, 'tweet_id': '123'}, ...]
    activated: set of tweet_ids that have been clicked (shown as 🔥)
    """
    activated = activated or set()
    buttons = []
    for pd in picks_data:
        idx = pd['index']
        tid = pd['tweet_id']
        emoji = '🔥' if tid in activated else '🪨'
        buttons.append({
            'text': f'{emoji} {idx}',
            'callback_data': f'interesting:{tid}',
        })

    # Split into rows of 5 max
    rows = []
    for i in range(0, len(buttons), 5):
        rows.append(buttons[i:i + 5])

    return {'inline_keyboard': rows}


def group_picks(picks):
    by = defaultdict(list)
    for p in picks:
        by[p.get('category')].append(p)
    return by


def _send_via_telegram_http(text: str, target: str, reply_markup=None) -> bool:
    token = os.getenv('TELEGRAM_DIGEST_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        return False

    payload = {
        'chat_id': target,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)

    try:
        print(f"[x-trend][telegram] HTTP send -> chat={target}, len={len(text)}, has_buttons={'yes' if reply_markup else 'no'}")
        r = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json=payload,
            timeout=20,
        )
        ok = r.status_code == 200
        body = (r.text or "")[:400].replace("\n", " ")
        print(f"[x-trend][telegram] response status={r.status_code}, ok={ok}, body_snippet={body!r}")
        return ok
    except Exception as e:
        print(f"[x-trend][telegram] HTTP error: {e!r}")
        return False


def send_messages(messages, target: str, channel: str = 'telegram'):
    sent = 0
    # Keep HTML mode mandatory for Telegram by default (no env toggle needed).
    require_html = True

    for m in messages:
        text = m['text']
        picks_data = m.get('picks_data', [])

        token = os.getenv('TELEGRAM_DIGEST_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')

        if channel == 'telegram' and require_html and not token:
            print("[x-trend][telegram] Missing TELEGRAM_DIGEST_BOT_TOKEN/TELEGRAM_BOT_TOKEN; skip sending to avoid plain-text output")
            continue

        # Build inline keyboard if we have picks_data
        reply_markup = None
        if picks_data:
            reply_markup = _build_interesting_keyboard(picks_data)

        if token:
            print("[x-trend][telegram] Using direct Telegram Bot API path")
            if _send_via_telegram_http(text, target=target, reply_markup=reply_markup):
                sent += 1
                continue
            if channel == 'telegram' and require_html:
                print("[x-trend][telegram] HTTP send failed and TELEGRAM_REQUIRE_HTML=1; skip fallback to avoid losing bold formatting")
                continue

        print(f"[x-trend][telegram] Falling back to openclaw CLI for target={target}")
        plain_text = _strip_html(text)
        cmd = [
            'openclaw', 'message', 'send',
            '--channel', channel,
            '--target', target,
            '--message', plain_text,
        ]
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            print(f"[x-trend][telegram] openclaw CLI sent ok, output={out!r}")
            sent += 1
        except Exception as e:
            print(f"[x-trend][telegram] openclaw CLI error: {e!r}")
    return sent

#!/usr/bin/env python3
from collections import defaultdict
import html
import os
import re
import subprocess

import requests

ORDER = [
    'AI Marketing',
    'AI Coding',
    'AI Design',
    'General AI',
    'OpenClaw',
    'GitHubProjects',
]

CAT_EMOJI = {
    'AI Marketing':   '📣',
    'AI Coding':      '⚡',
    'AI Design':      '🎨',
    'General AI':     '🧠',
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


def render_messages(picks_by_category, ts, max_picks=7):
    messages = []
    for cat in ORDER:
        picks = (picks_by_category.get(cat) or [])[:max(1, min(7, max_picks))]
        if not picks:
            continue

        emoji = CAT_EMOJI.get(cat, '🔹')
        lines = [f'{emoji} <b>{_html_esc(cat)}</b> — last 48h']

        for i, p in enumerate(picks):
            title = _html_esc((p.get('title', '').strip() or '(no title)')[:300])
            why = _html_esc(p.get('why_interesting', '').strip() or '')
            url = p.get('url', '').strip()

            lines.append('')  # blank line before item
            lines.append(f'<b>{title}</b>')
            if why:
                lines.append(f'Why: {why}')
            if url:
                lines.append(url)
            lines.append('')  # blank line after item

        messages.append({'category': cat, 'text': '\n'.join(lines)})
    return messages


def group_picks(picks):
    by = defaultdict(list)
    for p in picks:
        by[p.get('category')].append(p)
    return by


def _send_via_telegram_http(text: str, target: str) -> bool:
    token = os.getenv('TELEGRAM_DIGEST_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        return False

    payload = {
        'chat_id': target,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    try:
        print(f"[x-trend][telegram] HTTP send -> chat={target}, len={len(text)}")
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

        token = os.getenv('TELEGRAM_DIGEST_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')

        if channel == 'telegram' and require_html and not token:
            print("[x-trend][telegram] Missing TELEGRAM_DIGEST_BOT_TOKEN/TELEGRAM_BOT_TOKEN; skip sending to avoid plain-text output")
            continue

        if token:
            print("[x-trend][telegram] Using direct Telegram Bot API path")
            if _send_via_telegram_http(text, target=target):
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

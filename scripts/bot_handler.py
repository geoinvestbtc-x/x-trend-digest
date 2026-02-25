#!/usr/bin/env python3
"""
Telegram Bot long-polling handler for 🔥 Interesting button callbacks.

Run as a persistent process on the server:
    python3 scripts/bot_handler.py

Flow:
1. Listen for callback_query with data "interesting:{tweet_id}"
2. Save tweet to bookmarks.jsonl
3. Update inline keyboard: 🪨 → 🔥
4. Answer with "🔥 Saved!"

Weekly digest (weekly_digest.py) will later batch-analyze all saved tweets.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import requests

# ── Root detection ────────────────────────────────────────────

def _detect_root() -> Path:
    env_root = os.getenv('X_TREND_ROOT')
    if env_root:
        return Path(env_root).expanduser()
    server_root = Path('/home/geo/.openclaw/workspace')
    if server_root.exists():
        return server_root
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()


def _load_env():
    p = ROOT / '.env'
    if not p.exists():
        return
    for line in p.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

sys.path.insert(0, str(ROOT / 'scripts'))
from bookmarks_store import save as bk_save, exists as bk_exists

# ── Config ────────────────────────────────────────────────────

TG_TOKEN = os.getenv('TELEGRAM_DIGEST_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
TG_API = f'https://api.telegram.org/bot{TG_TOKEN}'
POLL_TIMEOUT = 30


# ── Telegram helpers ──────────────────────────────────────────

def tg_get_updates(offset=None):
    params = {
        'timeout': POLL_TIMEOUT,
        'allowed_updates': json.dumps(['callback_query']),
    }
    if offset is not None:
        params['offset'] = offset
    r = requests.get(f'{TG_API}/getUpdates', params=params, timeout=POLL_TIMEOUT + 10)
    r.raise_for_status()
    return r.json().get('result', [])


def tg_answer_callback(callback_query_id, text=''):
    requests.post(f'{TG_API}/answerCallbackQuery', json={
        'callback_query_id': callback_query_id,
        'text': text,
        'show_alert': False,
    }, timeout=10)


def tg_edit_reply_markup(chat_id, message_id, reply_markup):
    r = requests.post(f'{TG_API}/editMessageReplyMarkup', json={
        'chat_id': chat_id,
        'message_id': message_id,
        'reply_markup': reply_markup,
    }, timeout=10)
    if r.status_code != 200:
        print(f"[bot] ❌ editMessageReplyMarkup failed: {r.status_code} {r.text}")
    return r.status_code == 200


def _update_keyboard_activate(existing_markup, activated_tweet_id):
    """Clone keyboard, replace 🪨 with 🔥 for the activated tweet."""
    if not existing_markup:
        return None
    new_rows = []
    for row in existing_markup.get('inline_keyboard', []):
        new_row = []
        for btn in row:
            cb = btn.get('callback_data', '')
            text = btn.get('text', '')
            if cb == f'interesting:{activated_tweet_id}':
                text = text.replace('🪨', '🔥')
            new_row.append({'text': text, 'callback_data': cb})
        new_rows.append(new_row)
    return {'inline_keyboard': new_rows}


# ── Callback handler ─────────────────────────────────────────

def _extract_tweet_info_from_message(message: dict, tweet_id: str) -> dict:
    """Try to extract tweet URL and title from the Telegram message text."""
    text = message.get('text', '')
    url = ''
    title = ''
    # Find URL containing the tweet_id
    import re
    for m in re.finditer(r'https?://x\.com/\S+/status/' + re.escape(tweet_id), text):
        url = m.group(0)
        break
    # Find category from first line
    category = ''
    first_line = text.split('\n')[0] if text else ''
    for cat in ['AI Marketing', 'AI Coding', 'AI Design', 'General AI', 'OpenClaw', 'GitHubProjects']:
        if cat in first_line:
            category = cat
            break
    return {'url': url, 'category': category}


def handle_interesting(callback_query):
    cb_data = callback_query.get('data', '')
    tweet_id = cb_data.split(':', 1)[1] if ':' in cb_data else ''
    if not tweet_id:
        return

    cq_id = callback_query.get('id')
    message = callback_query.get('message', {})
    chat_id = message.get('chat', {}).get('id')
    message_id = message.get('message_id')

    # Already saved?
    if bk_exists(tweet_id):
        tg_answer_callback(cq_id, text='🔥 Already saved!')
        return

    # Extract info from message
    info = _extract_tweet_info_from_message(message, tweet_id)

    # Save to bookmarks
    bk_save(
        tweet_id=tweet_id,
        url=info.get('url', f'https://x.com/i/status/{tweet_id}'),
        category=info.get('category', ''),
    )

    # Update keyboard: 🪨 → 🔥
    existing_markup = message.get('reply_markup')
    if existing_markup and message_id:
        new_markup = _update_keyboard_activate(existing_markup, tweet_id)
        if new_markup:
            tg_edit_reply_markup(chat_id, message_id, new_markup)

    # Answer callback
    tg_answer_callback(cq_id, text='🔥 Saved!')
    print(f"[bot] 🔥 Saved tweet {tweet_id} (cat={info.get('category', '?')})")


# ── Main polling loop ────────────────────────────────────────

def main():
    if not TG_TOKEN:
        print("[bot] ERROR: No TELEGRAM_DIGEST_BOT_TOKEN or TELEGRAM_BOT_TOKEN set")
        sys.exit(1)

    print(f"[bot] 🤖 Bot handler started")
    print(f"[bot]    ROOT: {ROOT}")
    print(f"[bot] Listening for 🔥 Interesting callbacks...")

    offset = None

    while True:
        try:
            updates = tg_get_updates(offset=offset)
            for update in updates:
                offset = update['update_id'] + 1
                cq = update.get('callback_query')
                if not cq:
                    continue
                data = cq.get('data', '')
                if data.startswith('interesting:'):
                    try:
                        handle_interesting(cq)
                    except Exception as e:
                        print(f"[bot] ❌ Error: {e}")
                        traceback.print_exc()

        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.ConnectionError as e:
            print(f"[bot] Connection error: {e}, retrying in 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"[bot] ❌ Poll error: {e}")
            traceback.print_exc()
            time.sleep(5)


if __name__ == '__main__':
    main()

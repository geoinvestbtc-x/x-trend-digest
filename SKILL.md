---
name: x-trend-digest
description: Daily trend digest for X/Twitter and AI GitHub projects. Use when user asks to collect trends for AI Marketing, AI Coding, AI Design, General AI, plus interesting new AI projects from @GithubProjects; summarize via OpenRouter nano model; deduplicate with 7-day memory; and prepare Telegram/Notion outputs.
---

# X Trend Digest

## Goal
Run a daily pipeline (manual or cron):
- Discover candidate posts/links
- Extract content with fallback
- Rank + summarize
- Dedup with 7-day memory
- Publish digest (Telegram)

## Required env
- `BRAVE_API_KEY`
- `OPENROUTER_API_KEY`

## Optional env
- `DIGEST_MODEL=openai/gpt-5-mini`
- `DIGEST_ONLY_CATEGORY=AI Marketing` (for focused testing)
- `DIGEST_MAX_PER_TOPIC=5`
- `TREND_MEMORY_DAYS=30` (dedup window)
- `SEND_TELEGRAM=1` (send immediately)
- `TELEGRAM_TARGET=<chat id | @username | phone>`
- `TELEGRAM_CHANNEL=telegram`
- `TELEGRAM_BOT_TOKEN=<telegram bot token>` (local-only direct Telegram API; server still uses OpenClaw channel)
- `X_TREND_ROOT=/path/to/workspace` (override workspace root for local runs; defaults to OpenClaw workspace if present, otherwise repo root)

## Topics
- OpenClaw Marketing
- OpenClaw Coding
- AI Marketing
- AI Coding
- AI Design
- General AI
- AI Business
- GitHub Projects (from `https://x.com/GithubProjects`)

## Run (manual)
```bash
python3 skills/x-trend-digest/scripts/run.py
```

## Outputs
- `out_trends/digest-YYYYMMDD-HHMM.md`
- `out_trends/payload-YYYYMMDD-HHMM.json`
- `out_trends/telegram-ready-YYYYMMDD-HHMM.txt`
- Memory (TTL 7d): `memory/trend-radar.jsonl`

## Pipeline
1. `discover.py` — Brave + extra sources
2. `extract.py` — page text extraction with fallback
3. `rank.py` — relevance/quality scoring
4. `summarize.py` — short RU summaries via OpenRouter nano
5. `memory_store.py` — dedup + TTL cleanup
6. `publish_telegram.py` — Telegram-ready text with 🪨 Interesting buttons

## 🔥 Interesting / Weekly Digest

Each digest tweet in Telegram has numbered 🪨 buttons. Press a button to:
1. Toggle it to 🔥 (visual feedback)
2. Save tweet to `data/bookmarks.jsonl`

On Saturday (or manually), run `weekly_digest.py` to:
1. Collect all 🔥 tweets from the past 7 days
2. Fetch full tweet + top replies + linked articles
3. LLM deep analysis per category (tools, approaches, ideas)
4. Send formatted weekly digest to Telegram

### Run bot handler (persistent process)
```bash
python3 scripts/bot_handler.py
```

### Run weekly digest (cron on Saturday or manual)
```bash
python3 scripts/weekly_digest.py
```

### Files
- `bot_handler.py` — long-polling Telegram callback handler (save only)
- `weekly_digest.py` — batch deep analysis of saved tweets
- `bookmarks_store.py` — JSONL storage for interesting tweets
- `data/bookmarks.jsonl` — bookmark records

### Optional env
- `WEEKLY_DIGEST_MODEL=openai/gpt-4o` (LLM for weekly analysis)

# X Trend Digest 🚀

Professional pipeline for discovering, ranking, and summarizing X (Twitter) trends for technical audiences. Designed to track AI and Dev topics with high precision and low noise.

## ✨ Key Features

- **Multi-Category Discovery**: Scans Top and Latest tweets for AI Coding, AI Design, AI Marketing, General AI, and more.
- **Smart Ranking**: Advanced scoring system based on engagement metrics (bookmarks, retweets, followers) with content filtering.
- **LLM-Powered Summarization**: Uses `gpt-5-mini` (via OpenRouter) to extract meaningful insights.
- **Human-Readable Titles**: Implements `_smart_excerpt` to handle original tweet wording, ensuring title integrity and technical relevance.
- **Two-Tier Memory System**:
    - **Picks**: Saved for 30 days (never repeat sent content).
    - **Ranked**: Saved for 3 days (don't waste LLM tokens on the same content twice, but allow re-evaluation later).
- **Clean Telegram Output**: Beautifully formatted messages with bold titles, "Why" explanations, and source links.
- **Usage & Cost Tracking**: Detailed reporting of token usage (including reasoning tokens) and pipeline operation costs.

## 🛠 Tech Stack

- **Python 3.9+**
- **LLM**: OpenAI GPT-5-mini via OpenRouter
- **Data Source**: TwitterAPI.io
- **Messaging**: Telegram Bot API / OpenClaw CLI

## 🚀 Getting Started

1. **Clone & Setup**:
   ```bash
   git clone https://github.com/geoinvestbtc-x/x-trend-digest.git
   cd x-trend-digest
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configuration**:
   Create a `.env` file:
   ```env
   OPENROUTER_API_KEY=your_key
   TWITTERAPI_API_KEY=your_key
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_TARGET=@your_channel
   SEND_TELEGRAM=1
   DIGEST_MODEL=openai/gpt-5-mini
   ```

3. **Run Pipeline**:
   ```bash
   python3 scripts/run.py
   ```

## 📂 Project Structure

- `scripts/run.py`: Main entry point / orchestrator.
- `scripts/discover.py`: X data fetching.
- `scripts/rank.py`: Scoring and filtering logic.
- `scripts/summarize.py`: LLM integration & title excerpting.
- `scripts/publish_telegram.py`: Message rendering and delivery.
- `scripts/memory_store.py`: Two-tier deduplication logic.

## 📊 Pipeline Observability

Each run ends with a summary in the console and an optional report in Telegram:
- 📊 Funnel stats: Discovered → Ranked → Picks → Sent.
- 🤖 LLM stats: Prompt/Completion/Reasoning tokens.
- 💰 Cost: Exact USD cost of the run.

---
*Built for Advanced Agentic Coding and AI Enthusiasts.*

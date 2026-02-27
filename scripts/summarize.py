#!/usr/bin/env python3
import json
import os
import re
import time
import requests

MODEL = os.getenv('DIGEST_MODEL', 'openai/gpt-4o-mini')

# Per-category framing injected into user context so LLM curates with intent
CATEGORY_CONTEXT = {
    'AI Marketing':   'Focus on AI-powered marketing tools, growth tactics, automation workflows, and content strategies used by real practitioners.',
    'AI Coding':      'Focus on AI coding tools, LLM integrations, agent frameworks, developer workflows, and hands-on engineering with AI.',
    'AI Design':      'Focus on AI-assisted design tools, UI/UX workflows, generative design, Figma plugins, and creative AI applications.',
    'General AI':     'Focus on AI research breakthroughs, new model releases, technical insights, benchmarks, and significant industry developments.',
    'AI Business':    'Focus on AI startup strategies, SaaS building with AI, indie hacker workflows, revenue milestones, and concrete business outcomes.',
    'OpenClaw':       'Focus on OpenClaw product insights, feature discussions, developer experience, and community feedback.',
    'GitHubProjects': 'Focus on new open-source AI projects, novel libraries, interesting repos — preference for projects with working demos or clear practical value.',
}

SYSTEM_PROMPT = (
    "Return ONLY valid minified JSON matching the provided schema. "
    "No markdown. No extra text. No reasoning.\n\n"
    "Language: ENGLISH ONLY. Do NOT translate.\n\n"
    "You are curating a BUILDER'S DIGEST — not a news feed.\n\n"
    "PREFER (pick these first):\n"
    "- Real use cases: someone shares HOW they use an AI tool and what result they got\n"
    "- Workflows & tips: practical setups, prompts, configs that worked\n"
    "- Personal results: \"I built X with Y\", \"my agent does Z\", \"this saved me N hours\"\n"
    "- Demos with substance: showing a real working thing, not just announcing it\n"
    "- Unexpected discoveries: \"I found that if you do X, Y happens\"\n\n"
    "SKIP (deprioritize or drop):\n"
    "- Corporate announcements: \"X is now live\", \"we released Y\" (unless it includes a real demo)\n"
    "- Product launches with no substance: just a name + link\n"
    "- Hype without specifics: \"AI is crazy\", \"this changes everything\"\n"
    "- Vague promo, engagement bait, giveaways\n\n"
    "TITLE:\n"
    "- Use the original tweet wording (excerpt).\n"
    "- Keep enough context so the title alone tells a story.\n"
    "- Prefer a complete sentence (end with . ! ? …).\n"
    "- Remove links and trailing hashtags.\n"
    "- Max 240 characters (up to 300 for long/article tweets, end with \"…\").\n"
    "- Do NOT cut mid-phrase.\n\n"
    "WHY_INTERESTING:\n"
    "- 1 short line: what makes this useful or surprising for a practitioner.\n"
    "- Focus on: what they built, what result they got, what technique they used.\n"
    "- NOT: \"this is interesting because…\" — just state the insight.\n\n"
    "If fewer than target_picks tweets are worth picking, return fewer. Quality > quantity."
)

JSON_SCHEMA = {
    'category': 'string',
    'picks': [{
        'id': 'string',
        'url': 'string',
        'title': 'string (original excerpt, 120-300 chars, complete thought)',
        'why_interesting': 'string (1 line, why worth reading)',
    }]
}


def _clean_tweet_text(text: str) -> str:
    """Strip t.co links, trailing hashtags, collapse whitespace."""
    t = text or ''
    t = re.sub(r'https?://t\.co/\S+', '', t)           # remove t.co links
    t = re.sub(r'https?://\S+', '', t)                  # remove other URLs
    t = re.sub(r'(\s#\w+)+\s*$', '', t)                 # trailing hashtags
    t = re.sub(r'\n{2,}', '\n', t)                      # collapse double newlines
    t = re.sub(r'[ \t]+', ' ', t)                       # collapse spaces
    return t.strip()


def _smart_excerpt(text: str, max_chars=240, min_chars=120) -> str:
    """
    Extract a self-contained excerpt from tweet text.
    Tries to end on sentence boundary (. ! ? …), then clause boundary (; : —),
    then last word boundary.
    """
    text = _clean_tweet_text(text)
    if len(text) <= max_chars:
        return text

    # take a window to search in
    window = text[:max_chars + 40]

    # try sentence-enders within [min_chars..max_chars]
    best = -1
    for m in re.finditer(r'[.!?…](?:\s|$)', window):
        pos = m.end()
        if min_chars <= pos <= max_chars:
            best = pos

    if best > 0:
        return window[:best].strip()

    # try clause-enders (; : — –)
    for m in re.finditer(r'[;:—–](?:\s|$)', window):
        pos = m.end()
        if min_chars <= pos <= max_chars:
            best = pos

    if best > 0:
        return window[:best].strip()

    # fallback: last space within max_chars
    space_pos = text.rfind(' ', min_chars, max_chars)
    if space_pos > 0:
        return text[:space_pos].strip() + '…'

    return text[:max_chars].strip() + '…'


def _mask_key(key: str) -> str:
    if not key or len(key) < 12:
        return '***'
    return key[:6] + '...' + key[-4:]


def _call_llm(category, candidates, picks_n=5, attempt=0):
    key = os.getenv('OPENROUTER_API_KEY')
    if not key:
        raise RuntimeError('OPENROUTER_API_KEY missing')

    picks_n = min(picks_n, len(candidates))

    compact = []
    for c in candidates:
        raw_text = (c.get('text') or '')
        is_long = len(raw_text) > 260
        compact.append({
            'id': c.get('id'),
            'url': c.get('url'),
            'text': raw_text[:320],
            'is_long': is_long,
            'score': c.get('score', 0),
            'metrics': c.get('metrics', {}),
        })

    user_obj = {
        'category': category,
        'category_context': CATEGORY_CONTEXT.get(category, ''),
        'target_picks': picks_n,
        'candidates': compact,
        'json_schema': JSON_SCHEMA,
    }

    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://local.openclaw',
        'X-Title': 'x-trend-digest',
    }
    payload = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps(user_obj, ensure_ascii=False)}
        ],
        'max_tokens': 2000,
        'temperature': 0.25,
        'response_format': {'type': 'json_object'},
    }

    print(f"[x-trend][llm] cat={category} model={MODEL} candidates={len(candidates)} "
          f"picks_n={picks_n} attempt={attempt}")

    r = requests.post('https://openrouter.ai/api/v1/chat/completions', headers=headers, json=payload, timeout=120)

    print(f"[x-trend][llm] cat={category} status={r.status_code} raw_len={len(r.text or '')}")

    r.raise_for_status()

    data_json = r.json()
    content = None
    finish_reason = None
    usage = {}

    try:
        choice = data_json['choices'][0]
        content = choice.get('message', {}).get('content')
        finish_reason = choice.get('finish_reason') or choice.get('native_finish_reason')
        usage = data_json.get('usage', {})
    except (KeyError, IndexError, TypeError) as e:
        print(f"[x-trend][llm] ⚠ structure error: {e}")

    prompt_tokens = usage.get('prompt_tokens', 0)
    completion_tokens = usage.get('completion_tokens', 0)
    reasoning_tokens = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)

    call_usage = {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'reasoning_tokens': reasoning_tokens,
    }

    print(f"[x-trend][llm] cat={category} finish={finish_reason} "
          f"tokens={completion_tokens} reasoning={reasoning_tokens} "
          f"content_len={len(content or '')}")

    if finish_reason in ('length', 'max_output_tokens'):
        print(f"[x-trend][llm] ⚠ TRUNCATED! reasoning={reasoning_tokens}/{completion_tokens}")

    content = (content or "").strip()

    if not content:
        msg_obj = data_json.get('choices', [{}])[0].get('message', {})
        print(f"[x-trend][llm] ⚠ EMPTY content! keys={list(msg_obj.keys())}")

    if '```' in content:
        m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', content)
        if m:
            content = m.group(1)

    return content, call_usage


# gpt-4o-mini pricing via OpenRouter (per 1M tokens)
_PRICE_INPUT = 0.15   # $/1M prompt tokens
_PRICE_OUTPUT = 0.60  # $/1M completion tokens


def run(items, picks_n=5):
    by = {}
    for it in items:
        by.setdefault(it['category'], []).append(it)

    results = []
    total_usage = {
        'prompt_tokens': 0,
        'completion_tokens': 0,
        'reasoning_tokens': 0,
        'llm_calls': 0,
    }

    for category, arr in by.items():
        arr.sort(key=lambda x: x.get('score', 0), reverse=True)
        arr = arr[:15]
        effective_picks_n = min(picks_n, len(arr))

        raw = ""
        last_error = None
        max_attempts = 2

        for attempt in range(max_attempts):
            try:
                raw, call_usage = _call_llm(category, arr, picks_n=effective_picks_n, attempt=attempt)
                total_usage['prompt_tokens'] += call_usage.get('prompt_tokens', 0)
                total_usage['completion_tokens'] += call_usage.get('completion_tokens', 0)
                total_usage['reasoning_tokens'] += call_usage.get('reasoning_tokens', 0)
                total_usage['llm_calls'] += 1

                if not raw:
                    print(f"[x-trend][llm] ⚠ empty for {category} (attempt {attempt})")
                    if attempt < max_attempts - 1:
                        time.sleep(2)
                        continue
                    raise ValueError("LLM returned empty content after retry")

                data = json.loads(raw)
                picks = data.get('picks', [])[:max(1, min(7, effective_picks_n))]
                idx = {str(a.get('id')): a for a in arr if a.get('id')}

                out_picks = []
                for p in picks:
                    base = idx.get(str(p.get('id')), {})
                    title = (p.get('title') or '').strip()
                    if len(title) < 40:
                        title = _smart_excerpt(base.get('text', ''))
                    out_picks.append({
                        'id': p.get('id') or base.get('id') or '',
                        'url': p.get('url') or base.get('url') or '',
                        'title': title,
                        'why_interesting': (p.get('why_interesting') or '').strip(),
                        'score': base.get('score', 0),
                        'category': category,
                    })

                skipped = effective_picks_n - len(out_picks)
                avg_title = (sum(len(p['title']) for p in out_picks) / len(out_picks)) if out_picks else 0
                print(f"[x-trend][llm] ✅ cat={category} picks={len(out_picks)} "
                      f"skipped_by_llm={skipped} avg_title_len={avg_title:.0f}")

                results.extend(out_picks)
                last_error = None
                break

            except Exception as e:
                last_error = e
                snippet = repr(raw[:200]) if raw else 'EMPTY'
                print(f"[x-trend][llm] ❌ {category} attempt={attempt}: {e}")
                print(f"[x-trend][llm]   snippet: {snippet}")
                if attempt < max_attempts - 1:
                    time.sleep(2)

        if last_error is not None:
            print(f"[x-trend][llm] ⚠ fallback for {category}")
            sorted_c = sorted(arr, key=lambda x: x.get('score', 0), reverse=True)
            for c in sorted_c[:effective_picks_n]:
                m = c.get('metrics', {})
                title = _smart_excerpt(c.get('text', ''), max_chars=240)
                results.append({
                    'id': c.get('id', ''),
                    'url': c.get('url', ''),
                    'title': title if title else '(no text)',
                    'why_interesting': (
                        f"bookmarks={m.get('bookmark', 0)}, "
                        f"RT={m.get('retweet', 0)}, "
                        f"followers={c.get('author', {}).get('followers', 0)}"
                    ),
                    'score': c.get('score', 0),
                    'category': category,
                })

    # calculate cost
    total_tokens = total_usage['prompt_tokens'] + total_usage['completion_tokens']
    cost_input = total_usage['prompt_tokens'] / 1_000_000 * _PRICE_INPUT
    cost_output = total_usage['completion_tokens'] / 1_000_000 * _PRICE_OUTPUT
    total_cost = cost_input + cost_output

    total_usage['total_tokens'] = total_tokens
    total_usage['cost_usd'] = round(total_cost, 4)

    return results, total_usage

#!/usr/bin/env python3
import json
import re
import subprocess

TOPIC_URLS = {
    'OpenClaw Marketing': 'https://x.com/search?q=openclaw%20marketing&src=typed_query&f=live',
    'OpenClaw Coding': 'https://x.com/search?q=openclaw%20coding&src=typed_query&f=live',
    'AI Marketing': 'https://x.com/search?q=AI%20marketing&src=typed_query&f=live',
    'AI Coding': 'https://x.com/search?q=AI%20coding&src=typed_query&f=live',
    'AI Design': 'https://x.com/search?q=AI%20design&src=typed_query&f=live',
    'General AI': 'https://x.com/search?q=AI%20tools&src=typed_query&f=live',
    'GitHub Projects': 'https://x.com/GithubProjects',
}


def _run(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


def _open(url):
    _run(['openclaw', 'browser', 'open', url, '--browser-profile', 'openclaw'])


def _snapshot():
    return _run(['openclaw', 'browser', 'snapshot', '--browser-profile', 'openclaw', '--limit', '200'])


def _extract_status_urls(text: str):
    urls = set()
    for u in re.findall(r'https?://x\.com/[A-Za-z0-9_]+/status/\d+', text):
        urls.add(u)
    for u in re.findall(r'https?://twitter\.com/[A-Za-z0-9_]+/status/\d+', text):
        urls.add(u)
    return sorted(urls)


def run(max_per_topic=8):
    out = []
    for topic, url in TOPIC_URLS.items():
        try:
            _open(url)
            snap = _snapshot()
            post_urls = _extract_status_urls(snap)[:max_per_topic]
            if not post_urls:
                # fallback: keep topic page itself
                out.append({'topic': topic, 'query': topic, 'url': url, 'title': f'{topic} page', 'snippet': 'Browser snapshot source', 'source': 'browser'})
                continue
            for pu in post_urls:
                out.append({'topic': topic, 'query': topic, 'url': pu, 'title': pu.split('/')[-3], 'snippet': 'Extracted from browser snapshot', 'source': 'browser'})
        except Exception as e:
            out.append({'topic': topic, 'query': topic, 'url': url, 'title': 'ERROR', 'snippet': str(e), 'source': 'browser'})

    # match discover.py return shape
    by = {}
    for it in out:
        by.setdefault(it['topic'], []).append(it)
    return [{'topic': t, 'items': items} for t, items in by.items()]

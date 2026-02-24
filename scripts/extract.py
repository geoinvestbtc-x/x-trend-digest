#!/usr/bin/env python3
import re
import requests


def _extract_main_text(html: str) -> str:
    txt = re.sub(r'<script[\s\S]*?</script>', ' ', html, flags=re.I)
    txt = re.sub(r'<style[\s\S]*?</style>', ' ', txt, flags=re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt


def _extract_external_urls(item: dict):
    urls = []
    entities = item.get('entities') or {}
    for u in entities.get('urls', []) or []:
        if isinstance(u, dict):
            val = u.get('expanded_url') or u.get('url') or u.get('display_url')
            if val:
                urls.append(val)
        elif isinstance(u, str):
            urls.append(u)
    # from text fallback
    for m in re.findall(r'https?://\S+', item.get('text', '')):
        urls.append(m.strip('.,)'))
    # dedup keep github/article links mostly
    out, seen = [], set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:3]


def extract_external(url: str):
    try:
        r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        html = r.text or ''
        title = ''
        m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
        if m:
            title = re.sub(r'\s+', ' ', m.group(1)).strip()
        md = ''
        m2 = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.I)
        if m2:
            md = m2.group(1).strip()
        text = _extract_main_text(html)[:2500]
        return {'ok': True, 'title': title, 'snippet': md, 'text': text, 'confidence': 0.7}
    except Exception:
        return {'ok': False, 'title': '', 'snippet': '', 'text': '', 'confidence': 0.2}


def run(items):
    out = []
    for it in items:
        it = dict(it)
        it['external'] = []
        ext_urls = _extract_external_urls(it)
        for u in ext_urls:
            if 'x.com/' in u or 'twitter.com/' in u:
                continue
            e = extract_external(u)
            e['url'] = u
            it['external'].append(e)
        if not it.get('text'):
            it['text'] = ''
        out.append(it)
    return out

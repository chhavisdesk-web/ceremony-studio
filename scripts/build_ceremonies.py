#!/usr/bin/env python3
"""Build reference/ceremonies.json: published facts about real ceremonies, each with its citation.

What it does
  1. Lists ceremonies from 2000 onwards: Olympic opening and closing ceremonies from Wikidata,
     plus the Wikipedia categories for World Cup, Commonwealth Games, Asian Games, multi-sport and
     cricket opening ceremonies and Super Bowl halftime shows, plus a short list of state ceremonies.
  2. Downloads each English Wikipedia article as wikitext, with its revision id.
  3. Keeps only sentences that state a figure we can use (budget or cost, cast and volunteers,
     TV audience, duration, attendance) AND carry a citation. The sentence, the figure as written,
     and the cited source (URL, title, publisher, date) are stored together.

What it does not do
  - It does not judge, average or scale anything. A sentence about another event (for example the
    London 2012 article quoting Beijing's budget) is kept but flagged `otherEvent`, and the app shows
    every figure with its sentence and source so a reader can judge it.
  - Figures without a citation in the article are dropped.

Text is quoted from Wikipedia under CC BY-SA 4.0; each ceremony records its article URL and revision.

Usage:  python3 scripts/build_ceremonies.py           (network: wikidata.org, en.wikipedia.org)
Then:   python3 scripts/build_reference.py closeouts  (re-checks sources and rebuilds bundle.json)
Standard library only.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'reference', 'ceremonies.json')
CACHE = os.path.join(ROOT, '.cache', 'wikipedia')
UA = 'reference-data-builder/1.0 (python-urllib; research script)'
FROM_YEAR = 2000
PAUSE = 1.5  # seconds between Wikipedia requests

CATEGORIES = [  # (category, type label)
    ('FIFA_World_Cup_opening_ceremonies', 'FIFA World Cup opening'),
    ('Super_Bowl_halftime_shows', 'Super Bowl halftime show'),
    ('Commonwealth_Games_opening_ceremonies', 'Commonwealth Games ceremony'),
    ('Asian_Games_opening_ceremonies', 'Asian Games ceremony'),
    ('Opening_ceremonies_at_multi-sport_events', 'Multi-sport opening'),
    ('Cricket_World_Cup_opening_ceremonies', 'Cricket World Cup opening'),
    ('Closing_ceremonies', 'Closing ceremony'),
]
EXTRA = [  # state and royal ceremonies with their own articles
    ('Coronation_of_Charles_III_and_Camilla', 'State ceremony'),
    ('State_funeral_of_Elizabeth_II', 'State ceremony'),
    ('Wedding_of_Prince_William_and_Catherine_Middleton', 'Royal wedding'),
    ('Wedding_of_Prince_Harry_and_Meghan_Markle', 'Royal wedding'),
]
SPARQL = """SELECT ?item ?article ?kind WHERE {
  VALUES (?cls ?kind) { (wd:Q130192067 "Olympic opening ceremony") (wd:Q94998068 "Olympic closing ceremony") }
  ?item wdt:P31 ?cls . ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }"""

METRICS = {
    'budget': re.compile(r'\b(budget|budgeted|cost|costs|costing|spent|spend|spending|price tag|expenditure)\b', re.I),
    'cast': re.compile(r'\b(performers|volunteers|dancers|cast members|cast of|drummers|musicians|participants|artists|singers|children)\b', re.I),
    'audience': re.compile(r'\b(viewers|viewership|watched by|audience of|television audience|tv audience|watched|ratings)\b', re.I),
    'duration': re.compile(r'\b(lasted|lasting|duration|minutes long|hours long|running time)\b', re.I),
    'attendance': re.compile(r'\b(attendance|spectators|attended by|crowd of|in attendance)\b', re.I),
}
CUR = r'(?:US\$|A\$|C\$|R\$|HK\$|NZ\$|\$|£|€|¥|₹|₩|RMB|CNY|USD|GBP|EUR|JPY|INR|AUD|CAD|BRL|QAR|AED|SAR)'
SCALE = r'(?:\s?(?:million|billion|trillion|bn|m|mn)\b)?'
MONEY = re.compile(CUR + r'\s?\d[\d,]*(?:\.\d+)?' + SCALE + r'|\d[\d,]*(?:\.\d+)?' + SCALE + r'\s?(?:yen|yuan|rupees|euros|pounds|dollars|reais|riyals)\b', re.I)
NUM = r'(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?(?:\s?(?:million|billion))?)'
NEAR = {  # number within three words before the noun (or "crowd of 80,000" style after it)
    'cast': re.compile(NUM + r'\s+(?:[A-Za-z-]+\s+){0,3}?(?:performers|volunteers|dancers|cast members|drummers|musicians|singers|children|athletes|participants|artists)\b', re.I),
    'audience': re.compile(NUM + r'\s+(?:[A-Za-z-]+\s+){0,3}?(?:viewers|people watched|watched|households)\b|(?:audience|viewership)\s+of\s+(?:about\s+|around\s+|an estimated\s+|over\s+|more than\s+)?' + NUM, re.I),
    'attendance': re.compile(NUM + r'\s+(?:[A-Za-z-]+\s+){0,2}?(?:spectators|attendees|people in attendance|fans in attendance)\b|(?:attendance|crowd)\s+of\s+(?:about\s+|around\s+|over\s+|more than\s+)?' + NUM, re.I),
}
DURATION = re.compile(r'\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|forty|forty-five|ninety)(?:\s+and a half)?\s?(?:hours?|minutes?|mins?)\b', re.I)
CEREMONY_WORDS = re.compile(r'\b(ceremony|ceremonies|show|halftime|performance|performed|production|opening|closing|cauldron|wedding|coronation|funeral|procession|segment|rehearsals?|cast|parade)\b', re.I)
YEAR = re.compile(r'\b(19[0-9]{2}|20[0-3][0-9])\b')
ROMAN = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}


def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')


def get(url, accept=None, tries=4):
    for i in range(tries):
        req = urllib.request.Request(url, headers={'User-Agent': UA, **({'Accept': accept} if accept else {})})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and i < tries - 1:
                wait = e.headers.get('Retry-After', '')
                time.sleep(min(120, int(wait)) if wait.isdigit() else 10 * (i + 1))
                continue
            if e.code == 404:
                return None
            raise
    return None


def cached(name, url):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(name.encode()).hexdigest() + '.txt')
    if os.path.exists(path):
        return open(path, encoding='utf-8').read()
    time.sleep(PAUSE)
    text = get(url)
    if text is not None:
        open(path, 'w', encoding='utf-8').write(text)
    return text


def roman(s):
    total, prev = 0, 0
    for ch in reversed(s):
        v = ROMAN.get(ch, 0)
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def year_of(title, text=''):
    m = YEAR.search(title.replace('_', ' '))
    if m:
        return int(m.group(1))
    if text:
        d = infobox(text).get('date', '')
        m = YEAR.search(d)
        if m:
            return int(m.group(1))
    sb = re.match(r'Super_Bowl_([IVXLC]+|\d+)_halftime_show$', title)
    if sb:  # Super Bowl I was played in January 1967; game n is played in early year 1966 + n
        n = int(sb.group(1)) if sb.group(1).isdigit() else roman(sb.group(1))
        return 1966 + n
    if text:  # first year in the lead paragraph, after templates are removed
        lead = plain(strip_templates(re.sub(r'<ref[^>]*?(?:/>|>.*?</ref>)', '', text, flags=re.S)))[:800]
        m = YEAR.search(lead)
        if m:
            return int(m.group(1))
    return None


def category_pages(cat, depth=2, seen=None):
    seen = seen if seen is not None else set()
    if cat in seen or depth < 0:
        return []
    seen.add(cat)
    h = cached('cat:' + cat, 'https://en.wikipedia.org/wiki/Category:' + cat) or ''
    sub = h[h.find('id="mw-subcategories"'):h.find('id="mw-pages"')] if 'mw-subcategories' in h else ''
    pages = h[h.find('id="mw-pages"'):] if 'mw-pages' in h else ''
    if 'class="printfooter"' in pages:
        pages = pages[:pages.find('class="printfooter"')]
    out = [urllib.parse.unquote(t) for t in re.findall(r'href="/wiki/([^":#?]+)"', pages)]
    for sc in re.findall(r'href="/wiki/Category:([^"#?]+)"', sub):
        out += category_pages(urllib.parse.unquote(sc), depth - 1, seen)
    return out


def is_ceremony(title):
    t = title.lower()
    return any(k in t for k in ('ceremony', 'ceremonies', 'halftime_show')) and not t.startswith('list_of') and 'controversy' not in t


# ---------------------------------------------------------------- wikitext parsing
def strip_templates(s):
    """Remove {{...}} templates (nested), keeping the visible text of a few inline ones."""
    s = re.sub(r'\{\{\s*(?:convert|cvt)\s*\|\s*([\d.,]+)\s*\|\s*([^|}]+)[^}]*\}\}', r'\1 \2', s, flags=re.I)
    s = re.sub(r'\{\{\s*(?:nowrap|nobr)\s*\|([^{}]*)\}\}', r'\1', s, flags=re.I)
    s = re.sub(r'\{\{\s*(?:US\$|USD)\s*\|\s*([\d.,]+)\s*(?:\|\s*(million|billion))?[^}]*\}\}', lambda m: 'US$' + m.group(1) + (' ' + m.group(2) if m.group(2) else ''), s, flags=re.I)
    s = re.sub(r'\{\{\s*(?:GBP|£)\s*\|\s*([\d.,]+)[^}]*\}\}', r'£\1', s, flags=re.I)
    s = re.sub(r'\{\{\s*(?:€|EUR)\s*\|\s*([\d.,]+)[^}]*\}\}', r'€\1', s, flags=re.I)
    out, depth, i = [], 0, 0
    while i < len(s):
        if s.startswith('{{', i):
            depth += 1; i += 2; continue
        if s.startswith('}}', i) and depth:
            depth -= 1; i += 2; continue
        if not depth:
            out.append(s[i])
        i += 1
    return ''.join(out)


def drop_files(s):
    out, i = [], 0
    while i < len(s):
        m = re.compile(r'\[\[\s*(?:File|Image)\s*:', re.I).match(s, i)
        if m:
            depth, j = 0, i
            while j < len(s):
                if s.startswith('[[', j): depth += 1; j += 2; continue
                if s.startswith(']]', j):
                    depth -= 1; j += 2
                    if depth == 0: break
                    continue
                j += 1
            i = j; continue
        out.append(s[i]); i += 1
    return ''.join(out)


def plain(s):
    s = drop_files(html.unescape(s))
    s = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', s)
    s = re.sub(r'\[https?://\S+\s([^\]]*)\]', r'\1', s)
    s = re.sub(r"'{2,}", '', s)
    s = re.sub(r'<[^>]+>', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def cite_of(body):
    body = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]*)\]\]', r'\1', body)
    def f(k):
        m = re.search(r'\|\s*' + k + r'\s*=\s*([^|}]*)', body, re.I)
        return plain(m.group(1)) if m else ''
    m = re.search(r'\|\s*url\s*=\s*(https?://[^\s|}]+)', body, re.I) or re.search(r'https?://[^\s|\]}<]+', body)
    url = m.group(1) if m and m.re.groups else (m.group(0) if m else '')
    return {'url': url, 'title': f('title') or plain(strip_templates(body))[:160], 'work': f('work') or f('newspaper') or f('website') or f('publisher') or f('magazine') or f('journal'),
            'date': f('date'), 'archive': f('archive-url')}


def statements(wikitext, event_year, only_sections=None):
    t = re.sub(r'<!--.*?-->', '', wikitext, flags=re.S)
    named = {}
    for m in re.finditer(r'<ref\s+name\s*=\s*"?([^">/]+?)"?\s*>(.*?)</ref>', t, flags=re.S | re.I):
        named[m.group(1).strip()] = m.group(2)
    # replace every ref with a marker that carries its citation index
    cites = []
    def ref_marker(m):
        body = m.group(2) if m.group(2) is not None else named.get((m.group(1) or '').strip(), '')
        if not body:
            return ''
        cites.append(cite_of(body))
        return f' ⟦{len(cites) - 1}⟧'
    t = re.sub(r'<ref(?:\s+name\s*=\s*"?([^">/]+?)"?)?\s*(?:/>|>(.*?)</ref>)', ref_marker, t, flags=re.S | re.I)
    if only_sections:
        parts, keep, cur = re.split(r'^(==+[^=\n]+==+)\s*$', t, flags=re.M), [], False
        for p in parts:
            if re.match(r'^==+', p):
                cur = any(k in p.lower() for k in only_sections)
            elif cur:
                keep.append(p)
        t = '\n'.join(keep)
    t = drop_files(strip_templates(t))
    t = re.sub(r'^==+[^=\n]+==+\s*$', '\n', t, flags=re.M)
    out = []
    for para in re.split(r'\n\s*\n', t):
        para = re.sub(r'\s*\n\s*', ' ', para).strip()
        if not para or para.startswith(('|', '!', '{|', '*', '#', ';', ':')) and len(para) < 40:
            continue
        para = re.sub(r'([.!?]["\u201d\u2019]?)((?:\s*⟦\d+⟧)+)', r'\2\1', para)  # citations after the full stop belong to that sentence
        for sent in re.split(r'(?<=[.!?])["\u201d\u2019]?\s+(?=[A-Z"\u201c])', para):
            # citations that follow this sentence belong to it
            idx = [int(x) for x in re.findall(r'⟦(\d+)⟧', sent)]
            text = plain(re.sub(r'\s*⟦\d+⟧', '', sent))
            if len(text) < 25 or not idx:
                continue
            refs = [cites[i] for i in dict.fromkeys(idx) if cites[i].get('url') or cites[i].get('title')]
            if not refs:
                continue
            for metric, rx in METRICS.items():
                if not rx.search(text):
                    continue
                if metric == 'budget':
                    vals = MONEY.findall(text)
                elif metric == 'duration':
                    vals = DURATION.findall(text)
                else:
                    vals = [next(g for g in m.groups() if g) for m in NEAR[metric].finditer(text)]
                    vals = [v for v in vals if not YEAR.fullmatch(v.strip())]
                if not vals:
                    continue
                years = {int(y) for y in YEAR.findall(text)}
                out.append({'metric': metric, 'figures': list(dict.fromkeys(v.strip() for v in vals))[:6],
                            'scope': 'ceremony' if CEREMONY_WORDS.search(text) else 'context',
                            'text': text[:420], 'otherEvent': bool(event_year and years and event_year not in years and any(abs(y - event_year) > 1 for y in years)),
                            'refs': refs[:4]})
    return out


def infobox(wikitext):
    m = re.search(r'\{\{\s*Infobox[^|]*\|(.*?)\n\}\}', wikitext, flags=re.S | re.I)
    if not m:
        return {}
    box = {}
    for k, v in re.findall(r'^\s*\|\s*([a-z_ ]+?)\s*=\s*(.*)$', m.group(1), flags=re.M | re.I):
        if k.strip().lower() == 'date' and not re.search(r'\d', v):
            continue
        k = k.strip().lower().replace(' ', '_')
        if k in ('date', 'time', 'venue', 'location', 'director', 'artistic_director', 'producer', 'theme', 'also_known_as', 'headliner', 'attendance', 'duration', 'organiser', 'organizer'):
            val = plain(strip_templates(v))
            if val:
                box[k] = val[:200]
    dm = re.search(r'\{\{\s*(?:Start date(?: and age)?|start date)\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})', m.group(1), flags=re.I)
    if dm:
        box['date'] = f'{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}'
    return box


def main():
    sparql = get('https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': SPARQL}), accept='application/sparql-results+json')
    titles = {}
    for b in json.loads(sparql)['results']['bindings']:
        t = urllib.parse.unquote(b['article']['value'].rsplit('/wiki/', 1)[1])
        titles[t] = {'type': b['kind']['value'], 'wikidata': b['item']['value'].rsplit('/', 1)[1]}
    for cat, kind in CATEGORIES:
        for t in category_pages(cat):
            if is_ceremony(t) and t not in titles:
                titles[t] = {'type': kind}
    for t, kind in EXTRA:
        titles.setdefault(t, {'type': kind})
    print(f'{len(titles)} candidate articles')
    out, skipped = [], []
    for t, meta in sorted(titles.items()):
        y = year_of(t)
        if y is not None and y < FROM_YEAR:
            continue
        url = 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(t)
        raw = cached('raw:' + t, 'https://en.wikipedia.org/w/index.php?' + urllib.parse.urlencode({'title': t, 'action': 'raw'}))
        redir = re.match(r'\s*#REDIRECT\s*\[\[([^\]|#]+)', raw or '', re.I)
        if redir:  # follow one redirect
            t2 = redir.group(1).strip().replace(' ', '_')
            raw = cached('raw:' + t2, 'https://en.wikipedia.org/w/index.php?' + urllib.parse.urlencode({'title': t2, 'action': 'raw'}))
            if raw and not re.match(r'\s*#REDIRECT', raw, re.I):
                t, url = t2, 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(t2)
        if not raw or re.match(r'\s*#REDIRECT', raw, re.I):
            skipped.append(t); continue
        curated = meta['type'] in ('State ceremony', 'Royal wedding') or meta.get('wikidata')
        if not YEAR.search(t.replace('_', ' ')) and not re.match(r'Super_Bowl_([IVXLC]+|\d+)_halftime_show$', t) and not curated:
            continue  # yearless articles outside the curated list are not single ceremonies
        page = cached('html:' + t, url) or ''
        rev = re.search(r'"wgRevisionId":(\d+)', page)
        y = y or year_of(t, raw)
        if y is None or y < FROM_YEAR:
            continue
        box = infobox(raw)
        st = statements(raw, y)
        out.append({'id': re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-'), 'title': t.replace('_', ' '), 'type': meta['type'], 'year': y,
                    **({'wikidata': meta['wikidata']} if meta.get('wikidata') else {}),
                    'url': url, 'revision': int(rev.group(1)) if rev else None,
                    'permalink': f'https://en.wikipedia.org/w/index.php?title={urllib.parse.quote(t)}&oldid={rev.group(1)}' if rev else url,
                    'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(), 'facts': box, 'statements': st})
        print(f'  {y} {t}: {len(st)} cited figures')
    out.sort(key=lambda c: (c['year'], c['title']))
    data = {
        '_about': 'Published facts about ceremonies since 2000, extracted from English Wikipedia. Each statement is the article sentence, the figures as written, and the sources the article cites for it. Figures are not averaged, converted or scaled. otherEvent = the sentence names another year, so it may describe a different event. Check the cited source before quoting a figure to a client.',
        'source': 'wikipedia-en', 'license': 'Text quoted from Wikipedia, CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)',
        'builtAt': now(), 'fromYear': FROM_YEAR, 'skipped': skipped, 'ceremonies': out,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')
    n = sum(len(c['statements']) for c in out)
    print(f'wrote reference/ceremonies.json: {len(out)} ceremonies, {n} cited figures')


if __name__ == '__main__':
    sys.exit(main())

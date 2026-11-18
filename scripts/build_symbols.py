#!/usr/bin/env python3
"""Build reference/symbols.json: sourced local symbols and traditions for the design step.

For every country (sovereign states, plus England, Scotland, Wales and Northern Ireland) and every
host city in the catalogue, it collects from Wikidata:
  - the flag's colours with their sRGB hex codes (flag P163 -> color P462 -> hex P465)
  - the anthem (P85), the motto (P1451) and other official symbols (P2238), such as a national flower
  - elements on the UNESCO intangible cultural heritage lists (P3259), with their UNESCO page (P10221)
It also builds a small motif dictionary: for each motif, the sentences from its English Wikipedia
article that say what it symbolises, and Iconclass entries that name it as a symbol (Iconclass is CC0).

Nothing is written by hand except the list of motifs to look up. Every item keeps its Wikidata,
Wikipedia, UNESCO or Iconclass link.

Usage:  python3 scripts/build_symbols.py   (after build_catalog.py and build_places.py)
Network: query.wikidata.org, en.wikipedia.org, raw.githubusercontent.com (Iconclass data)
Then:   python3 scripts/build_reference.py check
"""
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ceremonies import get, cached, now, plain  # noqa: E402
from build_catalog import follow  # noqa: E402
from build_places import prose  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'reference', 'symbols.json')
ICH_LISTS = {
    'Q110319947': 'Representative List of the Intangible Cultural Heritage of Humanity',
    'Q17323370': 'List of Intangible Cultural Heritage in Need of Urgent Safeguarding',
    'Q877988': 'Masterpieces of the Oral and Intangible Heritage of Humanity',
    'Q17325849': 'Register of Good Safeguarding Practices',
}

# Motifs to look up (the only hand-written list): the Wikipedia article for each, and the app motif it suits.
MOTIFS = [
    ('Olive branch', 'Olive_branch', None), ('Laurel wreath', 'Laurel_wreath', 'rings'), ('Dove', 'Doves_as_symbols', None),
    ('Phoenix', 'Phoenix_(mythology)', None), ('Torch', 'Torch', 'rays'), ('Flame', 'Eternal_flame', 'rays'),
    ('Tree of life', 'Tree_of_life', None), ('Rose', 'Rose_symbolism', 'petals'), ('Lotus', 'Nelumbo_nucifera', 'petals'),
    ('Heart', 'Heart_symbol', None), ('Handshake', 'Handshake', None), ('Endless knot', 'Endless_knot', 'lozenge'),
    ('Crescent', 'Crescent', None), ('Five-pointed star', 'Five-pointed_star', 'star'), ('Sun', 'Solar_symbol', 'rays'),
    ('Rainbow', 'Rainbow', 'stripes'), ('Eagle', 'Eagle_(heraldry)', None), ('Lion', 'Lion_(heraldry)', None),
    ('Crown', 'Crown_(heraldry)', None), ('Oak', 'Oak', None), ('Anchor', 'Anchor', None), ('Key', 'Key_(lock)', None),
    ('Bridge', 'Bridge', None), ('Wheel', 'Wheel', 'rings'), ('Spiral', 'Spiral', 'rings'), ('Labyrinth', 'Labyrinth', 'grid'),
    ('Mandala', 'Mandala', 'petals'), ('Butterfly', 'Butterfly', None), ('Lantern', 'Sky_lantern', None), ('Wave', 'Wave_(heraldry)', 'waves'),
]
ICONCLASS_FILES = ['txt_en_2_3.txt', 'txt_en_4.txt', 'txt_en_5_6_7_8.txt', 'txt_en_9.txt']  # nature, society, ideas, history, myth (not the religious sections)
ICONCLASS_RAW = 'https://raw.githubusercontent.com/iconclass/data/main/txt/en/'


API = 'https://www.wikidata.org/w/api.php?'


def api(params, key):
    path_key = 'wdapi:' + key
    try:
        body = cached(path_key, API + urllib.parse.urlencode({**params, 'format': 'json'}))
    except Exception as err:  # rate limited: wait once more, then retry
        print('  waiting after', err); time.sleep(90)
        body = cached(path_key, API + urllib.parse.urlencode({**params, 'format': 'json'}))
    return json.loads(body) if body else {}


def search(expr):
    """Item ids with a given statement, via Wikidata's search (haswbstatement)."""
    ids, off = [], 0
    while off is not None:
        d = api({'action': 'query', 'list': 'search', 'srsearch': expr, 'srlimit': 500, 'sroffset': off, 'srprop': '', 'srnamespace': 0}, f'search:{expr}:{off}')
        ids += [x['title'] for x in d.get('query', {}).get('search', [])]
        off = d.get('continue', {}).get('sroffset')
    return ids


def entities(ids=(), titles=()):
    """Entities in batches of 50, by id or by English Wikipedia title."""
    out = {}
    for field, items in (('ids', list(dict.fromkeys(ids))), ('titles', list(dict.fromkeys(titles)))):
        for i in range(0, len(items), 50):
            chunk = '|'.join(items[i:i + 50])
            q = {'action': 'wbgetentities', field: chunk, 'props': 'labels|descriptions|claims|sitelinks/urls', 'languages': 'en', 'sitefilter': 'enwiki'}
            if field == 'titles':
                q['sites'] = 'enwiki'
            for k, e in api(q, f'ent:{field}:{chunk}').get('entities', {}).items():
                if 'missing' not in e:
                    out[e.get('id', k)] = e
    return out


def label(e):
    return (e or {}).get('labels', {}).get('en', {}).get('value')


def desc(e):
    return (e or {}).get('descriptions', {}).get('en', {}).get('value', '')


def article(e):
    return (e or {}).get('sitelinks', {}).get('enwiki', {}).get('url')


def claims(e, prop):
    """Current values of a property: deprecated and ended statements are skipped; preferred ones win."""
    cs = [c for c in (e or {}).get('claims', {}).get(prop, []) if c.get('rank') != 'deprecated' and 'P582' not in c.get('qualifiers', {})
          and c['mainsnak'].get('snaktype') == 'value']
    pref = [c for c in cs if c.get('rank') == 'preferred']
    return pref or cs


def value(c):
    v = c['mainsnak']['datavalue']['value']
    return v.get('id') if isinstance(v, dict) and 'id' in v else v


def commons(name):
    return 'https://commons.wikimedia.org/wiki/File:' + urllib.parse.quote(name.replace(' ', '_')) if name else None


def wd_url(q):
    return 'https://www.wikidata.org/wiki/' + q


def collect(ents, ich_props):
    """Symbols for the given entities; ich_props are the properties that tie a UNESCO element to them."""
    out = {}
    for q, e in ents.items():
        out[q] = {'name': (e.get('sitelinks', {}).get('enwiki', {}).get('title') or label(e)), 'wikidata': wd_url(q), 'article': article(e),
                  'flag': next((commons(value(c)) for c in claims(e, 'P41')), None), 'coatOfArms': next((commons(value(c)) for c in claims(e, 'P94')), None),
                  'anthem': None, 'mottos': [], 'colours': [], 'symbols': [], 'traditions': []}
        for c in claims(e, 'P1451'):
            v = value(c)
            if isinstance(v, dict) and v.get('text') and v['text'] not in [m['text'] for m in out[q]['mottos']]:
                out[q]['mottos'].append({'text': v['text'], 'lang': v.get('language', '')})
    refs = set()
    for e in ents.values():
        for p in ('P85', 'P163', 'P462', 'P2238'):
            refs |= {value(c) for c in claims(e, p)}
    sub = entities(ids=sorted(refs))
    colours = {value(c) for f in sub.values() for c in claims(f, 'P462')} | {value(c) for e in ents.values() for c in claims(e, 'P462')}
    types = {value(c) for x in sub.values() for c in claims(x, 'P31')}
    more = entities(ids=sorted((colours | types) - set(sub)))
    allx = {**sub, **more}
    def colour(cq, via):
        ce = allx.get(cq)
        hexv = next((value(c) for c in claims(ce, 'P465')), None)
        if hexv and re.fullmatch(r'#?[0-9A-Fa-f]{6}', hexv):
            return {'name': label(ce), 'hex': '#' + hexv.upper().lstrip('#'), 'via': via, 'wikidata': wd_url(cq)}
    for q, e in ents.items():
        o = out[q]
        an = next((value(c) for c in claims(e, 'P85')), None)
        if an and label(allx.get(an)):
            o['anthem'] = {'name': label(allx[an]), 'wikidata': wd_url(an), 'article': article(allx[an])}
        flag = next((value(c) for c in claims(e, 'P163')), None)
        cols = [colour(value(c), 'flag') for c in claims(allx.get(flag), 'P462')] if flag else []
        if not any(cols):
            cols = [colour(value(c), 'entity') for c in claims(e, 'P462')]
        for c in cols:
            if c and c['hex'] not in [x['hex'] for x in o['colours']]:
                o['colours'].append(c)
        if flag and o['colours'] and o['colours'][0]['via'] == 'flag':
            o['flagItem'] = wd_url(flag)
        for c in claims(e, 'P2238'):
            sq = value(c); se = allx.get(sq)
            if not label(se):
                continue
            o['symbols'].append({'name': label(se), 'description': desc(se), 'types': [label(allx.get(value(t))) for t in claims(se, 'P31') if label(allx.get(value(t)))],
                                 'wikidata': wd_url(sq), 'article': article(se)})
    # UNESCO intangible heritage: every listed element, tied to the entity by country (P17) or location (P131/P276)
    for lq, lname in ICH_LISTS.items():
        for xq, x in entities(ids=search(f'haswbstatement:P3259={lq}')).items():
            if not label(x):
                continue
            st = next((c for c in x.get('claims', {}).get('P3259', []) if value(c) == lq), None) if 'P3259' in x.get('claims', {}) else None
            start = st and st.get('qualifiers', {}).get('P580', [{}])[0].get('datavalue', {}).get('value', {}).get('time')
            ich = next((value(c) for c in claims(x, 'P10221')), None)
            item = {'name': label(x), 'description': desc(x), 'list': lname, 'year': int(start[1:5]) if start else None,
                    'unesco': f'https://ich.unesco.org/en/{ich}' if ich else None, 'wikidata': wd_url(xq), 'article': article(x)}
            for p in ich_props:
                for c in claims(x, p):
                    o = out.get(value(c))
                    if o and item['wikidata'] not in [t['wikidata'] for t in o['traditions']]:
                        o['traditions'].append(item)
    for e in out.values():
        e['traditions'].sort(key=lambda x: (x['year'] or 0), reverse=True)
    return out


def symbol_sentences(title):
    """Sentences from the article's lead and symbolism sections that say what the thing symbolises."""
    t, text = follow(title)
    if not text:
        return t, []
    body = prose(text)
    parts = re.split(r'^(==[^=].*?==)\s*$', body, flags=re.M)
    keep = parts[0] + ''.join(parts[i + 1] for i in range(1, len(parts) - 1, 2) if re.search(r'symbol|meaning|significance|culture', parts[i], re.I))
    flat = plain(re.sub(r'\s*\n\s*', ' ', keep))
    flat = re.sub(r'\(\s*[;,\s]*\)', '', flat)
    out = []
    for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])', flat):
        s = re.sub(r'\s{2,}', ' ', s).strip()
        if 40 < len(s) < 320 and re.search(r'\bsymbol(?:s|ise|ize|ises|izes|ised|ized|ic|ism)?\b|\brepresent(?:s|ed|ing)?\b|\bemblem', s, re.I):
            out.append(s)
        if len(out) >= 3:
            break
    return t, out


def iconclass_lines():
    lines = []
    for f in ICONCLASS_FILES:
        text = cached('iconclass:' + f, ICONCLASS_RAW + f) or ''
        for ln in text.splitlines():
            if '|' in ln:
                n, d = ln.split('|', 1)
                if re.search(r'symbol|allegor|meaning', d, re.I) and not re.search(r' - [A-Z]{2} - |\(\.\.\.\)', d):
                    lines.append((n, d))
    return lines


def motifs():
    ic = iconclass_lines()
    out = []
    for name, title, app_motif in MOTIFS:
        t, sents = symbol_sentences(title)
        word = name.lower().split()[-1].rstrip('s')
        hits = [{'notation': n, 'text': d, 'url': 'https://iconclass.org/' + urllib.parse.quote(n)} for n, d in ic
                if re.search(r'\b' + re.escape(word) + r's?\b', d, re.I)][:4]
        if not sents and not hits:
            print(f'  motif {name}: nothing sourced, left out')
            continue
        out.append({'name': name, 'appMotif': app_motif, 'article': 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(t), 'sentences': sents, 'iconclass': hits})
        print(f'  motif {name}: {len(sents)} sentences, {len(hits)} Iconclass')
    return out


def main():
    print('countries ...')
    ids = search('haswbstatement:P31=Q3624078 -haswbstatement:P576') + ['Q21', 'Q22', 'Q25', 'Q26']
    countries = collect(entities(ids=ids), ('P17',))
    places = json.load(open(os.path.join(ROOT, 'reference', 'places.json'), encoding='utf-8'))
    print('cities ...')
    cents = entities(titles=[v['title'] for v in places['cities'].values()])
    cities = collect(cents, ('P131', 'P276'))
    by_title = {e['name']: e for e in cities.values()}
    by_city = {name: by_title[v['title']] for name, v in places['cities'].items() if v['title'] in by_title}
    data = {
        '_about': 'Local symbols and traditions for the design step. Countries and catalogue host cities: flag colours (with hex), anthem, motto and official symbols from Wikidata, and elements of the UNESCO intangible cultural heritage lists (from Wikidata, linked to their UNESCO page). Motifs: sentences from English Wikipedia that state what each motif symbolises, and Iconclass entries. Nothing is written by hand except the list of motifs looked up.',
        'source': 'wikidata', 'license': 'Wikidata CC0; Wikipedia text CC BY-SA 4.0; Iconclass CC0', 'builtAt': now(),
        'iconclassCitation': 'H. van de Waal, Iconclass, an iconographic classification system. Online edition by E. Posthumus & J.P.J. Brandhorst, 2024. https://iconclass.org/',
        'countries': {e['name']: e for e in sorted(countries.values(), key=lambda e: e['name'] or '') if e['name']},
        'cities': by_city,
        'motifs': motifs(),
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1); fh.write('\n')
    n_t = sum(len(e['traditions']) for e in data['countries'].values())
    print(f"wrote reference/symbols.json: {len(data['countries'])} countries ({n_t} UNESCO traditions), {len(by_city)} cities, {len(data['motifs'])} motifs")


if __name__ == '__main__':
    main()

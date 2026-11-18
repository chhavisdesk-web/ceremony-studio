#!/usr/bin/env python3
"""Build reference/places.json: short, sourced descriptions of the cities and venues in the catalogue.

For every venue in reference/catalog.json and every host city of its events, it takes the opening
sentences of the English Wikipedia article (and, for cities, the first paragraph of the Culture
section when there is one). Each entry keeps the article permalink (revision id). Research uses these
for the "local connection" part when the city has no built-in profile, instead of anything invented.

Usage:  python3 scripts/build_places.py   (after build_catalog.py; network: en.wikipedia.org)
Then:   python3 scripts/build_reference.py closeouts
Standard library only. Text quoted from Wikipedia is CC BY-SA 4.0.
"""
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ceremonies import plain, strip_templates, drop_files, now  # noqa: E402
from build_catalog import follow, box_fields, revision, SERIES, to_roman  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'reference', 'places.json')


def prose(wikitext):
    t = re.sub(r'<!--.*?-->', '', wikitext, flags=re.S)
    t = re.sub(r'<ref[^>]*?(?:/>|>.*?</ref>)', '', t, flags=re.S | re.I)
    t = drop_files(strip_templates(t))
    t = re.sub(r'^\s*[{|!].*$', '', t, flags=re.M)  # table rows left after template removal
    return t


def sentences(text, n, limit):
    text = plain(re.sub(r'\s*\n\s*', ' ', text))
    text = re.sub(r'\(\s*(?:[A-Z][A-Za-z ]{1,30}:\s*)?[;,\s]*\)', '', text)  # brackets emptied by removed pronunciation/language templates
    text = re.sub(r'\(\s*[;,]\s*', '(', text)
    text = re.sub(r',\s*,', ',', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\s+([,.;])', r'\1', text)
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    out = ''
    for p in parts[:n]:
        if len(out) + len(p) > limit:
            break
        out += (' ' if out else '') + p
    return out.strip()


def lead(wikitext, n=3, limit=600):
    body = prose(wikitext)
    first = re.split(r'^==[^=].*?==\s*$', body, maxsplit=1, flags=re.M)[0]
    return sentences(first, n, limit)


def section(wikitext, names, n=3, limit=600):
    body = prose(wikitext)
    parts = re.split(r'^(==[^=].*?==)\s*$', body, flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        if any(re.fullmatch(r'==\s*' + nm + r'\s*==', parts[i].strip(), re.I) for nm in names):
            sub = re.sub(r'^=+[^=\n]+=+\s*$', '\n\n', parts[i + 1], flags=re.M)  # sub-headings become paragraph breaks
            paras = [p for p in re.split(r'\n\s*\n', sub) if len(p.strip()) > 80]
            return sentences(paras[0], n, limit) if paras else ''
    return ''


def entry(title, text, kind):
    rev = revision(title)
    url = 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(title)
    e = {'title': title.replace('_', ' '), 'url': url, 'permalink': f'https://en.wikipedia.org/w/index.php?title={urllib.parse.quote(title)}&oldid={rev}' if rev else url,
         'summary': lead(text)}
    if kind == 'city':
        e['culture'] = section(text, ['Culture', 'Arts and culture', 'Culture and contemporary life', 'Cultural life'])
    return e


def city_titles(catalog):
    """City article titles from each event's infobox link (exact), else the city name."""
    out = {}
    for ev in catalog['events']:
        city = ev['fields'].get('city', {}).get('value')
        if not city or city in out:
            continue
        title = None
        for pat in [p for k, _, ps, _, _ in SERIES if k == ev['series'] for p in ps]:
            cand = pat.format(y=ev['year'], sb=to_roman(ev['year'] - 1966)) if '{sb}' in pat else pat.format(y=ev['year'])
            _, text = follow(cand)
            if text:
                f = box_fields(text)
                for k in ('city', 'host_city', 'location', 'venue'):
                    for tgt in re.findall(r'\[\[([^\]|#]+)', f.get(k, '')):
                        if city.lower() in tgt.lower():
                            title = tgt.strip().replace(' ', '_'); break
                    if title: break
                break
        out[city] = {'title': title or city.replace(' ', '_'), 'country': ev['fields'].get('country', {}).get('value')}
    return out


def main():
    catalog = json.load(open(os.path.join(ROOT, 'reference', 'catalog.json'), encoding='utf-8'))
    venues, cities = {}, {}
    for v in catalog.get('venues', []):
        title, text = follow(v['name'].replace(' ', '_'))
        if text:
            venues[v['name']] = entry(title, text, 'venue')
    for city, meta in city_titles(catalog).items():
        title, text = follow(meta['title'])
        if not text or re.search(r'\{\{\s*(?:disambiguation|disambig|geodis|hndis)', text, re.I):
            print(f'  no single article for {city}'); continue
        cities[city] = {**entry(title, text, 'city'), 'country': meta['country']}
        print(f"  {city}: {len(cities[city]['summary'])} + {len(cities[city]['culture'])} chars")
    data = {'_about': 'Opening sentences (and, for cities, the start of the Culture section) of English Wikipedia articles for the catalogue\'s cities and venues. Quoted, not rewritten; each entry links to the article revision.',
            'source': 'wikipedia-en', 'license': 'Text quoted from Wikipedia, CC BY-SA 4.0', 'builtAt': now(), 'cities': cities, 'venues': venues}
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1); fh.write('\n')
    print(f'wrote reference/places.json: {len(cities)} cities, {len(venues)} venues')


if __name__ == '__main__':
    main()

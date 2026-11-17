#!/usr/bin/env python3
"""Build reference/catalog.json: real past and upcoming events the Brief can autocomplete.

For each edition of a recurring event (UEFA finals, Olympics, World Cups, Super Bowl, Eurovision...)
it reads the English Wikipedia article's infobox for the date, venue, city and country, then the
venue's own article for its capacity, and Wikidata for each country's currency. Every value keeps
its source: the article (with revision) and, where the infobox cites one, that citation.

Nothing is estimated. An edition whose article has no venue yet simply has no venue.

Usage:  python3 scripts/build_catalog.py      (network: en.wikipedia.org, query.wikidata.org)
Then:   python3 scripts/build_reference.py closeouts
Standard library only. Text quoted from Wikipedia is CC BY-SA 4.0.
"""
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ceremonies import cached, get, plain, strip_templates, cite_of, now, roman  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'reference', 'catalog.json')

R = lambda a, b, step=1: list(range(a, b + 1, step))
FOOT = ('Football final kickoff show', 'Stadium')
OPEN = ('Tournament opening ceremony', 'Stadium')
SERIES = [  # key, display name, title patterns, years, (event type, venue type)
    ('ucl', 'UEFA Champions League final', ['{y}_UEFA_Champions_League_final'], R(2015, 2030), FOOT),
    ('uel', 'UEFA Europa League final', ['{y}_UEFA_Europa_League_final'], R(2015, 2030), FOOT),
    ('uecl', 'UEFA Conference League final', ['{y}_UEFA_Conference_League_final', '{y}_UEFA_Europa_Conference_League_final'], R(2022, 2030), FOOT),
    ('uwcl', "UEFA Women's Champions League final", ["{y}_UEFA_Women's_Champions_League_final"], R(2015, 2030), FOOT),
    ('euro', 'UEFA European Championship (EURO)', ['UEFA_Euro_{y}'], [2016, 2020, 2024, 2028, 2032], OPEN),
    ('weuro', "UEFA Women's Euro", ["UEFA_Women's_Euro_{y}"], [2017, 2022, 2025, 2029], OPEN),
    ('wc', 'FIFA World Cup', ['{y}_FIFA_World_Cup'], [2014, 2018, 2022, 2026, 2030, 2034], OPEN),
    ('wwc', "FIFA Women's World Cup", ["{y}_FIFA_Women's_World_Cup"], [2015, 2019, 2023, 2027, 2031], OPEN),
    ('cwc', 'FIFA Club World Cup', ['{y}_FIFA_Club_World_Cup'], [2025, 2029], OPEN),
    ('afcon', 'Africa Cup of Nations', ['{y}_Africa_Cup_of_Nations'], [2019, 2021, 2023, 2025, 2027], OPEN),
    ('rwc', 'Rugby World Cup', ['{y}_Rugby_World_Cup'], [2015, 2019, 2023, 2027, 2031], OPEN),
    ('cricket', 'Cricket World Cup', ['{y}_Cricket_World_Cup'], [2015, 2019, 2023, 2027], OPEN),
    ('summer', 'Summer Olympics', ['{y}_Summer_Olympics'], R(2012, 2036, 4), OPEN),
    ('winter', 'Winter Olympics', ['{y}_Winter_Olympics'], R(2014, 2034, 4), OPEN),
    ('spara', 'Summer Paralympics', ['{y}_Summer_Paralympics'], R(2012, 2032, 4), OPEN),
    ('wpara', 'Winter Paralympics', ['{y}_Winter_Paralympics'], R(2014, 2030, 4), OPEN),
    ('cwg', 'Commonwealth Games', ['{y}_Commonwealth_Games'], [2014, 2018, 2022, 2026, 2030], OPEN),
    ('asian', 'Asian Games', ['{y}_Asian_Games'], [2014, 2018, 2022, 2026, 2030], OPEN),
    ('pan', 'Pan American Games', ['{y}_Pan_American_Games'], [2015, 2019, 2023, 2027], OPEN),
    ('sb', 'Super Bowl', ['Super_Bowl_{sb}'], R(2015, 2030), ('Halftime show', 'Stadium')),
    ('esc', 'Eurovision Song Contest', ['Eurovision_Song_Contest_{y}'], [y for y in R(2015, 2027) if y != 2020], ('Televised song contest', 'Arena')),
]
CURRENCY_SPARQL = """SELECT ?countryLabel ?code WHERE {
  ?country wdt:P31 wd:Q6256 ; p:P38 ?st . ?st ps:P38 ?cur . ?cur p:P498 ?cs . ?cs ps:P498 ?code . FILTER NOT EXISTS { ?cs pq:P582 ?codeEnd }
  FILTER NOT EXISTS { ?st pq:P582 ?end }
  FILTER NOT EXISTS { ?cur wdt:P576 ?abolished }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }"""


def to_roman(n):
    out = ''
    for v, s in [(50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]:
        while n >= v:
            out += s; n -= v
    return out


def raw(title):
    return cached('raw:' + title, 'https://en.wikipedia.org/w/index.php?' + urllib.parse.urlencode({'title': title, 'action': 'raw'}))


def revision(title):
    page = cached('html:' + title, 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(title)) or ''
    m = re.search(r'"wgRevisionId":(\d+)', page)
    return int(m.group(1)) if m else None


def follow(title):
    """Wikitext of a title, following one redirect; returns (title, text)."""
    t = raw(title)
    m = re.match(r'\s*#REDIRECT\s*\[\[([^\]|#]+)', t or '', re.I)
    if m:
        title = m.group(1).strip().replace(' ', '_')
        t = raw(title)
    return (title, t) if t and not re.match(r'\s*#REDIRECT', t, re.I) else (title, None)


def box_fields(text):
    """Raw infobox field values (with markup), keyed by lower-case name. Handles nested templates."""
    m = re.search(r'\{\{\s*Infobox', text, re.I)
    if not m:
        return {}
    i, depth = m.start(), 0
    while i < len(text):
        if text.startswith('{{', i): depth += 1; i += 2; continue
        if text.startswith('}}', i):
            depth -= 1; i += 2
            if depth == 0: break
            continue
        i += 1
    body = text[m.start() + 2:i - 2]
    fields, depth, cur, buf = {}, 0, None, ''
    j = 0
    while j < len(body):  # split on top-level pipes only
        if body.startswith('{{', j) or body.startswith('[[', j): depth += 1; buf += body[j:j + 2]; j += 2; continue
        if (body.startswith('}}', j) or body.startswith(']]', j)) and depth: depth -= 1; buf += body[j:j + 2]; j += 2; continue
        if body[j] == '|' and depth == 0:
            if cur is not None: fields[cur] = buf.strip()
            k = re.match(r'\s*([^=|]+?)\s*=', body[j + 1:])
            if k:
                cur = k.group(1).strip().lower().replace(' ', '_'); j += 1 + k.end(); buf = ''; continue
            cur = None; buf = ''; j += 1; continue
        buf += body[j]; j += 1
    if cur is not None: fields[cur] = buf.strip()
    return fields


def first_ref(v):
    m = re.search(r'<ref[^>/]*>(.*?)</ref>', v, re.S | re.I)
    return cite_of(m.group(1)) if m else None


def clean(v):
    v = re.sub(r'<ref[^>]*?(?:/>|>.*?</ref>)', '', v, flags=re.S | re.I)
    v = re.sub(r'<br\s*/?>', ', ', v, flags=re.I)
    v = re.sub(r'\{\{\s*lang\s*\|[^|]*\|(\[\[[^\]]+\]\]|[^|}]+)[^}]*\}\}', r'\1', v, flags=re.I)
    return plain(strip_templates(v)).strip(' ,;')


def iso_date_in_year(v, year):
    """'9 June – 9 July' or 'June 11 – July 19' plus the edition year from the title."""
    months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
    t = clean(v)
    m = re.search(r'(\d{1,2})\s+([A-Za-z]+)', t)
    if m and m.group(2).lower() in months:
        return f'{year}-{months.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}'
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2})\b', t)
    if m and m.group(1).lower() in months:
        return f'{year}-{months.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}'
    return None


def iso_date(v):
    m = re.search(r'\{\{\s*Start date(?: and age)?\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})', v, re.I)
    if m:
        return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    months = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'], 1)}
    t = clean(v)
    m = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', t) or None
    if m and m.group(2).lower() in months:
        return f'{m.group(3)}-{months[m.group(2).lower()]:02d}-{int(m.group(1)):02d}'
    m = re.search(r'([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})', t)
    if m and m.group(1).lower() in months:
        return f'{m.group(3)}-{months[m.group(1).lower()]:02d}-{int(m.group(2)):02d}'
    return None


def link_target(v, prefer=None):
    links = re.findall(r'\[\[([^\]|#]+)(?:\|([^\]]*))?\]\]', v)
    if prefer:  # e.g. the venue listed for the opening ceremony
        for seg in re.split(r'\{\{\s*Bulleted list\s*\||\|', v):
            if prefer.lower() in seg.lower():
                l = re.findall(r'\[\[([^\]|#]+)(?:\|([^\]]*))?\]\]', seg)
                if l: return l[0][0].strip(), (l[0][1] or l[0][0]).strip()
    return (links[0][0].strip(), (links[0][1] or links[0][0]).strip()) if links else (None, None)


def article(title):
    return {'title': title.replace('_', ' '), 'url': 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(title),
            'revision': revision(title)}


def main():
    cur_rows = json.loads(get('https://query.wikidata.org/sparql?' + urllib.parse.urlencode({'query': CURRENCY_SPARQL}), accept='application/sparql-results+json'))['results']['bindings']
    currency = {}
    for r in cur_rows:
        if re.fullmatch(r'[A-Z]{3}', r['code']['value']):  # ISO 4217 alphabetic codes only
            currency.setdefault(r['countryLabel']['value'], set()).add(r['code']['value'])
    currency = {k: sorted(v) for k, v in currency.items()}
    events, venues = [], {}
    for key, name, patterns, years, (etype, vtype) in SERIES:
        for y in years:
            title, text = None, None
            for pat in patterns:
                cand = pat.format(y=y, sb=to_roman(y - 1966)) if '{sb}' in pat else pat.format(y=y)
                title, text = follow(cand)
                if text: break
            if not text:
                continue
            f = box_fields(text)
            src = article(title)
            ev = {'id': f'{key}-{y}', 'series': key, 'seriesName': name, 'year': y, 'name': f'{name} {y}' if key != 'sb' else f'Super Bowl {to_roman(y - 1966)} ({y})',
                  'eventType': etype, 'venueType': vtype, 'source': src, 'fields': {}}
            def put(field, value, cite=None):
                if value not in (None, ''):
                    ev['fields'][field] = {'value': value, **({'cite': cite} if cite else {})}
            date = next((iso_date(f[k]) for k in ('date', 'opening', 'final', 'dates') if k in f and iso_date(f[k])), None)
            if not date and 'dates' in f and not re.search(r'\d{4}', clean(f['dates'])):
                date = iso_date_in_year(f['dates'], y)
                if date: ev['dateNote'] = 'First day of the dates field in the infobox, in the edition year'
            put('date', date)
            vraw = f.get('stadium') or f.get('venue') or ''
            vt, vlabel = link_target(vraw, prefer='opening') if vraw else (None, None)
            if vt:
                put('venue', vlabel)
            loc = clean(f.get('host_city') or f.get('city') or f.get('location') or '')
            if not loc and vraw:  # e.g. Eurovision: "venue<br />Basel, Switzerland"
                rest = clean(vraw).split(', ', 1)
                loc = rest[1] if len(rest) > 1 else ''
            parts = [p.strip() for p in loc.split(',') if p.strip()]
            if parts:
                put('city', parts[0])
            hosts = [clean(f[k]) for k in ('country', 'country2', 'country3', 'country4') if f.get(k)]
            country = parts[-1] if len(parts) > 1 else (hosts[0] if len(hosts) == 1 else '')
            if len(hosts) > 1:
                ev['hosts'] = hosts  # several host countries: the ceremony's country is left for the user
            if 'attendance' in f and re.search(r'\d', clean(f['attendance'])) and y <= 2026:
                put('attendance', clean(f['attendance']), first_ref(f['attendance']))
            if vt:
                if vt not in venues:
                    vtitle, vtext = follow(vt.replace(' ', '_'))
                    vf = box_fields(vtext or '')
                    cap = vf.get('capacity', '')
                    capraw = re.sub(r'<ref[^>]*?(?:/>|>.*?</ref>)', '', cap, flags=re.S | re.I) if cap else ''
                    capraw = re.sub(r'\{\{\s*(?:ubl|ubli|unbulleted list|plainlist|bulleted list|flatlist)\s*\|', '', capraw, flags=re.I)
                    capnum = re.search(r'\d{1,3}(?:,\d{3})+|\b\d{4,6}\b', capraw) if capraw else None
                    vloc = [p.strip() for p in clean(vf.get('location', '')).split(',') if p.strip()]
                    venues[vt] = {'name': vtitle.replace('_', ' '), 'source': article(vtitle) if vtext else None,
                                  'capacity': int(capnum.group(0).replace(',', '')) if capnum else None, 'capacityText': plain(re.sub(r'\{\{|\}\}|\|', ' ', capraw))[:160] if cap else None,
                                  'capacityCite': first_ref(cap) if cap else None, 'country': vloc[-1] if len(vloc) > 1 else None, 'city': next((x for x in reversed(vloc[:-1]) if not re.search(r'\d', x)), None) if len(vloc) > 1 else None}
                v = venues[vt]
                ev['venueRef'] = vt
                if v['capacity']:
                    put('capacity', v['capacity'], v['capacityCite'])
                    ev['fields']['capacity']['from'] = v['source']
                if v['country']:  # the venue's own article is the most specific source for its country
                    country = v['country']
                if v.get('city') and (not ev['fields'].get('city') or ev['fields']['city']['value'] in (country, v['country'])):
                    put('city', v['city'])
            if country:
                country = re.sub(r'^(U\.S\.|US|USA)$', 'United States', country)
                put('country', country)
                codes = currency.get(country) or currency.get({'England': 'United Kingdom', 'Scotland': 'United Kingdom', 'Wales': 'United Kingdom', 'Northern Ireland': 'United Kingdom'}.get(country, ''), [])
                if len(codes) == 1:
                    put('currency', codes[0])
                    ev['fields']['currency']['from'] = {'title': 'Wikidata: currency (P38) of ' + country, 'url': 'https://query.wikidata.org/'}
                elif len(codes) > 1:
                    ev['currencyOptions'] = codes  # Wikidata lists several: the user chooses
            events.append(ev)
            print(f"  {ev['name']}: {', '.join(k for k in ev['fields'])}")
    data = {
        '_about': 'Real events for Brief autocomplete. Each field value comes from the event\'s English Wikipedia infobox (date, venue, city, country), the venue\'s article (capacity, with its citation when the infobox gives one) or Wikidata (currency of the country). Missing fields are left empty, never estimated.',
        'source': 'wikipedia-en', 'license': 'Wikipedia text CC BY-SA 4.0; Wikidata CC0', 'builtAt': now(),
        'series': [{'key': k, 'name': n, 'eventType': t[0], 'venueType': t[1]} for k, n, _, _, t in SERIES],
        'events': events,
        'currencyByCountry': currency,
        'currencySource': 'Wikidata: sovereign states (Q6256) with currency (P38) and ISO 4217 code (P498); currencies or ISO codes with an end time, and abolished currencies (P576), excluded',
    }
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1); fh.write('\n')
    print(f'wrote reference/catalog.json: {len(events)} events, {len(venues)} venues')


if __name__ == '__main__':
    main()

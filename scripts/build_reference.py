#!/usr/bin/env python3
"""Build Ceremony Studio's reference directory from published datasets.

Every number the app uses for an estimate comes from reference/. This script
downloads the official source files, records their SHA-256 hashes, extracts
exactly the rows the app needs and writes:

  reference/datasets.json           where each downloaded file came from
  reference/emission-factors.json   UK DESNZ 2026 conversion factors
  reference/grid-intensity.json     electricity carbon intensity by country
  reference/spend-multipliers.json  Defra/Leeds spend-based multipliers
  reference/currency.json           exchange rates for the multiplier year
  reference/closeout-benchmarks.json  actuals aggregated from reference/closeouts/
  reference/bundle.json             everything above in one file for the app

Then it checks that every value cites a known source (see `check`).

Usage:
  python3 scripts/build_reference.py            # download, build, check
  python3 scripts/build_reference.py closeouts  # rebuild from committed close-outs only (no network)
  python3 scripts/build_reference.py check      # validate only (no network)

Standard library only, so it runs anywhere Python 3.9+ runs.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import statistics
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, 'reference')
CACHE = os.path.join(ROOT, '.cache', 'reference-src')

DATASETS = {
    'desnz-2026': {
        'title': 'Greenhouse gas reporting: conversion factors 2026 (flat file, revised July 2026, version 1.2)',
        'publisher': 'UK Department for Energy Security and Net Zero (DESNZ)',
        'page': 'https://www.gov.uk/government/publications/greenhouse-gas-reporting-conversion-factors-2026',
        'url': 'https://assets.publishing.service.gov.uk/media/6a6c9748862aaf18d9c62ac9/ghg-conversion-factors-2026-flat-format-revised.xlsx',
        'licence': 'Open Government Licence v3.0',
        'file': 'desnz-2026-flat.xlsx',
    },
    'defra-spend-2023': {
        'title': 'Spend-based emissions multipliers, 1997 to 2023 (UK and England\'s carbon footprint to 2023)',
        'publisher': 'Defra / University of Leeds',
        'page': 'https://www.gov.uk/government/statistics/uks-carbon-footprint',
        'url': 'https://assets.publishing.service.gov.uk/media/6a3ce5fa33bc5beefd3c4983/spend_based_emissions_multipliers_1997_to_2023.ods',
        'licence': 'Open Government Licence v3.0',
        'file': 'spend-multipliers.ods',
    },
    'owid-ember-energy': {
        'title': 'Our World in Data energy dataset (carbon_intensity_elec, from Ember Yearly Electricity Data 2026)',
        'publisher': 'Our World in Data, based on Ember',
        'page': 'https://github.com/owid/energy-data',
        'url': 'https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv',
        'licence': 'CC BY 4.0',
        'file': 'owid-energy-data.csv',
    },
    'fred-fx-annual': {
        'title': 'Foreign exchange rates, annual averages (US Federal Reserve H.10 via FRED)',
        'publisher': 'Board of Governors of the Federal Reserve System, packaged by datasets/exchange-rates',
        'page': 'https://github.com/datasets/exchange-rates',
        'url': 'https://raw.githubusercontent.com/datasets/exchange-rates/main/data/annual.csv',
        'licence': 'Public domain (ODC-PDDL)',
        'file': 'fx-annual.csv',
    },
    'fred-fx-monthly': {
        'title': 'Foreign exchange rates, monthly averages (US Federal Reserve H.10 via FRED)',
        'publisher': 'Board of Governors of the Federal Reserve System, packaged by datasets/exchange-rates',
        'page': 'https://github.com/datasets/exchange-rates',
        'url': 'https://raw.githubusercontent.com/datasets/exchange-rates/main/data/monthly.csv',
        'licence': 'Public domain (ODC-PDDL)',
        'file': 'fx-monthly.csv',
    },
}

# App factor key -> DESNZ 2026 row IDs. The factor is the sum of the rows:
# the direct factor plus its well-to-tank (WTT) factor where DESNZ publishes one,
# so fuel and travel include upstream fuel supply (GHG Protocol Scope 3 cat. 3).
FACTOR_ROWS = {
    'diesel':   (['1_101_1012_8_1', '11_101_1012_8_1'], 'Generator diesel', 'kg CO2e per litre',
                 'Diesel (100% mineral diesel) combustion + WTT. Mineral diesel rather than the UK forecourt blend, because generator fuel outside the UK is not the UK blend; this is the higher of the two.'),
    'hvo':      (['2_103_1036_8_1', '12_900_1036_8_1'], 'Generator HVO', 'kg CO2e per litre',
                 'Biodiesel HVO + WTT. DESNZ reports biogenic CO2 separately as outside of scopes.'),
    'truck':    (['27_304_3140_14_1', '28_907_3140_14_1'], 'Road freight', 'kg CO2e per tonne-km',
                 'HGV (non-refrigerated, all diesel), average of all HGVs, average laden, + WTT.'),
    'air':      (['27_317_3158_14_1', '28_913_3158_14_1'], 'Air freight', 'kg CO2e per tonne-km',
                 'Freight flights, international to/from non-UK, with radiative forcing (RF), + WTT.'),
    'sea':      (['27_320_3235_14_1', '28_916_3235_14_1'], 'Sea freight', 'kg CO2e per tonne-km',
                 'Container ship, average size, + WTT.'),
    'rail':     (['27_315_3151_14_1', '28_911_3151_14_1'], 'Rail freight', 'kg CO2e per tonne-km',
                 'Freight train + WTT.'),
    'flight':   (['21_316_3178_11_1', '22_912_3178_11_1'], 'Flights', 'kg CO2e per passenger-km',
                 'Flights international to/from non-UK, average passenger, with RF, + WTT. DESNZ includes an uplift for indirect routing.'),
    'train':    (['25_315_3147_11_1', '26_911_3147_11_1'], 'Train', 'kg CO2e per passenger-km',
                 'UK national rail + WTT. UK-specific: use a national operator factor where you have one.'),
    'road':     (['25_301_3074_4_1', '26_904_3074_4_1'], 'Car', 'kg CO2e per vehicle-km',
                 'Average car, unknown fuel, + WTT. Per vehicle-km: the app counts each road traveller as one vehicle unless you enter shared trips, which can only overstate.'),
    'coach':    (['25_314_3146_11_1', '26_910_3146_11_1'], 'Coach', 'kg CO2e per passenger-km', 'Coach + WTT.'),
    'bus':      (['25_314_3145_11_1', '26_910_3145_11_1'], 'Local bus', 'kg CO2e per passenger-km', 'Average local bus + WTT.'),
    'landfill': (['20_507_5313_15_1'], 'Waste to landfill', 'kg CO2e per tonne', 'Commercial and industrial waste, landfill.'),
    'recycled': (['20_507_5310_15_1'], 'Recycled waste', 'kg CO2e per tonne', 'Commercial and industrial waste, closed-loop recycling.'),
    'compost':  (['20_507_5305_15_1'], 'Composted waste', 'kg CO2e per tonne', 'Organic: mixed food and garden waste, composting.'),
    'incin':    (['20_507_5311_15_1'], 'Waste to energy', 'kg CO2e per tonne', 'Commercial and industrial waste, combustion.'),
}

# Spend-based multipliers: which SIC product group each app category maps to.
# This is a classification choice, recorded here with its reason so it can be reviewed.
SPEND_MAP = {
    'element': {
        'Performance': ('90', 'Performers are bought as creative, arts and entertainment services.'),
        'Stage & set': ('42.99', 'Temporary stages and sets are built and erected on site as specialised construction works.'),
        'Projection': ('77', 'Projectors and media servers are normally hired: rental and leasing services.'),
        'Pitch art': ('13', 'Pitch art is large printed or sewn fabric: textiles.'),
        'Music': ('90', 'Musicians, composers and producers: creative, arts and entertainment services.'),
        'Cast & community': ('90', 'Cast costs (choreography, rehearsal, stipends) are creative, arts and entertainment services.'),
        'Crowd participation': ('18', 'Card mosaics and printed items are printing services; for electronic wristbands choose 26.'),
        'Special effects': ('20.5', 'Pyrotechnics and effects consumables are other chemical products (SIC 20.51 explosives sits in this group).'),
        'Lighting': ('77', 'Lighting fixtures are normally hired: rental and leasing services.'),
        'Broadcast': ('59', 'Broadcast specials are motion picture, video and TV programme production services.'),
        'Sound': ('77', 'Audio systems are normally hired: rental and leasing services.'),
        'Costume': ('14', 'Costumes are wearing apparel.'),
        'Other': ('90', 'Default for show elements: creative, arts and entertainment services.'),
    },
    'line': {
        'Artist fees & travel': ('90', 'Artist fees. Enter travel in the carbon calculator, not here, or it is counted twice.'),
        'Rehearsals': ('77', 'Rehearsal fields, marquees and equipment are hired.'),
        'Venue & site': ('93', 'Venue hire: sports services and amusement and recreation services.'),
        'Crew & labour': ('78', 'Freelance crew through agencies: employment services.'),
        'Logistics & freight': ('49.3-5', 'Land transport services. Use the activity calculator instead when you have tonne-km.'),
        'Accommodation': ('55', 'Accommodation services. Use hotel nights in the calculator instead when you have them.'),
        'Catering': ('56', 'Food and beverage serving services.'),
        'Security & safety': ('80', 'Security and investigation services.'),
        'Content & marketing': ('73', 'Advertising and market research services.'),
        'Sustainability': (None, 'Mitigation payments are not a purchase of goods or services that emit; no multiplier.'),
        'Other': ('90', 'Default: creative, arts and entertainment services.'),
    },
}
# Budget-line categories whose emissions the activity calculator already covers when
# activity data is entered; the app defaults these lines to "counted in calculator".
LINE_ACTIVITY_DEFAULT = ['Logistics & freight', 'Accommodation']

CURRENCIES = {  # code -> name used in the FRED dataset (all quoted as currency per USD)
    'EUR': 'Euro', 'GBP': 'United Kingdom', 'CHF': 'Switzerland', 'JPY': 'Japan', 'INR': 'India', 'BRL': 'Brazil',
    'AUD': 'Australia', 'CAD': 'Canada', 'CNY': 'China', 'DKK': 'Denmark', 'HKD': 'Hong Kong', 'MXN': 'Mexico',
    'MYR': 'Malaysia', 'NZD': 'New Zealand', 'NOK': 'Norway', 'SGD': 'Singapore', 'ZAR': 'South Africa',
    'KRW': 'South Korea', 'SEK': 'Sweden', 'TWD': 'Taiwan', 'THB': 'Thailand',
}


# ---------------------------------------------------------------- helpers
def now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')


def fetch(key):
    d = DATASETS[key]
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, d['file'])
    if not os.path.exists(path):
        print(f'  downloading {d["url"]}')
        req = urllib.request.Request(d['url'], headers={'User-Agent': 'ceremony-studio-reference-builder'})
        with urllib.request.urlopen(req, timeout=300) as r, open(path, 'wb') as f:
            f.write(r.read())
    data = open(path, 'rb').read()
    return path, hashlib.sha256(data).hexdigest()


def write(name, obj):
    with open(os.path.join(REF, name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'  wrote reference/{name}')


def load(name, default=None):
    p = os.path.join(REF, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def xlsx_rows(path, sheet_name):
    """Read one worksheet from an .xlsx file with the standard library."""
    z = zipfile.ZipFile(path)
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])))
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    target = None
    for s in wb.find('m:sheets', ns):
        if s.get('name') == sheet_name:
            rid = s.get('{%s}id' % ns['r'])
            for rel in rels:
                if rel.get('Id') == rid:
                    target = 'xl/' + rel.get('Target').lstrip('/').replace('xl/', '')
    if not target:
        raise SystemExit(f'sheet {sheet_name!r} not found in {path}')

    def col(ref):
        n = 0
        for ch in re.match(r'[A-Z]+', ref).group(0):
            n = n * 26 + ord(ch) - 64
        return n - 1
    for row in ET.fromstring(z.read(target)).iter('{%s}row' % ns['m']):
        out = {}
        for c in row.findall('m:c', ns):
            v = c.find('m:v', ns)
            if c.get('t') == 'inlineStr':
                val = ''.join(t.text or '' for t in c.iter('{%s}t' % ns['m']))
            elif v is None:
                val = None
            elif c.get('t') == 's':
                val = shared[int(v.text)]
            else:
                val = v.text
            out[col(c.get('r'))] = val
        yield [out.get(i) for i in range(max(out) + 1)] if out else []


def ods_rows(path, sheet_name):
    """Read one sheet from an .ods file with the standard library."""
    T = '{urn:oasis:names:tc:opendocument:xmlns:table:1.0}'
    O = '{urn:oasis:names:tc:opendocument:xmlns:office:1.0}'
    P = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p'
    root = ET.fromstring(zipfile.ZipFile(path).read('content.xml'))
    for tb in root.iter(T + 'table'):
        if tb.get(T + 'name') != sheet_name:
            continue
        for r in tb.iter(T + 'table-row'):
            row = []
            for c in r:
                if c.tag not in (T + 'table-cell', T + 'covered-table-cell'):
                    continue
                n = min(int(c.get(T + 'number-columns-repeated', '1')), 50)
                v = c.get(O + 'value')
                if v is None:
                    v = ' '.join(''.join(p.itertext()) for p in c.iter(P))
                row += [v] * n
            yield row
        return
    raise SystemExit(f'sheet {sheet_name!r} not found in {path}')


# ---------------------------------------------------------------- builders
def build_factors(path):
    rows = {}
    header = None
    for r in xlsx_rows(path, 'Factors by Category'):
        if r and r[0] == 'ID':
            header = r
            continue
        if header and r and r[0]:
            rows[r[0]] = r
    def row(i):
        r = rows.get(i)
        if not r or r[8] != 'kg CO2e' or r[9] in (None, ''):
            raise SystemExit(f'DESNZ row {i} missing or empty')
        return {'id': i, 'scope': r[1], 'path': ' > '.join(x for x in r[2:7] if x), 'unit': r[7], 'value': float(r[9])}
    factors = {}
    for key, (ids, label, unit, note) in FACTOR_ROWS.items():
        parts = [row(i) for i in ids]
        factors[key] = {'value': round(sum(p['value'] for p in parts), 5), 'label': label, 'unit': unit,
                        'method': note, 'components': parts, 'source': 'desnz-2026'}
    hotels = {}
    for i, r in rows.items():
        if r[2] == 'Hotel stay' and r[8] == 'kg CO2e' and r[9] not in (None, ''):
            hotels[r[4]] = {'value': float(r[9]), 'id': i}
    return {
        '_about': 'Emission factors used by the carbon calculator. Each value is the sum of the listed DESNZ 2026 rows (direct + well-to-tank where published).',
        'source': 'desnz-2026', 'factorYear': 2026,
        'factors': factors,
        'hotel': {'label': 'Hotel stay', 'unit': 'kg CO2e per room-night', 'source': 'desnz-2026',
                  'method': 'DESNZ hotel stay factor for the country of the event. No fallback: a country not in the list needs a supplier figure.',
                  'byCountry': dict(sorted(hotels.items()))},
        'notCovered': {
            'meals': 'DESNZ publishes no per-meal factor. Catering is estimated from catering spend with the spend-based multiplier, or from a per-meal figure supplied by the caterer.',
            'generatorOutput': 'DESNZ publishes fuel factors, not generator efficiency. The grid-versus-generator comparison needs the generator output in kWh from the hire company\'s sizing or logs.',
        },
    }


def build_grid(path):
    best = {}
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if not r['carbon_intensity_elec'] or len(r['iso_code'] or '') != 3:
                continue
            y = int(r['year'])
            if y > best.get(r['country'], {}).get('year', 0):
                best[r['country']] = {'value': round(float(r['carbon_intensity_elec']) / 1000, 4), 'year': y, 'iso': r['iso_code']}
    return {
        '_about': 'Lifecycle carbon intensity of electricity generation, latest year available per country. Lifecycle figures include upstream emissions, so they are higher than generation-only factors such as DESNZ UK electricity.',
        'source': 'owid-ember-energy', 'unit': 'kg CO2e per kWh',
        'method': 'carbon_intensity_elec (g CO2e/kWh) divided by 1000. Use a supplier-specific factor instead when the venue has a renewable contract with certificates.',
        'byCountry': dict(sorted(best.items())),
    }


def build_spend(path):
    sic = {}
    started = False
    for r in ods_rows(path, 'SIC_multipliers_2023'):
        if r and str(r[0]).startswith('SIC code'):
            started = True
            continue
        if started and r and r[0] and len(r) > 2 and r[2] not in ('', None):
            code = str(r[0])
            code = re.sub(r'^(\d+\.\d)9{5,}\d*$', lambda m: str(round(float(code), 2)), code)  # spreadsheet float noise, e.g. 20.399999
            sic[code] = {'label': r[1], 'value': round(float(r[2]), 5)}
    for group in SPEND_MAP.values():
        for cat, (code, _) in group.items():
            if code and code not in sic:
                raise SystemExit(f'SIC {code} for {cat} not found in spend multipliers')
    return {
        '_about': 'Spend-based (environmentally extended input-output) multipliers for when no activity data exists. GHG Protocol Scope 3 guidance treats spend-based results as the lowest-quality method; replace them with activity or supplier data where you can.',
        'source': 'defra-spend-2023', 'method_source': 'ghgp-scope3-technical', 'priceYear': 2023,
        'unit': 'kg CO2e per GBP spent (2023 prices, excluding VAT)',
        'method': 'Cost in the plan currency -> USD -> GBP at 2023 average exchange rates (currency.json), times the multiplier for the mapped SIC group. Costs are not deflated to 2023 prices: no published UK price index for the event year is in the reference set, and leaving prices undeflated can only overstate emissions. The multipliers describe UK supply chains.',
        'sic': sic,
        'map': {g: {cat: {'sic': code, 'reason': why} for cat, (code, why) in m.items()} for g, m in SPEND_MAP.items()},
        'lineActivityDefault': LINE_ACTIVITY_DEFAULT,
    }


def build_currency(annual, monthly, year=2023):
    per_usd = {'USD': 1.0}
    with open(annual, newline='') as f:
        rows = list(csv.DictReader(f))
    names = {v: k for k, v in CURRENCIES.items()}
    for r in rows:
        if r['Date'].startswith(str(year)) and r['Country'] in names:
            per_usd[names[r['Country']]] = float(r['Exchange rate'])
    if 'GBP' not in per_usd:  # the annual file has no UK series; average the 12 monthly values
        with open(monthly, newline='') as f:
            vals = [float(r['Exchange rate']) for r in csv.DictReader(f) if r['Country'] == 'United Kingdom' and r['Date'].startswith(str(year))]
        if len(vals) != 12:
            raise SystemExit(f'expected 12 monthly GBP values for {year}, got {len(vals)}')
        per_usd['GBP'] = round(statistics.mean(vals), 5)
    return {
        '_about': f'Average {year} exchange rates, quoted as units of currency per US dollar. Used only to bring costs into the price basis of the spend-based multipliers ({year} GBP).',
        'year': year, 'perUSD': dict(sorted(per_usd.items())),
        'sources': {'annual': 'fred-fx-annual', 'GBP': 'fred-fx-monthly (mean of the 12 monthly averages)'},
        'notCovered': 'Currencies missing here (for example AED, SAR, QAR) have no published rate in this dataset, so spend-based CO2e is not calculated for them.',
    }


def build_closeouts():
    """Aggregate committed close-out files into benchmarks. Medians only, with n and file names."""
    folder = os.path.join(REF, 'closeouts')
    files = sorted(f for f in os.listdir(folder) if f.endswith('.json')) if os.path.isdir(folder) else []
    el, lead, intens, events = {}, {}, {}, []
    for name in files:
        with open(os.path.join(folder, name), encoding='utf-8') as f:
            c = json.load(f)
        if c.get('schema') != 'ceremony-studio/closeout@1':
            raise SystemExit(f'{name}: unknown schema {c.get("schema")!r}')
        p = c['plan']
        events.append({'file': name, 'name': p.get('name'), 'date': p.get('date'), 'city': p.get('city'), 'attendance': p.get('attendance')})
        for e in c.get('elements', []):
            if e.get('actualCost') is not None and e.get('actualRef'):
                el.setdefault((e['category'], e['tier'], p['currency']), []).append((e['actualCost'], name))
            if e.get('actualLeadWeeks') is not None:
                lead.setdefault(e['category'], []).append((e['actualLeadWeeks'], name))
        att = p.get('attendance')
        if att:
            for k, v in (c.get('carbon') or {}).get('measured', {}).items():
                if isinstance(v, (int, float)):
                    intens.setdefault(k, []).append((v / att, name))
    def summ(vals):
        xs = [v for v, _ in vals]
        return {'median': statistics.median(xs), 'min': min(xs), 'max': max(xs), 'n': len(xs), 'files': sorted({n for _, n in vals})}
    return {
        '_about': 'Benchmarks built only from actuals in reference/closeouts/ (invoices, delivery dates, metered data). No scaling or extrapolation: the app shows the median with its range and sample size, and cites the files.',
        'builtFrom': files, 'events': events,
        'elementCost': [{'category': k[0], 'tier': k[1], 'currency': k[2], **summ(v)} for k, v in sorted(el.items())],
        'leadWeeks': [{'category': k, **summ(v)} for k, v in sorted(lead.items())],
        'perAttendee': [{'input': k, **summ(v)} for k, v in sorted(intens.items())],
    }


# ---------------------------------------------------------------- checks
def check():
    srcs = set(load('sources.json', {}).get('sources', {})) | set(load('datasets.json', {}).get('datasets', {}))
    problems = []
    def need(where, ids):
        ids = [ids] if isinstance(ids, str) else (ids or [])
        if not ids:
            problems.append(f'{where}: no source')
        for i in ids:
            if i.split(' ')[0] not in srcs:
                problems.append(f'{where}: unknown source {i!r}')
    ef = load('emission-factors.json')
    if ef:
        for k, f in ef['factors'].items():
            need(f'emission-factors.{k}', f['source'])
            if not f.get('components'):
                problems.append(f'emission-factors.{k}: no DESNZ row ids')
        need('emission-factors.hotel', ef['hotel']['source'])
    for name, key in [('grid-intensity.json', 'source'), ('spend-multipliers.json', 'source')]:
        d = load(name)
        if d:
            need(name, d[key])
    cur = load('currency.json')
    if cur:
        need('currency.json', [cur['sources']['annual'], cur['sources']['GBP']])
    for e in load('events.json', {}).get('events', []):
        need(f'events.{e["id"]}', e.get('sources'))
    for b in load('ceremony-budgets.json', {}).get('budgets', []):
        need(f'ceremony-budgets.{b["id"]}', b.get('sources'))
    for k, v in load('policy.json', {}).items():
        if not k.startswith('_'):
            need(f'policy.{k}', v.get('sources'))
    cer = load('ceremonies.json')
    if cer:
        need('ceremonies.json', cer.get('source'))
        for c in cer['ceremonies']:
            if not c.get('url'):
                problems.append(f'ceremonies.{c.get("id")}: no article URL')
            for i, st in enumerate(c['statements']):
                if not any(r.get('url') or r.get('title') for r in st.get('refs', [])):
                    problems.append(f'ceremonies.{c["id"]}.statements[{i}]: no cited source')
    cat = load('catalog.json')
    if cat:
        need('catalog.json', cat.get('source'))
        for e in cat['events']:
            if not (e.get('source') or {}).get('url'):
                problems.append(f'catalog.{e.get("id")}: no article URL')
            cap = e['fields'].get('capacity')
            if cap and not (cap.get('from') or {}).get('url'):
                problems.append(f'catalog.{e["id"]}.capacity: no venue article')
    pl = load('places.json')
    if pl:
        need('places.json', pl.get('source'))
        for kind in ('cities', 'venues'):
            for name, e in pl[kind].items():
                if not e.get('permalink'):
                    problems.append(f'places.{kind}.{name}: no article link')
    sym = load('symbols.json')
    if sym:
        need('symbols.json', sym.get('source'))
        for kind in ('countries', 'cities'):
            for name, e in sym[kind].items():
                for c in e.get('colours', []):
                    if not c.get('wikidata') or not re.fullmatch(r'#[0-9A-F]{6}', c.get('hex', '')):
                        problems.append(f'symbols.{kind}.{name}: colour without a Wikidata hex code')
                for t in e.get('traditions', []):
                    if not (t.get('unesco') or t.get('wikidata')):
                        problems.append(f'symbols.{kind}.{name}.{t.get("name")}: no UNESCO or Wikidata link')
                for x in e.get('symbols', []):
                    if not x.get('wikidata'):
                        problems.append(f'symbols.{kind}.{name}.{x.get("name")}: no Wikidata link')
        for m in sym.get('motifs', []):
            if not m.get('article') or not (m.get('sentences') or m.get('iconclass')):
                problems.append(f'symbols.motifs.{m.get("name")}: no sourced meaning')
    ph = load('photos.json')
    if ph:
        need('photos.json', ph.get('source'))
        for name, x in ph['photos'].items():
            if not (x.get('page') and x.get('license') and x.get('author')):
                problems.append(f'photos.{name}: missing Commons page, licence or author')
            if not os.path.exists(os.path.join(REF, x['file'].split('/', 1)[1])):
                problems.append(f'photos.{name}: file {x["file"]} missing')
    if problems:
        print('Reference check FAILED:')
        for p in problems:
            print('  -', p)
        sys.exit(1)
    print(f'Reference check passed: every value cites one of {len(srcs)} known sources.')


def bundle():
    names = ['sources', 'datasets', 'emission-factors', 'grid-intensity', 'spend-multipliers', 'currency',
             'events', 'ceremony-budgets', 'policy', 'closeout-benchmarks']
    out = {'_about': 'Generated by scripts/build_reference.py. Do not edit by hand; edit the source files and rebuild.', 'builtAt': now()}
    for n in names:
        d = load(n + '.json')
        if d is None:
            raise SystemExit(f'reference/{n}.json missing; run the full build first')
        out[n] = d
    write('bundle.json', out)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if cmd == 'check':
        check()
        return
    if cmd == 'all':
        meta = {}
        paths = {}
        for k in DATASETS:
            paths[k], sha = fetch(k)
            meta[k] = {kk: vv for kk, vv in DATASETS[k].items() if kk != 'file'}
            meta[k].update({'sha256': sha, 'accessed': now()})
        write('datasets.json', {'_about': 'Downloaded datasets, with the SHA-256 of the exact file used, so anyone can re-download and verify.', 'datasets': meta})
        write('emission-factors.json', build_factors(paths['desnz-2026']))
        write('grid-intensity.json', build_grid(paths['owid-ember-energy']))
        write('spend-multipliers.json', build_spend(paths['defra-spend-2023']))
        write('currency.json', build_currency(paths['fred-fx-annual'], paths['fred-fx-monthly']))
    elif cmd != 'closeouts':
        raise SystemExit(__doc__)
    write('closeout-benchmarks.json', build_closeouts())
    check()
    bundle()


if __name__ == '__main__':
    main()

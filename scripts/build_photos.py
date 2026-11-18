#!/usr/bin/env python3
"""Build reference/photos.json and reference/photos/: one freely licensed photo per catalogue venue.

For each venue in reference/catalog.json it takes the image named in the venue's English Wikipedia
infobox, asks Wikimedia Commons for its licence, author and a 960-pixel-wide copy, and keeps it only
when the licence allows reuse (public domain, CC0, CC BY or CC BY-SA). Each photo is stored with its
author, licence, licence link and Commons page, so the app can credit it wherever it is shown.

Usage:  python3 scripts/build_photos.py   (after build_catalog.py)
Network: en.wikipedia.org, commons.wikimedia.org, upload.wikimedia.org
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ceremonies import UA, cached, now  # noqa: E402
from build_catalog import follow, box_fields  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'reference', 'photos.json')
DIR = os.path.join(ROOT, 'reference', 'photos')
WIDTH = 960
FREE = re.compile(r'^(public domain|pd\b|cc0|cc[ -]by(?:[ -]sa)?[ -]\d)', re.I)


def file_name(raw):
    raw = re.sub(r'<!--.*?-->', '', raw or '', flags=re.S)
    m = re.search(r'([^|\[\]{}=]+\.(?:jpe?g|png|webp|tiff?))', raw, re.I)
    if not m:
        return None
    return re.sub(r'^\s*(?:File|Image)\s*:\s*', '', m.group(1).strip(), flags=re.I).replace('_', ' ')


def text(v):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', v or ''))).strip()


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60]


def infos(names):
    """Commons image info for many files, 50 per request (Wikimedia rate-limits per request)."""
    out = {}
    names = sorted(set(names))
    for i in range(0, len(names), 50):
        chunk = names[i:i + 50]
        q = {'action': 'query', 'titles': '|'.join('File:' + n for n in chunk), 'prop': 'imageinfo', 'iiprop': 'url|extmetadata|size|mime',
             'iiurlwidth': WIDTH, 'iiextmetadatafilter': 'Artist|Credit|LicenseShortName|LicenseUrl|ImageDescription|DateTimeOriginal', 'format': 'json'}
        body = cached('commons-batch:' + '|'.join(chunk), 'https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode(q))
        d = json.loads(body or '{}').get('query', {})
        norm = {x['to']: x['from'] for x in d.get('normalized', [])}
        for page in d.get('pages', {}).values():
            t = norm.get(page.get('title'), page.get('title', ''))
            if page.get('imageinfo'):
                out[re.sub(r'^File:', '', t)] = page['imageinfo'][0]
                out[re.sub(r'^File:', '', page['title'])] = page['imageinfo'][0]
    return out


def download(url, path):
    if os.path.exists(path):
        return True
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            open(path, 'wb').write(data)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = e.headers.get('Retry-After', '')
                time.sleep(min(120, int(wait)) if wait.isdigit() else 15 * (i + 1)); continue
            print('   download failed', e.code); return False
    return False


def main():
    catalog = json.load(open(os.path.join(ROOT, 'reference', 'catalog.json'), encoding='utf-8'))
    os.makedirs(DIR, exist_ok=True)
    photos, wanted = {}, []
    for v in catalog.get('venues', []):
        title, wt = follow(v['name'].replace(' ', '_'))
        name = file_name(box_fields(wt or '').get('image', '')) if wt else None
        if not name:
            print(f"  {v['name']}: no infobox image"); continue
        wanted.append((v, title, name))
    meta = infos([n for _, _, n in wanted])
    for v, title, name in wanted:
        ii = meta.get(name)
        if not ii:
            print(f"  {v['name']}: {name} not on Commons"); continue
        md = {k: (x or {}).get('value') for k, x in (ii.get('extmetadata') or {}).items()}
        lic = text(md.get('LicenseShortName'))
        if not FREE.search(lic):
            print(f"  {v['name']}: licence '{lic}' not reusable, skipped"); continue
        thumb = (ii.get('thumburl') or ii.get('url')).split('?')[0].replace('://thumb.wikimedia.org/', '://upload.wikimedia.org/')  # same file on the upload host
        ext = os.path.splitext(urllib.parse.urlparse(thumb).path)[1].lower() or '.jpg'
        local = slug(v['name']) + ext
        if not download(thumb, os.path.join(DIR, local)):
            continue
        time.sleep(1)
        photos[v['name']] = {
            'file': 'reference/photos/' + local, 'width': ii.get('thumbwidth') or ii.get('width'), 'height': ii.get('thumbheight') or ii.get('height'),
            'title': name, 'page': ii.get('descriptionurl'), 'author': text(md.get('Artist')) or text(md.get('Credit')) or 'See the Commons page',
            'license': lic, 'licenseUrl': md.get('LicenseUrl') or '', 'description': text(md.get('ImageDescription'))[:240],
            'date': text(md.get('DateTimeOriginal'))[:40], 'from': {'title': title.replace('_', ' '), 'url': 'https://en.wikipedia.org/wiki/' + urllib.parse.quote(title)},
        }
        print(f"  {v['name']}: {name} ({lic})")
    keep = {os.path.basename(p['file']) for p in photos.values()}
    for f in os.listdir(DIR):  # photos of venues no longer in the catalogue
        if f not in keep:
            os.remove(os.path.join(DIR, f))
    data = {'_about': 'One photo per catalogue venue: the image in the venue\'s English Wikipedia infobox, as published on Wikimedia Commons, kept only under a reusable licence. Credit the author and licence wherever a photo is shown.',
            'source': 'wikimedia-commons', 'builtAt': now(), 'width': WIDTH, 'photos': photos}
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1); fh.write('\n')
    size = sum(os.path.getsize(os.path.join(DIR, f)) for f in os.listdir(DIR))
    print(f'wrote reference/photos.json: {len(photos)} photos, {size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()

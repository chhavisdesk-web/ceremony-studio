# Reference library

Ceremony Studio has one rule for numbers: **every figure the app produces must trace back to a published standard, a published event report, or the planner's own documented data** (a vendor quote, an invoice, a meter reading). The app never makes up an estimate. If it has no source for a number, it says so and asks for one.

This folder holds everything the app relies on. The app loads `bundle.json` when it starts.

## What is here

| File | What it holds | Where it comes from |
|---|---|---|
| `emission-factors.json` | Fuel, freight, travel, waste and hotel factors | UK DESNZ 2026 conversion factors. Each factor lists the exact DESNZ row IDs it adds up (direct + well-to-tank) |
| `grid-intensity.json` | Electricity carbon intensity for 213 countries | Ember Yearly Electricity Data 2026, via Our World in Data |
| `spend-multipliers.json` | kg CO2e per £ spent, by industry (SIC) group, plus the mapping from each app category to a SIC group and the reason | Defra / University of Leeds, UK carbon footprint 2023 |
| `currency.json` | 2023 average exchange rates, used only to bring costs into the multipliers' price basis | US Federal Reserve H.10 via FRED |
| `events.json` | Published whole-event footprints (Olympics, World Cups, EURO 2024 and others) | Organiser reports and press, one or more sources per figure |
| `ceremony-budgets.json` | Published ceremony and show budgets | Press and government reporting |
| `catalog.json` | 155 real past and upcoming events (UEFA finals, Olympics and Paralympics, World Cups, EURO, Super Bowl, Eurovision, Commonwealth, Asian and Pan American Games and others) for Brief autocomplete. Each has its date, venue, city and country from the event's article, the venue's capacity from the venue's article (with its citation where given), and the country's currency from Wikidata. When Wikidata lists several currencies, or an event has several host countries, that field is left for the user | Wikipedia infoboxes and Wikidata, built by `scripts/build_catalog.py` |
| `places.json` | The opening sentences of the English Wikipedia articles for the catalogue's 75 host cities and 90 venues, plus the start of each city's Culture section where there is one (52 cities), with revision links. Built-in research uses these for the local connection when a city has no hand-written profile | English Wikipedia (CC BY-SA 4.0), built by `scripts/build_places.py` |
| `symbols.json` | For every country and each catalogue host city: flag colours with sRGB hex codes, anthem, motto and official symbols (national flower, animal and so on), and the elements of UNESCO's intangible cultural heritage lists tied to it. Also a motif dictionary: what each motif symbolises, quoted from its Wikipedia article, plus Iconclass entries | Wikidata (CC0), UNESCO lists via Wikidata, English Wikipedia (CC BY-SA 4.0), Iconclass (CC0). Built by `scripts/build_symbols.py` |
| `photos.json`, `photos/` | One photo per catalogue venue: the image in the venue's Wikipedia infobox, 960 px wide. A photo is kept only under a reusable licence (public domain, CC0, CC BY, CC BY-SA), with its author, licence and Commons page. The app loads it from upload.wikimedia.org; `photos/` holds optional local copies, used first when present | Wikimedia Commons, built by `scripts/build_photos.py` |
| `ceremonies.json` | Cited figures from past ceremonies since 2000: budget or cost, cast and volunteers, TV audience, duration and attendance. Each figure is stored with its sentence, the source the article cites, and the article revision | English Wikipedia (CC BY-SA 4.0), built by `scripts/build_ceremonies.py` |
| `policy.json` | The internal carbon price (EUR 25/t) | UEFA EURO 2024 Climate Fund |
| `closeouts/` | Close-out files exported from finished projects | Your own invoices, delivery dates and meter readings |
| `closeout-benchmarks.json` | Medians, ranges and sample sizes built from `closeouts/` | Generated |
| `sources.json` | The hand-curated bibliography | Edited by hand |
| `datasets.json` | Every downloaded file, with its URL, licence, access date and SHA-256 hash | Generated |
| `bundle.json` | All of the above in one file for the app | Generated |

## How the app uses it

| Number | Method | Basis |
|---|---|---|
| Energy, freight, travel, hotel and waste CO2e | Activity data you enter × a DESNZ factor | GHG Protocol activity-based method |
| Grid electricity CO2e | kWh you enter × the country's grid intensity | Ember / OWID |
| CO2e of a show element or budget line with no activity data | Quoted cost → 2023 GBP → × the SIC multiplier | GHG Protocol spend-based method (the lowest-quality method; replace it with supplier data when you can) |
| Costs | Only what you enter, with its reference (quote number, invoice, rate card) | Your documents |
| Cost benchmarks and lead times | Medians from your own close-out files, with n and the file names | Your past projects |
| Weighing CO2e savings against cost when fitting a budget | EUR 25 per tonne | UEFA EURO 2024 Climate Fund |

Anything without a source is marked **No source** in the app, is listed in the export, and is counted against the plan's source coverage.

### Known limitations, stated rather than hidden
- DESNZ factors describe UK conditions. Rail and hotel factors are UK or country-specific as published; use a local operator's factor where you have one.
- Spend-based multipliers describe UK supply chains in 2023 prices. Costs are converted at 2023 exchange rates and not deflated, which can only overstate emissions.
- Grid intensity is lifecycle (it includes upstream emissions), so it is higher than generation-only factors.
- There is no published per-meal factor in these datasets, so catering uses catering spend or the caterer's own figure.
- `events.json` and `ceremony-budgets.json` were transcribed from search excerpts because the build environment could not open those sites. Each entry says how sure the attribution is (`verification`). Open the cited page before quoting a figure to a client.

### Past-ceremony figures
`scripts/build_ceremonies.py` covers these ceremonies since 2000:
- Olympic opening and closing ceremonies, listed from Wikidata
- the Wikipedia categories for FIFA World Cup, Commonwealth Games, Asian Games, multi-sport and Cricket World Cup opening ceremonies, Super Bowl halftime shows and closing ceremonies
- a short list of state and royal ceremonies

It reads each article and keeps only the sentences that state a figure AND carry a citation. Each stored figure has the sentence, the figure as written, the cited source (URL, publication, date) and a permalink to the article revision. It never averages, converts or scales a figure.

A sentence that names a different year is flagged as possibly about another event. For example, the London 2012 article quotes Beijing's budget. The app shows every figure with its sentence and source, so a reader can judge it.

```bash
python3 scripts/build_ceremonies.py
```
```bash
python3 scripts/build_catalog.py
```
```bash
python3 scripts/build_places.py
```
```bash
python3 scripts/build_symbols.py
```
```bash
python3 scripts/build_photos.py               # credits and Commons URLs
python3 scripts/build_photos.py --download 20 # also fetch local copies for up to 20 minutes (Wikimedia rate-limits this)
```
```bash
python3 scripts/build_reference.py closeouts
```

## Updating

A GitHub Action (`.github/workflows/refresh-reference.yml`) runs every builder on the 2nd of each month and opens a pull request with the changes. To update by hand:

Rebuild after DESNZ publishes new factors (every June), or to pick up new data:

```bash
python3 scripts/build_reference.py
```

This downloads the source files, records their hashes, regenerates the JSON, then refuses to finish if any value lacks a source.

To edit curated data (`events.json`, `ceremony-budgets.json`, `policy.json`), add the source to `sources.json` first, cite its id, then run the build.

## Close-outs: how the app learns from finished shows

1. After the show, open **Export → Close-out** in the app. Enter actual costs with their invoice references, actual lead times, attendance, and metered energy, freight and waste.
2. Download the close-out file.
3. Commit it to `reference/closeouts/`.
4. Rebuild without downloading anything:

```bash
python3 scripts/build_reference.py closeouts
```

5. Commit the updated `closeout-benchmarks.json` and `bundle.json`.

From then on, the app shows those actuals as benchmarks: the median, range and sample size per element category and tier, actual lead times, and per-attendee activity. It cites the close-out files. It never scales or extrapolates them.

# Ceremony Studio

A planning workspace for stadium ceremonies, kickoff shows and other live events. It covers the brief, concept, show design (three tiers per element), budget, production lead times, a CO2e calculator, a storyboard and a close-out.

Open it at **https://chhavisdesk-web.github.io/ceremony-studio/**

## Every number has a source

Ceremony Studio does not make up estimates. Each figure comes from one of these:

- **Your own documents.** Costs are entered from vendor quotes, invoices or rate cards, with their reference. Lead times are confirmed by the vendor.
- **Published standards and datasets**, kept in [`reference/`](reference/README.md):
  - UK DESNZ 2026 greenhouse gas conversion factors
  - Ember grid carbon intensity
  - Defra/Leeds spend-based multipliers, applied with the GHG Protocol spend-based method
  - The UEFA EURO 2024 carbon price
- **Published event reports**, such as the Olympic, World Cup and EURO 2024 footprints and ceremony budgets. These are shown as context only.
- **Your past projects.** Close-out files from finished shows become benchmarks, with their sample size.

Anything without a source is marked **No source**. The Budget step lists it, and the export includes it under "Figures without a source". Claude helps design and describe the show, but it is never asked for prices, CO2e or lead times.

## Starting a brief

Choose the event type, then start typing the event's name (for example "uef" or "super bowl"), its city, venue ("wemb") or country. The suggestions come from `reference/catalog.json`: real events, the venues they used, and countries with their currency and emission factors.

Picking one fills these fields from published records:
- date, venue, city and country, from the event's Wikipedia article
- capacity, from the venue's Wikipedia article
- currency, from Wikidata

Auto-filled fields are tinted green and tagged "Auto-filled", with their source under them. Empty fields the plan needs have a red dashed outline and a "Needed" tag. Fields you filled yourself look normal. Typing over a field replaces the source with your own value. Choosing another event never overwrites what you typed. Budget, show length and the written brief are always yours to fill in.

## How it works

- `index.html` is the whole app. It loads `reference/bundle.json` at start-up.
- `scripts/build_reference.py` rebuilds `reference/` from the official source files. It records each file's SHA-256 hash, and it refuses to finish if any value lacks a source.
- Plans are saved in your own browser. Nothing is sent anywhere unless you export it.

```bash
python3 scripts/build_reference.py            # refresh from the published datasets
python3 scripts/build_reference.py closeouts  # after committing a close-out file
python3 scripts/build_reference.py check      # validate sources only
```

## Close-outs

After a show:

1. Go to **Export → Close-out**. Enter actual costs with their invoice numbers, actual lead times and attendance.
2. Download the file and commit it to `reference/closeouts/`.
3. Run `python3 scripts/build_reference.py closeouts`, then commit the result.

From then on, new plans show those actuals as benchmarks: the median, range and number of projects, with the files cited.

The example plan (Madrid) is an independent concept. It is not affiliated with or endorsed by UEFA, and it has no prices until vendor quotes are added.

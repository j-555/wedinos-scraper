# wedinos scraper

scrapes all drug sample results from [wedinos.wales/sample](https://wedinos.wales/sample/) and outputs structured json

wedinos is a welsh government funded drug checking service. samples are submitted anonymously by the public and analysed by a laboratory; results are published on the website. this scraper collects the full dataset.

this project exists to support harm reduction. the data wedinos publishes saves lives when it reaches people who use drugs. this scraper exists to make that data easier to work with, build on, and share. it is not intended for law enforcement, commercial, or surveillance purposes. if you are building something with this data, please keep that purpose front and centre.

---

# what it does

what this script does
it loads the wedinos sample results page in a headless chromium browser, then walks through every page of results by clicking the next button. for each sample it finds, it parses the reference code, date received, purchase intent, lab results (major and minor substances), postcode, sample form, colour, consumption method, and self-reported effects.
on top of the raw data, it runs two checks automatically:

**adulterant mismatch detection** - it compares the purchase intent (what the person thought they were buying) against the lab results. if they do not match, the record is flagged. it uses a substance alias table to handle street names, brand names, and spelling variants, so "charlie" and "cocaine" are treated as the same thing. vague intents like "unknown" or "powder" are skipped since there is nothing meaningful to compare against.

**high-risk substance detection** - it scans every result for a list of substances that carry elevated overdose risk regardless of what was intended: nitazenes, fentanyl analogues, novel benzodiazepines, veterinary sedatives, and certain synthetic cannabinoids. if any are found, the record is flagged.
records with either flag set get an "alert": true field and are also written to wedinos-alerts.json for easy filtering.
the scraper deduplicates on reference code, so re-running or resuming from a checkpoint will not produce duplicate records. it saves a checkpoint every 10 pages so a long scrape can be interrupted and resumed without losing progress.

---

## why playwright

the site uses client-side javascript with hash-based pagination (`#page=n`). plain http requests only ever see page one. playwright launches a real chromium browser, renders the js, and clicks the next button between pages.

---

## requirements

```
python >= 3.9
playwright >= 1.40
```

```bash
pip install playwright
playwright install chromium
```

---

## usage

```bash
# full scrape
python scrape.py

# first 5 pages only (test)
python scrape.py --max-pages 5

# resume from page 10
python scrape.py --from-page 10

# resume from a saved checkpoint
python scrape.py --checkpoint output/checkpoint-page-50.json

# custom output directory
python scrape.py --output ./data

# slower delay between pages
python scrape.py --delay 2.0

# visible browser (useful for debugging)
python scrape.py --no-headless
```

---

## output files

all output is written to `./output/` (or whatever you set with `--output`).

| file | description |
|---|---|
| `wedinos-raw.json` | every record, all fields |
| `wedinos-alerts.json` | mismatches and high-risk finds |
| `wedinos-summary.json` | aggregate stats |
| `scrape-log.txt` | run log |
| `checkpoint-page-N.json` | checkpoint every 10 pages |

---

## dataset snapshot

scraped **06 june 2026**. records span **may 2002 (potentially a date error on wedinos' end) to june 2026**.

| metric | value |
|---|---|
| total records | 1,172 |
| analysed | 1,111 |
| not analysed | 61 |
| adulterant mismatches | 143 (12.9%) |
| high-risk substance finds | 118 |
| total alerts | 205 |
| unique postcodes | 556 |

### samples not analysed

wedinos publishes a reason for every sample they don't test.

| reason | count |
|---|---|
| incomplete effects form | 31 |
| no postcode supplied | 20 |
| inappropriate submission | 4 |
| multiple samples with same reference number | 3 |
| invalid reference code | 2 |
| inappropriate submission (iped) | 1 |

51 of the 61 are submission errors, not rejections. wedinos requires a postcode and a completed effects form; without them the sample won't be processed. the `reason` field is present on all unanalysed records in `wedinos-raw.json`.

### most common purchase intents

| intent | submissions |
|---|---|
| diazepam | 158 |
| mdma | 59 |
| cocaine | 58 |
| heroin | 49 |
| ketamine | 44 |
| alprazolam | 37 |
| valium | 32 |
| zopiclone | 23 |
| pregabalin | 23 |

diazepam and its street aliases (valium, blues, bensedin) together account for over 200 submissions.

### sample forms

| form | count | share |
|---|---|---|
| tablet | 579 | 52% |
| powder | 181 | 16% |
| liquid | 80 | 7% |
| crystalline | 78 | 7% |
| capsule | 64 | 6% |
| solid | 34 | 3% |

### high-risk substances detected

found across 118 samples.

| substance | finds |
|---|---|
| ethylbromazolam | 40 |
| 5f-adb | 17 |
| bromazolam | 16 |
| medetomidine | 13 |
| mdmb-4en-pinaca | 12 |
| clobromazolam | 8 |
| metonitazene | 6 |
| mdmb-inaca | 5 |
| clonazolam | 5 |
| etizolam | 3 |
| etonitazene | 2 |
| protonitazene | 1 |

- **ethylbromazolam** shows up in 40 samples, almost always in tablets sold as diazepam or bensedin (e.g. ref `000239691`: diazepam tablet, found to contain only ethylbromazolam).
- **nitazenes** (metonitazene, etonitazene, protonitazene) appear in 7 samples, all submitted as heroin. ref `W100279` (EH54): brown powder, found to contain etonitazene and metonitazene alongside opioids, reported effects included loss of consciousness.
- **medetomidine** (a veterinary sedative) was found in 13 samples: 11 in heroin powder and 2 in diazepam tablets.
- **synthetic cannabinoids** (5f-adb, mdmb-4en-pinaca, mdmb-inaca) appear in 29 samples, mostly in vape liquids sold as thc vapes.

### adulterant mismatches

143 samples (12.9%) contained something other than the stated purchase intent.

- multiple samples submitted as **alprazolam** found to contain tramadol (refs `W100706`, `W100887`, `000041793`, `W100737`, `W100874`, several from postcode LU7)
- **valium** (FK8) found to contain only paracetamol
- **temazepam 20mg egg** (TS25) found to contain tadalafil
- **heroin** (EH16) found to contain tapentadol, a prescription opioid

---

## what you can do with this data

this is a real-world lab-tested dataset going back to 2002 (again, potential error on wedinos' end). for a harm reduction site, some practical uses:

**live alerts feed** - run the scraper on a schedule and surface new high-risk finds (the `alert` flag makes this easy to filter). if nitazenes or novel benzos are showing up in a postcode area, that's worth publishing as a warning.

**what's actually in street drugs by substance** - for any given purchase intent (e.g. "diazepam"), you can pull every result and show users what that drug actually contained across hundreds of real samples. more honest than generic "may contain adulterants" copy.

**postcode-level patterns** - the `postcode` field is a partial postcode (district level, e.g. CF10). you can aggregate mismatch rates and high-risk finds by area and show regional drug supply patterns, or let users filter to their area.

**submission form explainer** - 51 of 61 unanalysed samples failed because of missing postcodes or incomplete forms. if your site links people to wedinos, you can pre-emptively explain exactly what they need to include to avoid rejection.

**substance glossary with real lab context** - pair each substance page on your site with actual detection rates from this dataset. for example: "bromazolam has been found in 16 samples submitted as diazepam since 2022" is a lot more grounded than generic NPS information.

**trend tracking over time** - the `dateReceived` field lets you chart how the supply of specific adulterants has changed. the rise of ethylbromazolam in diazepam press is visible in the data.

---

## record schema

```json
{
  "referenceCode":        "W12345",
  "analysed":             true,
  "dateReceived":         "01/06/2024",
  "purchaseIntent":       "MDMA",
  "majorSubstancesRaw":   "MDMA  Caffeine",
  "majorSubstancesArray": ["MDMA", "Caffeine"],
  "minorSubstancesRaw":   null,
  "minorSubstancesArray": [],
  "postcode":             "CF10",
  "packageLabel":         null,
  "sampleColour":         "White",
  "sampleForm":           "Powder",
  "consumptionMethod":    "Insufflation",
  "selfReportedEffects":  ["Euphoria", "Increased energy"],
  "scrapedAt":            "2026-06-01T12:00:00+00:00",
  "adulterantMismatch":   false,
  "highRiskSubstances":   [],
  "alert":                false
}
```

unanalysed records:

```json
{
  "referenceCode": "W99999",
  "analysed":      false,
  "reason":        "No postcode supplied",
  "scrapedAt":     "2026-06-01T12:00:00+00:00"
}
```

---

## alert logic

### adulterant mismatch

flagged when the lab finds something that doesn't match the stated purchase intent, and the intent was specific enough to check. vague intents ("unknown", "powder", "benzo") are excluded; only named substances trigger the check.

### high-risk substances

flagged regardless of intent if the analysis finds:

- nitazenes (etonitazene, metonitazene, isotonitazene, protonitazene, etc.)
- fentanyl analogues (fentanyl, carfentanil, acetylfentanyl, etc.)
- novel benzodiazepines (ethylbromazolam, bromazolam, flualprazolam, clonazolam, clobromazolam, etizolam, etc.)
- veterinary adulterants (xylazine, medetomidine)
- high-risk synthetic cannabinoids (5f-adb, mdmb-4en-pinaca, mdmb-inaca, ab-fubinaca)

---

## checkpointing

a checkpoint is saved every 10 pages. to resume:

```bash
python3 scrape.py --checkpoint output/checkpoint-page-50.json
```

duplicate reference codes are skipped automatically on resume.

---

## ethical use

- use a polite delay (`--delay 1.5` or higher)
- cite wedinos as the data source
- contact wedinos if you intend to use the data at scale

if wedinos ask for this scraper to be taken down or modified, i will comply immediately. this project has no interest in causing them any burden. if you represent wedinos and have concerns, please open an issue.

[wedinos.wales](https://wedinos.wales)

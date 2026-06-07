"""
wedinos full database scraper

scrapes all sample results from wedinos.wales/sample/ using a headless browser.
the site uses client-side js hash pagination (#page=n), so playwright + chromium
is used to render each page and click the next button.

output (all in ./output/):
  wedinos-raw.json      - every record, full fields
  wedinos-alerts.json   - adulterant mismatches + high-risk substance finds
  wedinos-summary.json  - aggregate stats
  scrape-log.txt        - timestamped run log

usage:
  python3 scrape.py                      # full scrape
  python3 scrape.py --max-pages 5        # first 5 pages only (test)
  python3 scrape.py --from-page 10       # resume from page 10
  python3 scrape.py --output ./my-dir    # custom output directory
  python3 scrape.py --delay 2.0          # seconds between pages (default 1.0)
  python3 scrape.py --checkpoint file    # resume from a checkpoint json file
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# cli args

parser = argparse.ArgumentParser(description="wedinos scraper")
parser.add_argument("--max-pages",   type=int,   default=None,     help="stop after n pages")
parser.add_argument("--from-page",   type=int,   default=1,        help="start at page n")
parser.add_argument("--output",      type=str,   default="output", help="output directory")
parser.add_argument("--delay",       type=float, default=1.0,      help="seconds between pages")
parser.add_argument("--headless",    action="store_true", default=True)
parser.add_argument("--no-headless", action="store_true", default=False)
parser.add_argument("--checkpoint",  type=str,   default=None,     help="resume from a checkpoint json file")
args = parser.parse_args()

OUTPUT_DIR = Path(args.output)
DELAY      = args.delay
FROM_PAGE  = args.from_page
MAX_PAGES  = args.max_pages
HEADLESS   = not args.no_headless
CHECKPOINT = args.checkpoint

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_DIR / "scrape-log.txt"
LOG_FILE.write_text("")  # clear log on each run


# logging

def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# high-risk substance detection

# terms that indicate elevated overdose risk if present in a sample
HIGH_RISK_TERMS = [
    # nitazenes (synthetic opioids, extreme potency)
    "nitazene", "etonitazene", "metonitazene", "isotonitazene", "protonitazene",
    "butonitazene", "metodesnitazene", "flunitazene", "n-desethyl-etonitazene",
    # fentanyl and analogues
    "fentanyl", "carfentanil", "acetylfentanyl", "furanylfentanyl", "ocfentanil",
    "butyrfentanyl", "valerylfentanyl",
    # novel benzodiazepines commonly found in street supply
    "bromazolam", "flualprazolam", "flubromazolam", "clonazolam", "etizolam",
    "metonitazolam", "nitrazolam", "nifoxipam", "meclonazepam", "ethylbromazolam",
    # veterinary sedatives used as adulterants
    "xylazine", "medetomidine",
    # synthetic cannabinoids associated with severe adverse events
    "5f-adb", "mdmb", "ab-fubinaca", "4f-mdmb",
]

# known aliases and brand names grouped by substance
# used to determine whether purchase intent matches what was actually found
SUBSTANCE_GROUPS = {
    # opioids
    "heroin":          ["heroin", "diamorphine", "6-mam", "diacetylmorphine", "smack", "brown", "boy", "gear", "dope"],
    "morphine":        ["morphine", "oramorph", "zomorph", "mst continus", "sevredol"],
    "codeine":         ["codeine", "codis", "solpadeine", "nurofen plus", "migraleve", "co-codamol", "kapake"],
    "oxycodone":       ["oxycodone", "oxy", "oxycontin", "percocet", "roxicodone", "shortec", "longtec"],
    "tramadol":        ["tramadol", "ultram", "tramal", "zydol", "tradolan", "ralivia", "dromadol", "zamadol"],
    "methadone":       ["methadone", "physeptone", "methadose"],
    "buprenorphine":   ["buprenorphine", "subutex", "suboxone", "espranor", "prefibin"],
    "fentanyl":        ["fentanyl", "duragesic", "actiq"],
    "dihydrocodeine":  ["dihydrocodeine", "df118", "dhc continus"],
    # stimulants
    "cocaine":         ["cocaine", "benzoylecgonine", "coke", "charlie", "snow", "blow", "white", "crack", "rock", "nose candy"],
    "mdma":            ["mdma", "mda", "methylenedioxymethamphetamine", "ecstasy", "mandy", "molly", "pills", "beans", "e", "xtc", "dolphins", "mitsubishi", "superman", "donkey kong"],
    "amphetamine":     ["amphetamine", "dextroamphetamine", "speed", "whizz", "base", "paste", "billy", "dexedrine", "elvanse"],
    "methamphetamine": ["methamphetamine", "crystal", "crystal meth", "meth", "ice", "tina", "yaba", "crank"],
    "mephedrone":      ["mephedrone", "4-mmc", "4mmc", "methylmethcathinone", "m-cat", "meow", "drone", "bubble"],
    "cathinones":      ["cathinone", "3-mmc", "3mmc", "mdpv", "pentylone", "n-ethylpentylone", "hexen", "n-ethylhexedrone", "alpha-php", "α-php", "bk-mdma", "ethylone", "butylone"],
    "methylphenidate": ["methylphenidate", "ritalin", "concerta", "equasym", "medikinet"],
    "modafinil":       ["modafinil", "provigil", "modalert"],
    # psychedelics
    "lsd":             ["lsd", "lysergic", "acid", "tabs", "blotter", "lucy", "dots", "window pane"],
    "psilocybin":      ["psilocybin", "psilocin", "mushrooms", "shrooms", "magic mushrooms", "liberty caps", "truffles", "philosopher stones"],
    "dmt":             ["dmt", "dimethyltryptamine", "changa", "ayahuasca", "yage"],
    "2c-b":            ["2c-b", "2cb", "nexus", "bees"],
    "2c-i":            ["2c-i", "2ci"],
    "nbome":           ["nbome", "n-bomb", "25i", "25b", "smiles"],
    "mescaline":       ["mescaline", "peyote", "san pedro"],
    # dissociatives
    "ketamine":        ["ketamine", "k", "special k", "ket", "vitamin k", "kit kat", "calvin klein"],
    "mxe":             ["mxe", "methoxetamine"],
    "2-fdck":          ["2-fdck", "2-fluorodeschloroketamine"],
    "nitrous":         ["nitrous oxide", "nitrous", "nos", "laughing gas", "balloons", "hippy crack"],
    # benzodiazepines and z-drugs
    # diazepam — foreign brands include bensedin (serbia), stesolid (nordic), apaurin (slovenia)
    "diazepam":        ["diazepam", "valium", "vallies", "blue", "blues", "msj", "msj blues", "diaz",
                        "bensedin", "stesolid", "diazemuls", "dialar", "tensium", "ansiolin",
                        "bialzepam", "antenex", "valpam", "apaurin", "relanium", "seduxen"],
    # alprazolam — foreign brands include xanor (sweden), tafil (mexico), ksalol (serbia)
    "alprazolam":      ["alprazolam", "xanax", "xans", "bars", "pfizer", "xanor", "tafil", "alprox",
                        "restyl", "solanax", "zopax", "frontin", "helex", "ksalol", "neurol"],
    # clonazepam — sold as rivotril in most of the world
    "clonazepam":      ["clonazepam", "klonopin", "rivotril", "rivatril", "clonex", "iktorivil",
                        "paxam", "clonotril", "kriadex"],
    "lorazepam":       ["lorazepam", "ativan", "temesta", "merlit", "wypax"],
    "temazepam":       ["temazepam", "tams", "jellies", "eggs", "normison", "euhypnos", "restoril",
                        "planum", "signopam"],
    "nitrazepam":      ["nitrazepam", "moggies", "mogadon", "alodorm", "arem"],
    "zopiclone":       ["zopiclone", "zimmos", "zimmers", "imovane", "zimovane", "amoban", "ximovan", "zopicalm"],
    "zolpidem":        ["zolpidem", "ambien", "stilnoct", "sanval", "myslee"],
    "phenazepam":      ["phenazepam"],
    "pregabalin":      ["pregabalin", "lyrica", "pregabs", "pregab", "alzain", "axalid"],
    "gabapentin":      ["gabapentin", "neurontin", "gabbies"],
    "ghb":             ["ghb", "gbl", "gamma-hydroxybutyrate", "gamma-butyrolactone", "liquid ecstasy", "georgia home boy", "1,4-butanediol", "1,4-b"],
    # novel benzodiazepines commonly appearing in uk street supply
    "etizolam":        ["etizolam", "etilaam", "etizest"],
    "bromazolam":      ["bromazolam"],
    "flualprazolam":   ["flualprazolam"],
    "flubromazolam":   ["flubromazolam"],
    "clonazolam":      ["clonazolam"],
    "pyrazolam":       ["pyrazolam"],
    "diclazepam":      ["diclazepam"],
    "nifoxipam":       ["nifoxipam"],
    "meclonazepam":    ["meclonazepam"],
    "deschloroetizolam": ["deschloroetizolam"],
    "cinazepam":       ["cinazepam"],
    "gidazepam":       ["gidazepam"],
    "tofisopam":       ["tofisopam"],
    # cannabis and cannabinoids
    "cannabis":        ["cannabis", "thc", "cbd", "cannabinoid", "marijuana", "weed", "bud", "skunk", "haze", "green", "grass", "pot", "doobs", "joint", "stoner", "hash", "hashish", "resin", "oil", "shatter", "wax", "thc vape", "thc e-liquid", "cannabis vape", "cannabis oil", "cbd oil", "kush", "cheese", "gelato", "og", "sour diesel"],
    "spice":           ["spice", "k2", "mamba", "synthetic cannabinoid", "synthetic cannabinoids", "kronic", "black mamba"],
    # other
    "alcohol":         ["alcohol", "ethanol", "booze", "drink", "vodka", "whisky", "gin", "beer", "wine"],
    "tobacco":         ["tobacco", "nicotine", "cigarette", "fag", "vape", "e-cig"],
    "caffeine":        ["caffeine", "coffee", "energy drink", "red bull", "monster"],
    "steroids":        ["testosterone", "trenbolone", "deca", "nandrolone", "dianabol", "anavar", "winstrol", "sustanon", "roids"],
}

# vague purchase intents that should never trigger a mismatch alert
GENERIC_INTENTS = {
    "not stated", "unknown", "unsure", "test", "testing",
    "pill", "pills", "tablet", "tablets", "capsule", "capsules",
    "powder", "white powder", "crystal", "crystals", "rock", "rocks",
    "liquid", "oil", "sample", "drug", "drugs", "substance", "substances",
    "chemical", "research chemical", "research chemicals", "rc", "nps",
    "legal high", "legal highs", "unknown powder", "unknown substance",
    "unknown pill", "unknown tablet", "plant", "herbal", "incense",
    "benzo", "benzos", "benzodiazepine", "benzodiazepines",
    "opioid", "opiate", "opiates", "painkiller", "painkillers",
    "stimulant", "stimulants", "upper", "uppers",
    "downer", "downers", "sedative", "sedatives", "hypnotic", "hypnotics",
    "psychedelic", "psychedelics", "hallucinogen", "hallucinogens",
    "dissociative", "dissociatives", "cannabinoid", "cannabinoids",
    "steroid", "steroids", "performance enhancing",
    "homebrew", "home brew", "homemade", "street", "street bought",
    "dealer", "friend", "gift", "free sample", "darknet", "online",
}


def norm(s):
    """strip everything except alphanumerics for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def substances_match(intent, found):
    """
    return true if the purchase intent and found substance belong to the same group.
    uses alias lookup so street names match lab names (e.g. 'charlie' matches 'cocaine').
    """
    if not intent or not found:
        return True
    ni, nf = norm(intent), norm(found)
    if nf in ni or ni in nf:
        return True
    for aliases in SUBSTANCE_GROUPS.values():
        i_in = any(norm(a) in ni for a in aliases)
        f_in = any(norm(a) in nf for a in aliases)
        if i_in and f_in:
            return True
    return False


def detect_mismatch(record):
    """
    return true if the substance found in analysis does not match what the
    submitter intended to purchase, and the intent was specific enough to check.
    """
    intent = record.get("purchaseIntent")
    major  = record.get("majorSubstancesArray", [])
    if not intent or not major:
        return False
    intent_lower = intent.lower().strip()
    if intent_lower in GENERIC_INTENTS:
        return False
    return not any(substances_match(intent, f) for f in major)


def detect_high_risk(record):
    """return any high-risk substance names found in the major or minor results."""
    all_subs = record.get("majorSubstancesArray", []) + record.get("minorSubstancesArray", [])
    found = []
    for sub in all_subs:
        n = norm(sub)
        for term in HIGH_RISK_TERMS:
            if norm(term) in n:
                found.append(sub)
                break
    return found


# page parsing

def parse_substance_text(text):
    """
    split a substance cell into individual substance names.
    wedinos separates co-detected substances with two or more spaces.
    """
    if not text or text.strip().lower() == "not stated":
        return []
    parts = re.split(r" {2,}|\t|\n", text)
    return [p.strip() for p in parts if p.strip() and p.strip().lower() != "not stated"]


def parse_page(page_obj):
    """
    extract all sample records from the currently rendered playwright page.
    each sample is an <li> containing an <h2> reference code and result tables.
    """
    records    = []
    scraped_at = datetime.now(timezone.utc).isoformat()

    for li in page_obj.locator("li").all():
        h2 = li.locator("h2")
        if h2.count() == 0:
            continue

        ref_text = h2.first.inner_text().strip()
        if not re.match(r"^(W\d{5,}|\d{7,})$", ref_text):
            continue

        ref       = ref_text
        full_text = li.inner_text()

        # samples not yet analysed have a reason string instead of results
        unanalysed_match = re.search(
            r"not\s+analy[sz]ed[^:]*:\s*(.+?)(?:\n|$)",
            full_text, re.IGNORECASE
        )
        if unanalysed_match:
            records.append({
                "referenceCode": ref,
                "analysed":      False,
                "reason":        unanalysed_match.group(1).strip(),
                "scrapedAt":     scraped_at,
            })
            continue

        date_match    = re.search(r"Date received\s*[-–]\s*(\d{2}/\d{2}/\d{4})", full_text)
        date_received = date_match.group(1) if date_match else None

        tables   = li.locator("table")
        n_tables = tables.count()

        def parse_table(idx):
            """extract key/value pairs from a table at the given index."""
            rows = {}
            if idx >= n_tables:
                return rows
            for tr in tables.nth(idx).locator("tr").all():
                cells = tr.locator("th, td").all()
                if len(cells) >= 2:
                    key = cells[0].inner_text().strip().lower().replace(" ", "_")
                    val = cells[1].inner_text().strip()
                    rows[key] = val
            return rows

        results_table = parse_table(0)
        info_table    = parse_table(1)

        # field names vary slightly across different page versions
        intent_raw = results_table.get("purchase_intent") or results_table.get("purchase intent")
        major_raw  = (results_table.get("sample_upon_analysis_(major)")
                      or results_table.get("sample_upon_analysis"))
        minor_raw  = results_table.get("sample_upon_analysis_(minor)")
        postcode   = results_table.get("postcode")
        effects_raw = (info_table.get("self-reported_effects")
                       or info_table.get("self_reported_effects")
                       or info_table.get("self-reported effects"))

        major_list = parse_substance_text(major_raw or "")
        minor_list = parse_substance_text(minor_raw or "")
        effects    = parse_substance_text(effects_raw or "")

        record = {
            "referenceCode":        ref,
            "analysed":             True,
            "dateReceived":         date_received,
            "purchaseIntent":       intent_raw.strip() if intent_raw else None,
            "majorSubstancesRaw":   major_raw,
            "majorSubstancesArray": major_list,
            "minorSubstancesRaw":   minor_raw,
            "minorSubstancesArray": minor_list,
            "postcode":             postcode.strip() if postcode else None,
            "packageLabel":         info_table.get("package_label", "").strip() or None,
            "sampleColour":         info_table.get("sample_colour", "").strip() or None,
            "sampleForm":           info_table.get("sample_form", "").strip() or None,
            "consumptionMethod":    info_table.get("consumption_method", "").strip() or None,
            "selfReportedEffects":  effects,
            "scrapedAt":            scraped_at,
        }

        record["adulterantMismatch"] = detect_mismatch(record)
        record["highRiskSubstances"] = detect_high_risk(record)
        record["alert"]              = record["adulterantMismatch"] or len(record["highRiskSubstances"]) > 0

        records.append(record)

    return records


# summary generation

def build_summary(all_records):
    """aggregate stats across all scraped records."""
    analysed   = [r for r in all_records if r.get("analysed")]
    mismatches = [r for r in analysed if r.get("adulterantMismatch")]
    high_risk  = [r for r in analysed if r.get("highRiskSubstances")]

    def count_field(records, field):
        counts = {}
        for r in records:
            k = (r.get(field) or "not stated").lower()
            counts[k] = counts.get(k, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    by_intent = count_field(analysed, "purchaseIntent")
    by_form   = count_field([r for r in analysed if r.get("sampleForm")], "sampleForm")
    by_method = count_field([r for r in analysed if r.get("consumptionMethod")], "consumptionMethod")

    # count individual high-risk substances across all alerts
    hr_counts = {}
    for r in high_risk:
        for sub in r["highRiskSubstances"]:
            k = sub.lower()
            hr_counts[k] = hr_counts.get(k, 0) + 1
    hr_counts = dict(sorted(hr_counts.items(), key=lambda x: -x[1]))

    # mismatch rate by purchase intent (top 15)
    mismatch_by_intent = {}
    for r in mismatches:
        k = (r.get("purchaseIntent") or "not stated").lower()
        if k not in mismatch_by_intent:
            mismatch_by_intent[k] = {"mismatches": 0, "total": by_intent.get(k, 0)}
        mismatch_by_intent[k]["mismatches"] += 1
    for k, v in mismatch_by_intent.items():
        v["rate"] = f"{(v['mismatches'] / v['total'] * 100):.1f}%" if v["total"] else "n/a"
    mismatch_by_intent = dict(
        sorted(mismatch_by_intent.items(), key=lambda x: -x[1]["mismatches"])[:15]
    )

    def parse_date(d):
        try:
            return tuple(reversed(d.split("/")))
        except Exception:
            return ("0000", "00", "00")

    recent = sorted(
        [r for r in analysed if r.get("dateReceived")],
        key=lambda r: parse_date(r["dateReceived"]),
        reverse=True
    )[:25]

    n_analysed = len(analysed)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "scraped":              len(all_records),
            "analysed":             n_analysed,
            "notAnalysed":          len(all_records) - n_analysed,
            "adulterantMismatches": len(mismatches),
            "mismatchRate":         f"{len(mismatches) / n_analysed * 100:.1f}%" if n_analysed else "0%",
            "highRiskFinds":        len(high_risk),
        },
        "highRiskSubstanceCounts": hr_counts,
        "topPurchaseIntents":      dict(list(by_intent.items())[:20]),
        "topSampleForms":          dict(list(by_form.items())[:10]),
        "topConsumptionMethods":   dict(list(by_method.items())[:10]),
        "mismatchByIntent":        mismatch_by_intent,
        "recentSamples":           recent,
    }


# browser helpers

def find_next_button(pg):
    """
    try multiple selector strategies to locate the pagination next button.
    returns the first matching locator, or none if no next page is found.
    """
    selectors = [
        'a:has-text("Next")',
        'a:has-text("next")',
        'button:has-text("Next")',
        'button:has-text("next")',
        'a[href*="#page="]',
        'a[class*="next"]',
        'a[class*="pagination"]',
        'nav a',
    ]
    for sel in selectors:
        loc = pg.locator(sel)
        if loc.count() > 0:
            for i in range(min(loc.count(), 3)):
                text = loc.nth(i).inner_text().strip()
                if "next" in text.lower() or ">" in text or re.search(r"\d+.*of.*\d+", text):
                    return loc.nth(i)

    # fallback: scan all links for next-like attributes
    for link in pg.locator("a").all():
        try:
            text = link.inner_text().strip()
            href = link.get_attribute("href") or ""
            if "next" in text.lower() or ">" in text or "page=" in href.lower():
                return link
        except Exception:
            continue

    return None


def accept_cookies(page):
    """dismiss cookie consent banners using common button labels."""
    cookie_texts = [
        "Accept cookies", "Accept all cookies", "Accept", "I accept",
        "Agree", "Allow cookies", "OK", "Continue", "Got it",
    ]
    for txt in cookie_texts:
        try:
            btns = page.locator("button", has_text=re.compile(txt, re.IGNORECASE))
            if btns.count() > 0:
                btns.first.click(timeout=2000)
                log(f"accepted cookies via '{txt}'")
                return
            links = page.locator("a", has_text=re.compile(txt, re.IGNORECASE))
            if links.count() > 0:
                links.first.click(timeout=2000)
                log(f"accepted cookies via '{txt}'")
                return
        except (PWTimeout, Exception):
            continue


def navigate_to_start_page(page, target_page):
    """advance through pages by clicking next until the target page is reached."""
    for _ in range(target_page - 1):
        nxt = find_next_button(page)
        if nxt is None:
            log("could not reach start page — fewer pages than expected")
            break
        nxt.click()
        page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(1.0)


def detect_total_pages(page):
    """attempt to read total page count from pagination text on the page."""
    try:
        for t in page.locator("a, button, span, div").all_inner_texts():
            m = re.search(r"(\d+)\s*of\s*(\d+)", t)
            if m:
                return int(m.group(2))
    except Exception:
        pass
    return None


# main

def main():
    log("wedinos scraper starting")
    log(f"output dir : {OUTPUT_DIR.resolve()}")
    log(f"delay      : {DELAY}s")
    log(f"start page : {FROM_PAGE}")
    log(f"max pages  : {MAX_PAGES or 'unlimited'}")

    # load checkpoint if provided
    if CHECKPOINT:
        cp_path = Path(CHECKPOINT)
        if cp_path.exists():
            try:
                cp_data     = json.loads(cp_path.read_text(encoding="utf-8", errors="replace"))
                all_records = cp_data.get("records", [])
                seen_refs   = set(r["referenceCode"] for r in all_records)
                current_page = cp_data.get("lastPage", 1)
                log(f"resumed from checkpoint: {cp_path} ({len(all_records)} records, page {current_page})")
            except Exception as e:
                log(f"warning: could not load checkpoint: {e}, starting fresh")
                all_records  = []
                seen_refs    = set()
                current_page = FROM_PAGE
        else:
            log(f"warning: checkpoint not found: {cp_path}, starting fresh")
            all_records  = []
            seen_refs    = set()
            current_page = FROM_PAGE
    else:
        all_records  = []
        seen_refs    = set()
        current_page = FROM_PAGE

    total_pages = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent="safe-harm-reduction-platform/1.0 (uk harm reduction research)",
            locale="en-GB",
        )
        page = context.new_page()

        # skip images and fonts to reduce bandwidth and improve speed
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf}", lambda r: r.abort())

        log("loading https://wedinos.wales/sample/")
        page.goto("https://wedinos.wales/sample/", wait_until="networkidle", timeout=60000)

        accept_cookies(page)

        # results load asynchronously — wait for h2 headings then let js settle
        log("waiting for sample results to render")
        try:
            page.wait_for_selector("h2", timeout=15000)
            time.sleep(1.5)
        except PWTimeout:
            log("warning: timed out waiting for sample headings")

        if FROM_PAGE > 1:
            log(f"navigating to start page {FROM_PAGE}")
            navigate_to_start_page(page, FROM_PAGE)
            current_page = FROM_PAGE

        while True:
            if MAX_PAGES and current_page > MAX_PAGES:
                log(f"reached max pages ({MAX_PAGES})")
                break

            log(f"page {current_page}{f'/{total_pages}' if total_pages else ''}")

            page_records = parse_page(page)

            # deduplicate against already-seen reference codes
            new_records = [r for r in page_records if r["referenceCode"] not in seen_refs]
            for r in new_records:
                seen_refs.add(r["referenceCode"])
            all_records.extend(new_records)

            n_analysed  = sum(1 for r in new_records if r.get("analysed"))
            n_mismatch  = sum(1 for r in new_records if r.get("adulterantMismatch"))
            n_high_risk = sum(1 for r in new_records if r.get("highRiskSubstances"))
            n_dupes     = len(page_records) - len(new_records)

            log(
                f"  {len(new_records)} new | {n_analysed} analysed | "
                f"{n_mismatch} mismatches | {n_high_risk} high-risk"
                + (f" | {n_dupes} dupes skipped" if n_dupes else "")
            )

            if total_pages is None:
                total_pages = detect_total_pages(page)
                if total_pages:
                    log(f"total pages detected: {total_pages} (~{total_pages * 10} records)")

            # save a checkpoint every 10 pages
            if current_page % 10 == 0:
                cp = OUTPUT_DIR / f"checkpoint-page-{current_page}.json"
                cp.write_text(
                    json.dumps({"lastPage": current_page, "totalUnique": len(all_records), "records": all_records},
                               indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                log(f"  checkpoint saved ({len(all_records)} unique records total)")

            nxt = find_next_button(page)
            if nxt is None:
                log("no next button found — scrape complete")
                break

            nxt.click()
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PWTimeout:
                pass
            try:
                page.wait_for_selector("h2", timeout=5000)
            except PWTimeout:
                pass
            time.sleep(DELAY)
            current_page += 1

        browser.close()

    log(f"scrape done — {len(all_records)} unique records across {current_page} pages")

    # write outputs

    raw_path = OUTPUT_DIR / "wedinos-raw.json"
    raw_path.write_text(json.dumps({
        "metadata": {
            "source":       "https://wedinos.wales/sample/",
            "scrapedAt":    datetime.now(timezone.utc).isoformat(),
            "totalRecords": len(all_records),
            "scraper":      "safe-harm-reduction-platform/wedinos-scraper v2.0 (playwright)",
        },
        "records": all_records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"raw data written to {raw_path}")

    alerts      = [r for r in all_records if r.get("analysed") and r.get("alert")]
    alerts_path = OUTPUT_DIR / "wedinos-alerts.json"
    alerts_path.write_text(json.dumps({
        "metadata": {
            "source":      "https://wedinos.wales/sample/",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "totalAlerts": len(alerts),
            "note":        "records with adulterant mismatches or high-risk substance detections",
        },
        "alerts": alerts,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"alerts written to {alerts_path} ({len(alerts)} records)")

    summary      = build_summary(all_records)
    summary_path = OUTPUT_DIR / "wedinos-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"summary written to {summary_path}")

    log(f"total unique records  : {len(all_records)}")
    log(f"analysed              : {summary['totals']['analysed']}")
    log(f"not analysed          : {summary['totals']['notAnalysed']}")
    log(f"adulterant mismatches : {summary['totals']['adulterantMismatches']} ({summary['totals']['mismatchRate']})")
    log(f"high-risk finds       : {summary['totals']['highRiskFinds']}")

    if summary["highRiskSubstanceCounts"]:
        log("high-risk substances detected:")
        for sub, n in summary["highRiskSubstanceCounts"].items():
            log(f"  {sub}: {n}")


if __name__ == "__main__":
    main()

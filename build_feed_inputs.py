"""
Livestock inputs for the corn feed logic of the Draw Territory model, from NASS QuickStats (a handful of paced calls; the key is NASS_API_KEY in .streamlit/secrets.toml).

  data/feed/census_county_livestock.csv   Census of Agriculture county inventories (2012, 2017, 2022): year, fips, species, head. Withheld counties ('(D)') are kept with
                                          head empty and withheld = 1, so the shares can spread the state's undisclosed remainder over them instead of dropping them.
  data/feed/census_state_livestock.csv    the Census's own state totals for the same series (year, state, species, head): what the disclosed counties are measured against.
  data/feed/nass_state_livestock.csv      NASS survey state inventories, every year from 2012: year, state, species, period, head (hogs are quarterly, cattle are Jan 1 and
                                          July, broilers and turkeys are birds produced in the year).

Species: CATTLE ALL, CATTLE ON FEED, MILK COWS, BEEF COWS, HOGS, LAYERS, BROILERS, TURKEYS.

    python build_feed_inputs.py            # about 40 calls, 3 seconds apart
"""
import csv
import json
import os
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "feed")
KEY = tomllib.load(open(os.path.join(HERE, ".streamlit", "secrets.toml"), "rb"))["NASS_API_KEY"]
CENSUS_YEARS = ("2012", "2017", "2022")
SERIES = {      # species -> (census short_desc, survey short_desc)
    "CATTLE ALL": ("CATTLE, INCL CALVES - INVENTORY", "CATTLE, INCL CALVES - INVENTORY"),
    "CATTLE ON FEED": ("CATTLE, ON FEED - INVENTORY", "CATTLE, ON FEED - INVENTORY"),
    "MILK COWS": ("CATTLE, COWS, MILK - INVENTORY", "CATTLE, COWS, MILK - INVENTORY"),
    "BEEF COWS": ("CATTLE, COWS, BEEF - INVENTORY", "CATTLE, COWS, BEEF - INVENTORY"),
    "HOGS": ("HOGS - INVENTORY", "HOGS - INVENTORY"),
    "LAYERS": ("CHICKENS, LAYERS - INVENTORY", "CHICKENS, LAYERS - INVENTORY"),
    "BROILERS": ("CHICKENS, BROILERS - INVENTORY", "CHICKENS, BROILERS - PRODUCTION, MEASURED IN HEAD"),
    "TURKEYS": ("TURKEYS - INVENTORY", "TURKEYS - PRODUCTION, MEASURED IN HEAD"),
}


def call(params, tries=4):
    q = urllib.parse.urlencode({**params, "key": KEY, "format": "JSON"})
    for i in range(tries):
        try:
            with urllib.request.urlopen("https://quickstats.nass.usda.gov/api/api_GET/?" + q, timeout=180) as r:
                return json.load(r)["data"]
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return []                                                 # no rows
            time.sleep(20 * (i + 1) if e.code in (403, 429) else 5)
        except Exception:
            time.sleep(5)
    raise RuntimeError(f"NASS call failed: {params}")


def val(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def survey_only(species):
    """Append (or replace) just these species in nass_state_livestock.csv."""
    path = os.path.join(OUT, "nass_state_livestock.csv")
    keep = [r for r in csv.reader(open(path, newline="")) if r and (r[2] not in species or r[0] == "year")]
    for sp in species:
        rows = call({"short_desc": SERIES[sp][1], "source_desc": "SURVEY", "agg_level_desc": "STATE", "domain_desc": "TOTAL", "unit_desc": "HEAD", "year__GE": "2012"})
        time.sleep(3)
        for r in rows:
            v = val(r["Value"])
            if v is not None:
                keep.append([r["year"], r["state_alpha"], sp, r.get("reference_period_desc", ""), v])
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(keep)
    print("survey rows", len(keep) - 1)


def main():
    os.makedirs(OUT, exist_ok=True)
    county, cstate = [], []
    for sp, (cdesc, _) in SERIES.items():
        for yr in CENSUS_YEARS:
            for agg, bucket in (("COUNTY", county), ("STATE", cstate)):
                rows = call({"short_desc": cdesc, "source_desc": "CENSUS", "year": yr, "agg_level_desc": agg, "domain_desc": "TOTAL", "unit_desc": "HEAD"})
                time.sleep(3)
                for r in rows:
                    v = val(r["Value"])
                    if agg == "COUNTY":
                        if r.get("county_code") in ("", "998", None) or not r.get("state_fips_code"):
                            continue
                        bucket.append((yr, r["state_fips_code"] + r["county_code"], sp, "" if v is None else v, 1 if v is None else 0))
                    else:
                        bucket.append((yr, r["state_alpha"], sp, "" if v is None else v))
            print(sp, yr, "county rows", len(county), flush=True)
    with open(os.path.join(OUT, "census_county_livestock.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "fips", "species", "head", "withheld"])
        w.writerows(county)
    with open(os.path.join(OUT, "census_state_livestock.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "state", "species", "head"])
        w.writerows(cstate)
    survey = []
    for sp, (_, sdesc) in SERIES.items():
        rows = call({"short_desc": sdesc, "source_desc": "SURVEY", "agg_level_desc": "STATE", "domain_desc": "TOTAL", "unit_desc": "HEAD", "year__GE": "2012"})
        time.sleep(3)
        for r in rows:
            v = val(r["Value"])
            if v is not None:
                survey.append((r["year"], r["state_alpha"], sp, r.get("reference_period_desc", ""), v))
        print(sp, "survey rows", len(survey), flush=True)
    with open(os.path.join(OUT, "nass_state_livestock.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "state", "species", "period", "head"])
        w.writerows(survey)
    print("done")


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--survey-only"]:
        survey_only(sys.argv[2:] or list(SERIES))
    else:
        main()

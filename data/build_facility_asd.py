#!/usr/bin/env python3
"""
build_facility_asd.py
Extracts ethanol and crush plant data from JSA spreadsheets,
maps each plant to a NASS ASD, and saves:
  data/ethanol_plants_asd.csv
  data/crush_plants_asd.csv

Run once (or when the source spreadsheets update):
  python data/build_facility_asd.py
"""

import csv, json, time, os, sys, re
import urllib.request, urllib.parse
import pandas as pd
from pathlib import Path
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

ETHANOL_XLSX = (
    r"C:\Users\KoltenPostin\John Stewart and Associates"
    r"\JSA - Documents\Research Analyst\Misc\Ethanol\PRX Corn Plant List - All.xlsx"
)
CRUSH_XLSX = (
    r"C:\Users\KoltenPostin\John Stewart and Associates"
    r"\JSA - Documents\Research Analyst\Misc\Crush\Crush Downtime Calculator.xlsx"
)
DATA_DIR = Path(__file__).parent
NASS_KEY = "9A6D1EB8-4D94-3221-BA0C-ADD4533EA0C1"
NASS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"

# Ethanol conversion constants
BU_PER_GAL_ETHANOL = 1 / 2.8        # 1 bu corn -> ~2.8 gal ethanol
DDGS_LBS_PER_BU_CORN = 17.5         # 17.5 lb DDGS per bu corn input
WET_FRACTION = 0.35                  # ~35% of coproduct leaves as wet distillers
DRY_FRACTION = 0.65                  # ~65% leaves as dry (railshippable) DDGS


# ── Step 1: Build county FIPS -> ASD crosswalk via NASS ─────────────────────

def fetch_nass_county_asd(state_alpha: str) -> list[dict]:
    """One NASS API call -> list of {county_ansi, asd_code, asd_desc, state_fips_code}."""
    params = {
        "key": NASS_KEY,
        "commodity_desc": "CORN",
        "statisticcat_desc": "AREA PLANTED",
        "year": "2023",
        "agg_level_desc": "COUNTY",
        "state_alpha": state_alpha,
        "format": "json",
    }
    url = NASS_BASE + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
        rows = data.get("data", [])
        seen = set()
        out = []
        for row in rows:
            key = (row.get("state_fips_code", ""), row.get("county_ansi", ""))
            if key in seen or not key[1]:
                continue
            seen.add(key)
            out.append({
                "state_fips": row.get("state_fips_code", "").zfill(2),
                "county_ansi": row.get("county_ansi", "").zfill(3),
                "asd_code": row.get("asd_code", ""),
                "asd_desc": row.get("asd_desc", ""),
            })
        return out
    except Exception as e:
        print(f"  NASS error for {state_alpha}: {e}")
        return []


def build_fips_to_asd(states: list[str]) -> dict:
    """Returns {county_fips_5: (asd_code, asd_desc)} for all requested states."""
    mapping = {}
    for st in states:
        print(f"  Fetching ASD map: {st}")
        rows = fetch_nass_county_asd(st)
        for r in rows:
            fips5 = r["state_fips"] + r["county_ansi"]
            mapping[fips5] = (r["asd_code"], r["asd_desc"])
        time.sleep(0.5)
    return mapping


# ── Step 2: Census county FIPS lookup ──────────────────────────────────────

def load_census_county_fips() -> dict:
    """
    Download Census national county list and return
    {(state_abbr.upper(), county_name.lower()): fips_5}.
    Falls back to cached file if present.
    """
    cache = DATA_DIR / "census_county_fips.csv"
    if cache.exists():
        df = pd.read_csv(cache, dtype=str)
    else:
        url = "https://www2.census.gov/geo/docs/reference/codes/files/national_county.txt"
        df = pd.read_csv(url, header=None, dtype=str,
                         names=["state", "state_fips", "county_fips", "county_name", "class"])
        df.to_csv(cache, index=False)

    # Build lookup: (ST, county_name_lower) -> fips5
    lookup = {}
    for _, row in df.iterrows():
        st = str(row["state"]).strip().upper()
        name = str(row["county_name"]).strip().lower()
        fips5 = str(row["state_fips"]).zfill(2) + str(row["county_fips"]).zfill(3)
        # Store full name and each suffix-stripped variant
        lookup[(st, name)] = fips5
        for suffix in [" county", " parish", " borough", " census area", " municipality"]:
            bare = name.replace(suffix, "").strip()
            if bare != name:
                lookup[(st, bare)] = fips5
    return lookup


def county_name_to_fips(state: str, county: str, fips_lookup: dict) -> str | None:
    st = state.strip().upper()
    cn = county.strip().lower()
    # Try exact, then strip "county"/"parish"
    for suffix in ["", " county", " parish", " borough"]:
        key = (st, cn.replace(suffix, "").strip())
        if key in fips_lookup:
            return fips_lookup[key]
    return None


# ── Step 3: Geocode city+state to county FIPS ──────────────────────────────

_geocoder = Nominatim(user_agent="jsa_facility_mapper", timeout=10)

def geocode_to_county_fips(city: str, state: str, fips_lookup: dict) -> str | None:
    """Geocode city+state via Nominatim, then reverse-match county name to FIPS."""
    query = f"{city}, {state}, USA"
    try:
        loc = _geocoder.geocode(query, addressdetails=True, country_codes="us")
        if not loc:
            return None
        addr = loc.raw.get("address", {})
        county_raw = (addr.get("county") or addr.get("village") or "").lower()
        return county_name_to_fips(state, county_raw, fips_lookup)
    except (GeocoderTimedOut, Exception):
        return None


# ── Step 4: Extract ethanol plants ─────────────────────────────────────────

def extract_ethanol(fips_lookup: dict, asd_map: dict) -> pd.DataFrame:
    import openpyxl
    wb = openpyxl.load_workbook(ETHANOL_XLSX, read_only=True, data_only=True)
    ws = wb["Corn Processing Plants"]
    rows = list(ws.iter_rows(values_only=True))
    # row 0 = title, row 1 = header, row 2+ = data
    records = []
    for r in rows[2:]:
        if not r[0]:
            continue
        company, state, city, county, status, cls, typ, eth_gal, corn_truck, start = r[:10]
        if status != "Run" or cls != "Ethanol":
            continue
        eth_gal = eth_gal or 0
        corn_input_bu = eth_gal * BU_PER_GAL_ETHANOL
        ddgs_lbs_total = corn_input_bu * DDGS_LBS_PER_BU_CORN
        ddgs_lbs_wet   = ddgs_lbs_total * WET_FRACTION
        ddgs_lbs_dry   = ddgs_lbs_total * DRY_FRACTION

        fips5 = county_name_to_fips(state or "", county or "", fips_lookup)
        asd_code, asd_desc = asd_map.get(fips5, ("", "")) if fips5 else ("", "")

        records.append({
            "company":        company,
            "state":          state,
            "city":           city,
            "county":         county,
            "process_type":   typ,          # Dry / Wet / Cellulosic
            "ethanol_gal_yr": eth_gal,
            "corn_input_bu_yr": round(corn_input_bu),
            "ddgs_lbs_yr":    round(ddgs_lbs_total),
            "ddgs_wet_lbs_yr": round(ddgs_lbs_wet),
            "ddgs_dry_lbs_yr": round(ddgs_lbs_dry),
            "county_fips5":   fips5 or "",
            "asd_code":       asd_code,
            "asd_desc":       asd_desc,
        })
    return pd.DataFrame(records)


# ── Step 5: Extract crush plants ───────────────────────────────────────────

def extract_crush(fips_lookup: dict, asd_map: dict) -> pd.DataFrame:
    import openpyxl
    wb = openpyxl.load_workbook(CRUSH_XLSX, read_only=True, data_only=True)
    ws = wb["JSA Sheet"]
    rows = list(ws.iter_rows(values_only=True))
    records = []
    for r in rows[2:]:
        if not r[0]:
            continue
        company = r[0]; region = r[2]; state = r[4]; city = r[5]; status = r[6]
        plate_annual = r[7] or 0   # Plate Annual Grind (bu/yr)
        annual_grind = r[8] or 0   # Actual Annual Grind (bu/yr)
        daily_rate   = r[10] or 0  # Daily Rate (bu/day)

        if status != "Run":
            continue

        # Geocode city+state -> county FIPS (with rate limiting)
        print(f"  Geocoding: {city}, {state}")
        fips5 = geocode_to_county_fips(city or "", state or "", fips_lookup)
        time.sleep(1.1)  # Nominatim rate limit: 1 req/sec

        asd_code, asd_desc = asd_map.get(fips5, ("", "")) if fips5 else ("", "")

        records.append({
            "company":          company,
            "region":           region,
            "state":            state,
            "city":             city,
            "plate_annual_bu":  round(plate_annual),
            "actual_annual_bu": round(annual_grind),
            "daily_rate_bu":    round(daily_rate),
            "county_fips5":     fips5 or "",
            "asd_code":         asd_code,
            "asd_desc":         asd_desc,
        })
    return pd.DataFrame(records)


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # States with meaningful corn/soy facility presence
    STATES = [
        "AL","AR","GA","IL","IN","IA","KS","KY","LA","MI","MN","MO",
        "MS","NE","ND","OH","OK","SD","TN","TX","WI","WY","CO","MT","ID",
        "WA","OR","CA","AZ","PA","NY","VA","NC","SC","WV","MD","DE","NJ",
    ]

    print("Building county FIPS -> ASD crosswalk from NASS...")
    asd_map = build_fips_to_asd(STATES)
    print(f"  {len(asd_map)} county->ASD entries loaded")

    print("Loading Census county FIPS lookup...")
    fips_lookup = load_census_county_fips()
    print(f"  {len(fips_lookup)} county entries loaded")

    print("\nExtracting ethanol plants...")
    eth_df = extract_ethanol(fips_lookup, asd_map)
    out_eth = DATA_DIR / "ethanol_plants_asd.csv"
    eth_df.to_csv(out_eth, index=False)
    mapped = eth_df["asd_code"].ne("").sum()
    print(f"  {len(eth_df)} running ethanol plants -> {mapped} ASD-mapped")
    print(f"  Saved: {out_eth}")

    print("\nExtracting crush plants (geocoding cities — ~90 sec)...")
    crush_df = extract_crush(fips_lookup, asd_map)
    out_crush = DATA_DIR / "crush_plants_asd.csv"
    crush_df.to_csv(out_crush, index=False)
    mapped = crush_df["asd_code"].ne("").sum()
    print(f"  {len(crush_df)} running crush plants -> {mapped} ASD-mapped")
    print(f"  Saved: {out_crush}")

    print("\nDone.")
    print(f"\nEthanol summary:")
    print(eth_df[["state","asd_desc","ethanol_gal_yr","ddgs_lbs_yr"]].groupby(["state","asd_desc"]).sum().to_string())

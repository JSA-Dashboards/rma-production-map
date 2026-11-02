"""
Regional class priors for wheat: data/wheat/county_class_prior.csv (state, fips, county, group, class, weight, basis).

NASS publishes wheat by county only as WINTER / SPRING (excl. durum) / DURUM, and by market class (hard red winter, soft white ...) only as each STATE's share. Inside a state the app
therefore needs an assumption about WHERE each class is grown. These weights are that assumption: a relative likelihood of a class in a county (only ratios matter). The app fits them to the
NASS numbers (iterative proportional fitting in app.py: county winter/spring production fixed, state class totals fixed), so a prior can move bushels between counties of a state but never
changes a state's class total or a county's winter/spring production. No public county-by-class series exists (checked 11-02: Washington's variety survey is statewide, Michigan and Idaho
sources are anecdotes), so each rule says what it rests on in the `basis` column. States not listed here keep the plain state mix in every county.

Rules (the states carrying ~70% of the minority-class bushels: ID, KS, WA, MI; then OR, TX, NE)
  yield   WA, OR   a county's mean NASS yield (2015-2025, published years) relative to its state is a rain/irrigation signal: soft white goes where yields are high (wetter Palouse),
                   hard red where they are low (dry Columbia basin). weight = exp(+0.5 z) for soft white, exp(-1.0 z) for hard red, z = clipped z-score of the yield.
  region  ID       north Idaho (Palouse, Camas Prairie, Panhandle) soft white; southeast / upper Snake hard red; Magic Valley and west neutral (Idaho Wheat Commission: north and south grow different classes).
  region  MI       Thumb and Saginaw Valley ~75-80% soft white (a Thumb miller quoted in Farms.com 'Wheat Watchers 2023 Wrap Up'); elsewhere mostly soft red.
  region  NE       hard white identity-preserved programs sit in the Panhandle.
  lon     KS, TX   soft red grows on the wetter east side (SRW weight rises eastward); hard white in Kansas rises westward.
The numbers are agronomic judgement, not a fitted model: review them (they are in this file and in the CSV) and change what you know better.

    python build_wheat_county_prior.py        # needs NASS_API_KEY in the environment or .streamlit/secrets.toml (yields only); fetches the county GeoJSON the app uses
"""
import json
import math
import os
import sys
import time
import tomllib
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wheat", "county_class_prior.csv")
GEO = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
NASS = "https://quickstats.nass.usda.gov/api/api_GET/"
STATE_FIPS = {"WA": "53", "OR": "41", "ID": "16", "MI": "26", "KS": "20", "TX": "48", "NE": "31"}
WINTER = ["Hard red winter", "Soft red winter", "Soft white winter", "Hard white winter"]
SPRING = ["Hard red spring", "Soft white spring"]

ID_NORTH = ["Benewah", "Bonner", "Boundary", "Clearwater", "Idaho", "Kootenai", "Latah", "Lewis", "Nez Perce", "Shoshone"]
ID_SOUTHEAST = ["Bannock", "Bear Lake", "Bingham", "Bonneville", "Butte", "Caribou", "Clark", "Custer", "Franklin", "Fremont", "Jefferson", "Lemhi", "Madison", "Oneida", "Power", "Teton"]
MI_THUMB = ["Huron", "Sanilac", "Tuscola", "Saginaw", "Bay", "Lapeer", "St. Clair", "Genesee"]
NE_PANHANDLE = ["Banner", "Box Butte", "Cheyenne", "Dawes", "Deuel", "Garden", "Kimball", "Morrill", "Scotts Bluff", "Sheridan", "Sioux"]


def key():
    k = os.environ.get("NASS_API_KEY", "")
    if not k:
        p = os.path.join(HERE, ".streamlit", "secrets.toml")
        if os.path.exists(p):
            k = tomllib.load(open(p, "rb")).get("NASS_API_KEY", "")
    if not k:
        sys.exit("REFUSING TO RUN: no NASS_API_KEY in the environment or .streamlit/secrets.toml")
    return k


def counties():
    """{fips: (state, name, lon, lat)} for the states above, from the app's GeoJSON (centroid of the largest part)."""
    with urllib.request.urlopen(GEO, timeout=120) as r:
        feats = json.load(r)["features"]
    inv = {v: k for k, v in STATE_FIPS.items()}
    out = {}
    for f in feats:
        p = f["properties"]
        if p["STATE"] not in inv:
            continue
        g = f["geometry"]
        parts = [g["coordinates"][0]] if g["type"] == "Polygon" else [c[0] for c in g["coordinates"]]
        best = max(parts, key=lambda ring: abs(sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))))
        out[p["STATE"] + p["COUNTY"]] = (inv[p["STATE"]], p["NAME"], sum(x for x, _ in best) / len(best), sum(y for _, y in best) / len(best))
    return out


def yields(k, state, cls):
    """{fips: mean county yield 2015+} for one NASS class group (published years only)."""
    p = dict(key=k, format="JSON", source_desc="SURVEY", commodity_desc="WHEAT", class_desc=cls, statisticcat_desc="YIELD", unit_desc="BU / ACRE", agg_level_desc="COUNTY",
             state_alpha=state, year__GE="2015")
    rows = []
    for a in range(4):
        try:
            with urllib.request.urlopen(NASS + "?" + urllib.parse.urlencode(p), timeout=90) as r:
                rows = json.load(r).get("data", [])
            break
        except urllib.error.HTTPError as e:
            if e.code == 400:
                break
            time.sleep(4 * (a + 1))
        except Exception:
            time.sleep(4 * (a + 1))
    time.sleep(0.6)
    acc = {}
    for r in rows:
        if str(r.get("county_name", "")).upper().startswith("OTHER") or r.get("county_ansi") in (None, "", "998", "000", "999"):
            continue
        try:
            v = float(str(r["Value"]).replace(",", ""))
        except ValueError:
            continue
        acc.setdefault(r["state_fips_code"].zfill(2) + r["county_ansi"].zfill(3), []).append(v)
    return {f: sum(v) / len(v) for f, v in acc.items()}


def zscores(y):
    if len(y) < 4:
        return {}
    m = sum(y.values()) / len(y)
    sd = math.sqrt(sum((v - m) ** 2 for v in y.values()) / len(y)) or 1.0
    return {f: max(-2.0, min(2.0, (v - m) / sd)) for f, v in y.items()}


def main():
    k = key()
    cty = counties()
    rows = []

    def add(fips, group, cls, w, basis):
        st, name, _, _ = cty[fips]
        rows.append((st, fips, name, group, cls, round(max(w, 0.02), 4), basis))

    for st in ("WA", "OR"):                                              # yield-as-rainfall rule, winter and (WA) spring
        for group, soft, hard, cls in (("WINTER", "Soft white winter", "Hard red winter", "WINTER"), ("SPRING", "Soft white spring", "Hard red spring", "SPRING, (EXCL DURUM)")):
            if st == "OR" and group == "SPRING":
                continue
            y = yields(k, st, cls)
            z = zscores(y)
            print(f"  {st} {group}: yields for {len(y)} counties, mean {sum(y.values()) / max(len(y), 1):.1f}")
            basis = f"{st} {group.lower()}: county mean NASS yield 2015+ as a rainfall signal; soft white weight exp(+0.5 z), hard red exp(-1.0 z) (dry Columbia basin = hard red)"
            for f, (s_, *_r) in cty.items():
                if s_ != st:
                    continue
                zz = z.get(f, 0.0)
                add(f, group, soft, math.exp(0.5 * zz), basis)
                add(f, group, hard, math.exp(-1.0 * zz), basis)
    b_id = "ID: north Idaho (Palouse, Camas Prairie, Panhandle) soft white; southeast / upper Snake hard red; Magic Valley and west neutral (Idaho Wheat Commission: north and south grow different classes)"
    for f, (s_, name, _, _) in cty.items():
        if s_ == "ID":
            north, se = name in ID_NORTH, name in ID_SOUTHEAST
            add(f, "WINTER", "Soft white winter", 3.0 if north else 0.5 if se else 1.0, b_id)
            add(f, "WINTER", "Hard red winter", 0.3 if north else 3.0 if se else 1.0, b_id)
            add(f, "WINTER", "Hard white winter", 1.0, b_id)
            add(f, "SPRING", "Soft white spring", 3.0 if north else 0.6 if se else 1.0, b_id)
            add(f, "SPRING", "Hard red spring", 0.3 if north else 2.5 if se else 1.0, b_id)
    b_mi = "MI: Thumb and Saginaw Valley ~75-80% soft white (a Thumb miller in Farms.com 'Wheat Watchers 2023 Wrap Up'); elsewhere mostly soft red"
    for f, (s_, name, _, _) in cty.items():
        if s_ == "MI":
            thumb = name in MI_THUMB
            add(f, "WINTER", "Soft white winter", 0.78 if thumb else 0.2, b_mi)
            add(f, "WINTER", "Soft red winter", 0.22 if thumb else 0.8, b_mi)
            add(f, "WINTER", "Hard red winter", 0.05, b_mi)
    b_ne = "NE: identity-preserved hard white programs sit in the Panhandle"
    for f, (s_, name, _, _) in cty.items():
        if s_ == "NE":
            add(f, "WINTER", "Hard white winter", 5.0 if name in NE_PANHANDLE else 1.0, b_ne)
            add(f, "WINTER", "Hard red winter", 1.0, b_ne)
    b_ks = "KS: soft red grows on the wetter east side (weight exp((lon+96.5)/1.5)); hard white rises westward (exp(-(lon+99)/3), 0.2-4)"
    b_tx = "TX: soft red only on the wetter east side (Blacklands), weight exp((lon+97.5)/1.2); the Panhandle and Rolling Plains are hard red"
    for f, (s_, name, lon, _) in cty.items():
        if s_ == "KS":
            add(f, "WINTER", "Hard red winter", 1.0, b_ks)
            add(f, "WINTER", "Soft red winter", math.exp((lon + 96.5) / 1.5), b_ks)
            add(f, "WINTER", "Hard white winter", min(4.0, max(0.2, math.exp(-(lon + 99.0) / 3.0))), b_ks)
        elif s_ == "TX":
            add(f, "WINTER", "Hard red winter", 1.0, b_tx)
            add(f, "WINTER", "Soft red winter", math.exp((lon + 97.5) / 1.2), b_tx)
    d = pd.DataFrame(rows, columns=["state", "fips", "county", "group", "class", "weight", "basis"]).sort_values(["state", "fips", "class"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    d.to_csv(OUT, index=False)
    print(f"wrote {OUT}: {len(d)} rows, {d.fips.nunique()} counties, states {sorted(d.state.unique())}")


if __name__ == "__main__":
    main()

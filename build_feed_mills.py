"""
Feed mill locations: the FDA medicated feed mill license list, geocoded, merged with the basis tracker's feed mills -> data/feed_mills.csv.

    python build_feed_mills.py [fda_xlsx] [location_meta_parquet]

Inputs
  * the FDA medicated feed mill license list (Site Name, License No., Address, City, State, Zip Code, CountryProvince): 745 sites, no coordinates, no capacity.
    Default: ~/Downloads/fda_medicated_feed_mills_by_state.xlsx
  * the basis tracker's location_meta export (facility_type 'Feed Mill': mills that post cash corn bids). Default: ../basis-tracker-streamlit/snowflake/export/location_meta.parquet
  * data/rail_map/geo_search.json.gz (ZIP, town and county centres already in this repo): no geocoding service is called.

Geocoding (approximate: good to a few miles, not a street address)
  1. the centre of the 5-digit ZIP; 2. if there is no ZIP, the town "City, ST". Each ZIP centre is checked against the town centre; a gap over 25 miles is flagged in geo_check
     (a mistyped ZIP or an unusual town), and the town centre is used when the ZIP is more than 60 miles from it. Canada and Puerto Rico are left without coordinates.
Merge with the tracker: a tracker mill matches an FDA site when the city and state agree and the operator's name appears in the site name (match 'operator+city'), or, failing
  that, when exactly one FDA site is in that city (match 'city only'). Tracker mills with no FDA match are kept as their own rows, with the tracker's coordinates.
Columns named *_guess are my reading of the site name, not data.
"""
import gzip
import json
import math
import os
import re
import sys

import pandas as pd
from openpyxl.styles import Font

HERE = os.path.dirname(os.path.abspath(__file__))
FDA = os.path.join(os.path.expanduser("~"), "Downloads", "fda_medicated_feed_mills_by_state.xlsx")
META = os.path.normpath(os.path.join(HERE, "..", "basis-tracker-streamlit", "snowflake", "export", "location_meta.parquet"))
GEO = os.path.join(HERE, "data", "rail_map", "geo_search.json.gz")
OUT = os.path.join(HERE, "data", "feed_mills.csv")
MANUAL_TOWNS = {("clackamas", "OR"): (45.4073, -122.5706)}              # towns the index lacks (unincorporated places) and the FDA list gives no ZIP for
OPERATORS = {"Tyson": r"tyson", "JBS": r"\bjbs\b", "Scoular": r"scoular", "ADM": r"\badm\b|archer daniels", "Oracle Pork Nutrition": r"oracle"}


def norm(s: str) -> str:
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"\bsaint\b|\bst\.", "st", s)
    s = re.sub(r"\bft\.?\b", "fort", s)
    s = re.sub(r"\bmt\.?\b", "mount", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def miles(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def name_kind(name: str) -> str:
    n = name.lower()
    if re.search(r"feedlot|feeders|feed ?yard|cattle|beef|dairy|\bfarms?\b|ranch|livestock|calf|\bcow", n):
        return "feedyard_or_farm"
    if re.search(r"tyson|pilgrim|perdue|koch|wayne|sanderson|mountaire|cargill|\bjbs\b|smithfield|seaboard|butterball|foster|amick|raeford|peco|simmons|cal-maine|hormel|murphy", n):
        return "integrator_or_major"
    if re.search(r"co-?op|cooperative|farmers|elevator|grain|agri|\bag\b|agfinity|land o|purina|nutrena|hubbard|alltech|archer|\badm\b|kent|ridley|nutrition|feeds?\b|mills?\b", n):
        return "feed_company_or_coop"
    return "other"


def main(fda_path=FDA, meta_path=META, xlsx_path=None):
    fda = pd.read_excel(fda_path, dtype={"Zip Code": str, "License No.": str})
    fda.columns = ["name", "license_no", "address", "city", "state", "zip_raw", "country"]
    fda["zip"] = fda.zip_raw.fillna("").str.extract(r"(\d{5})", expand=False)
    rows = json.load(gzip.open(GEO, "rt", encoding="utf-8"))
    zips = {r[0]: (r[1], r[2]) for r in rows if r[3] == "z"}
    towns = dict(MANUAL_TOWNS)
    for lab, la, lo, k in rows:
        if k == "p":
            city, st = lab.rsplit(", ", 1)
            towns.setdefault((norm(city), st), (la, lo))
    res = []
    for r in fda.itertuples():
        z = zips.get(r.zip) if isinstance(r.zip, str) else None
        t = towns.get((norm(r.city), r.state)) if r.country in ("United States", "Puerto Rico") else None
        lat = lon = None
        method, gap = "none", None
        if r.country not in ("United States", "Puerto Rico"):
            method = "not_us"                                          # Canada
        elif z and t:
            gap = miles(z, t)
            if gap > 60:
                (lat, lon), method = t, "town (zip far from town)"
            else:
                (lat, lon), method = z, "zip"
        elif z:
            (lat, lon), method = z, "zip (town not found)"
        elif t:
            (lat, lon), method = t, "town (no zip)"
        res.append((lat, lon, method, None if gap is None else round(gap, 1), "check" if gap is not None and gap > 25 else ""))
    fda[["lat", "lon", "geo_method", "geo_gap_miles", "geo_check"]] = pd.DataFrame(res, index=fda.index)
    fda["kind_guess"] = fda.name.fillna("").map(name_kind)
    fda["in_fda"] = True

    meta = pd.read_parquet(meta_path)
    tm = meta[meta.facility_type == "Feed Mill"].copy()

    def tracker_city(loc: str) -> str:
        loc = re.sub(r"(?i)\bcash bids?\b", "", loc)
        loc = re.sub(r"(?i)^.*\bat\b", "", loc)
        loc = re.sub(r",.*$", "", loc)
        loc = re.sub(r"(?i)\b(scoular|tyson|jbs)\b", "", loc)
        return norm(loc)

    tm["city_n"] = tm.location.map(tracker_city)
    fda["city_n"] = fda.city.map(norm)
    for c in ("posts_bids", "tracker_provider", "tracker_location", "tracker_lat", "tracker_lon", "match"):
        fda[c] = None
    extra = []
    for t in tm.itertuples():
        cand = fda[(fda.city_n == t.city_n) & (fda.state == t.state)]
        pat = OPERATORS.get(t.provider)
        by_op = cand[cand.name.fillna("").str.contains(pat, case=False, regex=True)] if pat else cand.iloc[0:0]
        hit, how = (by_op.index[0], "operator+city") if len(by_op) else ((cand.index[0], "city only") if len(cand) == 1 else (None, None))
        info = dict(posts_bids=True, tracker_provider=t.provider, tracker_location=t.location, tracker_lat=t.lat if pd.notna(t.lat) else None, tracker_lon=t.lon if pd.notna(t.lon) else None, match=how)
        if hit is not None and fda.at[hit, "tracker_provider"] is None:
            for k, v in info.items():
                fda.at[hit, k] = v
        else:                                                           # no FDA site: keep the tracker's mill as its own row (its own pin, else the town's centre)
            ll = (t.lat, t.lon) if pd.notna(t.lat) else towns.get((t.city_n, t.state), (None, None))
            extra.append(dict(name=f"{t.provider}: {t.location}", state=t.state, country="United States", lat=ll[0], lon=ll[1],
                              geo_method="tracker" if pd.notna(t.lat) else ("town (tracker row)" if ll[0] is not None else "none"), kind_guess=name_kind(t.provider + " " + t.location), in_fda=False,
                              **{**info, "match": "tracker only" if hit is None else "tracker only (FDA site already matched)"}))
    out = pd.concat([fda, pd.DataFrame(extra)], ignore_index=True)
    out["posts_bids"] = out.posts_bids.fillna(False).astype(bool)
    out["in_tracker"] = out.tracker_provider.notna()
    out.insert(0, "id", range(1, len(out) + 1))
    cols = ["id", "name", "license_no", "address", "city", "state", "zip", "country", "lat", "lon", "geo_method", "geo_gap_miles", "geo_check", "kind_guess", "in_fda", "in_tracker",
            "posts_bids", "tracker_provider", "tracker_location", "tracker_lat", "tracker_lon", "match"]
    out[cols].to_csv(OUT, index=False, encoding="utf-8")
    if xlsx_path:                                                       # an Excel copy with a short read-me sheet
        notes = pd.DataFrame({"column": ["lat, lon", "geo_method", "geo_check", "kind_guess", "in_fda / in_tracker / posts_bids", "match", "capacity"],
                              "meaning": ["Approximate: the centre of the 5-digit ZIP (else the town); good to a few miles, not a street address.",
                                          "zip, town (no zip), town (zip far from town), tracker (the basis tracker's own pin), town (tracker row), not_us (Canada)",
                                          "'check' = the ZIP centre and the town centre are more than 25 miles apart; look at the address.",
                                          "A guess from the site name only: feedyard_or_farm, integrator_or_major, feed_company_or_coop, other.",
                                          "on the FDA license list / one of the basis tracker's Feed Mill locations / that mill posts cash corn bids.",
                                          "operator+city (same operator and town), city only (the only FDA site in that town), tracker only (not on the FDA list).",
                                          "Not in this file: neither the FDA list nor the tracker has capacities."]})
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
            out[cols].to_excel(xw, sheet_name="Feed mills", index=False)
            notes.to_excel(xw, sheet_name="Read me", index=False)
            for ws in xw.book.worksheets:
                ws.freeze_panes = "A2"
                for c in ws[1]:
                    c.font = Font(bold=True)
                for col in ws.columns:
                    ws.column_dimensions[col[0].column_letter].width = min(48, max(10, max(len(str(c.value or "")) for c in col[:60]) + 2))
    print(f"wrote {OUT}: {len(out)} rows")
    print("  FDA sites:", int(out.in_fda.sum()), "| tracker mills:", len(tm), "| matched to an FDA site:", int((out.in_fda & out.in_tracker).sum()), "| tracker only:", int((~out.in_fda).sum()))
    print("  geocoded:", out.geo_method.value_counts().to_dict())
    print("  with coordinates:", int(out.lat.notna().sum()), "of", len(out), "| zip-vs-town gap over 25 miles (flagged 'check'):", int((out.geo_check == "check").sum()))


if __name__ == "__main__":
    main(*sys.argv[1:4])

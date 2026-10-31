"""
BEA economic-area key for the STB Public Use Carload Waybill Sample: data/draw/bea_key.json.

The waybill sample (AgTransport xve5-xb56) reports where a carload started and ended as a 3-digit "BEA" code, not a county or state. STB's Waybill Reference Guide
(Table 4-4 names the codes, Table 4-5 lists the counties in each) is the key. Codes 001-172 are Commerce's BEA economic areas; 173-185 are added by the railroads (Canadian
provinces 173-183, Puerto Rico 184, Mexico 185); 000 is not in the key. The county table has names and state, not FIPS, so counties are matched to data/census_county_fips.csv.

Input: the guide's text, pulled with poppler (the -raw mode keeps one county per line; -layout scrambles the columns):
    curl -L -o guide.pdf https://www.stb.gov/wp-content/uploads/2022-STB-Waybill-Reference-Guide.pdf
    pdftotext -raw -f 83 -l 85 guide.pdf names.txt
    pdftotext -raw -f 85 -l 130 guide.pdf counties.txt
    python build_bea_key.py names.txt counties.txt
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "draw", "bea_key.json")
FIPS = os.path.join(HERE, "data", "census_county_fips.csv")


def norm(s: str) -> str:
    s = s.upper().replace("&", "AND")
    s = re.sub(r"\b(COUNTY|PARISH|BOROUGH|CENSUS AREA|MUNICIPALITY)\b", "", s)
    s = re.sub(r"\bSAINT\b", "ST", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def parse_names(path: str) -> dict:
    lines = [l.strip() for l in open(path, encoding="latin-1")]
    out, cur = {}, None
    for l in lines:
        m = re.match(r"^(\d{3}) (.+)$", l)
        if m and not l.startswith("Note"):
            cur = m.group(1)
            out[cur] = m.group(2).strip()
        elif cur and (out[cur].endswith("-") or out[cur].endswith(",")) and not l.startswith(("Note", "Business", "Surface", "Table")):
            out[cur] = (out[cur] + (" " if out[cur].endswith(",") else "") + l).strip()
            cur = None
        else:
            cur = None
    return out


def main(names_path: str, counties_path: str) -> None:
    names = parse_names(names_path)
    rows = []
    for l in open(counties_path, encoding="latin-1"):
        m = re.match(r"^(\d{3}) (.+?) ([A-Z]{2})$", l.strip())
        if m and int(m.group(1)) <= 172 and m.group(2) == m.group(2).upper():             # the area-name lines that start the page are not counties
            rows.append(m.groups())
    fips = {}
    for r in csv.DictReader(open(FIPS, encoding="utf-8")):
        fips.setdefault((r["state"], norm(r["county_name"])), r["state_fips"] + r["county_fips"])
        if "city" in r["county_name"].lower():
            fips.setdefault((r["state"], norm(r["county_name"]) + "CITY"), r["state_fips"] + r["county_fips"])
    # old or misspelled names in STB's table (it also cuts county names off at 13 characters, handled by the prefix match below)
    alias = {("SD", "SHANNON"): "46102", ("AK", "WADEHAMPTON"): "02158", ("VA", "BEDFORDCITY"): "51019", ("VA", "CLIFTONFORGE"): "51005",
             ("MD", "BALTCITY"): "24510", ("DC", "DISTOFCOLUMBIA"): "11001", ("VA", "LOUDON"): "51107", ("FL", "DADE"): "12086", ("FL", "SAINTLUCIEN"): "12111",
             ("VA", "VIRGINIABCH"): "51810", ("VA", "KINGQUEEN"): "51097", ("FL", "STLUCIEN"): "12111", ("LA", "EBATONROUGE"): "22033", ("LA", "WBATONROUGE"): "22121", ("LA", "JEFFDAVIS"): "22053", ("MN", "LAKEINWOODS"): "27077", ("SD", "WASHABAUGH"): "46071",
             ("TX", "SHACKLEFORD"): "48417", ("NV", "ORMSBY"): "32510", ("CO", "ARCHULETTA"): "08007", ("MT", "YELLOWSTONEP"): "30067"}
    by_state = {}
    for (st, nm), f in fips.items():
        by_state.setdefault(st, []).append((nm, f))
    county_area, miss = {}, []
    for code, cname, st in rows:
        k = (st, norm(cname))
        f = fips.get(k) or alias.get(k) or fips.get((st, norm(cname) + "CITY"))
        if not f:
            hits = {ff for nm, ff in by_state.get(st, []) if nm.startswith(k[1])}
            f = hits.pop() if len(hits) == 1 else None
        if f:
            county_area[f] = code
        else:
            miss.append((code, cname, st))
    if "46113" in county_area:                                  # Shannon County SD became Oglala Lakota (46102) in 2015; the model uses the new code
        county_area["46102"] = county_area["46113"]
    county_area.setdefault("51550", "020")                      # Chesapeake city VA is missing from STB's list; it is in the Norfolk-Virginia Beach area
    area_names = {c: n for c, n in names.items() if int(c) <= 185}
    with open(OUT, "w") as fh:
        json.dump({"source": "STB Waybill Reference Guide 2022, Tables 4-4 and 4-5", "areas": area_names, "county_area": county_area}, fh, indent=0)
    print(f"{len(area_names)} area names, {len(rows)} county rows, {len(county_area)} matched to FIPS, {len(miss)} unmatched")
    for x in miss[:60]:
        print("  unmatched", x)


if __name__ == "__main__":
    main(*sys.argv[1:3])

"""
Builds data/draw/osrm_ring.json (the road-mile core of the Draw Territory tab's inputs; assemble_draw_inputs.py widens it to the whole country): soybean supply by county, the buyers (crush plants and river
terminals) and road miles from every county centroid to every buyer.

  Supply   5-year average NASS county soybean production (data/rail_map/county_prod_avg.json)
  Plants   Kolten's "Crush Downtime Calculator.xlsx" (JSA Sheet): annual need = Daily Max x 0.88 x 350 days, his own formula
           (K = J*0.88, I = K*350); Gilman = the existing line + the 2026 expansion row; plants Kolten says are live are in
  River    the basis tracker's river-terminal locations (data/rail_map/map_points.json)
  Miles    OSRM public demo server (OpenStreetMap roads, car profile), county centroid to buyer. The app reads the saved file.

    python build_draw_lanes.py
"""
import datetime
import io
import json
import math
import os
import time
import urllib.request
import zipfile

import openpyxl
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WORKBOOK = (r"C:\Users\KoltenPostin\John Stewart and Associates\JSA - Documents\Research Analyst\Misc\Crush"
            r"\Crush Downtime Calculator.xlsx")
GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip"
RING = {"IL", "IN", "IA", "MO", "WI", "KY", "OH"}           # states whose beans can reach an Illinois buyer
BUYER_STATES = RING | {"TN", "AR", "MS", "LA", "MI", "MN", "NE", "KS"}
RUN_DAYS, RUN_RATE = 350, 0.88                               # Kolten's workbook: Daily Rate = Daily Max x 0.88, Annual Grind = Daily Rate x 350
METERS_PER_MILE = 1609.344
# The workbook lists a few plants Kolten says are live now with state/status slips; coordinates for plants placed by city
PLANT_FIX = {("Norfolk", "IA"): "NE", ("David City", "SD"): "NE"}
LIVE_NOW = {("Upper Sandusky", "OH"), ("Mitchell", "SD"), ("David City", "NE")}


def plants():
    ws = openpyxl.load_workbook(WORKBOOK, data_only=True)["JSA Sheet"]
    pts = {(r["city"], r["state"]): r for r in json.load(open(os.path.join(DATA, "rail_map", "map_points.json")))["crush"]}
    out = {}
    for r in range(3, 100):
        co, river, st, city, status, dmax = (ws.cell(r, c).value for c in (1, 2, 5, 6, 7, 10))
        if not (co and st and city and dmax):
            continue
        st = PLANT_FIX.get((city, st), st)
        if status not in ("Run", "New/Exp", "Expansion") and (city, st) not in LIVE_NOW:
            continue                                          # Marquis (2028?) and idle plants are out
        key = (city, st)
        if key not in out:
            out[key] = {"company": co, "city": city, "state": st, "river": river or "", "dmax": 0}
        out[key]["dmax"] += dmax                              # Gilman: the existing line + the 2026 expansion row
    res = []
    for (city, st), p in out.items():
        loc = pts.get((city, st))
        if not loc or st not in BUYER_STATES:
            continue
        need = p["dmax"] * RUN_RATE * RUN_DAYS
        res.append({"key": f"{p['company'].split(',')[0].split(' Co.')[0]} {city} {st}", "company": p["company"], "city": city, "state": st,
                    "river": p["river"], "dmax": p["dmax"], "need_bu": round(need), "lat": loc["lat"], "lon": loc["lon"]})
    return res


def counties(supply):
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(GAZ, timeout=120).read()))
    g = pd.read_csv(z.open(z.namelist()[0]), sep="\t", encoding="latin1", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    g = g[g["USPS"].isin(RING)]
    return [{"fips": r.GEOID, "name": r.NAME, "state": r.USPS, "lat": round(r.INTPTLAT, 4), "lon": round(r.INTPTLONG, 4),
             "supply_bu": supply.get(r.GEOID, 0)} for r in g.itertuples() if supply.get(r.GEOID, 0) > 0]


def table(src, dst):
    """Road miles from every src (lon, lat) to every dst, in OSRM table calls of at most 100 coordinates."""
    miles = [[None] * len(dst) for _ in src]
    si, di = 40, 55
    for a in range(0, len(src), si):
        for b in range(0, len(dst), di):
            s, d = src[a:a + si], dst[b:b + di]
            coords = ";".join(f"{x:.5f},{y:.5f}" for x, y in s + d)
            ss = ";".join(str(i) for i in range(len(s)))
            dd = ";".join(str(len(s) + i) for i in range(len(d)))
            url = f"https://router.project-osrm.org/table/v1/driving/{coords}?sources={ss}&destinations={dd}&annotations=distance"
            for attempt in range(5):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "jsa-rma-production-map draw lane build"})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        res = json.load(r)
                    if res.get("code") == "Ok":
                        break
                except Exception:
                    pass
                time.sleep(3 * (attempt + 1))
            else:
                raise RuntimeError(f"OSRM failed for block {a},{b}")
            for i, row in enumerate(res["distances"]):
                for j, m in enumerate(row):
                    miles[a + i][b + j] = None if m is None else round(m / METERS_PER_MILE)
            time.sleep(1.1)                                   # the demo server asks for about a request a second
    return miles


def main():
    supply = json.load(open(os.path.join(DATA, "rail_map", "county_prod_avg.json")))["avg"]["Soybeans"]
    cty = counties(supply)
    pl = plants()
    pts = json.load(open(os.path.join(DATA, "rail_map", "map_points.json")))
    rv = [{"key": r["name"], "state": r.get("state", ""), "lat": r["lat"], "lon": r["lon"]} for r in pts["river"]
          if 33.5 <= r["lat"] <= 46 and -95 <= r["lon"] <= -80]
    buyers = [("plant", p) for p in pl] + [("river", r) for r in rv]
    miles = table([(c["lon"], c["lat"]) for c in cty], [(b["lon"], b["lat"]) for _, b in buyers])
    out = {"built": datetime.date.today().isoformat(), "source": "OSRM public demo (OpenStreetMap), car profile, county centroid to buyer",
           "run_rate": RUN_RATE, "run_days": RUN_DAYS, "counties": cty,
           "plants": pl, "river": rv, "miles": miles}
    os.makedirs(os.path.join(DATA, "draw"), exist_ok=True)
    with open(os.path.join(DATA, "draw", "osrm_ring.json"), "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    il = sum(c["supply_bu"] for c in cty if c["state"] == "IL")
    print(len(cty), "counties,", len(pl), "plants,", len(rv), "river terminals;",
          f"IL supply {il / 1e6:,.0f} M bu; IL plant need {sum(p['need_bu'] for p in pl if p['state'] == 'IL') / 1e6:,.1f} M bu")
    miss = sum(1 for row in miles for m in row if m is None)
    print("unreachable pairs:", miss)


if __name__ == "__main__":
    main()

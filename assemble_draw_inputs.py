"""
Builds data/draw/draw_inputs.json from data/draw/osrm_ring.json: the whole country's soybean counties and crush plants.

Road miles are OSRM's where build_draw_lanes.py has them (Illinois and its neighbours to the buyers near them); everywhere else
(the western plants, the far counties) it is straight-line miles x a circuity factor fitted to the OSRM pairs, so the plants
out west have western beans to draw on and don't pull Illinois beans across the country. Buyers added: every live crush plant
in Kolten's Crush Downtime Calculator, the basis tracker's river terminals, and one Pacific Northwest export node that shares
the 'residual' group with the river (beans that leave by rail to the coast, not by barge).
    python assemble_draw_inputs.py
"""
import io
import json
import math
import os
import urllib.request
import zipfile

import numpy as np
import pandas as pd

import build_draw_lanes as B

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
PNW = {"key": "PNW export (rail)", "state": "OR", "lat": 45.6, "lon": -122.7}

# Terminals the tracker has no bids for but that load barges in the St. Louis area (owners checked 2026-10-29; see the draw-territory memory note).
# `site` keeps pins that share a geocode apart: pins with the same site, or with no site and the same coordinates, count once in the model.
SITE_OVERRIDES = {"CGB - Cahokia": "cgb-cahokia", "CHS Cahokia": "cofco-chs-cahokia"}     # CHS operates COFCO's Cahokia terminal (lease, Jan 2026)
EXTRA_RIVER_PINS = [
    # PROVISIONAL cap until Richardson's barge soybean volume is verified: its elevator holds ~4 M bu (one full turn as a ceiling), and it sits beside a
    # semolina mill, so most of what it ships is likely wheat.
    {"key": "Richardson Elevator (Italgrani), St. Louis MO", "state": "MO", "lat": 38.5414, "lon": -90.2539, "site": "richardson-stl", "cap_mbu": 4.0,
     "cap_note": "Provisional until verified: the elevator holds about 4M bu (one full turn), and it sits beside a semolina mill, so much of what it loads is likely wheat."},
    # cap_mbu: a ceiling on the pin's annual soybeans. Ingram's own bid notice puts ALL grain at Tyler Street at ~175,000 tons a year, and the
    # terminal is mainly fertilizer; 175,000 t x 2000/60 = 5.8 M bu is the ceiling even if every ton were soybeans.
    {"key": "Ingram Tyler Street Terminal, St. Louis MO", "state": "MO", "lat": 38.6467, "lon": -90.1822, "site": "ingram-tyler-st", "cap_mbu": 5.8,
     "cap_note": "Ingram's own bid notice puts all grain at Tyler Street at about 175,000 tons a year (fertilizer is its main cargo), so 5.8M bu is the ceiling even if every ton were soybeans."},
    # A hub point, not an elevator: the Pacific Northwest is modeled by region (Columbia River, Puget Sound), fed by BNSF and UP rail.
    {"key": "PNW Puget Sound hub (Seattle/Tacoma)", "state": "WA", "lat": 47.25, "lon": -122.42, "site": "pnw-puget-sound"},
    # Texas Gulf and Mexico rail are modeled as hubs too. The Texas Gulf pin stands for Houston, Galveston and Corpus Christi together; the border
    # pins are the crossings the grain rails through (UP and BNSF to Laredo, Eagle Pass, Brownsville and El Paso; CPKC/KCS to Laredo).
    {"key": "Texas Gulf hub (Houston/Galveston)", "state": "TX", "lat": 29.73, "lon": -95.27, "site": "texas-gulf-hub"},
    {"key": "Mexico rail: Laredo TX", "state": "TX", "lat": 27.50, "lon": -99.51, "site": "mx-laredo"},
    {"key": "Mexico rail: Eagle Pass TX", "state": "TX", "lat": 28.71, "lon": -100.50, "site": "mx-eagle-pass"},
    {"key": "Mexico rail: Brownsville TX", "state": "TX", "lat": 25.90, "lon": -97.50, "site": "mx-brownsville"},
    {"key": "Mexico rail: El Paso TX", "state": "TX", "lat": 31.76, "lon": -106.49, "site": "mx-el-paso"},
    # East Gulf export elevators that beans reach by rail or truck instead of barge: Louisiana (Reserve to Destrehan) and Mobile, each one hub point.
    {"key": "Louisiana rail hub (Reserve/Convent)", "state": "LA", "lat": 30.06, "lon": -90.55, "site": "la-rail-hub"},
    {"key": "Mobile hub (Mobile/Pascagoula)", "state": "AL", "lat": 30.70, "lon": -88.04, "site": "mobile-hub"},
    # The South Atlantic ports (Norfolk/Chesapeake, Charleston, Savannah) as one hub point at Norfolk, fed by CSX and NS.
    {"key": "South Atlantic hub (Norfolk/Chesapeake)", "state": "VA", "lat": 36.85, "lon": -76.30, "site": "south-atlantic-hub"},
    # Container loading hubs: where soybeans are stuffed into containers and railed or trucked to the coast. FGIS records the STATE of each container lot,
    # not the city, so Iowa and the smaller states sit at their main intermodal city (a guess); Chicago is Joliet / Elwood, where the big transloads are.
    {"key": "Container hub: Chicago/Joliet IL", "state": "IL", "lat": 41.52, "lon": -88.08, "site": "cont-joliet"},
    {"key": "Container hub: Columbus OH", "state": "OH", "lat": 39.96, "lon": -82.99, "site": "cont-columbus"},
    {"key": "Container hub: Kansas City", "state": "KS", "lat": 39.10, "lon": -94.58, "site": "cont-kc"},
    {"key": "Container hub: Omaha NE", "state": "NE", "lat": 41.26, "lon": -95.93, "site": "cont-omaha"},
    {"key": "Container hub: Iowa (Des Moines)", "state": "IA", "lat": 41.59, "lon": -93.62, "site": "cont-iowa"},
    {"key": "Container hub: Fargo ND", "state": "ND", "lat": 46.88, "lon": -96.79, "site": "cont-fargo"},
    {"key": "Container hub: Milwaukee WI", "state": "WI", "lat": 43.04, "lon": -87.91, "site": "cont-milwaukee"},
    {"key": "Container hub: Detroit MI", "state": "MI", "lat": 42.33, "lon": -83.05, "site": "cont-detroit"},
    {"key": "Container hub: Minneapolis MN", "state": "MN", "lat": 44.98, "lon": -93.27, "site": "cont-minneapolis"},
    {"key": "Container hub: Indianapolis IN", "state": "IN", "lat": 39.77, "lon": -86.16, "site": "cont-indianapolis"},
    {"key": "Container hub: Baltimore MD", "state": "MD", "lat": 39.29, "lon": -76.61, "site": "cont-baltimore"},
    {"key": "Container hub: Norfolk VA", "state": "VA", "lat": 36.85, "lon": -76.30, "site": "cont-norfolk"},
    {"key": "Container hub: Charleston SC", "state": "SC", "lat": 32.78, "lon": -79.93, "site": "cont-charleston"},
    {"key": "Container hub: New Jersey", "state": "NJ", "lat": 40.67, "lon": -74.20, "site": "cont-nj"},
    # The Great Lakes ports (Toledo, Chicago, Lake Ontario) as one hub point at Toledo, where almost all the soybeans load.
    {"key": "Great Lakes hub (Toledo)", "state": "OH", "lat": 41.68, "lon": -83.46, "site": "great-lakes-hub"},
    # Canada: truck and rail shipments leave the US at these gateways (Census customs district of exit). Detroit and Buffalo carry nearly all of it.
    {"key": "Canada hub: Detroit MI", "state": "MI", "lat": 42.31, "lon": -83.07, "site": "canada-detroit"},
    {"key": "Canada hub: Buffalo NY", "state": "NY", "lat": 42.90, "lon": -78.90, "site": "canada-buffalo"},
    {"key": "Canada hub: Pembina ND", "state": "ND", "lat": 48.97, "lon": -97.24, "site": "canada-pembina"},
    {"key": "Louis Dreyfus Cahokia IL", "state": "IL", "lat": 38.5662, "lon": -90.1794, "site": "ldc-cahokia"},
    {"key": "Bruce Oakley Cahokia IL", "state": "IL", "lat": 38.5662, "lon": -90.1794, "site": "oakley-cahokia"},
]


# Annual crush (M bu) where the Crush Downtime Calculator is behind a plant. Bunge Destrehan: the workbook has 130,000 bu/day (40M bu/yr); Kolten says the
# plant doubled (the new Bunge Chevron Ag Renewables plant beside it) and now crushes 85M bu a year.
PLANT_NEED_OVERRIDES_MBU = {"Bunge North America Destrehan LA": 85.0}


def hav(lat1, lon1, lat2, lon2):
    """Great-circle miles, broadcasting."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon2 - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 3958.8 * 2 * np.arcsin(np.sqrt(a))


def main():
    core = json.load(open(os.path.join(D, "draw", "osrm_ring.json")))
    supply = json.load(open(os.path.join(D, "rail_map", "county_prod_avg.json")))["avg"]["Soybeans"]
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(B.GAZ, timeout=120).read()))
    g = pd.read_csv(z.open(z.namelist()[0]), sep="\t", encoding="latin1", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    g = g[g["USPS"].isin(set(pd.Series(["AL","AR","DE","GA","IL","IN","IA","KS","KY","LA","MD","MI","MN","MS","MO","NE","NJ","NY","NC","ND","OH","OK","PA","SC","SD","TN","TX","VA","WI","CO","FL","WV","NM","ID","MT","WA","OR"])))]
    cty = [{"fips": r.GEOID, "name": r.NAME, "state": r.USPS, "lat": round(r.INTPTLAT, 4), "lon": round(r.INTPTLONG, 4),
            "supply_bu": supply[r.GEOID]} for r in g.itertuples() if supply.get(r.GEOID, 0) > 0]

    B.BUYER_STATES = {"AL","AR","DE","GA","IL","IN","IA","KS","KY","LA","MD","MI","MN","MS","MO","NE","NJ","NY","NC","ND","OH","OK","PA","SC","SD","TN","TX","VA","WI","CO","FL","WV","NM","ID","MT","WA","OR"}
    plants = B.plants()
    for p in plants:
        if p["key"] in PLANT_NEED_OVERRIDES_MBU:
            p["need_bu"] = PLANT_NEED_OVERRIDES_MBU[p["key"]] * 1e6
            p["dmax"] = int(round(p["need_bu"] / (B.RUN_RATE * B.RUN_DAYS), -2))
            p["need_note"] = "Annual crush overridden to 85M bu (the plant doubled; the workbook still has 40M bu)"
    pts = json.load(open(os.path.join(D, "rail_map", "map_points.json")))
    river = [{"key": r["name"], "state": r.get("state", ""), "lat": r["lat"], "lon": r["lon"]} for r in pts["river"]]
    for r in river:                                       # the tracker geocodes several different Cahokia terminals to the same point
        if r["key"] in SITE_OVERRIDES:
            r["site"] = SITE_OVERRIDES[r["key"]]
    river += [dict(p) for p in EXTRA_RIVER_PINS]
    buyers = plants + river + [PNW]

    # core OSRM lookup: (county fips, buyer lat/lon key) -> miles
    ck = {c["fips"]: i for i, c in enumerate(core["counties"])}
    core_buyers = [("p", p) for p in core["plants"]] + [("r", r) for r in core["river"]]
    bk = {(round(b["lat"], 3), round(b["lon"], 3)): j for j, (_, b) in enumerate(core_buyers)}

    clat = np.array([c["lat"] for c in cty])[:, None]
    clon = np.array([c["lon"] for c in cty])[:, None]
    blat = np.array([b["lat"] for b in buyers])[None, :]
    blon = np.array([b["lon"] for b in buyers])[None, :]
    straight = hav(clat, clon, blat, blon)

    ratios = []
    for i, c in enumerate(cty):
        for j, b in enumerate(buyers):
            ci, bj = ck.get(c["fips"]), bk.get((round(b["lat"], 3), round(b["lon"], 3)))
            if ci is not None and bj is not None and 30 < straight[i, j] < 400:
                m = core["miles"][ci][bj]
                if m:
                    ratios.append(m / straight[i, j])
    circ = float(np.median(ratios))
    miles = np.round(straight * circ)
    used = 0
    for i, c in enumerate(cty):
        ci = ck.get(c["fips"])
        if ci is None:
            continue
        for j, b in enumerate(buyers):
            bj = bk.get((round(b["lat"], 3), round(b["lon"], 3)))
            if bj is not None and core["miles"][ci][bj] is not None:
                miles[i, j] = core["miles"][ci][bj]
                used += 1
    out = {"built": core["built"], "source": f"OSRM road miles where built ({used:,} pairs), else straight-line x {circ:.3f} (median OSRM / straight-line)",
           "run_rate": core["run_rate"], "run_days": core["run_days"], "counties": cty, "plants": plants, "river": river + [PNW],
           "miles": miles.astype(int).tolist()}
    with open(os.path.join(D, "draw", "draw_inputs.json"), "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(len(cty), "counties,", len(plants), "plants,", len(river), "river terminals + PNW; circuity", round(circ, 3), "; OSRM pairs", used,
          f"; US supply {sum(c['supply_bu'] for c in cty) / 1e6:,.0f} M, plant need {sum(p['need_bu'] for p in plants) / 1e6:,.0f} M")


if __name__ == "__main__":
    main()

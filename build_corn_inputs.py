"""
Builds data/draw/draw_inputs_corn.json, the inputs of the corn Draw Territory model (draw_model.solve is the same machine as for soybeans):
  counties   every county with corn production (NASS 5-year average, data/rail_map/county_prod_avg.json), in the lower 48
  plants     the running ethanol plants and the corn processors that carry an ethanol capacity (data/rail_map/map_points.json, JSA's plant list):
             annual need = ethanol capacity (million gallons) / 2.9 gallons a bushel at full capacity; the tab's run-rate slider scales it
             (about 0.95 reproduces USDA's 5.65 billion bushels of ethanol corn use). Food mills and wet-mill corn processors carry no capacity in the
             list, so their corn is not drawn by name: it stays in 'other' (feed, food, seed, stocks) with the livestock feed.
  river      the same barge terminals and hub pins as the soybean model, minus the soybean-specific ones (container hubs and the St. Louis ceilings)
  miles      straight-line miles x the circuity factor fitted for the soybean model (1.244, the median OSRM / straight-line ratio); no OSRM lanes for corn

    python build_corn_inputs.py
"""
import io
import json
import os
import urllib.request
import zipfile

import numpy as np
import pandas as pd

import assemble_draw_inputs as A
import build_draw_lanes as B

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
GAL_PER_BU = 2.9                          # net ethanol gallons per bushel (rail_map.py's 3.0 is gross; 2.9 with 0.95 utilisation matches USDA's ethanol corn use)
CIRC = 1.244
MIN_SUPPLY = 0.2e6                        # counties under 0.2 M bu a year are left out (they only add rows)
KEEP_STATUS = {"Run"}


def main():
    supply = json.load(open(os.path.join(D, "rail_map", "county_prod_avg.json")))["avg"]["Corn"]
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(B.GAZ, timeout=120).read()))
    g = pd.read_csv(z.open(z.namelist()[0]), sep="\t", encoding="latin1", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    g = g[~g["USPS"].isin(["AK", "HI", "PR", "DC"])]
    cty = [{"fips": r.GEOID, "name": r.NAME, "state": r.USPS, "lat": round(r.INTPTLAT, 4), "lon": round(r.INTPTLONG, 4), "supply_bu": supply[r.GEOID]}
           for r in g.itertuples() if supply.get(r.GEOID, 0) >= MIN_SUPPLY]

    e = pd.DataFrame(json.load(open(os.path.join(D, "rail_map", "map_points.json")))["ethanol"])
    e = e[e.status.isin(KEEP_STATUS) & e.mgy.notna() & (e.mgy > 0) & e.country.isna()]
    plants, seen = [], {}
    for r in e.itertuples():
        key = f"{r.name} {r.city} {r.state}"
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key += f" #{seen[key]}"                                          # two plants on one site (a company's first and second plant)
        plants.append({"key": key, "company": r.name, "city": r.city, "state": r.state, "river": "", "dmax": 0, "need_bu": round(r.mgy / GAL_PER_BU * 1e6),
                       "lat": round(r.lat, 4), "lon": round(r.lon, 4), "mgy": float(r.mgy), "cls": r.cls})

    soy = json.load(open(os.path.join(D, "draw", "draw_inputs.json")))
    river = [{k: v for k, v in p.items() if k not in ("cap_mbu", "cap_note")} for p in soy["river"] if not p["key"].startswith("Container hub:")]
    buyers = plants + river
    clat = np.array([c["lat"] for c in cty])[:, None]
    clon = np.array([c["lon"] for c in cty])[:, None]
    blat = np.array([b["lat"] for b in buyers])[None, :]
    blon = np.array([b["lon"] for b in buyers])[None, :]
    miles = np.round(A.hav(clat, clon, blat, blon) * CIRC).astype(int)
    out = {"built": pd.Timestamp.today().strftime("%Y-%m-%d"), "source": f"straight-line miles x {CIRC} (the soybean model's median OSRM / straight-line ratio)",
           "crop": "corn", "gal_per_bu": GAL_PER_BU, "counties": cty, "plants": plants, "river": river, "miles": miles.tolist()}
    with open(os.path.join(D, "draw", "draw_inputs_corn.json"), "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    need = sum(p["need_bu"] for p in plants)
    print(f"{len(cty)} counties ({sum(c['supply_bu'] for c in cty) / 1e6:,.0f} M bu), {len(plants)} ethanol plants (need {need / 1e6:,.0f} M bu at full capacity, "
          f"{need * 0.95 / 1e6:,.0f} at 95%), {len(river)} river and hub pins")


if __name__ == "__main__":
    main()

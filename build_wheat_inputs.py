"""
Builds data/draw/draw_inputs_wheat.json, the inputs of the wheat Draw Territory model (draw_model.solve is the same machine as for soybeans and corn):
  counties   every county with wheat production (NASS/RMA 2021-25 average, data/rail_map/county_prod_avg.json), lower 48; `cls` = the county's supply by market class
             (data/draw/wheat_county_class.json: HRW, SRW, White, HRS, Durum)
  plants     none yet: flour mills are the natural named buyers but there is no mill list with capacities (regional capacity only), so domestic use (flour, feed, seed, stocks)
             is 'other' and the model draws only the export hubs
  river      the same barge terminals and hub pins as the corn model
  miles      straight-line miles x 1.244 (the soybean model's median OSRM / straight-line ratio)

    python build_wheat_inputs.py
"""
import io
import json
import os
import urllib.request
import zipfile

import numpy as np
import pandas as pd

import build_draw_lanes as B

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
CIRC = 1.244
MIN_SUPPLY = 0.1e6                        # wheat counties are small: keep those above 0.1 M bu a year


def main():
    supply = json.load(open(os.path.join(D, "rail_map", "county_prod_avg.json")))["avg"]["Wheat"]
    cc = json.load(open(os.path.join(D, "draw", "wheat_county_class.json")))["counties"]
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(B.GAZ, timeout=120).read()))
    g = pd.read_csv(z.open(z.namelist()[0]), sep="\t", encoding="latin1", dtype={"GEOID": str})
    g.columns = [c.strip() for c in g.columns]
    g = g[~g["USPS"].isin(["AK", "HI", "PR", "DC"])]
    cty = [{"fips": r.GEOID, "name": r.NAME, "state": r.USPS, "lat": round(r.INTPTLAT, 4), "lon": round(r.INTPTLONG, 4), "supply_bu": supply[r.GEOID], "cls": cc.get(r.GEOID, {})}
           for r in g.itertuples() if supply.get(r.GEOID, 0) >= MIN_SUPPLY]
    corn = json.load(open(os.path.join(D, "draw", "draw_inputs_corn.json")))
    river = corn["river"]
    clat = np.array([c["lat"] for c in cty])[:, None]
    clon = np.array([c["lon"] for c in cty])[:, None]
    blat = np.array([b["lat"] for b in river])[None, :]
    blon = np.array([b["lon"] for b in river])[None, :]
    import assemble_draw_inputs as A
    miles = np.round(A.hav(clat, clon, blat, blon) * CIRC).astype(int)
    out = {"built": pd.Timestamp.today().strftime("%Y-%m-%d"), "source": f"straight-line miles x {CIRC} (the soybean model's median OSRM / straight-line ratio)",
           "crop": "wheat", "counties": cty, "plants": [], "river": river, "miles": miles.tolist()}
    with open(os.path.join(D, "draw", "draw_inputs_wheat.json"), "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"{len(cty)} counties ({sum(c['supply_bu'] for c in cty) / 1e6:,.0f} M bu), {len(river)} river and hub pins")


if __name__ == "__main__":
    main()

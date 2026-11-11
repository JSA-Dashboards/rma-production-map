"""
Corn demand located by county, for the net exportable supply on the ASD & County Level tab.

  data/corn_demand_by_county.csv   year, fips, state, ethanol_bu, feed_gross_bu, ddgs_credit_bu, feed_net_bu,
                                   feed_dairy_bu, feed_beef_bu, feed_hogs_bu, feed_poultry_bu, residual_bu

For each marketing year (the crop year's NASS year) with a USDA balance on file:
  * ethanol_bu   the corn the ethanol and processing plants of the Draw Territory model grind, in the county the plant stands in (point in the county's outline), the plants' nameplate need
                 scaled so the plants together use USDA's ethanol corn use for that year (WASDE);
  * feed_gross_bu  livestock corn feed by county: Census county herds spread over each year's NASS state inventories, times feed per head, tied to USDA's feed and residual
                 (feed_demand.py: the same logic as the Draw Territory tab, at its default settings);
  * ddgs_credit_bu  the corn the plants' distillers grains replace in the livestock rations (it stays near the plants that make it, so it lowers that region's corn feed need);
  * feed_net_bu  feed less that credit: the corn the region's animals still need from the grain supply.
  * feed_dairy / beef / hogs / poultry_bu  the per-head feed by kind of animal (milk cows; cattle on feed and other cattle; hogs; layers, broilers and turkeys), from feeding budgets and the
                 county herds, BEFORE tying to USDA;
  * residual_bu  what is left of feed_gross_bu once those are taken out: USDA's "feed and residual" is bigger than the per-head budgets add up to (the budgets cover 75-80% of it), and the gap
                 is the residual (shrink, waste, feeding rations the budgets miss, error in production and stocks). Here it sits in each county in proportion to its animals' feed, which is
                 what scaling the budgets to USDA does; corn_net_export can spread it by production instead.
Not located: food, seed and other industrial use outside these plants (about 1.3 billion bushels), which has no plant list here.

    python build_corn_demand_by_county.py
"""
import csv
import json
import os
import urllib.request

import numpy as np

import draw_model as dm
import draw_tab as dt
import export_scenario as es
import feed_demand as fd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "corn_demand_by_county.csv")
GEOJSON = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"          # the county outlines the dashboard itself loads


def plant_counties(plants, counties):
    """{plant key: county fips}: the county whose outline holds the plant, else the nearest county centroid."""
    from shapely.geometry import Point, shape
    with urllib.request.urlopen(GEOJSON, timeout=120) as r:
        feats = json.load(r)["features"]
    shapes = [(f["id"], shape(f["geometry"])) for f in feats]
    c_lat = np.array([c["lat"] for c in counties], float)
    c_lon = np.array([c["lon"] for c in counties], float)
    out = {}
    for p in plants:
        pt = Point(p["lon"], p["lat"])
        hit = next((fid for fid, g in shapes if g.bounds[0] <= pt.x <= g.bounds[2] and g.bounds[1] <= pt.y <= g.bounds[3] and g.contains(pt)), None)
        if hit is None:
            hit = counties[int(fd._miles(p["lat"], p["lon"], c_lat, c_lon).argmin())]["fips"]
        out[p["key"]] = str(hit).zfill(5)
    return out


def main():
    inp = dm.load(dt.PATHS["corn"][0])
    plants, counties = inp["plants"], inp["counties"]
    where = plant_counties(plants, counties)
    need = np.array([p["need_bu"] for p in plants], float)
    bal = es.balance("corn")
    wet_items = tuple(sorted({p["state"] for p in plants}))
    wet_items = tuple(sorted((s, fd.WET_SHARE.get(s, fd.WET_SHARE_DEFAULT)) for s in wet_items))
    export_bu = float(fd.export_credit_bu(fd.default_export_tonnes()))
    rows = []
    for y in sorted(int(k) for k in bal):
        b = bal[str(y)]
        if not b.get("ethanol") or not b.get("feed_residual"):
            continue
        util = float(b["ethanol"]) * 1e6 / need.sum()
        f = dt._feed.__wrapped__(y, float(b["feed_residual"]) * 1e6, util, fd.CORN_CREDIT, export_bu, fd.WET_RADIUS_MI, fd.CREDIT_CAP, wet_items, None, True, True)
        eth = {}
        for p, n_ in zip(plants, need):
            eth[where[p["key"]]] = eth.get(where[p["key"]], 0.0) + n_ * util
        gross, credit, net = np.asarray(f["gross"], float), np.asarray(f["credit"], float), np.asarray(f["net"], float)
        k = float(f["k"])
        sp = {a: np.asarray(v, float) / k for a, v in f["by_species"].items()}                      # the per-head feed, before the scaling to USDA
        grp = {"dairy": sp["MILK COWS"], "beef": sp["CATTLE ON FEED"] + sp["OTHER CATTLE"], "hogs": sp["HOGS"], "poultry": sp["LAYERS"] + sp["BROILERS"] + sp["TURKEYS"]}
        resid = gross - sum(grp.values())
        n_out = 0
        for i, c in enumerate(counties):
            e = eth.get(c["fips"], 0.0)
            if e or gross[i] or net[i]:
                rows.append((y, c["fips"], c["state"], round(e), round(gross[i]), round(credit[i]), round(net[i]), round(grp["dairy"][i]), round(grp["beef"][i]), round(grp["hogs"][i]),
                             round(grp["poultry"][i]), round(resid[i])))
                n_out += 1
        missing = set(eth) - {c["fips"] for c in counties}
        st_of = {where[p["key"]]: p["state"] for p in plants}
        for fp in sorted(missing):                                           # a plant in a county the model has no crop for: its grind still counts where it stands
            rows.append((y, fp, st_of[fp], round(eth[fp]), 0, 0, 0, 0, 0, 0, 0, 0))
        print(y, f"ethanol {sum(eth.values()) / 1e6:,.0f}  feed gross {gross.sum() / 1e6:,.0f}  credit {credit.sum() / 1e6:,.0f}  net {net.sum() / 1e6:,.0f} M bu  (WASDE feed+residual {b['feed_residual']:,.0f})",
              "(plant counties with no modeled crop added: %d)" % len(missing))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["year", "fips", "state", "ethanol_bu", "feed_gross_bu", "ddgs_credit_bu", "feed_net_bu", "feed_dairy_bu", "feed_beef_bu", "feed_hogs_bu", "feed_poultry_bu", "residual_bu"])
        w.writerows(rows)
    print("wrote", OUT, len(rows), "rows")


if __name__ == "__main__":
    main()

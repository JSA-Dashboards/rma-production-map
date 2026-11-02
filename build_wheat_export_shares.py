"""
Wheat exports by class and where they leave from, by marketing year (June-May): the wheat counterpart of build_corn_export_shares.py.

    python build_wheat_export_shares.py            # no key needed (FGIS is open)

Source: USDA AgTransport sruw-w49i (FGIS export inspections), bushels = pounds / 60. Unlike corn and soybeans, every wheat inspection carries its CLASS, which is what makes wheat
exports separable by where the wheat is grown: soft white leaves only through the Pacific Northwest, soft red winter almost only down the Mississippi, hard red spring through the
Pacific Northwest, Texas, the Lakes and Mexico by rail, hard red winter through the Pacific Northwest, Texas, Mexico by rail and the Mississippi.

Hubs (the same way the corn and soybean models cut them)
  Pacific Northwest   West Coast ports (Columbia River, Puget Sound, California)
  Mississippi River   the East Gulf (Mississippi River ports)
  Texas Gulf          West Gulf (North Texas = Houston/Galveston, South Texas = Corpus Christi)
  Great Lakes         Duluth-Superior, Chicago, Toledo and the Seaway
  South Atlantic      East Coast ports
  Mexico rail         interior lots whose destination is Mexico (carried by rail; by state of inspection)
  Interior other      interior lots to anywhere else (containers, a little to Canada)
Classes: HRW, HRS, SRW, SWW (soft white), HDWH (hard white), DUWH (durum), XWHT (mixed), UNCL. 'White' in USDA's export sales = SWW + HDWH.

Writes (data/wheat/)
  wheat_export_class_hub_by_my.csv   marketing_year_start, class, hub, mbu        (the class x hub matrix, the heart of the export structure)
  wheat_export_shares_by_my.csv      one row per marketing year: inspected by hub, by class, and the hub shares
  wheat_mexico_rail_by_my.csv        marketing_year_start, class, state, mbu       (interior wheat to Mexico, by state of inspection)
Only marketing years with all twelve months are written. Refuses to write when FGIS returns nothing.
"""
import json
import os
import time
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wheat")
FGIS = "https://agtransport.usda.gov/resource/sruw-w49i.json?"
FIRST_YEAR = 2010
CLASS_GROUP = {"HRW": "HRW", "HRS": "HRS", "SRW": "SRW", "SWW": "White", "HDWH": "White", "DUWH": "Durum", "XWHT": "Mixed", "UNCL": "Unclassed"}
HUBS = ["Pacific Northwest", "Mississippi River", "Texas Gulf", "Great Lakes", "South Atlantic", "Mexico rail", "Interior other"]


def q(**p):
    url = FGIS + urllib.parse.urlencode(p)
    for a in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=170) as r:
                return json.load(r)
        except Exception:
            time.sleep(4 * (a + 1))
    raise SystemExit("REFUSING TO WRITE: FGIS did not answer")


def hub(fgis_reg: str, destination: str, carrier: str) -> str:
    if fgis_reg == "WEST COAST":
        return "Pacific Northwest"
    if fgis_reg == "EAST GULF":
        return "Mississippi River"
    if fgis_reg == "WEST GULF":
        return "Texas Gulf"
    if fgis_reg in ("LAKES", "CANADA"):
        return "Great Lakes"
    if fgis_reg == "EAST COAST":
        return "South Atlantic"
    if fgis_reg == "INTERIOR":
        return "Mexico rail" if "MEXICO" in str(destination) else "Interior other"
    return "Interior other"


def main():
    rows = q(**{"$select": "year,month,class,fgis_reg,destination,state,type_carrier_text,sum(pounds) as lbs", "$group": "year,month,class,fgis_reg,destination,state,type_carrier_text",
                "$limit": "50000", "$where": f"grain='WHEAT' and year>={FIRST_YEAR}"})
    d = pd.DataFrame(rows)
    if d.empty:
        raise SystemExit("REFUSING TO WRITE: no wheat rows")
    d["year"], d["month"], d["mbu"] = d.year.astype(int), d.month.astype(int), d.lbs.astype(float) / 60 / 1e6
    d["my"] = d.year.where(d.month >= 6, d.year - 1)                         # the wheat marketing year starts June 1
    d["hub"] = [hub(a, b, c) for a, b, c in zip(d.fgis_reg, d.destination, d.type_carrier_text)]
    d["class"] = d["class"].fillna("UNCL")
    months = d.groupby("my").month.nunique()
    full = [m for m in months.index if months[m] == 12]
    d = d[d.my.isin(full)]
    ch = d.groupby(["my", "class", "hub"]).mbu.sum().round(3).reset_index().rename(columns={"my": "marketing_year_start"})
    os.makedirs(OUT, exist_ok=True)
    ch.to_csv(os.path.join(OUT, "wheat_export_class_hub_by_my.csv"), index=False)
    mx = d[d.hub == "Mexico rail"].groupby(["my", "class", "state"]).mbu.sum().round(3).reset_index().rename(columns={"my": "marketing_year_start"})
    mx.to_csv(os.path.join(OUT, "wheat_mexico_rail_by_my.csv"), index=False)
    t = d.pivot_table(index="my", columns="hub", values="mbu", aggfunc="sum").reindex(columns=HUBS).fillna(0.0)
    t.columns = [c.lower().replace(" ", "_") + "_mbu" for c in t.columns]
    t["fgis_inspected_mbu"] = d.groupby("my").mbu.sum()
    g = d.assign(g=d["class"].map(CLASS_GROUP).fillna("Unclassed")).pivot_table(index="my", columns="g", values="mbu", aggfunc="sum").fillna(0.0)
    for c in g.columns:
        t[c.lower() + "_class_mbu"] = g[c]
    for h in HUBS:
        col = h.lower().replace(" ", "_")
        t[col + "_share_pct"] = t[col + "_mbu"] / t.fgis_inspected_mbu * 100
    t.index.name = "marketing_year_start"
    t.round(3).to_csv(os.path.join(OUT, "wheat_export_shares_by_my.csv"))
    print(f"wrote {OUT}: marketing years {min(full)}-{max(full)} ({len(full)} complete), {len(ch)} class x hub rows")
    print(t[[c for c in t.columns if c.endswith("_mbu") and "class" not in c]].tail(5).round(0).to_string())


if __name__ == "__main__":
    main()

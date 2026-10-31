"""
FGIS export inspections by CALENDAR year, hub by hub: data/draw/fgis_hub_calendar_<crop>.csv (M bu). The STB waybill sample is by calendar year, so the Draw Territory
tab's "Match a waybill year" option and the rail validation panel's volume check need the hub volumes on the same footing. The marketing-year history the export scenario
normally uses (corn_export_shares_by_my.csv, soy_export_shares_by_my.csv) has the same columns and the same definitions; only the year differs (January-December here).

Columns (M bu): months (how many months of the year FGIS has: use only 12), fgis_inspected_mbu, east_gulf_mbu, west_gulf_mbu (Texas), west_coast_mbu (Pacific Northwest),
interior_mbu (rail, truck and container loaded inland), mexico_rail_mbu (rail inspected for Mexico, part of interior), south_atlantic_mbu, container_mbu, lakes_mbu, and
canada_mbu (trucks and railcars into Canada from the Census customs-district files, as build_canada_exports.py groups them; only years with all twelve months).
Source: USDA AgTransport sruw-w49i (FGIS export grain inspections), bushels = pounds / 56 (corn) or / 60 (soybeans).

    python build_fgis_hub_calendar.py [corn|soy]
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import pandas as pd

import build_canada_exports as BC

HERE = os.path.dirname(os.path.abspath(__file__))
FGIS = "https://agtransport.usda.gov/resource/sruw-w49i.json?"
CROPS = {"corn": ("CORN", 56.0, BC.CORN_SRC, 39.368 / 1000), "soy": ("SOYBEANS", 60.0, BC.SRC, BC.BU_PER_KT)}


def get(q):
    for a in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(FGIS + urllib.parse.urlencode(q), headers={"User-Agent": "Mozilla/5.0"}), timeout=170) as r:
                return pd.DataFrame(json.load(r))
        except Exception as e:
            print("  retry", type(e).__name__, flush=True)
            time.sleep(4 * (a + 1))
    raise SystemExit("REFUSING TO WRITE: FGIS did not answer")


def series(grain, lb, cond=None, by=None):
    q = {"$select": "year,month," + (by + "," if by else "") + "sum(pounds) as lbs", "$group": "year,month" + ("," + by if by else ""), "$limit": "50000",
         "$where": f"grain='{grain}' and year>=2015 and year<=2026" + (" and " + cond if cond else "")}
    f = get(q)
    f["year"], f["month"], f["mbu"] = f.year.astype(int), f.month.astype(int), f.lbs.astype(float) / lb / 1e6
    return (f.groupby(["year", by]).mbu.sum().unstack().fillna(0.0) if by else f.groupby("year").mbu.sum()), f.groupby("year").month.nunique()


def main(crop="corn"):
    grain, lb, csrc, per_kt = CROPS[crop]
    by_reg, months = series(grain, lb, None, "fgis_reg")
    t = pd.DataFrame({"months": months, "fgis_inspected_mbu": by_reg.sum(axis=1), "east_gulf_mbu": by_reg["EAST GULF"], "west_gulf_mbu": by_reg["WEST GULF"],
                      "west_coast_mbu": by_reg["WEST COAST"], "interior_mbu": series(grain, lb, "ams_reg='INTERIOR'")[0]})
    for col, cond in (("mexico_rail_mbu", "type_carrier_text='RAIL' and destination like '%MEXICO%'"),
                      ("south_atlantic_mbu", "fgis_reg='EAST COAST' and port='S. ATLANTIC' and type_carrier_text='SHIP'"),
                      ("container_mbu", "type_carrier_text='CONTAINER'"), ("lakes_mbu", "fgis_reg='LAKES' and type_carrier_text='SHIP'")):
        t[col] = series(grain, lb, cond)[0]
    d = json.load(open(csrc))
    rows = [{"year": int(ym[:4]), "ym": ym, **v["d"]} for ym, v in d["months"].items()]
    c = pd.DataFrame(rows).fillna(0.0)
    full = c.groupby("year").ym.nunique()
    c = c[c.year.isin(full[full == 12].index)]
    dists = [x for g in BC.GROUPS.values() for x in g if x in c]
    t["canada_mbu"] = c.groupby("year")[dists].sum().sum(axis=1) * per_kt
    t = t.fillna(0.0).round(3)
    t.index.name = "year"
    path = os.path.join(HERE, "data", "draw", f"fgis_hub_calendar_{crop}.csv")
    t.to_csv(path)
    print(t.round(0).to_string())
    print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "corn")

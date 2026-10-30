"""
US corn exports and where they leave from, by marketing year: FGIS corn export inspections by region (the same columns as the soybean share history, so
export_scenario.py works on both), joined to USDA's export number from the WASDE balance sheet (data/draw/corn_balance_wasde.json, build_soy_balance.py --crop corn)
and to the trucks and railcars into Canada (data/draw/canada_corn_by_my.csv).

Writes data/draw/corn_export_shares_by_my.csv, marketing years 2005..last completed. USDA's number is only on file from 2019/20 (the WASDE report files start in 2021),
so earlier years carry inspections and shares but no USDA export figure; the scenario's inspected/USDA ratio is the 2021-25 average either way.
FGIS: https://agtransport.usda.gov/resource/sruw-w49i.json, marketing year = certificate month September onward, bushels = pounds / 56. East Gulf = Mississippi River ports,
West Gulf = Texas, West Coast = the Pacific Northwest, interior = rail, truck and container loaded inland (Mexico by rail is the part of it inspected for Mexico).
Not in FGIS at all: trucks into Mexico and Canada, which is why USDA's number runs above inspections (corn: about 8%).

    python build_corn_export_shares.py
"""
import datetime
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data", "draw")
FGIS = "https://agtransport.usda.gov/resource/sruw-w49i.json?"


def series(cond, by=None):
    """Corn inspections (M bu) by marketing year (and by `by`), for the extra WHERE condition `cond`."""
    q = {"$select": "year,month," + (by + "," if by else "") + "sum(pounds) as lbs", "$group": "year,month" + ("," + by if by else ""), "$limit": "50000",
         "$where": "grain='CORN' and year>=2003" + (" and " + cond if cond else "")}
    f = pd.DataFrame(json.load(urllib.request.urlopen(FGIS + urllib.parse.urlencode(q), timeout=180)))
    f["year"], f["month"] = f.year.astype(int), f.month.astype(int)
    f["my"] = f.year.where(f.month >= 9, f.year - 1)
    f["mbu"] = f.lbs.astype(float) / 56 / 1e6
    return f.groupby(["my", by]).mbu.sum().unstack().fillna(0) if by else f.groupby("my").mbu.sum()


def main():
    by_reg = series(None, "fgis_reg")
    interior = series("ams_reg='INTERIOR'")
    t = pd.DataFrame({"east_gulf_mbu": by_reg["EAST GULF"], "west_gulf_mbu": by_reg["WEST GULF"], "west_coast_mbu": by_reg["WEST COAST"], "interior_mbu": interior})
    t["fgis_inspected_mbu"] = by_reg.sum(axis=1)
    t["mexico_rail_mbu"] = series("type_carrier_text='RAIL' and destination like '%MEXICO%'")
    t["south_atlantic_mbu"] = series("fgis_reg='EAST COAST' and port='S. ATLANTIC' and type_carrier_text='SHIP'")
    t["container_mbu"] = series("type_carrier_text='CONTAINER'")
    t["lakes_mbu"] = series("fgis_reg='LAKES' and type_carrier_text='SHIP'")
    cpath = os.path.join(D, "canada_corn_by_my.csv")
    t["canada_mbu"] = pd.read_csv(cpath, index_col=0).canada_modeled_mbu.reindex(t.index) if os.path.exists(cpath) else 0.0
    bal = json.load(open(os.path.join(D, "corn_balance_wasde.json")))["years"]
    t["usda_exports_mbu"] = pd.Series({int(k): v["exports"] for k, v in bal.items()}).reindex(t.index)
    last_full = datetime.date.today().year - (0 if datetime.date.today().month >= 9 else 1) - 1
    t = t[(t.index >= 2005) & (t.index <= last_full)].fillna({"mexico_rail_mbu": 0.0, "south_atlantic_mbu": 0.0, "container_mbu": 0.0, "lakes_mbu": 0.0}).copy()
    t["canada_mbu"] = t.canada_mbu.fillna(0.0)
    t["inspected_over_usda"] = t.fgis_inspected_mbu / t.usda_exports_mbu
    for c, name in (("east_gulf_mbu", "east_gulf"), ("west_coast_mbu", "west_coast"), ("interior_mbu", "interior"), ("west_gulf_mbu", "west_gulf"), ("mexico_rail_mbu", "mexico_rail"),
                    ("south_atlantic_mbu", "south_atlantic"), ("container_mbu", "container"), ("lakes_mbu", "lakes"), ("canada_mbu", "canada")):
        t[name + "_share_pct"] = t[c] / t.fgis_inspected_mbu * 100
    t["us_gulf_share_pct"] = (t.east_gulf_mbu + t.west_gulf_mbu) / t.fgis_inspected_mbu * 100
    t.index.name = "marketing_year_start"
    cols = ["usda_exports_mbu", "fgis_inspected_mbu", "inspected_over_usda", "east_gulf_mbu", "west_gulf_mbu", "west_coast_mbu", "interior_mbu", "mexico_rail_mbu",
            "south_atlantic_mbu", "container_mbu", "lakes_mbu", "canada_mbu", "east_gulf_share_pct", "us_gulf_share_pct", "west_coast_share_pct", "interior_share_pct",
            "west_gulf_share_pct", "mexico_rail_share_pct", "south_atlantic_share_pct", "container_share_pct", "lakes_share_pct", "canada_share_pct"]
    t[cols].round(3).to_csv(os.path.join(D, "corn_export_shares_by_my.csv"))
    print(t[["usda_exports_mbu", "fgis_inspected_mbu", "inspected_over_usda", "east_gulf_mbu", "west_coast_mbu", "mexico_rail_mbu", "canada_mbu"]].tail(8).round(1).to_string())


if __name__ == "__main__":
    main()

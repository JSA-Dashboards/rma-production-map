"""
USDA AMS grain truck rates for the Draw Territory model's freight: data/draw/truck_rates_ams.json.

Sources (USDA AgTransport, public)
  fxkn-2w9c  Quarterly Grain Truck Rates: dollars per LOADED MILE for a truckload at 25, 100 and 200 miles, national and five regions (survey of elevators), 2015Q1-
  x88w-atzp  Weekly On-Highway Diesel Fuel Prices (EIA), the US series

Method: each region and haul length takes the average of the last four quarters it reports (the quarterly series is noisy: the 200-mile rate swung from 2.94 to 3.77 in one
quarter), and `diesel_base` is the average diesel price over those same quarters. AMS publishes quarterly and lags, so the model adds a fuel adjustment for today's diesel:
(diesel now - diesel_base) / 6 miles a gallon, dollars per loaded mile (draw_model.truck_curve). Run after each quarterly AMS update, or whenever.

    python build_truck_rates.py
"""
import datetime
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "draw", "truck_rates_ams.json")
BASE = "https://agtransport.usda.gov/resource/"


def get(id_, **q):
    q.setdefault("$limit", "50000")
    return pd.DataFrame(json.load(urllib.request.urlopen(BASE + id_ + ".json?" + urllib.parse.urlencode(q), timeout=120)))


def main():
    t = get("fxkn-2w9c")
    t["rate"] = pd.to_numeric(t.rate_mile_trukload)
    t["miles"] = t.distance.str.extract(r"(\d+)")[0].astype(int)
    quarters = sorted(t.yearquarter.unique())[-4:]
    last4 = t[t.yearquarter.isin(quarters)]
    regions = {}
    for (reg, mi), g in last4.groupby(["region", "miles"]):
        regions.setdefault(reg, {})[str(mi)] = round(float(g.rate.mean()), 3)
    regions = {k: v for k, v in regions.items() if all(m in v for m in ("25", "100", "200"))}
    if "National" not in regions:
        raise SystemExit("REFUSING TO WRITE: no complete national 25/100/200-mile rates in the last four quarters")
    d = get("x88w-atzp", **{"$where": "region='US' and year>=2024", "$order": "date DESC"})
    d["date"], d["p"] = pd.to_datetime(d.date), pd.to_numeric(d.diesel_price)
    d["yq"] = d.date.dt.year.astype(str) + "Q" + d.date.dt.quarter.astype(str)
    base = float(d[d.yq.isin(quarters)].p.mean())
    out = {"built": datetime.date.today().isoformat(), "quarters": quarters, "unit": "dollars per loaded mile, average of the last four quarters",
           "regions": regions, "diesel_base": round(base, 3), "diesel_now": round(float(d.sort_values("date").p.iloc[-1]), 3), "diesel_now_date": str(d.date.max().date()),
           "source": "USDA AMS Quarterly Grain Truck Rates (AgTransport fxkn-2w9c) and EIA weekly diesel (x88w-atzp)"}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({k: out[k] for k in ("quarters", "diesel_base", "diesel_now", "diesel_now_date")}), json.dumps(regions["National"]))


if __name__ == "__main__":
    main()

"""
Rail carload flows of corn (and soybeans) from the STB Public Use Carload Waybill Sample, by origin and termination BEA area: data/draw/waybill_<crop>_flows.csv
and waybill_<crop>_monthly.csv. Source: USDA AgTransport xve5-xb56 (a stratified sample of waybills, expanded to total traffic: extons, excars, exrev).

    python build_rail_waybill.py [corn|soy] [first_year] [last_year]

Commodity codes (STB's STCC5, checked 10-31): 01132 corn, 01144 soybeans, 01137 wheat. (01137 is wheat, not corn.)
Fields kept per row of the flows file (one row per year x origin x termination x kind of move):
  move     'Type of Move (inferred)': 0 neither import nor export, 1 import, 2 export, 3 import and export
  water    'Type of Move via Water': 0 none, 1 ex-lake, 2 lake cargo, 3 intercoastal, 4 coastwise, 5 inland waterways (rail to or from a barge)
  transit  1 = the outbound leg from a transit point (an elevator or mill that already received the grain: the same grain can show twice), 0 = not
  size     the sample's shipment-size class: cars in the shipment (1-5, 6-25, 26-50, 51-75, 76+, a unit train)
  dist     the sample's shipment distance class, miles
  tons, cars, rev (dollars), n (sample waybills behind the row: thin cells are noisy)
The server drops long unindexed queries, so each year is pulled alone, with retries.
"""
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://agtransport.usda.gov/resource/xve5-xb56.json?"
STCC = {"corn": "01132", "soy": "01144"}


def q(**params):
    url = BASE + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=170) as r:
                return json.load(r)
        except Exception as e:
            print(f"    retry {attempt + 1}: {type(e).__name__}", flush=True)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("REFUSING TO WRITE: the waybill service did not answer")


def pull(stcc: str, year: int, group: str, order: str) -> list[dict]:
    rows, off = [], 0
    while True:
        part = q(**{"$select": f"{group},sum(extons) as tons,sum(excars) as cars,sum(exrev) as rev,count(*) as n",
                    "$where": f"datayear='{year}' and stcc='{stcc}'", "$group": group, "$order": order, "$limit": "20000", "$offset": str(off)})
        rows += part
        if len(part) < 20000:
            return rows
        off += 20000


def main(crop: str = "corn", y0: int = 2019, y1: int = 2024) -> None:
    stcc = STCC[crop]
    flow_cols = ["originbea", "terminationbea", "typeofmoveimportexport", "typeofmoveviawater", "transitcode", "shipment_size_category", "shipment_distance_category"]
    flows, monthly = [], []
    for y in range(y0, y1 + 1):
        a = pull(stcc, y, ",".join(flow_cols), "originbea,terminationbea")
        b = pull(stcc, y, "month,typeofmoveimportexport,originbea", "month,originbea")
        print(f"{crop} {y}: {len(a)} flow rows, {len(b)} monthly rows, {sum(float(r['tons']) for r in a) / 1e6:.1f} M tons", flush=True)
        flows += [dict(year=y, **r) for r in a]
        monthly += [dict(year=y, **r) for r in b]
    for rows, name, cols in ((flows, "flows", flow_cols), (monthly, "monthly", ["month", "typeofmoveimportexport", "originbea"])):
        path = os.path.join(HERE, "data", "draw", f"waybill_{crop}_{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["year"] + cols + ["tons", "cars", "rev", "n"])
            for r in rows:
                w.writerow([r["year"]] + [r.get(c, "") for c in cols] + [round(float(r["tons"])), round(float(r["cars"])), round(float(r["rev"])), r["n"]])
        print("wrote", path, len(rows))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "corn", int(a[1]) if len(a) > 1 else 2019, int(a[2]) if len(a) > 2 else 2024)

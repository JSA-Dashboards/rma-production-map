"""
US imports of Canadian wheat (HS 1001.99, wheat other than durum and seed: Canada's red spring wheat above all) by state of destination, from the Census international trade API.
It says where in the US the Canadian spring wheat is delivered, which is where the flour mills that grind it are.

  data/wheat/wheat_imports_canada_by_state.csv   year, state, value_usd   (calendar years; Census reports the state the shipment is destined for)

The Census key is not on this machine: run it where CENSUS_API_KEY is set (the droplet has it in /root/corn-exporter-dashboard/.env) and copy the file back.

    python build_wheat_imports.py [output_dir]
"""
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wheat")
YEARS = (2021, 2022, 2023, 2024, 2025)
KEY = os.environ.get("CENSUS_API_KEY", "")
if not KEY:
    for line in open("/root/corn-exporter-dashboard/.env", encoding="utf-8", errors="ignore"):
        if line.startswith("CENSUS_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip("\"'\r")
assert KEY, "no CENSUS_API_KEY"
URL = "https://api.census.gov/data/timeseries/intltrade/imports/statehs"


def get(params):
    q = urllib.parse.urlencode({**params, "key": KEY})
    err = ""
    for i in range(3):
        try:
            with urllib.request.urlopen(URL + "?" + q, timeout=120) as r:
                body = r.read()
            rows = json.loads(body) if body.strip() else []
            return [dict(zip(rows[0], x)) for x in rows[1:]]
        except Exception as e:
            err = str(e).replace(KEY, "***")
            time.sleep(4 * (i + 1))
    raise RuntimeError(err)


def main():
    os.makedirs(OUT, exist_ok=True)
    out = []
    for y in YEARS:
        rows = get(dict(get="STATE,GEN_VAL_YR", COMM_LVL="HS6", I_COMMODITY="100199", CTY_CODE="1220", time=f"{y}-12"))
        for r in rows:
            if r["STATE"] not in ("-", "") and float(r["GEN_VAL_YR"] or 0) > 0:
                out.append((y, r["STATE"], float(r["GEN_VAL_YR"])))
        time.sleep(1)
    with open(os.path.join(OUT, "wheat_imports_canada_by_state.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "state", "value_usd"])
        w.writerows(sorted(out))
    for y in YEARS:
        tot = sum(v for yy, s, v in out if yy == y)
        top = sorted([(v, s) for yy, s, v in out if yy == y], reverse=True)[:6]
        print(y, f"${tot / 1e6:,.0f}M", [(s, round(v / 1e6)) for v, s in top])


if __name__ == "__main__":
    main()

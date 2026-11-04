"""
US exports of distillers grains (HS 2303.30) by state of origin, by route and by port of exit, from the Census international trade API, for the DDGS export allocation in feed_demand.py.

  data/feed/ddgs_exports_by_state.csv   year, state, value_usd   (state of origin of the movement: a port or transload state, Louisiana above all, is where the product was
                                        loaded onto the ship, not where it was made)
  data/feed/ddgs_exports_by_route.csv   year, total_t, vessel_t, container_t, bulk_vessel_t, land_t   (Census reports weights for the vessel and container shipments only; land = the rest)
  data/feed/ddgs_exports_by_port.csv    year, port, value_usd, vessel_t, container_t   (the biggest ports of exit)

The Census key is not on this machine: run it where CENSUS_API_KEY is set (the droplet has it in /root/corn-exporter-dashboard/.env) and copy the three files back.

    python build_ddgs_exports.py [output_dir]
"""
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feed")
YEARS = (2022, 2023, 2024, 2025)
KEY = os.environ.get("CENSUS_API_KEY", "")
if not KEY:
    for line in open("/root/corn-exporter-dashboard/.env", encoding="utf-8", errors="ignore"):
        if line.startswith("CENSUS_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip("\"'\r")
assert KEY, "no CENSUS_API_KEY"
BASE = "https://api.census.gov/data/timeseries/intltrade/exports/"


def get(ep, params):
    q = urllib.parse.urlencode({**params, "key": KEY})
    for i in range(3):
        try:
            with urllib.request.urlopen(BASE + ep + "?" + q, timeout=120) as r:
                body = r.read()
            rows = json.loads(body) if body.strip() else []
            return [dict(zip(rows[0], x)) for x in rows[1:]]
        except Exception as e:
            err = str(e).replace(KEY, "***")
            time.sleep(4 * (i + 1))
    raise RuntimeError(err)


def main():
    os.makedirs(OUT, exist_ok=True)
    by_state, by_route, by_port = [], [], []
    for y in YEARS:
        rows = get("statehs", dict(get="STATE,ALL_VAL_YR", COMM_LVL="HS6", E_COMMODITY="230330", time=f"{y}-12"))
        for r in rows:
            if r["STATE"] not in ("-", "") and float(r["ALL_VAL_YR"] or 0) > 0:
                by_state.append((y, r["STATE"], float(r["ALL_VAL_YR"])))
        time.sleep(1)
        rows = get("porths", dict(get="PORT,PORT_NAME,ALL_VAL_YR,VES_WGT_YR,CNT_WGT_YR", COMM_LVL="HS6", E_COMMODITY="230330", time=f"{y}-12"))
        tot = next(r for r in rows if r["PORT_NAME"].startswith("TOTAL FOR ALL PORTS"))
        ves, cnt = float(tot["VES_WGT_YR"]) / 1000.0, float(tot["CNT_WGT_YR"]) / 1000.0          # kg to metric tons
        ports = [r for r in rows if not r["PORT_NAME"].startswith("TOTAL FOR ALL PORTS") and float(r["ALL_VAL_YR"] or 0) > 0]
        for r in sorted(ports, key=lambda r: -float(r["ALL_VAL_YR"]))[:25]:
            by_port.append((y, r["PORT_NAME"], float(r["ALL_VAL_YR"]), float(r["VES_WGT_YR"] or 0) / 1000.0, float(r["CNT_WGT_YR"] or 0) / 1000.0))
        time.sleep(1)
        t = get("hs", dict(get="QTY_1_YR,UNIT_QY1", COMM_LVL="HS10", E_COMMODITY="2303300000", time=f"{y}-12"))
        total_t = sum(float(r["QTY_1_YR"] or 0) for r in t)
        by_route.append((y, total_t, ves, cnt, ves - cnt, total_t - ves))
        time.sleep(1)
    with open(os.path.join(OUT, "ddgs_exports_by_state.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "state", "value_usd"])
        w.writerows(by_state)
    with open(os.path.join(OUT, "ddgs_exports_by_route.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "total_t", "vessel_t", "container_t", "bulk_vessel_t", "land_t"])
        w.writerows([(y, round(a), round(b), round(c), round(d), round(e)) for y, a, b, c, d, e in by_route])
    with open(os.path.join(OUT, "ddgs_exports_by_port.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["year", "port", "value_usd", "vessel_t", "container_t"])
        w.writerows([(y, p, round(v), round(a), round(b)) for y, p, v, a, b in by_port])
    print("wrote", OUT, len(by_state), "state rows,", len(by_route), "route rows,", len(by_port), "port rows")


if __name__ == "__main__":
    main()

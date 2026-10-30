"""
Refresh data/draw/canada_soy_census.json: US soybean exports to Canada by month and customs district, from the Census Bureau's trade API (HS10 1201900005 + 1201900095,
Canada). Same file and shape as soy-crush-calculator's ontario_trade.json. build_canada_exports.py turns it into the marketing-year table the Draw Territory tab uses.

Needs a Census API key in the environment (CENSUS_API_KEY; the droplet's key is in /root/corn-exporter-dashboard/.env; an unkeyed call is quietly redirected to an HTML page).
Census posts a month's detail about five weeks after it ends (September data on 2026-11-04, October on 12-08, November on 2027-01-07, December on 02-04), so the droplet job
looks daily in the first days of each month and this script writes only when the data differ from what is on file.

    CENSUS_API_KEY=... python build_canada_census.py
Refuses to write (exit 1) when an answer has no rows, fewer months than the file on hand, or ends earlier than it.
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(globals().get("__file__", "build_canada_census.py")))      # (no __file__ when piped to python on the droplet)
URL = "https://api.census.gov/data/timeseries/intltrade/exports/hs"
# soybeans: oil-stock beans (kg) and soybeans except seed (tonnes), sowing seed 1201100000 left out. corn: every 1005 line, and only the 10059xxxxx (except seed) lines are kept
# (yellow dent grades 1, 2, 3 and nesoi, other corn; tonnes); 10051xxxxx is seed.
CROPS = {"soy": {"codes": ["1201900005", "1201900095"], "keep": None, "out": "canada_soy_census.json"},
         "corn": {"codes": ["1005*"], "keep": "10059", "out": "canada_corn_census.json"}}
CODES = CROPS["soy"]["codes"]
OUT = os.path.join(HERE, "data", "draw", CROPS["soy"]["out"])
CANADA = "1220"
START = "2015-01"


def query(key, **params):
    params["key"] = key
    url = URL + "?" + urllib.parse.urlencode(params)
    err = ""
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                body = r.read()
            return json.loads(body) if body.strip() else []          # an empty answer (HTTP 204) means no rows
        except Exception as e:
            err = str(e).replace(key, "***")
            time.sleep(2 + 2 * i)
    raise RuntimeError(err[:200])


def kt(qty, unit):
    q = float(qty or 0)
    return q / 1e6 if unit == "KG" else (q / 1e3 if unit == "T" else 0.0)


def fetch(key, codes=None, keep=None):
    months, names = {}, {}
    for code in (codes or CODES):
        rows = query(key, get="E_COMMODITY,DISTRICT,DIST_NAME,ALL_VAL_MO,QTY_1_MO,UNIT_QY1", COMM_LVL="HS10", E_COMMODITY=code, CTY_CODE=CANADA,
                     time=f"from {START} to {datetime.date.today().year}-12")
        if not rows:
            raise RuntimeError(f"no rows for {code}")
        hdr = rows[0]
        for r in rows[1:]:
            d = dict(zip(hdr, r))
            if keep and not str(d["E_COMMODITY"]).startswith(keep):
                continue
            m = months.setdefault(d["time"], {"total": 0.0, "usd": 0.0, "d": {}})
            if d["DISTRICT"] == "-":                                  # the all-districts row is the month's total
                m["total"] += kt(d["QTY_1_MO"], d["UNIT_QY1"])
                m["usd"] += float(d["ALL_VAL_MO"] or 0)
            else:
                names[d["DISTRICT"]] = d["DIST_NAME"].title()
                m["d"][d["DISTRICT"]] = round(m["d"].get(d["DISTRICT"], 0.0) + kt(d["QTY_1_MO"], d["UNIT_QY1"]), 3)
    for m in months.values():
        m["total"], m["usd"] = round(m["total"], 3), round(m["usd"])
    return names, dict(sorted(months.items()))


def main(crop="soy", stdout=False):
    cfg = CROPS[crop]
    out_path = os.path.join(HERE, "data", "draw", cfg["out"])
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        print("REFUSING TO WRITE: no CENSUS_API_KEY in the environment")
        return 1
    names, months = fetch(key, cfg["codes"], cfg["keep"])
    if stdout:                                                         # for running on the droplet and capturing the output
        print(json.dumps({"generated": datetime.date.today().isoformat(), "unit": "thousand tonnes (kt)", "districts": names, "months": months}, separators=(",", ":")))
        return 0
    old = json.load(open(out_path)) if os.path.exists(out_path) else None
    if old and (len(months) < len(old["months"]) or max(months) < max(old["months"])):
        print(f"REFUSING TO WRITE: Census returned {len(months)} months up to {max(months)}; the file on hand has {len(old['months'])} up to {max(old['months'])}")
        return 1
    if old and old["months"] == months and old["districts"] == names:
        print(f"nothing changed (latest month {max(months)})")
        return 0
    out = {"generated": datetime.date.today().isoformat(),
           "source": "US Census Bureau international trade API, exports by customs district, Canada, HS10 " + " + ".join(cfg["codes"]),
           "unit": "thousand tonnes (kt)", "districts": names, "months": months}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"wrote {cfg['out']}: {len(months)} months {min(months)} to {max(months)}")
    return 0


if __name__ == "__main__":
    sys.exit(main("corn" if "corn" in sys.argv else "soy", "--stdout" in sys.argv))

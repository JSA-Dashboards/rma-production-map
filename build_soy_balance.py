"""
US soybean balance sheet by marketing year from USDA's monthly WASDE report files: beginning stocks (the carry-in), production, imports, crush, exports,
seed, residual and ending stocks, in million bushels. The Draw Territory tab adds beginning stocks and imports to the supply the model draws from, and checks
its own uses (crush plants, export hubs, everything else) against these lines.

Writes
  data/draw/soy_balance_wasde.json   {"years": {"2026": {"beginning_stocks": 315.0, "production": 4562.0, ..., "as_of": "2026-10", "status": "Proj."}}}
                                     the key is the year the marketing year STARTS (2026 = 2026/27)

Source  https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{year}-{month:02d}.csv (no key; the site refuses the default python user agent, so a
        browser one is sent). One file per report; each has the US balance sheet for three marketing years (last final, current estimate, next projected), and the
        flat export only goes back to the August 2021 report. A year's numbers come from the LATEST report that carries it.

    python build_soy_balance.py --full     # every report since 2021-08 (about 60 small requests); first build
    python build_soy_balance.py            # the last three reports, merged into the file on hand (what the droplet cron runs after each WASDE)
Refuses to write (exit 1) when a report has no US soybean rows or a year's supply does not add up (stocks + production + imports vs total supply).
"""
import argparse
import datetime
import io
import json
import os
import time
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "draw", "soy_balance_wasde.json")
CORN_OUT = os.path.join(HERE, "data", "draw", "corn_balance_wasde.json")
URL = "https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{y}-{m:02d}.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
ATTR = {"Beginning Stocks": "beginning_stocks", "Production": "production", "Imports": "imports", "Supply, Total": "supply_total", "Crushings": "crush",
        "Exports": "exports", "Seed": "seed", "Residual": "residual", "Use, Total": "use_total", "Ending Stocks": "ending_stocks"}
# corn (WASDE's name "Corn"): ethanol and by-products, feed and residual, food/seed/industrial (which includes the ethanol line) and exports
CORN_ATTR = {"Beginning Stocks": "beginning_stocks", "Production": "production", "Imports": "imports", "Supply, Total": "supply_total", "Ethanol & by-products": "ethanol",
             "Feed and Residual": "feed_residual", "Food, Seed & Industrial": "fsi", "Domestic, Total": "domestic_total", "Exports": "exports", "Use, Total": "use_total",
             "Ending Stocks": "ending_stocks"}


def fetch(y, m):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(URL.format(y=y, m=m), headers=UA), timeout=90).read()
    except Exception:
        return None                                                     # a month with no report (or not published yet)
    return pd.read_csv(io.BytesIO(raw))


def us_soy(df, corn=False):
    s = df[(df.Commodity == ("Corn" if corn else "Oilseed, Soybean")) & (df.Region == "United States") & (df.Unit == "Million Bushels") & df.Attribute.isin(CORN_ATTR if corn else ATTR)]
    return s


def months(full):
    today = datetime.date.today()
    start = (2021, 8) if full else ((today.year, today.month - 2) if today.month > 2 else (today.year - 1, today.month + 10))
    y, m = start
    while (y, m) <= (today.year, today.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main(full=False, corn=False):
    out_path, attr = (CORN_OUT, CORN_ATTR) if corn else (OUT, ATTR)
    years = json.load(open(out_path))["years"] if (os.path.exists(out_path) and not full) else {}
    seen = 0
    for y, m in months(full):
        df = fetch(y, m)
        time.sleep(0.4)
        if df is None:
            continue
        s = us_soy(df, corn)
        if s.empty:
            print(f"REFUSING TO WRITE: the {y}-{m:02d} report has no US {'corn' if corn else 'soybean'} rows")
            raise SystemExit(1)
        seen += 1
        for my, g in s.groupby("MarketYear"):
            row = {attr[a]: float(v) for a, v in zip(g.Attribute, g.Value)}
            if abs(row["beginning_stocks"] + row["production"] + row["imports"] - row["supply_total"]) > 1.5:
                print(f"REFUSING TO WRITE: {my} in the {y}-{m:02d} report: stocks + production + imports != total supply")
                raise SystemExit(1)
            row["as_of"] = f"{y}-{m:02d}"
            row["status"] = str(g.ReliabilityProjection.iloc[0]) if "ReliabilityProjection" in g and g.ReliabilityProjection.notna().any() else ""
            years[str(int(my.split("/")[0]))] = row
    if not seen:
        print("REFUSING TO WRITE: no WASDE report file could be fetched")
        raise SystemExit(1)
    out = {"units": "M bu", "built": datetime.date.today().isoformat(),
           "source": "USDA WASDE monthly report CSV (oce-wasde-report-data), US " + ("corn" if corn else "soybeans") + "; each year from the latest report that carries it",
           "years": dict(sorted(years.items()))}
    old = json.load(open(out_path)) if os.path.exists(out_path) else None
    if old and old["years"] == out["years"]:
        print("nothing changed")
        return
    with open(out_path, "w") as fh:
        json.dump(out, fh, indent=1)
    cur = max(years, key=int)
    r = years[cur]
    use = f"ethanol {r['ethanol']:,.0f}" if corn else f"crush {r['crush']:,.0f}"
    print(f"{cur}/{str(int(cur) + 1)[2:]} ({r['as_of']}): carry-in {r['beginning_stocks']:,.0f}, production {r['production']:,.0f}, imports {r['imports']:,.0f}, "
          f"{use}, exports {r['exports']:,.0f}, ending stocks {r['ending_stocks']:,.0f}; {len(years)} marketing years")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="every report since 2021-08 instead of the last three")
    ap.add_argument("--crop", choices=["soy", "corn"], default="soy")
    a = ap.parse_args()
    main(a.full, a.crop == "corn")

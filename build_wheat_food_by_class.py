"""
US wheat food use by class, million bushels, from USDA's monthly WASDE ("U.S. Wheat by Class: Supply and Use"): the national control totals for what the flour mills grind.

    data/wheat/wheat_food_by_class_wasde.csv   marketing_year_start, class, food_mbu, source_report

Classes are WASDE's: Hard Red Winter (HRW), Hard Red Spring (HRS), Soft Red Winter (SRW), White, Durum. A marketing year is read from the October report that follows it (its estimate), or from the
latest report when it is still a projection. No key; the site refuses the default python user agent.

    python build_wheat_food_by_class.py
"""
import csv
import io
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wheat", "wheat_food_by_class_wasde.csv")
URL = "https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{y}-{m:02d}.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
TITLE = "U.S. Wheat by Class: Supply and Use"
CLASS = {"Hard Red Winter": "HRW", "Hard Red Spring": "HRS", "Soft Red Winter": "SRW", "Wheat, White": "White", "Wheat, Durum": "Durum"}


def fetch(y, m):
    try:
        return list(csv.DictReader(io.StringIO(urllib.request.urlopen(urllib.request.Request(URL.format(y=y, m=m), headers=UA), timeout=90).read().decode("utf-8-sig"))))
    except Exception:
        return None


def food(rows):
    """{marketing year start: {class: M bu}} for the Food rows of the by-class table."""
    out = {}
    for r in rows:
        if r["ReportTitle"] == TITLE and r["Attribute"] == "Food" and r["Commodity"] in CLASS and r["MarketYear"][:4].isdigit():
            out.setdefault(int(r["MarketYear"][:4]), {})[CLASS[r["Commodity"]]] = float(r["Value"])
    return out


def main():
    got, src = {}, {}
    latest = None
    for y in range(2026, 2018, -1):                                     # the October report of each year carries the year just ended and the year just begun
        rows = fetch(y, 10) or fetch(y, 9)
        if rows is None:
            continue
        f = food(rows)
        if latest is None:
            latest = (y, f)
        for my, d in f.items():
            if len(d) >= 5 and (my not in got or my + 1 == y):          # the report the year after is its estimate: prefer it
                got[my], src[my] = d, f"WASDE {y}-10"
    if latest:
        for my, d in latest[1].items():
            if my not in got and len(d) >= 5:
                got[my], src[my] = d, f"WASDE {latest[0]}-10 (projection)"
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["marketing_year_start", "class", "food_mbu", "source_report"])
        for my in sorted(got):
            for c in ("HRW", "HRS", "SRW", "White", "Durum"):
                w.writerow([my, c, got[my][c], src[my]])
    print("wrote", OUT, sorted(got))
    for my in sorted(got):
        print(my, {c: v for c, v in got[my].items()}, "total", sum(got[my].values()))


if __name__ == "__main__":
    main()

"""
US soybean exports and where they leave from, by marketing year: USDA's export number (PSD / WASDE) joined to FGIS export inspections by region.

Writes
  data/draw/soy_export_shares_by_my.csv   marketing years 2005..last completed: USDA exports, inspected bushels by region, shares
  data/draw/soy_exports_wasde.json        USDA's current export estimate for every marketing year, with the WASDE month it was last revised

Sources
  USDA FAS PSD API   https://api.fas.usda.gov/api/psd/commodity/2222000/country/US/year/{MY}?api_key=...   (needs FAS_API_KEY in the environment;
                     the key is on the droplet in /root/corn-exporter-dashboard/.env). PSD's marketYear is the year the marketing year STARTS
                     (2025 = 2025/26). Attribute 88 = Exports, in 1,000 metric tons; 36.7437 bushels per metric ton of soybeans.
  FGIS inspections   https://agtransport.usda.gov/resource/sruw-w49i.json (public). Marketing year = certificate month September onward;
                     bushels = pounds / 60. East Gulf = Mississippi River, West Gulf = Texas, West Coast = Pacific Northwest, Interior = rail, truck and
                     container loaded inland.

    FAS_API_KEY=... python build_soy_export_shares.py                  # fetches PSD itself
    python build_soy_export_shares.py --psd-json psd_soy.json          # or reuse a saved PSD response ({"rows": [...]})
Run it after each WASDE (the 12th) so the scenario panel's USDA number is current; the panel also lets you type any export number.
"""
import argparse
import datetime
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data", "draw")
MBU_PER_KMT = 0.0367437
FGIS = "https://agtransport.usda.gov/resource/sruw-w49i.json?"


def fetch_psd(first=2005, last=None):
    import requests
    key = os.environ["FAS_API_KEY"]
    last = last or datetime.date.today().year + 1
    rows = []
    for my in range(first, last + 1):
        r = requests.get(f"https://api.fas.usda.gov/api/psd/commodity/2222000/country/US/year/{my}", params={"api_key": key}, timeout=90)
        if r.status_code == 200 and isinstance(r.json(), list):
            rows += r.json()
    return rows


def psd_exports(rows):
    p = pd.DataFrame(rows)
    p = p[p.attributeId.astype(int) == 88].copy()
    p["marketYear"] = p.marketYear.astype(int)
    p["exports_mbu"] = p.value.astype(float) * MBU_PER_KMT
    p["as_of"] = p.calendarYear.astype(str) + p.month.astype(str).str.zfill(2)
    return p.sort_values("marketYear").drop_duplicates("marketYear", keep="last")[["marketYear", "exports_mbu", "as_of"]]


def fgis_by_region():
    q = {"$select": "year,month,ams_reg,fgis_reg,sum(pounds) as lbs", "$group": "year,month,ams_reg,fgis_reg",
         "$where": "grain='SOYBEANS' and year>=2003", "$limit": "50000"}
    f = pd.DataFrame(json.load(urllib.request.urlopen(FGIS + urllib.parse.urlencode(q), timeout=180)))
    f["year"], f["month"] = f.year.astype(int), f.month.astype(int)
    f["my"] = f.year.where(f.month >= 9, f.year - 1)
    f["mbu"] = f.lbs.astype(float) / 60 / 1e6
    return f


def _fail(msg):
    print("REFUSING TO WRITE:", msg)
    raise SystemExit(1)


def main(psd_json=None):
    rows = json.load(open(psd_json))["rows"] if psd_json else fetch_psd()
    if not rows:
        _fail("the PSD API returned no rows (a wrong or expired FAS_API_KEY, or the API is down)")
    ex = psd_exports(rows)
    if ex.empty or (ex.exports_mbu <= 0).any():
        _fail("the PSD response had no export rows or a non-positive export number")
    wasde = {str(int(r.marketYear)): {"exports_mbu": round(float(r.exports_mbu), 1), "as_of": r.as_of} for r in ex.itertuples()}
    jpath, cpath = os.path.join(D, "soy_exports_wasde.json"), os.path.join(D, "soy_export_shares_by_my.csv")
    old = json.load(open(jpath)) if os.path.exists(jpath) else None
    if old and (len(wasde) < len(old["years"]) or max(wasde, key=int) < max(old["years"], key=int)):
        _fail(f"PSD returned {len(wasde)} marketing years (up to {max(wasde, key=int)}) where the file on hand has {len(old['years'])}")

    f = fgis_by_region()
    by = f.groupby(["my", "fgis_reg"]).mbu.sum().unstack().fillna(0)
    inter = f[f.ams_reg == "INTERIOR"].groupby("my").mbu.sum()
    t = pd.DataFrame({"east_gulf_mbu": by["EAST GULF"], "west_gulf_mbu": by["WEST GULF"], "west_coast_mbu": by["WEST COAST"], "interior_mbu": inter})
    t["fgis_inspected_mbu"] = by.sum(axis=1)
    t = t.join(ex.set_index("marketYear").exports_mbu.rename("usda_exports_mbu"), how="inner")
    last_full = datetime.date.today().year - (0 if datetime.date.today().month >= 9 else 1) - 1      # the marketing year that just ended
    t = t[(t.index >= 2005) & (t.index <= last_full)]
    t["inspected_over_usda"] = t.fgis_inspected_mbu / t.usda_exports_mbu
    for c, name in (("east_gulf_mbu", "east_gulf"), ("west_coast_mbu", "west_coast"), ("interior_mbu", "interior")):
        t[name + "_share_pct"] = t[c] / t.fgis_inspected_mbu * 100
    t["us_gulf_share_pct"] = (t.east_gulf_mbu + t.west_gulf_mbu) / t.fgis_inspected_mbu * 100
    t.index.name = "marketing_year_start"
    cols = ["usda_exports_mbu", "fgis_inspected_mbu", "inspected_over_usda", "east_gulf_mbu", "west_gulf_mbu", "west_coast_mbu", "interior_mbu",
            "east_gulf_share_pct", "us_gulf_share_pct", "west_coast_share_pct", "interior_share_pct"]
    csv_text = t[cols].round(3).to_csv()
    if os.path.exists(cpath) and len(t) < sum(1 for _ in open(cpath)) - 1:
        _fail(f"the share table would shrink from {sum(1 for _ in open(cpath)) - 1} to {len(t)} marketing years")
    if (t.inspected_over_usda < 0.8).any() or (t.inspected_over_usda > 1.2).any():
        _fail("FGIS inspections and USDA exports disagree by more than 20% in some year")

    changed = []
    if not os.path.exists(cpath) or open(cpath, encoding="utf8").read() != csv_text.replace("\r\n", "\n"):
        with open(cpath, "w", encoding="utf8", newline="") as fh:
            fh.write(csv_text)
        changed.append("soy_export_shares_by_my.csv")
    if not old or old["years"] != wasde:
        with open(jpath, "w") as fh:
            json.dump({"units": "M bu", "source": "USDA FAS PSD, US soybeans, attribute 88 Exports (1,000 MT x 0.0367437)",
                       "built": datetime.date.today().isoformat(), "years": wasde}, fh, indent=1)
        changed.append("soy_exports_wasde.json")
    print("changed:", ", ".join(changed) if changed else "nothing")
    cur = max(wasde, key=int)
    print(f"USDA {cur}/{str(int(cur) + 1)[2:]} exports {wasde[cur]['exports_mbu']:,.1f} M bu (WASDE {wasde[cur]['as_of']}); share table through {t.index.max()}/{str(t.index.max() + 1)[2:]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--psd-json")
    main(ap.parse_args().psd_json)

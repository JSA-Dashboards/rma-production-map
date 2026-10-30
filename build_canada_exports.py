"""
US soybean exports to Canada by marketing year and gateway, from Census Bureau export data by customs district (HS10 1201900005 + 1201900095, Canada).
FGIS does not inspect trucks and railcars crossing into Canada, so these shipments are in no other hub; the Draw Territory tab models each gateway as a hub that pulls
beans from the counties around it.

Customs district = where the shipment LEFT the US, not where it grew:
  Detroit  = Detroit MI (the Ambassador Bridge and Port Huron) and Cleveland OH            -> Ontario, fed from Michigan, Ohio and Indiana
  Buffalo  = Buffalo NY (Peace Bridge, Lewiston), Ogdensburg NY, St. Albans VT, Portland ME -> Ontario and Quebec, fed from New York and its neighbours
  Pembina  = Pembina ND, Duluth MN, Minneapolis, Great Falls MT                              -> the Prairies, fed from North Dakota and Minnesota
Everything else (Seattle, Houston, Tampa, ...) is left out as "other".

Input   data/draw/canada_soy_census.json   a copy of soy-crush-calculator's ontario_trade.json ({"months": {"2026-08": {"d": {district: kt}}}}, thousand tonnes),
        which that app rebuilds from the Census API (build_ontario_trade.py, needs CENSUS_API_KEY; the key is on the droplet). Nothing refreshes itself: copy the new file
        over and run this, after Census's monthly trade release.
Output  data/draw/canada_soy_by_my.csv     M bu by marketing year (September start), complete years only

    python build_canada_exports.py [path/to/ontario_trade.json]
"""
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "draw", "canada_soy_census.json")
OUT = os.path.join(HERE, "data", "draw", "canada_soy_by_my.csv")
GROUPS = {"detroit_mbu": ["38", "41"], "buffalo_mbu": ["09", "07", "02", "01"], "pembina_mbu": ["34", "36", "35", "33"]}
BU_PER_KT = 36.7437 / 1000            # thousand tonnes -> M bu


def main(src=SRC):
    d = json.load(open(src))
    rows = []
    for ym, v in d["months"].items():
        y, m = int(ym[:4]), int(ym[5:])
        rows.append({"my": y if m >= 9 else y - 1, "ym": ym, **{dist: kt for dist, kt in v["d"].items()}})
    df = pd.DataFrame(rows).fillna(0.0)
    full = df.groupby("my").ym.nunique()
    df = df[df.my.isin(full[full == 12].index)]                       # a marketing year only counts once all twelve months are in
    out = pd.DataFrame(index=sorted(df.my.unique()))
    used = set()
    for name, dists in GROUPS.items():
        cols = [c for c in dists if c in df]
        used.update(cols)
        out[name] = df.groupby("my")[cols].sum().sum(axis=1) * BU_PER_KT
    other = [c for c in df.columns if c not in used and c not in ("my", "ym")]
    out["other_mbu"] = df.groupby("my")[other].sum().sum(axis=1) * BU_PER_KT
    out["canada_modeled_mbu"] = out[list(GROUPS)].sum(axis=1)
    out.index.name = "marketing_year_start"
    out.round(3).to_csv(OUT)
    print(out.round(1).tail(8).to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else SRC)

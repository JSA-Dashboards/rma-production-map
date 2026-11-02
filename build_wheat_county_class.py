"""
County wheat supply by market class for the Draw Territory model: data/draw/wheat_county_class.json.

  total     each county's 2021-25 average wheat production (data/rail_map/county_prod_avg.json, the same file the soybean and corn models draw their supply from)
  group     winter / spring (excl. durum) / durum share of a county: NASS county rows by class (QuickStats WINTER, SPRING (EXCL DURUM), SPRING DURUM; years 2021-23 and 2025,
            2024 has county rows for Illinois only), averaged; a county NASS does not list gets its state's group mix
  class     inside a state and group, market classes (hard red winter, soft red winter, soft white winter, hard white winter / hard red spring, soft white spring) come from
            NASS's state class totals (data/wheat/wheat_class_state.csv, 2021-25 average) placed on the counties by iterative proportional fitting with the regional priors
            (data/wheat/county_class_prior.csv, build_wheat_county_prior.py): county group production fixed, state class totals fixed, the prior decides which counties carry them.
Model classes (the Draw Territory selector): HRW, SRW, White (soft white winter + hard white winter + soft white spring: FGIS's 'white'), HRS, Durum.

    python build_wheat_county_class.py           # needs NASS_API_KEY in the environment or .streamlit/secrets.toml (12-15 paced calls)
"""
import json
import os
import sys
import time
import tomllib
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
NASS = "https://quickstats.nass.usda.gov/api/api_GET/"
YEARS = (2021, 2022, 2023, 2024, 2025)
GROUPS = {"WINTER": ["Hard red winter", "Soft red winter", "Soft white winter", "Hard white winter"],
          "SPRING, (EXCL DURUM)": ["Hard red spring", "Soft white spring"], "SPRING, DURUM": ["Durum"]}
MODEL = {"Hard red winter": "HRW", "Soft red winter": "SRW", "Soft white winter": "White", "Hard white winter": "White", "Soft white spring": "White", "Hard red spring": "HRS", "Durum": "Durum"}
MODEL_ORDER = ["HRW", "SRW", "White", "HRS", "Durum"]


def key():
    k = os.environ.get("NASS_API_KEY", "")
    if not k:
        p = os.path.join(HERE, ".streamlit", "secrets.toml")
        if os.path.exists(p):
            k = tomllib.load(open(p, "rb")).get("NASS_API_KEY", "")
    if not k:
        sys.exit("REFUSING TO RUN: no NASS_API_KEY in the environment or .streamlit/secrets.toml")
    return k


def county_rows(k, group, year):
    p = dict(key=k, format="JSON", source_desc="SURVEY", commodity_desc="WHEAT", class_desc=group, statisticcat_desc="PRODUCTION", unit_desc="BU", agg_level_desc="COUNTY", year=str(year))
    for a in range(4):
        try:
            with urllib.request.urlopen(NASS + "?" + urllib.parse.urlencode(p), timeout=120) as r:
                rows = json.load(r).get("data", [])
            break
        except urllib.error.HTTPError as e:
            if e.code == 400:
                rows = []
                break
            time.sleep(5 * (a + 1))
        except Exception:
            time.sleep(5 * (a + 1))
    else:
        sys.exit(f"REFUSING TO WRITE: NASS did not answer for {group} {year}")
    time.sleep(0.6)
    out = {}
    for r in rows:
        if str(r.get("county_name", "")).upper().startswith("OTHER") or r.get("county_ansi") in (None, "", "998", "000", "999"):
            continue
        if r.get("prodn_practice_desc") not in (None, "ALL PRODUCTION PRACTICES"):
            continue
        try:
            out[r["state_fips_code"].zfill(2) + r["county_ansi"].zfill(3)] = float(str(r["Value"]).replace(",", ""))
        except ValueError:
            pass
    return out


def ipf(rows, targets, seed, iters=400):
    """The same fit as app._wc_ipf (kept here so this builder does not import the Streamlit app): row totals scaled to the targets' sum, then alternate column/row scaling."""
    ids = [i for i, v in rows.items() if v > 0]
    cls = [c for c, v in targets.items() if v > 0]
    if not ids or not cls:
        return {}
    r = np.array([rows[i] for i in ids], float)
    t = np.array([targets[c] for c in cls], float)
    r = r * (t.sum() / r.sum())
    a = np.array([[max(seed.get((i, c), 1.0), 1e-6) for c in cls] for i in ids], float)
    x = a / a.sum(axis=1, keepdims=True) * r[:, None]
    for _ in range(iters):
        x = x * (t / np.maximum(x.sum(axis=0), 1e-12))[None, :]
        x = x * (r / np.maximum(x.sum(axis=1), 1e-12))[:, None]
        if np.abs(x.sum(axis=0) - t).max() <= 1e-9 * t.sum():
            break
    x = x * (t / np.maximum(x.sum(axis=0), 1e-12))[None, :]
    return {(i, c): float(x[n, m]) for n, i in enumerate(ids) for m, c in enumerate(cls)}


def main():
    k = key()
    cp = json.load(open(os.path.join(D, "rail_map", "county_prod_avg.json")))
    total = {f: v for f, v in cp["avg"]["Wheat"].items() if v > 0}
    cens = pd.read_csv(os.path.join(D, "census_county_fips.csv"), dtype=str)
    st_of = {r.state_fips + r.county_fips: r.state for r in cens.itertuples()}
    cls = pd.read_csv(os.path.join(D, "wheat", "wheat_class_state.csv"))
    cls = cls[cls.year.isin(YEARS) & (cls["class"] != "Other winter (unclassed)")]
    st_cls = cls.groupby(["state", "class"]).production_bu.sum().unstack(fill_value=0.0) / len(YEARS)
    prior = pd.read_csv(os.path.join(D, "wheat", "county_class_prior.csv"), dtype={"fips": str})
    seed = {(f_, c_): w_ for f_, c_, w_ in zip(prior["fips"], prior["class"], prior["weight"])}

    named = {g: {} for g in GROUPS}                                    # group -> fips -> list of yearly production
    for g in GROUPS:
        for y in YEARS:
            rows = county_rows(k, g, y)
            print(f"  NASS county {g} {y}: {len(rows)} counties", flush=True)
            for f, v in rows.items():
                named[g].setdefault(f, []).append(v)
    avg = {g: {f: float(np.mean(v)) for f, v in d.items()} for g, d in named.items()}

    out, miss_state, fallback_n = {}, set(), 0
    by_state = {}
    for f, w in total.items():
        s = st_of.get(f)
        if s:
            by_state.setdefault(s, []).append(f)
    for s, fl in sorted(by_state.items()):
        if s not in st_cls.index:
            miss_state.add(s)
            continue
        sc = st_cls.loc[s]
        grp_state = {g: float(sum(sc.get(c, 0.0) for c in cs)) for g, cs in GROUPS.items()}
        gs = sum(grp_state.values()) or 1.0
        frac = {}
        for f in fl:
            a = {g: avg[g].get(f, 0.0) for g in GROUPS}
            ta = sum(a.values())
            if ta > 0:
                frac[f] = {g: a[g] / ta for g in GROUPS}
            else:
                frac[f] = {g: grp_state[g] / gs for g in GROUPS}
                fallback_n += 1
        res = {f: {m: 0.0 for m in MODEL_ORDER} for f in fl}
        for g, cs in GROUPS.items():
            rows = {f: total[f] * frac[f][g] for f in fl if frac[f][g] > 0}
            tg = {c: float(sc.get(c, 0.0)) for c in cs}
            if not rows or sum(tg.values()) <= 0:
                for f, v in rows.items():                              # no class data for this group in the state: keep the bushels as the group's first class
                    res[f][MODEL[cs[0]]] += v
                continue
            x = ipf(rows, tg, seed)
            for (f, c), v in x.items():
                res[f][MODEL[c]] += v
        out.update({f: {m: round(v) for m, v in r.items() if v >= 1} for f, r in res.items()})
    tot_in = sum(total.values())
    tot_out = sum(sum(r.values()) for r in out.values())
    print(f"counties {len(out)} of {len(total)}; states with no class data: {sorted(miss_state)}; counties on the state's group mix: {fallback_n}")
    print(f"total {tot_in / 1e6:,.0f} M bu in, {tot_out / 1e6:,.0f} out")
    nat = pd.DataFrame(out).T.fillna(0).sum() / 1e6
    print("national class supply (M bu):", nat.round(0).to_dict())
    p = os.path.join(D, "draw", "wheat_county_class.json")
    with open(p, "w") as fh:
        json.dump({"built": pd.Timestamp.today().strftime("%Y-%m-%d"), "years": list(YEARS), "classes": MODEL_ORDER, "counties": out,
                   "source": "NASS county production by class (group mix), NASS state class totals, regional class priors; 2021-25 average"}, fh, separators=(",", ":"))
    print("wrote", p)


if __name__ == "__main__":
    main()

"""
Wheat production by market class and state: data/wheat/wheat_class_state.csv (+ wheat_class_national_check.csv).

The classes follow the Crop Conditions dashboard's model (crop-conditions-dashboard/app.py: hard red winter, soft red winter, white winter, hard red spring, durum, with
class weights by state), filled from the NASS series that actually exist (usda-nass-etl/CLAUDE.md, checked 2026-09-11 and again 11-01):
  * NASS publishes a wheat sub-class (WINTER, RED, HARD / WINTER, RED, SOFT / WINTER, WHITE, SOFT / WINTER, WHITE, HARD / SPRING, WHITE, SOFT) only as PRODUCTION in bushels
    at NATIONAL level, and at STATE level only as PRODUCTION in 'PCT BY TYPE': that class's share of the state's winter (or spring) wheat crop.
  * So class production by state = the state's WINTER production x that class's share, and for spring: SPRING, (EXCL DURUM) production split into hard red spring and
    spring white by the white share, with SPRING, DURUM production taken as it is. 'Other winter' is what is left of winter (hard white and unclassified).
  * Check: summed over states, each class matches NASS's national bushels (the check file shows the ratio by year; shares are whole percents, so small drift is expected).
No yield or acreage exists by sub-class, so class-level yields are not here. The latest year uses the best reference period NASS has (final, else the latest forecast).

    python build_wheat_class_state.py [first_year]       # needs NASS_API_KEY in the environment or .streamlit/secrets.toml
"""
import json
import os
import sys
import time
import tomllib
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "wheat")
URL = "https://quickstats.nass.usda.gov/api/api_GET/"
PRI = {"YEAR": 100, "YEAR - AUG FORECAST": 70, "YEAR - JUL FORECAST": 60, "YEAR - JUN FORECAST": 50, "YEAR - JUN ACREAGE": 45, "YEAR - MAY FORECAST": 30}
# class_desc -> (label in the output, kind)
SHARES = {"WINTER, RED, HARD": "Hard red winter", "WINTER, RED, SOFT": "Soft red winter", "WINTER, WHITE, SOFT": "Soft white winter", "WINTER, WHITE, HARD": "Hard white winter"}


def key() -> str:
    k = os.environ.get("NASS_API_KEY", "")
    if not k:
        p = os.path.join(HERE, ".streamlit", "secrets.toml")
        if os.path.exists(p):
            k = tomllib.load(open(p, "rb")).get("NASS_API_KEY", "")
    if not k:
        sys.exit("REFUSING TO RUN: no NASS_API_KEY in the environment or .streamlit/secrets.toml")
    return k


def q(k, **p):
    p.update(key=k, format="JSON", source_desc="SURVEY")
    err = ""
    for a in range(3):
        try:
            with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(p), timeout=120) as r:
                return json.load(r).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 400:                                           # NASS answers 400 when a series does not exist
                return []
            err = str(e).replace(k, "***")
        except Exception as e:
            err = str(e).replace(k, "***")
        time.sleep(3 + 3 * a)
    sys.exit("REFUSING TO WRITE: NASS did not answer: " + err[:160])


def frame(rows, level):
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    d["v"] = pd.to_numeric(d.Value.astype(str).str.replace(",", ""), errors="coerce")
    d = d.dropna(subset=["v"])
    d["year"] = d.year.astype(int)
    d["pri"] = d.reference_period_desc.str.strip().str.upper().map(PRI).fillna(0)
    keys = ["year"] + (["state_alpha"] if level == "state" else [])
    d = d.sort_values(keys + ["pri"], ascending=[True] * len(keys) + [False]).drop_duplicates(keys, keep="first")
    if level == "state":                                               # the in-season forecasts carry an 'Other States' row (OT); once a year has final state figures it only double counts them
        final = set(d[(d.state_alpha != "OT") & (d.reference_period_desc.str.strip().str.upper() == "YEAR")].year)
        d = d[~((d.state_alpha == "OT") & d.year.isin(final))]
    return d[keys + ["v", "reference_period_desc"]]


def main(first=2015):
    k = key()
    yrs = dict(year__GE=str(first))
    prod = {c: frame(q(k, commodity_desc="WHEAT", class_desc=c, statisticcat_desc="PRODUCTION", unit_desc="BU", agg_level_desc="STATE", **yrs), "state")
            for c in ("WINTER", "SPRING, (EXCL DURUM)", "SPRING, DURUM")}
    pct = {c: frame(q(k, commodity_desc="WHEAT", class_desc=c, statisticcat_desc="PRODUCTION", unit_desc="PCT BY TYPE", agg_level_desc="STATE", **yrs), "state")
           for c in list(SHARES) + ["SPRING, WHITE, SOFT"]}
    nat = {c: frame(q(k, commodity_desc="WHEAT", class_desc=c, statisticcat_desc="PRODUCTION", unit_desc="BU", agg_level_desc="NATIONAL", **yrs), "national")
           for c in list(SHARES) + ["SPRING, WHITE, SOFT", "SPRING, (EXCL DURUM)", "SPRING, DURUM", "WINTER"]}
    for name, d in list(prod.items()) + list(pct.items()):
        if d.empty:
            print(f"  note: no state rows for {name}")
    rows = []
    w = prod["WINTER"].rename(columns={"v": "winter_bu", "reference_period_desc": "ref"})
    classed = w.copy()
    for c, label in SHARES.items():
        p = pct[c][["year", "state_alpha", "v"]].rename(columns={"v": "pct"})
        m = w.merge(p, on=["year", "state_alpha"], how="inner")
        for r in m.itertuples():
            rows.append((r.year, r.state_alpha, label, r.winter_bu * r.pct / 100.0, f"{r.ref}; PCT BY TYPE {r.pct:g}% x winter {r.winter_bu:,.0f} bu"))
    used = pd.DataFrame(rows, columns=["year", "state", "class", "production_bu", "basis"])
    resid = used.groupby(["year", "state"]).production_bu.sum().rename("classed").reset_index().merge(w.rename(columns={"state_alpha": "state"}), on=["year", "state"], how="right")
    resid["classed"] = resid.classed.fillna(0)
    resid["production_bu"] = (resid.winter_bu - resid.classed).clip(lower=0)
    extra = [(r.year, r.state, "Other winter (unclassed)", r.production_bu, "winter production not covered by the class shares") for r in resid.itertuples() if r.production_bu >= 1000]
    sp = prod["SPRING, (EXCL DURUM)"].rename(columns={"v": "spring_bu", "reference_period_desc": "ref"})
    sw = pct["SPRING, WHITE, SOFT"][["year", "state_alpha", "v"]].rename(columns={"v": "pct"})
    sp = sp.merge(sw, on=["year", "state_alpha"], how="left").fillna({"pct": 0})
    for r in sp.itertuples():
        extra.append((r.year, r.state_alpha, "Hard red spring", r.spring_bu * (100 - r.pct) / 100.0, f"{r.ref}; spring (excl durum) {r.spring_bu:,.0f} bu less {r.pct:g}% soft white"))
        if r.pct > 0:
            extra.append((r.year, r.state_alpha, "Soft white spring", r.spring_bu * r.pct / 100.0, f"{r.ref}; PCT BY TYPE {r.pct:g}% x spring (excl durum)"))
    for r in prod["SPRING, DURUM"].itertuples():
        extra.append((r.year, r.state_alpha, "Durum", r.v, f"{r.reference_period_desc}; SPRING, DURUM production"))
    out = pd.concat([used, pd.DataFrame(extra, columns=used.columns)], ignore_index=True)
    out = out[out.production_bu > 0].sort_values(["year", "state", "class"])
    tot = out.groupby(["year", "state"]).production_bu.transform("sum")
    out["share_of_state_wheat_pct"] = (out.production_bu / tot * 100).round(1)
    out["production_bu"] = out.production_bu.round(0).astype("int64")
    os.makedirs(OUT, exist_ok=True)
    out.to_csv(os.path.join(OUT, "wheat_class_state.csv"), index=False)
    # the check against NASS's national class bushels
    chk = []
    natmap = {"Hard red winter": "WINTER, RED, HARD", "Soft red winter": "WINTER, RED, SOFT", "Soft white winter": "WINTER, WHITE, SOFT", "Hard white winter": "WINTER, WHITE, HARD",
              "Soft white spring": "SPRING, WHITE, SOFT", "Durum": "SPRING, DURUM"}
    for lab, c in natmap.items():
        n = nat[c].set_index("year").v if not nat[c].empty else pd.Series(dtype=float)
        s = out[out["class"] == lab].groupby("year").production_bu.sum()
        for y in sorted(set(s.index) | set(n.index)):
            chk.append((y, lab, s.get(y, 0), n.get(y), (s.get(y, 0) / n.get(y)) if n.get(y) else None))
    chk = pd.DataFrame(chk, columns=["year", "class", "states_sum_bu", "nass_national_bu", "ratio"])
    chk.to_csv(os.path.join(OUT, "wheat_class_national_check.csv"), index=False)
    print(f"wrote {OUT}: {len(out)} rows, years {out.year.min()}-{out.year.max()}, {out.state.nunique()} states")
    print("check vs NASS national (states sum / national), by class, min-max over years:")
    for lab, g in chk.dropna(subset=["ratio"]).groupby("class"):
        print(f"  {lab:20s} {g.ratio.min():.3f} - {g.ratio.max():.3f}  ({len(g)} years)")
    for lab in ("Hard red winter", "Soft red winter", "Hard red spring"):
        print(f"  {lab}: national total not checked here" if lab == "Hard red spring" else "", end="")
    print()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2015)

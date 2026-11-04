"""
Livestock feed demand for corn by county, and the distillers grains credit that ethanol plants hand back to it, for the Draw Territory tab.

  1. County livestock.   The Census of Agriculture (2012, 2017, 2022) gives each county's share of its state's herd or flock; the shares of the three Census years are averaged
                         (recent years count more), a county the Census withheld ('(D)') takes an equal part of the state's undisclosed remainder, and each year's NASS survey
                         inventory for the state is spread over the counties by those shares (build_feed_inputs.py writes data/feed/*.csv).
  2. Feed per animal.    Corn (bushels of corn energy with no distillers grains in the ration) and soybean meal (pounds) per head a year, by kind of animal. The corn figures are
                         relative weights: they are scaled so that, nationally, livestock corn demand net of the distillers grains credit equals USDA's feed and residual for the year.
  3. Distillers grains.  Each bushel ground returns about 0.15 bu of corn-equivalent feed (0.27 bu of DDGS by weight x 0.55 lb of corn displaced per lb). The wet and modified part is
                         fed near the plant, to cattle; the dried part goes to feed within a few hundred miles, and the share of it that is exported credits nobody here.
                         The plant still buys 100% of its grind: the credit lowers the feed corn its region needs, not the corn the plant takes.

Every number that is an assumption (feed per head, wet share by state, export share, radius) is a named constant or a function argument.
"""
from __future__ import annotations

import csv
import os
from functools import lru_cache

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data", "feed")
CENSUS_YEARS = (2012, 2017, 2022)
CENSUS_WEIGHT = {2012: 0.2, 2017: 0.3, 2022: 0.5}                      # the recent Census says more about where the animals are now
SPECIES = ["CATTLE ON FEED", "OTHER CATTLE", "MILK COWS", "HOGS", "LAYERS", "BROILERS", "TURKEYS"]
RUMINANTS = ("CATTLE ON FEED", "OTHER CATTLE", "MILK COWS")

# Corn energy per head a year (bu) with no distillers grains in the ration, from standard feeding budgets, and soybean meal per head a year (lb; the factors of the ASD supply-and-demand
# tab). Heads are the NASS survey inventory (hogs: average of the quarterly counts; broilers and turkeys: birds produced in the year), so a feedlot animal counts for the share of the
# year it is there: a finishing steer eats about 42 bu in a stay and the lot turns over about twice a year (85 bu per inventory head); a hog, with the sow herd's feed, about 19 bu per
# inventory head. They are RELATIVE weights: county_feed() scales them so the national total, net of the distillers grains credit, is USDA's feed and residual.
RAW_CORN_BU = {"CATTLE ON FEED": 85.0, "OTHER CATTLE": 6.0, "MILK COWS": 60.0, "HOGS": 19.0, "LAYERS": 1.1, "BROILERS": 0.12, "TURKEYS": 0.6}
SBM_LB = {"CATTLE ON FEED": 0.0, "OTHER CATTLE": 0.0, "MILK COWS": 1450.0, "HOGS": 150.0, "LAYERS": 20.0, "BROILERS": 3.0, "TURKEYS": 22.0}

# Distillers grains. 15.5 lb DDGS-equivalent per 56-lb bushel x 0.55 lb corn displaced per lb = 0.152 bu of corn per bushel ground (range 0.12-0.20); soybean meal 0.07 lb per lb.
CORN_CREDIT = 0.15
SBM_CREDIT_LB = 0.07 * 15.5                                              # lb of soybean meal displaced per bushel ground (about 1.1)
DRIED_EXPORT_SHARE = 0.33                                                # used only when no export tonnage is given: the part of the dried product that leaves the country


@lru_cache(maxsize=1)
def census_ddgs_exports() -> dict:
    """{year: metric tons} of US exports of distillers grains (HS 2303.30, Census, calendar years; data/feed/ddgs_exports_census.csv)."""
    return {int(r["year"]): float(r["tonnes"]) for r in _read("ddgs_exports_census.csv")}


def default_export_tonnes() -> float:
    """The latest three Census years averaged: what the dried product exports are taken at when nothing else is set."""
    ex = census_ddgs_exports()
    yrs = sorted(ex)[-3:]
    return sum(ex[y] for y in yrs) / len(yrs)


EXPORT_STATE_MAX_SHARE = 0.6        # most of a state's dried product that can leave from there: the rest is fed at home (a state's exports are also loaded from other states' product)


@lru_cache(maxsize=1)
def export_state_shares() -> dict:
    """{state: share of the value of US distillers grains exports} by Census state of origin of the movement, the latest three years averaged
    (data/feed/ddgs_exports_by_state.csv). A port or transload state (Louisiana above all) is where the product was loaded, not made; allocate_exports() hands those shares to the states that make it."""
    by: dict = {}
    for r in _read("ddgs_exports_by_state.csv"):
        by.setdefault(int(r["year"]), {})[r["state"]] = float(r["value_usd"])
    yrs = sorted(by)[-3:]
    tot = {y: sum(by[y].values()) for y in yrs}
    out: dict = {}
    for y in yrs:
        for s, v in by[y].items():
            out[s] = out.get(s, 0.0) + v / tot[y] / len(yrs)
    return out


@lru_cache(maxsize=1)
def export_routes() -> dict:
    """The latest year of Census export weights by route: {year, total_t, container_t, bulk_vessel_t, land_t} (vessel and container weights are reported; land is the rest), and the top ports by value."""
    rows = _read("ddgs_exports_by_route.csv")
    last = max(rows, key=lambda r: int(r["year"]))
    ports = [r for r in _read("ddgs_exports_by_port.csv") if r["year"] == last["year"]]
    ports = sorted(ports, key=lambda r: -float(r["value_usd"]))[:8]
    return {"year": int(last["year"]), **{k: float(last[k]) for k in ("total_t", "container_t", "bulk_vessel_t", "land_t")},
            "ports": [(r["port"], float(r["value_usd"]) / 1e6) for r in ports]}


def allocate_exports(total, shares, dried_by_state, max_frac=EXPORT_STATE_MAX_SHARE) -> dict:
    """Spread `total` (bushels of corn-equivalent) over the states that make dried product: each state's want is its Census origin share, a state can export at most `max_frac` of
    the dried product it makes, and what a state cannot take (a port state with no plants, or a state whose share is more than it makes) goes to the states with room, in
    proportion to their own shares. Returns {state: bu}; what no state has room for is left out."""
    cap = {s: max_frac * v for s, v in dried_by_state.items() if v > 0}
    alloc = {s: 0.0 for s in cap}
    for _ in range(60):
        left = total - sum(alloc.values())
        room = {s: cap[s] - alloc[s] for s in cap if cap[s] - alloc[s] > 1e-6}
        if left <= 1e-6 or not room:
            break
        w = {s: shares.get(s, 0.0) for s in room}
        wt = sum(w.values())
        if wt <= 0:
            w, wt = dict(room), sum(room.values())
        for s in room:
            alloc[s] += min(left * w[s] / wt, room[s])
    return alloc


def export_credit_bu(tonnes: float, corn_credit_per_lb: float = 0.55) -> float:
    """Bushels of corn-equivalent feed that `tonnes` of exported distillers grains stand for (0.55 lb of corn displaced per lb of DDGS, 56-lb bushels)."""
    return tonnes * 2204.62 * corn_credit_per_lb / 56.0
WET_RADIUS_MI = 90.0                                                     # wet and modified distillers grains are fed within about this many miles of the plant
DRIED_SCALE_MI = 300.0                                                   # dried product spreads with a distance decay of this scale
CREDIT_CAP = 0.35                                                        # distillers grains never replace more than this share of a county's corn feed energy
# Share of a state's distillers grains sold wet or modified (cattle feeding states high, the eastern Corn Belt low). A PLACEHOLDER by state until there is a plant-level list.
WET_SHARE_DEFAULT = 0.15
WET_SHARE = {"NE": 0.40, "KS": 0.50, "CO": 0.60, "TX": 0.50, "OK": 0.50, "NM": 0.70, "ID": 0.60, "AZ": 0.80, "CA": 0.80, "WA": 0.50, "OR": 0.50, "SD": 0.20, "IA": 0.15, "MN": 0.10,
             "IL": 0.10, "IN": 0.10, "OH": 0.10, "WI": 0.15, "MI": 0.15, "ND": 0.15, "MO": 0.15, "KY": 0.10, "TN": 0.10, "GA": 0.10, "NY": 0.10, "PA": 0.10}


def _read(name):
    with open(os.path.join(D, name), encoding="utf8", newline="") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def _census():
    """({(year, species): {fips: head or None}}, {(year, state, species): head})."""
    cty: dict = {}
    for r in _read("census_county_livestock.csv"):
        cty.setdefault((int(r["year"]), r["species"]), {})[r["fips"]] = float(r["head"]) if r["head"] != "" else None
    st: dict = {}
    for r in _read("census_state_livestock.csv"):
        if r["head"] != "":
            st[(int(r["year"]), r["state"], r["species"])] = float(r["head"])
    return cty, st


@lru_cache(maxsize=1)
def _survey():
    """{(year, state, species): head}: the NASS survey inventory, averaged over the reference periods of the year."""
    acc: dict = {}
    for r in _read("nass_state_livestock.csv"):
        acc.setdefault((int(r["year"]), r["state"], r["species"], r["period"]), []).append(float(r["head"]))
    per: dict = {}
    for (y, s, sp, p), v in acc.items():
        per.setdefault((y, s, sp), []).append(sum(v) / len(v))
    return {k: sum(v) / len(v) for k, v in per.items()}


def census_shares(counties, species):
    """{fips: share of its state's herd or flock} for one kind of animal, the three Census years blended. The shares of a state add to 1."""
    cty, stt = _census()
    fips_state = {c["fips"]: c["state"] for c in counties}
    out: dict = {}
    for yr in CENSUS_YEARS:
        if species == "OTHER CATTLE":
            allc, cof, milk = (cty.get((yr, k), {}) for k in ("CATTLE ALL", "CATTLE ON FEED", "MILK COWS"))
            heads = {}
            for f, v in allc.items():
                if v is None:
                    heads[f] = None
                else:
                    heads[f] = max(v - (cof.get(f) or 0.0) - (milk.get(f) or 0.0), 0.0)
        else:
            heads = cty.get((yr, species), {})
        by_state: dict = {}
        for f, s in fips_state.items():
            if f in heads:
                by_state.setdefault(s, []).append(f)
        for s, fl in by_state.items():
            disclosed = {f: heads[f] for f in fl if heads[f] is not None}
            withheld = [f for f in fl if heads[f] is None]
            tot = stt.get((yr, s, "CATTLE ALL" if species == "OTHER CATTLE" else species))
            tot_d = sum(disclosed.values())
            share = {}
            if withheld and tot and tot > tot_d:                           # the state total the Census published, less what it disclosed, shared out among the withheld counties
                rem = (tot - tot_d) / len(withheld)
                disclosed = {**disclosed, **{f: rem for f in withheld}}
            den = sum(disclosed.values())
            if den <= 0:
                continue
            for f, v in disclosed.items():
                share[f] = v / den
            for f, v in share.items():
                out.setdefault(f, {})[yr] = v
    shares = {}
    for f, by_year in out.items():
        w = sum(CENSUS_WEIGHT[y] for y in by_year)
        shares[f] = sum(CENSUS_WEIGHT[y] * v for y, v in by_year.items()) / w
    tot_by_state: dict = {}
    for f, v in shares.items():
        tot_by_state[fips_state[f]] = tot_by_state.get(fips_state[f], 0.0) + v
    return {f: v / tot_by_state[fips_state[f]] for f, v in shares.items() if tot_by_state[fips_state[f]] > 0}


def state_inventory(year, species):
    """{state: head} for one kind of animal in `year`: the NASS survey inventory, carried forward from the latest earlier year when NASS has not published `year` yet."""
    sv = _survey()
    if species == "OTHER CATTLE":
        a, c, m = (state_inventory(year, k) for k in ("CATTLE ALL", "CATTLE ON FEED", "MILK COWS"))
        return {s: max(v - c.get(s, 0.0) - m.get(s, 0.0), 0.0) for s, v in a.items()}
    out: dict = {}
    for back in range(0, 4):
        for (y, s, sp), v in sv.items():
            if y == year - back and sp == species and s not in out:
                out[s] = v
    return out


def county_head(counties, year):
    """{species: array of head by county}: each state's inventory in `year` spread over its counties by the blended Census shares."""
    out = {}
    for sp in SPECIES:
        sh = census_shares(counties, sp)
        inv = state_inventory(year, sp)
        out[sp] = np.array([inv.get(c["state"], 0.0) * sh.get(c["fips"], 0.0) for c in counties], float)
    return out


def raw_demand(heads):
    """(corn by species, SBM lb by species): head x the relative feed factors, before the USDA scaling."""
    return ({sp: heads[sp] * RAW_CORN_BU[sp] for sp in SPECIES}, {sp: heads[sp] * SBM_LB[sp] for sp in SPECIES})


def _miles(lat1, lon1, lat2, lon2):
    """Straight-line miles between points, plants (n,) against counties (m,) -> (n, m)."""
    p = np.pi / 180.0
    a = np.sin((lat2[None, :] - lat1[:, None]) * p / 2) ** 2 + np.cos(lat1[:, None] * p) * np.cos(lat2[None, :] * p) * np.sin((lon2[None, :] - lon1[:, None]) * p / 2) ** 2
    return 3959.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def ddgs_credit(plants, grind_bu, counties, gross_by_species, *, corn_credit=CORN_CREDIT, wet_share=None, dried_export=DRIED_EXPORT_SHARE, export_bu=None, wet_radius=WET_RADIUS_MI,
                dried_scale=DRIED_SCALE_MI, cap=CREDIT_CAP, export_shares=None, export_max=EXPORT_STATE_MAX_SHARE):
    """Where each plant's distillers grains credit lands, in bushels of corn equivalent.

    Returns {"county": credit by county, "wet": ..., "dried": ..., "export": bu of dried product exported, "unplaced": bu above the county cap or with no one to feed it, "by_plant": credit by plant}.
    Wet and modified product goes to cattle and dairy within `wet_radius` miles of the plant (in proportion to their corn feed energy); dried goes to every kind of animal with a
    distance decay of `dried_scale` miles; the exported part of the dried product is not placed: `export_bu` (corn-equivalent bushels, from Census export tonnage) when given, taken from every plant's dried product pro rata, else the `dried_export` share. A county's credit is capped at `cap` of its corn feed energy."""
    ws = wet_share or {}
    gross = sum(gross_by_species.values())
    rum = sum(gross_by_species[sp] for sp in RUMINANTS)
    lat_p = np.array([p["lat"] for p in plants], float)
    lon_p = np.array([p["lon"] for p in plants], float)
    lat_c = np.array([c["lat"] for c in counties], float)
    lon_c = np.array([c["lon"] for c in counties], float)
    dist = _miles(lat_p, lon_p, lat_c, lon_c)
    total = np.asarray(grind_bu, float) * corn_credit
    w = np.array([ws.get(p["state"], WET_SHARE_DEFAULT) for p in plants], float)
    wet_amt, dried_all = total * w, total * (1 - w)
    wet_w = np.where(dist <= wet_radius, rum[None, :], 0.0)
    no_wet = wet_w.sum(axis=1) <= 0                                         # no cattle in reach: that product is dried and moves like it
    dried_all = dried_all + np.where(no_wet, wet_amt, 0.0)
    wet_amt = np.where(no_wet, 0.0, wet_amt)
    export_state = None
    if export_bu is not None and export_shares:                              # where the dried product leaves from: Census origin shares, port states passed on to the states that make it
        states = [p["state"] for p in plants]
        dried_state: dict = {}
        for s, v in zip(states, dried_all):
            dried_state[s] = dried_state.get(s, 0.0) + float(v)
        export_state = allocate_exports(float(export_bu), export_shares, dried_state, export_max)
        export = np.array([export_state.get(s, 0.0) * float(v) / max(dried_state[s], 1e-9) for s, v in zip(states, dried_all)], float)
    else:
        export = dried_all * dried_export if export_bu is None else dried_all * min(float(export_bu) / max(float(dried_all.sum()), 1e-9), 1.0)
    dried_dom = dried_all - export
    dried_w = gross[None, :] * np.exp(-dist / dried_scale)
    A_wet = wet_amt[:, None] * wet_w / np.maximum(wet_w.sum(axis=1, keepdims=True), 1e-300)
    A_dry = dried_dom[:, None] * dried_w / np.maximum(dried_w.sum(axis=1, keepdims=True), 1e-300)
    A = A_wet + A_dry
    col = A.sum(axis=0)
    lim = cap * gross
    scale = np.where(col > lim, lim / np.maximum(col, 1e-300), 1.0)
    A = A * scale[None, :]
    return {"county": A.sum(axis=0), "wet": (A_wet * scale[None, :]).sum(axis=0), "dried": (A_dry * scale[None, :]).sum(axis=0), "export": float(export.sum()),
            "unplaced": float(total.sum() - export.sum() - A.sum()), "by_plant": A.sum(axis=1), "export_state": export_state,
            "dried_state": ({s: v for s, v in dried_state.items()} if export_state is not None else None)}


def county_feed(counties, year, plants, grind_bu, feed_residual_bu, **credit_kw):
    """Livestock corn feed demand by county, net of the distillers grains credit, tied to USDA's feed and residual for the year.

    Returns a dict: heads, gross (corn bu by county), gross_by_species, net (bu), credit (the ddgs_credit result), sbm_lb (by county), k (the USDA scaling of the raw factors)."""
    heads = county_head(counties, year)
    raw_corn, raw_sbm = raw_demand(heads)
    raw_total = float(sum(v.sum() for v in raw_corn.values()))
    k = (feed_residual_bu + float(np.sum(grind_bu)) * CORN_CREDIT * 0.8) / max(raw_total, 1.0)      # a first guess at the credit placed; refined below
    for _ in range(4):
        gbs = {sp: v * k for sp, v in raw_corn.items()}
        cr = ddgs_credit(plants, grind_bu, counties, gbs, **credit_kw)
        placed = float(cr["county"].sum())
        k = (feed_residual_bu + placed) / max(raw_total, 1.0)
    gbs = {sp: v * k for sp, v in raw_corn.items()}
    gross = sum(gbs.values())
    return {"heads": heads, "gross": gross, "gross_by_species": gbs, "credit": cr, "net": np.maximum(gross - cr["county"], 0.0), "k": k,
            "sbm_lb": sum(raw_sbm.values())}

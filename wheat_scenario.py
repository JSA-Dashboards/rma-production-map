"""
Wheat for the Draw Territory tab: the class selector, the marketing-year scenario and the domestic pull, as pure functions over the built data files.

Classes (the selector): HRW hard red winter, SRW soft red winter, White (soft white winter + soft white spring + hard white winter, FGIS's 'white', with the mixed Pacific cargoes),
HRS hard red spring, Durum. 'All classes' = all of them.

  county supply   data/draw/wheat_county_class.json (2021-25 average, build_wheat_county_class.py). A crop year other than the average scales each county by its state's class
                  production that year over the 2021-25 average (data/wheat/wheat_class_state.csv, NASS).
  hub targets     data/wheat/wheat_export_class_hub_by_my.csv (FGIS inspections by class and hub, June-May marketing years): the model's hub groups are scaled so that, for the
                  chosen class and marketing year, each kind of hub loads what FGIS inspected there. The wheat marketing year starts the June after the crop is cut, so
                  marketing year Y is paired with crop year Y.
  domestic pull   wheat is milled, fed and stored where people are, not pro rata: the weight of a county's domestic use is its region's flour milling capacity
                  (data/wheat/flour_milling_regional_capacity.csv, NASS, from Kolten) over what the region grows.
"""
from __future__ import annotations

import copy
import csv
import json
import os
from functools import lru_cache

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = ["HRW", "SRW", "White", "HRS", "Durum"]
LABELS = {"HRW": "Hard red winter", "SRW": "Soft red winter", "White": "White (soft white + hard white)", "HRS": "Hard red spring", "Durum": "Durum"}
KIND_OF_GROUP = {"lock": "river", "area": "river", "lagulf": "river", "mobile": "river", "pnw": "pnw", "txgulf": "txgulf", "mexico": "mexico", "lakes": "lakes", "satl": "satl"}
HUB_KIND = {"Pacific Northwest": "pnw", "Texas Gulf": "txgulf", "Mexico rail": "mexico", "Mississippi River": "river", "Great Lakes": "lakes", "South Atlantic": "satl"}
FGIS_CLASS = {"HRW": "HRW", "SRW": "SRW", "HRS": "HRS", "SWW": "White", "HDWH": "White", "XWHT": "White", "DUWH": "Durum"}
NASS_CLASS = {"Hard red winter": "HRW", "Soft red winter": "SRW", "Soft white winter": "White", "Hard white winter": "White", "Soft white spring": "White", "Hard red spring": "HRS",
              "Durum": "Durum"}
BASE = (2021, 2025)


@lru_cache(maxsize=1)
def county_class() -> dict:
    with open(os.path.join(HERE, "data", "draw", "wheat_county_class.json"), encoding="utf8") as fh:
        return json.load(fh)["counties"]


@lru_cache(maxsize=1)
def _fgis() -> list:
    with open(os.path.join(HERE, "data", "wheat", "wheat_export_class_hub_by_my.csv"), encoding="utf8", newline="") as fh:
        return [{"my": int(r["marketing_year_start"]), "cls": r["class"], "hub": r["hub"], "mbu": float(r["mbu"])} for r in csv.DictReader(fh)]


@lru_cache(maxsize=1)
def _state_class() -> dict:
    """{(year, state, model class): bu} from NASS."""
    out: dict = {}
    with open(os.path.join(HERE, "data", "wheat", "wheat_class_state.csv"), encoding="utf8", newline="") as fh:
        for r in csv.DictReader(fh):
            c = NASS_CLASS.get(r["class"])
            if c:
                k = (int(r["year"]), r["state"], c)
                out[k] = out.get(k, 0.0) + float(r["production_bu"])
    return out


def marketing_years() -> list:
    """Complete marketing years (the year they start), newest first."""
    return sorted({r["my"] for r in _fgis()}, reverse=True)


def label_my(my: int) -> str:
    return f"{my}/{str(my + 1)[2:]}"


def hub_mbu(period, cls) -> dict:
    """{hub kind: M bu} FGIS inspected: period = 'avg' (2021/22-2025/26 average) or a marketing year; cls = a model class or None for all wheat (including unclassed and mixed)."""
    rows = [r for r in _fgis() if r["hub"] in HUB_KIND and (BASE[0] <= r["my"] <= BASE[1] if period == "avg" else r["my"] == period)]
    n = (BASE[1] - BASE[0] + 1) if period == "avg" else 1
    out: dict = {}
    for r in rows:
        if cls is not None and FGIS_CLASS.get(r["cls"]) != cls:
            continue
        k = HUB_KIND[r["hub"]]
        out[k] = out.get(k, 0.0) + r["mbu"] / n
    return out


@lru_cache(maxsize=1)
def _mexico_states() -> dict:
    """{model class: {state: M bu a year}} FGIS wheat inspected on a railcar for Mexico, 2021/22-2025/26 average, by the state of inspection."""
    out: dict = {}
    n = BASE[1] - BASE[0] + 1
    with open(os.path.join(HERE, "data", "wheat", "wheat_mexico_rail_by_my.csv"), encoding="utf8", newline="") as fh:
        for r in csv.DictReader(fh):
            c = FGIS_CLASS.get(r["class"])
            if c and BASE[0] <= int(r["marketing_year_start"]) <= BASE[1]:
                out.setdefault(c, {})[r["state"]] = out.get(c, {}).get(r["state"], 0.0) + float(r["mbu"]) / n
    return out


def state_class_share(inp: dict, cls) -> dict:
    """{state: share of the state's wheat that is this class} from the county supply by class."""
    cc = county_class()
    tot: dict = {}
    mine: dict = {}
    for c in inp["counties"]:
        mix = cc.get(c["fips"], {})
        t = sum(mix.values())
        tot[c["state"]] = tot.get(c["state"], 0.0) + t
        mine[c["state"]] = mine.get(c["state"], 0.0) + float(mix.get(cls, 0.0))
    return {s: mine[s] / tot[s] for s in tot if tot[s] > 0}


def nodes_for(nodes: dict, period, cls, inp: dict | None = None) -> dict:
    """A copy of the wheat hub groups for this class (None = all wheat) and period. Targets are what FGIS inspected, kind by kind: every group of a kind is scaled by the same
    factor, so the barge pools, the Louisiana residual and the rail hubs keep their relative sizes (a kind with nothing inspected gets a zero target). For a class the draw by
    state follows: a held state mix (the Pacific Northwest's waybill mix) is multiplied by the class's share of that state's wheat, a limited list keeps only the states that grow
    the class, and Mexico's states are FGIS's own class-by-state inspections."""
    out = copy.deepcopy(nodes)
    want = hub_mbu(period, cls)
    have: dict = {}
    for g in out["groups"]:
        k = KIND_OF_GROUP.get(g["kind"])
        have[k] = have.get(k, 0.0) + g["target_mbu"]
    share = state_class_share(inp, cls) if (cls is not None and inp is not None) else None
    for g in out["groups"]:
        k = KIND_OF_GROUP.get(g["kind"])
        g["target_mbu"] = g["target_mbu"] * (want.get(k, 0.0) / have[k]) if have.get(k) else 0.0
        g.pop("range_mbu", None)
        if share is None:
            continue
        if g["kind"] == "mexico":
            g["origin_states_mbu"] = {s: round(v, 3) for s, v in _mexico_states().get(cls, {}).items() if v > 0} or None
        elif g.get("origin_states_mbu"):
            held = {s: v * share.get(s, 0.0) for s, v in g["origin_states_mbu"].items() if share.get(s, 0.0) > 0.005}
            g["origin_states_mbu"] = held or None
        elif g.get("origin_states"):
            g["origin_states"] = [s for s in g["origin_states"] if share.get(s, 0.0) > 0.005]
    return out


def county_supply(inp: dict, cls, year) -> np.ndarray:
    """Bushels each county supplies: the class's 2021-25 average (None = all wheat), scaled to the crop year by the state's class production (year None = the average)."""
    cc = county_class()
    sc = _state_class()
    classes = CLASSES if cls is None else [cls]
    avg: dict = {}
    for (y, s, c), v in sc.items():
        if BASE[0] <= y <= BASE[1] and c in classes:
            avg[(s, c)] = avg.get((s, c), 0.0) + v / (BASE[1] - BASE[0] + 1)
    out = []
    for c in inp["counties"]:
        mix = cc.get(c["fips"], {})
        tot = 0.0
        for k in classes:
            v = float(mix.get(k, 0.0))
            if year is not None and v > 0:
                a = avg.get((c["state"], k), 0.0)
                v = v * (sc.get((year, c["state"], k), 0.0) / a) if a > 0 else 0.0
            tot += v
        out.append(tot)
    return np.array(out, float)


@lru_cache(maxsize=1)
def _regions() -> list:
    out = []
    with open(os.path.join(HERE, "data", "wheat", "flour_milling_regional_capacity.csv"), encoding="utf8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.append((r["region"], float(r["daily_capacity_cwt"]), [s for s in r["states"].split(";") if s]))
    return out


BU_PER_CWT_DAY = 365 * 100 / 0.775 / 60          # a cwt of flour a day of capacity grinds this many bushels a year at full tilt (77.5% extraction, 60 lb bushels)
UTILIZATION = 0.76                               # share of capacity milled: 1,600,817 cwt/day x 597 = 956 M bu, USDA's food use of wheat
PNW_STATES = ("ID", "MT", "OR", "UT", "WA")
LOCAL_MAX = 0.70                                 # a county never keeps more than this for its own region: mills draw from a wide area and the export hubs take wheat from every region
                                                 # (soft red wheat is a third exports even in Illinois and Ohio, where capacity exceeds the crop)


def _region_index(inp: dict):
    named = {s: i for i, (_, _, ss) in enumerate(_regions()) for s in ss}
    other_i = len(_regions()) - 1
    return [named.get(c["state"], other_i) for c in inp["counties"]]


def milling_local(inp: dict, cap: float = None) -> np.ndarray:
    """Per-county share of its crop milled inside its own region: the region's flour-milling capacity (NASS table, x utilization) over what the region grows, capped at 1. A region
    that grows less than its mills grind (the Northeast, California) keeps all of its wheat; one that grows several times what it grinds (the Pacific Northwest, the Dakotas) keeps
    a fraction. Mills also take wheat from other regions: that is the 'rest' (rest_weight)."""
    reg = _region_index(inp)
    grown = np.zeros(len(_regions()))
    for c, r in zip(inp["counties"], reg):
        grown[r] += c["supply_bu"]
    grind = np.array([cp for _, cp, _ in _regions()], float) * BU_PER_CWT_DAY * UTILIZATION
    phi = np.minimum(LOCAL_MAX if cap is None else cap, grind / np.maximum(grown, 1.0))
    return np.array([phi[r] for r in reg], float)


def rest_weight(inp: dict, pnw: float = 0.25) -> np.ndarray:
    """Per-county weight for the rest of domestic use (feed, seed, stocks and the flour milled in regions that grow too little): 1 everywhere except the Pacific Northwest states,
    whose wheat is exported or milled locally and which ship little east (weight `pnw`)."""
    return np.array([pnw if c["state"] in PNW_STATES else 1.0 for c in inp["counties"]], float)


def hub_history(cls):
    """{marketing year: {hub kind: M bu}} for every complete marketing year (cls None = all wheat)."""
    return {my: hub_mbu(my, cls) for my in sorted(marketing_years())}


def class_hub_table(period) -> list:
    """Rows for the 'where each class leaves from' table: class, M bu by hub kind, total, in the chosen period."""
    rows = []
    for k in CLASSES:
        h = hub_mbu(period, k)
        rows.append({"cls": k, **{kind: h.get(kind, 0.0) for kind in ("pnw", "txgulf", "mexico", "river", "lakes", "satl")}, "total": sum(h.values())})
    return rows

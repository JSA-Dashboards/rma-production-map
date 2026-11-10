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


MILL_CLASSES = ["HRW", "HRS", "SRW", "White"]    # the classes the flour mills grind: durum is milled (semolina) at other plants, not in the flour mill list


@lru_cache(maxsize=1)
def _food_by_class() -> dict:
    """{marketing year start: {class: M bu}} USDA's food use of wheat by class (WASDE 'U.S. Wheat by Class', build_wheat_food_by_class.py)."""
    out: dict = {}
    with open(os.path.join(HERE, "data", "wheat", "wheat_food_by_class_wasde.csv"), encoding="utf8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(int(r["marketing_year_start"]), {})[r["class"]] = float(r["food_mbu"])
    return out


def mill_controls(period) -> dict:
    """{class: bushels} the flour mills together grind: USDA's food use by class, less durum, for a marketing year, or the average of the years on file inside 2021-25 ('avg'). A marketing year
    with no file takes the nearest one."""
    fb = _food_by_class()
    if period == "avg":
        yrs = [y for y in fb if BASE[0] <= y <= BASE[1]] or sorted(fb)
        pick = {c: sum(fb[y][c] for y in yrs) / len(yrs) for c in MILL_CLASSES}
    else:
        y = min(fb, key=lambda k: abs(k - int(period)))
        pick = {c: fb[y][c] for c in MILL_CLASSES}
    return {c: v * 1e6 for c, v in pick.items()}


# Which classes a flour mill buys, by where it is: the regional flour-type logic (hard-wheat bread flour on the Plains, spring wheat in the north, soft wheat for cookies, crackers and cakes in
# the East and Midwest, soft white in the Northwest, a mix in California, hard and soft wheat in Texas). Shares of the mill's grind in (HRW, HRS, SRW, White). A PRIOR: large mills blend and
# customer specs override geography, so the national class controls (USDA food use by class) pull these toward what the whole industry grinds (mill_class_needs).
REGION_PRIOR = {"plains": (0.80, 0.07, 0.08, 0.05), "north": (0.30, 0.60, 0.05, 0.05), "mountain north": (0.45, 0.45, 0.02, 0.08), "midwest east": (0.28, 0.15, 0.45, 0.12),
                "northwest": (0.25, 0.20, 0.00, 0.55), "utah": (0.45, 0.30, 0.00, 0.25), "west": (0.40, 0.20, 0.15, 0.25), "south central": (0.60, 0.20, 0.20, 0.00)}
STATE_REGION = {**{s: "plains" for s in ("KS", "OK", "CO", "NE")}, **{s: "north" for s in ("ND", "SD", "MN")}, "MT": "mountain north",
                **{s: "midwest east" for s in ("IL", "IN", "OH", "MI", "WI", "IA", "MO", "KY", "NY", "NJ", "MA", "ME", "PA", "VA", "NC", "SC", "GA", "FL", "AL", "TN", "MS")},
                **{s: "northwest" for s in ("WA", "OR", "ID")}, "UT": "utah", **{s: "west" for s in ("CA", "AZ", "NV")}, **{s: "south central" for s in ("TX", "AR", "LA")}}
LISTED_CLASS = {"Hard red winter": "HRW", "Hard spring": "HRS", "High gluten": "HRS", "Soft red": "SRW", "Soft white": "White", "Hard white": "White"}
LISTED_OTHER = 0.15                              # a class a mill does not list still gets this share of its regional prior: mills blend more than they list


def mill_class_prior(m: dict) -> np.ndarray:
    """A mill's prior class shares (HRW, HRS, SRW, White): its region's, weighted toward the classes the mill itself lists (a blank list leaves the regional prior)."""
    p = np.array(REGION_PRIOR[STATE_REGION.get(m["state"], "midwest east")], float)
    listed = {LISTED_CLASS[c] for c in m.get("classes", []) if c in LISTED_CLASS}
    if listed:
        p = p * np.array([1.0 if c in listed else LISTED_OTHER for c in MILL_CLASSES])
    return p / p.sum()


def mill_class_needs(mills: list, controls: dict, iters: int = 200) -> np.ndarray:
    """Bushels each mill grinds of each class (rows mills, columns MILL_CLASSES): a mill's total is its share of the capacity, the classes follow its prior, and the two are fitted together
    so that every class adds up to USDA's food use of that class (iterative proportional fitting). `mills` carry cap_cwt_day, state and classes."""
    cap = np.array([m["cap_cwt_day"] for m in mills], float)
    ctl = np.array([controls[c] for c in MILL_CLASSES], float)
    tot_m = cap / cap.sum() * ctl.sum()
    X = cap[:, None] * np.array([mill_class_prior(m) for m in mills])
    for _ in range(iters):
        X *= (tot_m / np.maximum(X.sum(axis=1), 1e-9))[:, None]
        X *= (ctl / np.maximum(X.sum(axis=0), 1e-9))[None, :]
    X *= (tot_m / np.maximum(X.sum(axis=1), 1e-9))[:, None]
    return X


@lru_cache(maxsize=1)
def _imports_by_class() -> dict:
    """{marketing year start: {class: M bu}} USDA's wheat imports by class (WASDE 'U.S. Wheat by Class', build_wheat_food_by_class.py)."""
    out: dict = {}
    with open(os.path.join(HERE, "data", "wheat", "wheat_imports_by_class_wasde.csv"), encoding="utf8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(int(r["marketing_year_start"]), {})[r["class"]] = float(r["imports_mbu"])
    return out


def hrs_imports(period) -> float:
    """Bushels of hard red spring wheat the US imports (Canada's red spring wheat, nearly all of it): USDA's WASDE by-class imports for a marketing year, or the average of the years on file
    inside 2021-25 ('avg'). Nearly all of it is milled."""
    ib = _imports_by_class()
    if period == "avg":
        yrs = [y for y in ib if BASE[0] <= y <= BASE[1]] or sorted(ib)
        return sum(ib[y]["HRS"] for y in yrs) / len(yrs) * 1e6
    return ib[min(ib, key=lambda k: abs(k - int(period)))]["HRS"] * 1e6


def class_imports(period) -> dict:
    """{class: bushels} of wheat the US imports for each class the mills grind (HRW, HRS, SRW, White), USDA's WASDE by-class imports for a marketing year or the 2021-25 average ('avg').
    Canada supplies nearly all of the spring wheat and most of the rest. Durum is left out: it is not milled in the flour mills."""
    ib = _imports_by_class()
    if period == "avg":
        yrs = [y for y in ib if BASE[0] <= y <= BASE[1]] or sorted(ib)
        return {c: sum(ib[y][c] for y in yrs) / len(yrs) * 1e6 for c in MILL_CLASSES}
    y = min(ib, key=lambda k: abs(k - int(period)))
    return {c: ib[y][c] * 1e6 for c in MILL_CLASSES}


def _import_dict(import_bu) -> dict:
    """The import credit as {class: bushels}: a number is hard red spring wheat only, a dict or a tuple of (class, bushels) pairs (hashable, for the cache) is by class."""
    if not import_bu:
        return {}
    if isinstance(import_bu, (int, float)):
        return {"HRS": float(import_bu)}
    return {c: float(v) for c, v in dict(import_bu).items() if v}


@lru_cache(maxsize=1)
def import_state_shares() -> dict:
    """{state: share} of the value of US wheat imports from Canada (HS 1001.99), 2021-25, by the state it is delivered to (Census; build_wheat_imports.py). Where the Canadian wheat goes."""
    tot: dict = {}
    with open(os.path.join(HERE, "data", "wheat", "wheat_imports_canada_by_state.csv"), encoding="utf8", newline="") as fh:
        for r in csv.DictReader(fh):
            tot[r["state"]] = tot.get(r["state"], 0.0) + float(r["value_usd"])
    s = sum(tot.values())
    return {k: v / s for k, v in tot.items()}


IMPORT_CAP = 0.90          # at most this share of a state's hard red spring grind is imported (the rest comes from US counties)


def mill_import_credit(states: list, hrs_need: np.ndarray, total_bu: float, shares: dict = None, cap: float = IMPORT_CAP) -> np.ndarray:
    """Bushels of imported wheat of one class credited to each mill (`hrs_need` is the mills' grind of that class). The imports go to the states Census says they are delivered to, where the mills are, in proportion to those states'
    shares; inside a state, to each mill by its hard red spring grind. A state takes no more than `cap` of its hard red spring grind, so what a state cannot use (a delivery to a state with
    no mill that grinds spring wheat, or more than its mills need) goes on to the other states in proportion to their shares. That fills the gaps where the US crop is short and leaves a
    region that grows its own (the Dakotas, Montana) mostly to its own farms."""
    shares = import_state_shares() if shares is None else shares
    st = np.array(states)
    need = np.asarray(hrs_need, float)
    room = {s: cap * float(need[st == s].sum()) for s in set(states)}
    got = {s: 0.0 for s in room}
    active = {s for s in room if shares.get(s, 0.0) > 0 and room[s] > 0}
    left = float(total_bu)
    for _ in range(50):
        w = sum(shares[s] for s in active)
        if w <= 0 or left <= 1.0:
            break
        give = {s: min(left * shares[s] / w, room[s] - got[s]) for s in active}
        for s, g in give.items():
            got[s] += g
        left -= sum(give.values())
        active = {s for s in active if room[s] - got[s] > 1.0}
    out = np.zeros(len(st))
    for s, g in got.items():
        m = st == s
        if need[m].sum() > 0:
            out[m] = g * need[m] / need[m].sum()
    return out


MILL_RAIL = {"rate": 0.12, "fixed": 15.0, "min_miles": 250.0}   # wheat to a mill on a railroad: cents per bushel-mile, cents of handling and switching, and the shortest haul that goes by rail
                                                                  # (a cheaper rate, 0.05, let a Wichita mill draw from 480 miles away; 0.12 keeps the local mills local)


MILL_RAIL_MIN_CWT = 10000.0                      # a mill this big is on a railroad even when the book lists none (a blank is not a no); the small ones without one stay on trucks


def mill_plant_rail(inp: dict, plants: list, rate: float = None) -> dict:
    """The plant_rail argument of draw_model.solve for the flour mills: rail from counties within 60 miles of Class I track to a mill that lists a railroad, or one that grinds 10,000 cwt a day or more, when it costs less than the truck."""
    import draw_model as dm
    rs = dm.load_rail_service()
    track = [min(rs.get(c["fips"], [9999] * 6)) for c in inp["counties"]]
    return {**MILL_RAIL, **({"rate": float(rate)} if rate is not None else {}), "reach": 60.0, "track": track, "mask": [bool(p.get("rail")) or p.get("cap_cwt_day", 0) >= MILL_RAIL_MIN_CWT for p in plants]}


def mill_plants(period="avg", cls=None, import_bu=None) -> list:
    """The US flour mills as named plants for the model (flour_mills.py, Sosland's Grain & Milling Annual 2027): each with its daily capacity in cwt, the bushels it grinds a year and how they
    split by class. Together the mills grind USDA's food use of wheat less durum (mill_controls), class by class, so capacity alone does not set the demand. `cls` None = all classes (the
    mill's total), a class key = that class's bushels at each mill. [] when the mill list is not available or the class is durum.
    `import_bu` is the imported wheat to credit to the mills (class_imports; a number is spring wheat only): mill_import_credit places each class's imports, a mill's need from US counties is net of
    it (need_bu), the credit is `import_bu` on each mill (`import_by_class` by class) and its gross grind stays in `class_need`."""
    import flour_mills
    if cls == "Durum":
        return []
    ms = [m for m in flour_mills.load() if m["cap_cwt_day"] and m["state"] != "PR" and m["lat"] is not None]
    if not ms:
        return []
    need = mill_class_needs(ms, mill_controls(period))
    credit = np.zeros(need.shape)                                           # rows mills, columns MILL_CLASSES
    for c, bu in _import_dict(import_bu).items():
        if c in MILL_CLASSES:
            credit[:, MILL_CLASSES.index(c)] = mill_import_credit([m["state"] for m in ms], need[:, MILL_CLASSES.index(c)], bu)
    out, seen = [], {}
    for m, row, crow in zip(ms, need, credit):
        cr = float(crow.sum() if cls is None else crow[MILL_CLASSES.index(cls)])
        base = f"{m['company']}, {m['city']} {m['state']}"
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base} #{seen[base]}"
        out.append({"key": key, "state": m["state"], "lat": m["lat"], "lon": m["lon"], "need_bu": float((row.sum() if cls is None else row[MILL_CLASSES.index(cls)]) - cr), "city": m["city"],
                    "company": m["company"], "cls": "Flour mill", "cap_cwt_day": m["cap_cwt_day"], "classes": m["classes"], "rail": m["rail"], "river": "",
                    "class_need": {c: float(v) for c, v in zip(MILL_CLASSES, row)}, "import_bu": cr, "import_by_class": {c: float(v) for c, v in zip(MILL_CLASSES, crow)}})
    return out


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

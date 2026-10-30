"""
Draw-territory model for soybeans: which buyer each county's beans go to.

Every county in the ring around Illinois holds its soybean supply (5-yr avg NASS production); every crush plant holds an annual
need (Kolten's Crush Downtime Calculator: Daily Max x 0.88 x 350 days); the river terminals together take whatever is left
(supply - crush need). A county's beans go to a buyer in proportion to exp(-(freight - buyer premium) / tau): the nearest buyer
wins most of them and the next nearest take a limited share, and tau (cents/bu) is how much crossover there is. The plants
fill to their need and the river group takes the residual (iterative proportional fitting, so every total ties out).

Pure (numpy only, no Streamlit): `load()` reads the saved inputs, `solve()` returns the flows and summaries.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import numpy as np

INPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "draw_inputs.json")
NODES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "river_nodes.json")
RAIL_SERVICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "county_rail_service.json")
CENTS_PER_BU_MILE = 0.02            # Kolten's NY note: 3 cents/bu for 152 miles


@lru_cache(maxsize=1)
def load(path: str = INPUTS) -> dict:
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_rail_service(path: str = RAIL_SERVICE) -> dict:
    """{county fips: [miles to BNSF, UP, CPKC track]} from build_county_rail_service.py."""
    with open(path, encoding="utf8") as fh:
        return json.load(fh)["counties"]


@lru_cache(maxsize=1)
def load_nodes(path: str = NODES) -> dict:
    """River pin groups with annual soybean targets, from build_river_segments.py (lock tonnage + Gulf inspections)."""
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


# Hubs the model feeds by rail rather than by truck or barge: which railroads reach them (indices into county_rail_service.json's [BNSF, UP, CPKC]).
# The Pacific Northwest is fed almost entirely by BNSF and UP; Texas Gulf and the Mexican border take all three (CPKC = the former KCS, strong at Laredo).
RAIL_KINDS = {"pnw": (0, 1), "txgulf": (0, 1, 2), "mexico": (0, 1, 2)}


def solve(inputs: dict, *, rate=CENTS_PER_BU_MILE, tau=1.0, river_premium=0.0, plant_premium=None, util=1.0, river_share=1.0, iters=400,
          nodes=None, lock_scale=1.0, area_mbu=None, pin_cap_mbu=None, river_scale=1.0, pnw_scale=1.0, pnw_reach_miles=30.0, pnw_rate=None,
          txg_scale=1.0, mex_scale=1.0, txg_rate=None, mex_rate=None):
    """Flows (bu) county x buyer.

    rate           freight, cents per bushel-mile
    tau            crossover softness, cents/bu: smaller = sharper territories, larger = more overlap
    river_premium  cents/bu the river terminals pay over the crush plants (net of their own basis), applied to every terminal
    plant_premium  {plant key: cents/bu} per-plant bid advantage
    util           scales every plant's need (1.0 = the workbook's 88% x 350 days)
    river_share    share of the beans left after crush that move through the river terminals; the rest (feed, rail export to the coast,
                   Mexico, stocks) is taken out of every county pro rata
                   (legacy single-pool mode, used when `nodes` is None)
    nodes          load_nodes(): the river is then a set of regional nodes, each with its own annual target (M bu), and river_share is ignored.
                   Upriver nodes are the soybeans the Corps/USDA lock tonnage says load in that pool; 'Below Lock 27' is East Gulf soybean
                   inspections less the barge arrivals the locks measure; PNW is West Coast inspections. A node's pins share its target by proximity.
    lock_scale     multiplies the lock-implied upriver targets (1.0 = the 2021-25 average)
    area_mbu       {area name: M bu/yr} overrides for the below-Lock-27 areas (St. Louis City, East St. Louis area, Cape Girardeau to Chester,
                   Lower Mississippi), whose defaults are the Corps' WCUS port tonnage, 2021-24 average, and for the two Pacific Northwest hubs
                   (Columbia River, Puget Sound), whose defaults are FGIS inspections by port
    river_scale    multiplies every barge-loading target (the lock-based nodes and the below-Lock-27 areas) after the overrides above, which are
                   then read as baseline (2021-25) figures; export_scenario.py turns a US soybean export number into this factor
    pnw_scale      the same for the Pacific Northwest hubs
    pnw_reach_miles  the Pacific Northwest is fed almost entirely by BNSF and UP rail, so a county sends it beans only if BNSF or UP track is within
                   this many miles of its centroid (data/draw/county_rail_service.json). CPKC is left out.
    pnw_rate       freight for the rail haul to the Pacific Northwest, cents per bushel-mile (None = the truck `rate`)
    txg_scale, mex_scale, txg_rate, mex_rate   the same for the Texas Gulf hub and for Mexico by rail. All three rail hubs use pnw_reach_miles, measured to
                   the railroads in RAIL_KINDS
    pin_cap_mbu    {river pin key: M bu/yr} ceilings, on top of any `cap_mbu` set on the pin in the inputs. A capped pin takes at most its cap
                   and the rest of its group's target spreads over the group's other pins (a ceiling, not a target)
    """
    cty, plants, river = inputs["counties"], inputs["plants"], inputs["river"]
    miles = np.array([[np.nan if m is None else m for m in row] for row in inputs["miles"]], float)
    supply = np.array([c["supply_bu"] for c in cty], float)
    need = np.array([p["need_bu"] for p in plants], float) * util
    n_p, n_r = len(plants), len(river)
    prem = np.zeros(n_p + n_r)
    prem[n_p:] = river_premium
    for j, p in enumerate(plants):
        prem[j] = (plant_premium or {}).get(p["key"], 0.0)
    cost = miles * rate - prem[None, :]
    cost = np.where(np.isnan(cost), 1e6, cost)
    K = np.exp(-(cost - cost.min(axis=1, keepdims=True)) / max(tau, 1e-3))      # per-row shift cancels in the row scaling

    total, plant_need = supply.sum(), need.sum()
    scale = min(1.0, total / plant_need) if plant_need else 1.0               # short of beans: every plant fills to the same share
    b = need * scale
    residual = max(0.0, total - plant_need)
    if nodes is not None:
        groups = _groups(inputs, nodes, n_p, lock_scale, area_mbu or {}, river_scale, {"pnw": pnw_scale, "txgulf": txg_scale, "mexico": mex_scale})
        want = sum(g["target_bu"] for g in groups)
        tscale = min(1.0, residual / want) if want else 1.0                   # targets can't exceed what is left after crush
        for g in groups:
            g["target_bu"] *= tscale
        river_total = sum(g["target_bu"] for g in groups)
        rs = load_rail_service()
        rates = {"pnw": pnw_rate, "txgulf": txg_rate, "mexico": mex_rate}
        rail_cols = {kind: [c for g in groups if g["kind"] == kind for c in g["cols"]] for kind in RAIL_KINDS}
        for kind, cols_k in rail_cols.items():
            if cols_k and rates[kind] is not None:                             # rail, not truck, freight on the way to the coast or the border
                cost[:, cols_k] = np.where(np.isnan(miles[:, cols_k]), 1e6, miles[:, cols_k] * rates[kind] - prem[cols_k][None, :])
        if any(rail_cols[k] and rates[k] is not None for k in RAIL_KINDS):
            K = np.exp(-(cost - cost.min(axis=1, keepdims=True)) / max(tau, 1e-3))
        for kind, cols_k in rail_cols.items():
            if cols_k:
                served = np.array([min(rs.get(c["fips"], [9999, 9999, 9999])[k] for k in RAIL_KINDS[kind]) <= pnw_reach_miles for c in cty])
                K[np.ix_(~served, cols_k)] = 0.0                               # only counties on a railroad that reaches the hub can ship to it
        st = np.array([c["state"] for c in cty])
        for g in groups:
            if g.get("origin_states"):                                         # a hub that only some states can feed
                K[np.ix_(~np.isin(st, g["origin_states"]), g["cols"])] = 0.0
            if g.get("origin_states_mbu"):                                     # a hub whose draw by state is observed: the solver is held to it below
                tot = sum(g["origin_states_mbu"].values())
                g["state_target_bu"] = {s_: v / tot * g["target_bu"] for s_, v in g["origin_states_mbu"].items()}
                K[np.ix_(~np.isin(st, list(g["state_target_bu"])), g["cols"])] = 0.0
        K0 = K.copy()
        state_groups = [g for g in groups if g.get("state_target_bu")]
        for g in state_groups:
            g["state_mult"] = {s_: 1.0 for s_ in g["state_target_bu"]}
        for g in groups:                                                       # a site listed twice counts once within its group (same `site` id, or the same coordinates when none is given)
            xy = {}
            for j in g["cols"]:
                r_ = river[j - n_p]
                xy.setdefault(r_.get("site") or (round(r_["lat"], 3), round(r_["lon"], 3)), []).append(j)
            for js in xy.values():
                K[:, js] *= 1.0 / len(js)
        dead = np.ones(n_p + n_r, bool)
        dead[:n_p] = False
        for g in groups:
            dead[g["cols"]] = False
        K[:, dead] = 0.0                                                       # pins in no group (inland / mis-placed) take nothing
    else:
        groups, tscale = None, 1.0
        river_total = residual * river_share
    other_total = residual - river_total
    if other_total > 0:                                                        # the non-river, non-crush beans leave every county pro rata
        supply = supply * (1 - other_total / total)
        total = supply.sum()
    caps = np.full(n_p + n_r, np.inf)
    for j, r_ in enumerate(river):
        c_ = (pin_cap_mbu or {}).get(r_["key"], r_.get("cap_mbu"))
        if c_ is not None:
            caps[n_p + j] = c_ * 1e6
    u = np.ones(len(cty))
    v = np.ones(n_p + n_r)
    for _ in range(iters):
        u = supply / np.maximum(K @ v, 1e-300)
        colsum = K.T @ u
        v[:n_p] = b / np.maximum(colsum[:n_p], 1e-300)
        if groups is not None:
            for g in groups:
                v[g["cols"]] = _fill(colsum[g["cols"]], g["target_bu"], caps[g["cols"]])
            for g in state_groups:                                             # nudge each state's pull toward its observed share of the hub
                f = u * (K[:, g["cols"]] @ v[g["cols"]])
                for s_, tgt_s in g["state_target_bu"].items():
                    now = f[st == s_].sum()
                    if now > 0:
                        m = float(np.clip(tgt_s / now, 0.5, 2.0))
                        g["state_mult"][s_] = float(np.clip(g["state_mult"][s_] * m, 1e-6, 1e6))
                        K[np.ix_(st == s_, g["cols"])] *= m
        else:
            rv = colsum[n_p:] @ np.ones(n_r) if n_r else 0.0
            if n_r:
                v[n_p:] = (river_total / max(rv, 1e-300)) if river_total > 0 else 0.0
    flow = u[:, None] * K * v[None, :]
    out = {"flow": flow, "supply": supply, "need": need, "scale": scale, "river_total": river_total, "other_total": other_total,
           "miles": miles, "cost": cost}
    if groups is not None:
        out["groups"] = [{"name": g["name"], "kind": g["kind"], "hub": g["hub"], "anchor": g.get("anchor"), "cols": g["cols"], "target_bu": g["target_bu"],
                          "drawn_bu": float(flow[:, g["cols"]].sum())} for g in groups]
        out["target_scale"] = tscale
    return out


def _fill(c, target, cap):
    """Scaling factors v for one group's pins so each pin's flow is v * c, the group adds to `target`, and no pin exceeds its cap.
    Capped pins are pinned at their cap and the balance is shared by the others in proportion to c."""
    v = np.zeros(len(c))
    free = np.ones(len(c), bool)
    for _ in range(len(c)):
        rem = target - cap[~free].sum()
        s = c[free].sum()
        if s <= 0 or rem <= 0:
            break
        lam = rem / s
        over = free & (lam * c > cap)
        if not over.any():
            v[free] = lam
            break
        free &= ~over
    v[~free] = cap[~free] / np.maximum(c[~free], 1e-300)
    return v


def _groups(inputs, nodes, n_p, lock_scale, area_mbu, river_scale=1.0, rail_scale=None):
    """Group columns (index on the buyer axis) and targets (bu) from river_nodes.json plus the sliders."""
    col = {r["key"]: n_p + j for j, r in enumerate(inputs["river"])}
    out = []
    for g in nodes["groups"]:
        cols = [col[k] for k in g["pins"] if k in col]
        if not cols:
            continue
        if g["kind"] == "lock":
            tgt = g["target_mbu"] * lock_scale * river_scale
        elif g["kind"] in RAIL_KINDS:
            tgt = area_mbu.get(g["name"], g["target_mbu"]) * (rail_scale or {}).get(g["kind"], 1.0)
        else:
            tgt = area_mbu.get(g["name"], g["target_mbu"]) * river_scale
        out.append({"name": g["name"], "kind": g["kind"], "hub": g.get("hub", g["name"]), "anchor": g.get("anchor"), "cols": cols, "target_bu": tgt * 1e6,
                    "origin_states": g.get("origin_states"), "origin_states_mbu": g.get("origin_states_mbu")})
    return out


def summarize(inputs: dict, res: dict, state="IL"):
    """Plant table, river terminal table and county table, for the state's beans only where asked."""
    cty, plants, river = inputs["counties"], inputs["plants"], inputs["river"]
    n_p = len(plants)
    flow, miles = res["flow"], res["miles"]
    in_state = np.array([state in (None, "US") or c["state"] == state for c in cty])
    rows = []
    for j, p in enumerate(plants):
        f = flow[:, j]
        got = f.sum()
        rows.append({"plant": p["key"], "state": p["state"], "need_mbu": res["need"][j] / 1e6, "drawn_mbu": got / 1e6,
                     "from_state_mbu": f[in_state].sum() / 1e6,
                     "avg_miles": float(np.nansum(f * miles[:, j]) / got) if got else 0.0,
                     "river_access": p.get("river", "")})
    rr = []
    for j, r in enumerate(river):
        f = flow[:, n_p + j]
        got = f.sum()
        rr.append({"terminal": r["key"], "state": r["state"], "drawn_mbu": got / 1e6, "from_state_mbu": f[in_state].sum() / 1e6,
                   "avg_miles": float(np.nansum(f * miles[:, n_p + j]) / got) if got else 0.0})
    cc = []
    for i, c in enumerate(cty):
        f = flow[i]
        top = int(np.argmax(f))
        label = plants[top]["key"] if top < n_p else "River: " + river[top - n_p]["key"]
        cc.append({"fips": c["fips"], "county": f'{c["name"]}, {c["state"]}', "state": c["state"], "supply_mbu": c["supply_bu"] / 1e6,
                   "top_buyer": label, "top_is_river": top >= n_p, "to_plants_pct": float(f[:n_p].sum() / max(f.sum(), 1e-9) * 100),
                   "top_share_pct": float(f[top] / max(f.sum(), 1e-9) * 100)})
    return rows, rr, cc


def group_table(inputs: dict, res: dict, state="IL"):
    """Rows for the river nodes: annual target, drawn, and how much of it comes from the state's counties."""
    cty = inputs["counties"]
    in_state = np.array([state in (None, "US") or c["state"] == state for c in cty])
    return [{"node": g["name"], "kind": g["kind"], "hub": g.get("hub", g["name"]), "target_mbu": g["target_bu"] / 1e6, "drawn_mbu": g["drawn_bu"] / 1e6,
             "from_state_mbu": float(res["flow"][in_state][:, g["cols"]].sum()) / 1e6} for g in res.get("groups", [])]


def hub_table(inputs: dict, res: dict, state="IL"):
    """One row per hub: the soybeans the model sends it (all states), and the part that comes from the selected state."""
    rows = {}
    for t in group_table(inputs, res, state):
        h = rows.setdefault(t["hub"], {"hub": t["hub"], "target_mbu": 0.0, "drawn_mbu": 0.0, "from_state_mbu": 0.0})
        h["target_mbu"] += t["target_mbu"]
        h["drawn_mbu"] += t["drawn_mbu"]
        h["from_state_mbu"] += t["from_state_mbu"]
    return sorted(rows.values(), key=lambda r: -r["drawn_mbu"])


def county_hub_flows(inputs: dict, res: dict) -> dict:
    """{hub: array of bushels per county} summed over the hub's groups."""
    out = {}
    for g in res.get("groups", []):
        f = res["flow"][:, g["cols"]].sum(axis=1)
        out[g["hub"]] = out[g["hub"]] + f if g["hub"] in out else f
    return out

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
CENTS_PER_BU_MILE = 0.02            # Kolten's NY note: 3 cents/bu for 152 miles


@lru_cache(maxsize=1)
def load(path: str = INPUTS) -> dict:
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


def solve(inputs: dict, *, rate=CENTS_PER_BU_MILE, tau=1.0, river_premium=0.0, plant_premium=None, util=1.0, river_share=1.0, iters=400):
    """Flows (bu) county x buyer.

    rate           freight, cents per bushel-mile
    tau            crossover softness, cents/bu: smaller = sharper territories, larger = more overlap
    river_premium  cents/bu the river terminals pay over the crush plants (net of their own basis), applied to every terminal
    plant_premium  {plant key: cents/bu} per-plant bid advantage
    util           scales every plant's need (1.0 = the workbook's 88% x 350 days)
    river_share    share of the beans left after crush that move through the river terminals; the rest (feed, rail export to the coast,
                   Mexico, stocks) is taken out of every county pro rata
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
    river_total = residual * river_share
    other_total = residual - river_total
    if other_total > 0:                                                        # the non-river, non-crush beans leave every county pro rata
        supply = supply * (1 - other_total / total)
        total = supply.sum()
    u = np.ones(len(cty))
    v = np.ones(n_p + n_r)
    for _ in range(iters):
        u = supply / np.maximum(K @ v, 1e-300)
        colsum = K.T @ u
        v[:n_p] = b / np.maximum(colsum[:n_p], 1e-300)
        rv = colsum[n_p:] @ np.ones(n_r) if n_r else 0.0
        if n_r:
            v[n_p:] = (river_total / max(rv, 1e-300)) if river_total > 0 else 0.0
    flow = u[:, None] * K * v[None, :]
    return {"flow": flow, "supply": supply, "need": need, "scale": scale, "river_total": river_total, "other_total": other_total,
            "miles": miles, "cost": cost}


def summarize(inputs: dict, res: dict, state="IL"):
    """Plant table, river terminal table and county table, for the state's beans only where asked."""
    cty, plants, river = inputs["counties"], inputs["plants"], inputs["river"]
    n_p = len(plants)
    flow, miles = res["flow"], res["miles"]
    in_state = np.array([c["state"] == state for c in cty])
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

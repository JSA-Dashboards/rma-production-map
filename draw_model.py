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
INPUTS_CORN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "draw_inputs_corn.json")        # the corn model: same solver, other inputs
NODES_CORN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "river_nodes_corn.json")
RAIL_SERVICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "county_rail_service.json")
CENTS_PER_BU_MILE = 0.02            # Kolten's NY note: 3 cents/bu for 152 miles. Superseded by the AMS truck curve below (10-25x higher); kept only as the fallback when no curve is given
TRUCK_RATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw", "truck_rates_ams.json")
MPG = 6.0                           # loaded semi, for the fuel adjustment to today's diesel
CIRC_TRACK = 1.244                  # straight-line miles to track to road miles, the same circuity the miles matrix uses


@lru_cache(maxsize=1)
def load_truck_rates(path: str = TRUCK_RATES) -> dict:
    """USDA AMS quarterly truck rates ($ per loaded mile at 25, 100 and 200 miles, by region) and diesel, from build_truck_rates.py."""
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


def truck_curve(rates: dict, region: str = "National", diesel: float | None = None, bu_per_load: float = 900.0, mult: float = 1.0, mpg: float = MPG) -> dict:
    """Truck freight as a cost curve in cents per bushel by one-way road miles, from AMS's survey: a truckload costs rate x miles at 25, 100 and 200 miles (the rate per mile falls
    with distance, so the cost is concave and has a fixed element of about $90 a load: loading, unloading and waiting). Between the anchors it is linear, below 25 it runs back along the
    25-100 mile slope, beyond 200 it keeps the 100-200 slope. `diesel` adds the fuel the survey does not yet show: (diesel - the AMS quarters' diesel) / mpg, dollars per loaded mile.
    `bu_per_load` is about 900 for corn (56 lb) and 830 for soybeans (60 lb); `mult` scales the whole curve (a region or a carrier you know is dearer)."""
    r = rates["regions"][region]
    t25, t100, t200 = float(r["25"]) * 25, float(r["100"]) * 100, float(r["200"]) * 200
    s_short = (t100 - t25) / 75.0
    t0 = max(0.0, t25 - s_short * 25)
    s_long = max((t200 - t100) / 100.0, 0.5)
    fuel = 0.0 if diesel is None else (float(diesel) - float(rates["diesel_base"])) / mpg
    return {"miles": [0.0, 25.0, 100.0, 200.0], "usd": [t0, t25, t100, t200], "slope_far": s_long, "fuel": fuel, "bu": float(bu_per_load), "mult": float(mult)}


def truck_cents(miles, curve: dict):
    """Cents per bushel to truck `miles` (an array) under truck_curve()."""
    m = np.asarray(miles, float)
    usd = np.interp(m, curve["miles"], curve["usd"]) + np.maximum(m - 200.0, 0.0) * curve["slope_far"] + curve["fuel"] * m
    return usd * curve["mult"] / curve["bu"] * 100.0


@lru_cache(maxsize=4)
def load(path: str = INPUTS) -> dict:
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_rail_service(path: str = RAIL_SERVICE) -> dict:
    """{county fips: [miles to BNSF, UP, CPKC track]} from build_county_rail_service.py."""
    with open(path, encoding="utf8") as fh:
        return json.load(fh)["counties"]


@lru_cache(maxsize=4)
def load_nodes(path: str = NODES) -> dict:
    """River pin groups with annual soybean targets, from build_river_segments.py (lock tonnage + Gulf inspections)."""
    with open(path, encoding="utf8") as fh:
        return json.load(fh)


# Hubs the model feeds by rail rather than by barge: which railroads reach them (indices into county_rail_service.json's [BNSF, UP, CPKC, CN, CSXT, NS]).
# The Pacific Northwest is fed almost entirely by BNSF and UP; Texas Gulf and the Mexican border take BNSF, UP and CPKC (the former KCS, strong at Laredo);
# Louisiana's export elevators take UP, CPKC and CN (the old Illinois Central, the line down from Illinois and Iowa); Mobile takes CSX, CN and NS; the South Atlantic (Norfolk) takes CSX and NS.
RAIL_KINDS = {"pnw": (0, 1), "txgulf": (0, 1, 2), "mexico": (0, 1, 2), "lagulf": (1, 2, 3), "mobile": (3, 4, 5), "satl": (4, 5), "cont_east": (4, 5)}

# Crush plants fed by barge from upriver instead of (or as well as) county trucks, as a share of the plant's need. Bunge Destrehan takes the barge and
# stops it before export; ADM Quincy and Cargill Owensboro take some, from north of where they sit. A barge bean a plant takes is a bean the Gulf export
# elevators do not receive. Quincy and Owensboro are capped by the Corps (WCUS, soybeans, 2021-24 average): barge deliveries to ALL docks on the stretch, receipts
# plus movements within the waterway, are only 11.2M bu on the Mississippi from Minneapolis to the Missouri and 9.4M bu on the Ohio. Half of each is assumed to
# go to these two plants: 5.6M bu of Quincy's 75.5M need (7.5%) and 4.7M bu of Owensboro's 46.2M (10%). Destrehan (85M bu a year since it doubled) has no
# Corps figure of its own: Port of South Louisiana's receipts include Reserve and Convent.
BARGE_FED_DEFAULT = {"Bunge North America Destrehan LA": 1.0, "Archer Daniels Midland Quincy IL": 0.075, "Cargill Owensboro KY": 0.10}
# The river node whose lock-differenced loadings a plant's barge intake sits in: a barge bean offloaded between two locks is missing from the lower lock's
# flow, so loadings in that pool (lower flow less upper flow) come out short by the intake. Destrehan sits below every measured node, so it has none.
BARGE_POOL = {"Archer Daniels Midland Quincy IL": "MS-4", "Cargill Owensboro KY": "OH-2"}
EAST_GULF_KINDS = ("lagulf", "mobile")         # East Gulf beans that did not arrive by barge: scale with the East Gulf, like the barge targets


# Plants that start up partway through the period, as the month they begin crushing ("YYYY-MM"). The workbook gives a plant's full-year need; the model multiplies it by the
# share of the marketing year the plant actually runs (run_fraction). LDC Upper Sandusky: workbook status "New", monthly-rate cell "Fall 2026"; Kolten set February 2027 (10-30).
PLANT_START_DEFAULT = {"LDC Upper Sandusky OH": "2027-02"}


def run_fraction(start_ym: str, ramp_months: int, my: int) -> float:
    """Share of a full-rate marketing year (September `my` to August `my + 1`) a plant produces when it starts in `start_ym` and takes `ramp_months` to reach
    full rate (a linear ramp: month k of the ramp runs at (k + 0.5) / ramp_months). 0 before it starts, 1 once the whole year is past the ramp."""
    sy, sm = int(start_ym[:4]), int(start_ym[5:7])
    start_idx = (sy - my) * 12 + (sm - 9)                                  # months from the marketing year's September to the start month
    total = 0.0
    for i in range(12):
        k = i - start_idx
        total += 0.0 if k < 0 else (1.0 if ramp_months <= 0 or k >= ramp_months else (k + 0.5) / ramp_months)
    return total / 12.0


def solve(inputs: dict, *, rate=CENTS_PER_BU_MILE, tau=1.0, river_premium=0.0, plant_premium=None, util=1.0, river_share=1.0, iters=400,
          nodes=None, lock_scale=1.0, area_mbu=None, pin_cap_mbu=None, river_scale=1.0, pnw_reach_miles=30.0, rail_scales=None, rail_rates=None, barge_fed=None, rail_tau=None, plant_extra_mbu=None, supply_total_bu=None, plant_run_fraction=None,
          truck=None, volume_response=0.0, volume_band=0.10):
    """Flows (bu) county x buyer.

    rate           freight, cents per bushel-mile: used only when `truck` is None
    truck          a truck_curve(): trucking cost in cents per bushel by road miles from USDA AMS rates (concave, diesel-adjusted). It prices every truck haul, and the truck
                   leg from a county to the rail loader that feeds a rail hub (so a county far from track is dearer to a rail hub, instead of a hard cutoff).
    volume_response  how much a buyer's volume moves per cent a bushel its bid is above the base (fraction: 0.01 = 1% of volume per cent), for the crush or ethanol plants (the
                   per-plant bid adjustments) and, with `river_premium`, the barge-loading targets. Capped at +/- `volume_band`. Without it a bid advantage does nothing: with
                   fixed volumes the solver absorbs any constant added to one buyer's cost. The swing comes out of, or goes into, 'other' (feed, stocks).
    volume_band    the cap on that swing, as a fraction of the volume (0.10 = +/-10%)
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
    rail_scales    {"pnw" | "txgulf" | "mexico": factor} for the rail-fed export hubs (West Coast, West Gulf, Mexico vs their 2021-25 averages); the East Gulf
                   rail hubs ("lagulf", "mobile") follow river_scale
    rail_rates     {rail kind: cents per bushel-mile} freight for the rail haul to each rail-fed hub (a missing kind = the truck `rate`)
    pnw_reach_miles  a county sends beans to a rail-fed hub only if track of a railroad that serves it (RAIL_KINDS) is within this many miles of its
                   centroid (data/draw/county_rail_service.json); applies to every rail-fed hub
    rail_tau       {rail kind: crossover softness, cents/bu} for a rail-fed hub that should spread over more origins than `tau` allows (None = tau).
                   Freight alone sends a hub's beans from the nearest rail counties; basis differences between origin states offset freight differences
                   of 20-30 cents, so the hubs fed from many states (Louisiana, Mobile) need a wider crossover
    plant_run_fraction  {plant key: share of the period it runs, 0-1}: a plant that starts up partway (PLANT_START_DEFAULT, run_fraction) crushes that share of its need
    plant_extra_mbu  {plant key: M bu/yr} soybeans a plant crushes beyond the workbook (a plant that expanded since): added to its need before the
                   barge-fed share is taken
    supply_total_bu  total soybeans the counties supply, in bushels (None = the sum of the counties' 5-year average production). The tab passes production
                   (5-year average or USDA's) plus the carry-in stocks and imports; every county's supply is scaled pro rata, since stocks sit where beans
                   are grown and nothing here records where imports enter
    barge_fed      {plant key: share of its need that arrives by barge} (None = BARGE_FED_DEFAULT). That part is not drawn from counties by truck: it comes
                   out of the barge loadings, so it is taken off what reaches the export elevators and, for a plant with a BARGE_POOL, added back to that
                   pool's loadings. The Louisiana rail and truck hub's target is an explicit estimate (LA_RAIL_TRUCK_MBU in build_river_segments.py)
    pin_cap_mbu    {river pin key: M bu/yr} ceilings, on top of any `cap_mbu` set on the pin in the inputs. A capped pin takes at most its cap
                   and the rest of its group's target spreads over the group's other pins (a ceiling, not a target)
    """
    cty, plants, river = inputs["counties"], inputs["plants"], inputs["river"]
    miles = np.array([[np.nan if m is None else m for m in row] for row in inputs["miles"]], float)
    supply = np.array([c["supply_bu"] for c in cty], float)
    supply_scale = float(supply_total_bu) / supply.sum() if supply_total_bu else 1.0
    supply = supply * supply_scale
    need = np.array([p["need_bu"] for p in plants], float) * util
    if volume_response:                                                                       # a plant with a better bid than the base takes more, within the band
        need = need * np.array([1.0 + np.clip(volume_response * (plant_premium or {}).get(p["key"], 0.0), -volume_band, volume_band) for p in plants])
    need = need * np.array([min(max(float((plant_run_fraction or {}).get(p["key"], 1.0)), 0.0), 1.0) for p in plants])
    need = need + np.array([float((plant_extra_mbu or {}).get(p["key"], 0.0)) * 1e6 for p in plants])
    bf = BARGE_FED_DEFAULT if barge_fed is None else barge_fed
    barge_in = need * np.array([min(max(float(bf.get(p["key"], 0.0)), 0.0), 1.0) for p in plants])      # beans these plants take off the barges
    need = need - barge_in
    n_p, n_r = len(plants), len(river)
    prem = np.zeros(n_p + n_r)
    prem[n_p:] = river_premium
    for j, p in enumerate(plants):
        prem[j] = (plant_premium or {}).get(p["key"], 0.0)
    cost = (truck_cents(miles, truck) if truck else miles * rate) - prem[None, :]
    cost = np.where(np.isnan(cost), 1e6, cost)
    K = np.exp(-(cost - cost.min(axis=1, keepdims=True)) / max(tau, 1e-3))      # per-row shift cancels in the row scaling

    total, plant_need = supply.sum(), need.sum()
    scale = min(1.0, total / plant_need) if plant_need else 1.0               # short of beans: every plant fills to the same share
    b = need * scale
    residual = max(0.0, total - plant_need)
    if nodes is not None:
        groups = _groups(inputs, nodes, n_p, lock_scale, area_mbu or {}, river_scale, {**{k: river_scale for k in EAST_GULF_KINDS}, **(rail_scales or {})})
        for j, p in enumerate(plants):
            if barge_in[j] <= 0:
                continue
            pool = BARGE_POOL.get(p["key"])
            tgt = [g for g in groups if g["kind"] == "lock" and pool and g["name"].startswith(pool)]
            if tgt:                                                            # the intake was loaded upstream and missing from this pool's lock-differenced flow
                tgt[0]["target_bu"] += barge_in[j]                             # (a plant with no pool, Destrehan, only stops trucking county beans)
        if volume_response:                                                                   # the river terminals' bid over the plants moves the barge-loading targets
            rv = 1.0 + float(np.clip(volume_response * river_premium, -volume_band, volume_band))
            for g in groups:
                if g["kind"] in ("lock", "area"):
                    g["target_bu"] *= rv
        want = sum(g["target_bu"] for g in groups)
        tscale = min(1.0, residual / want) if want else 1.0                   # targets can't exceed what is left after crush
        for g in groups:
            g["target_bu"] *= tscale
        river_total = sum(g["target_bu"] for g in groups)
        rs = load_rail_service()
        rates = {k: (rail_rates or {}).get(k) for k in RAIL_KINDS}
        rail_cols = {kind: [c for g in groups if g["kind"] == kind for c in g["cols"]] for kind in RAIL_KINDS}
        track = {kind: np.array([min(rs.get(c["fips"], [9999] * 6)[k] for k in RAIL_KINDS[kind]) for c in cty]) for kind in RAIL_KINDS}    # miles to the nearest serving track
        for kind, cols_k in rail_cols.items():
            if cols_k and rates[kind] is not None:                             # rail, not truck, freight on the way to the coast or the border, plus the truck leg to the loader
                leg = truck_cents(track[kind] * CIRC_TRACK, truck)[:, None] if truck else 0.0
                cost[:, cols_k] = np.where(np.isnan(miles[:, cols_k]), 1e6, miles[:, cols_k] * rates[kind] + leg - prem[cols_k][None, :])
        if any(rail_cols[k] and rates[k] is not None for k in RAIL_KINDS):
            K = np.exp(-(cost - cost.min(axis=1, keepdims=True)) / max(tau, 1e-3))
        for kind, cols_k in rail_cols.items():
            if cols_k and (rail_tau or {}).get(kind):
                K[:, cols_k] = np.exp(-(cost[:, cols_k] - cost.min(axis=1, keepdims=True)) / max(rail_tau[kind], 1e-3))
        for kind, cols_k in rail_cols.items():
            if cols_k:
                served = track[kind] <= pnw_reach_miles
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
    out = {"flow": flow, "supply": supply, "supply_scale": supply_scale, "need": need, "barge_in": barge_in, "scale": scale, "river_total": river_total, "other_total": other_total,
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
        elif g["kind"] in RAIL_KINDS or g["kind"] in ("container", "lakes", "canada"):
            key = "container" if g["kind"] in ("container", "cont_east") else g["kind"]          # all container hubs scale together
            tgt = area_mbu.get(g["name"], g["target_mbu"]) * (rail_scale or {}).get(key, 1.0)
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
                     "from_state_mbu": f[in_state].sum() / 1e6, "barge_mbu": float(res["barge_in"][j]) / 1e6 if "barge_in" in res else 0.0,
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

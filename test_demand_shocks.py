"""Demand shocks: a new plant is a new buyer with its own miles, an expansion is extra need, and both draw their corn from the counties around them."""
import numpy as np
import pytest

import draw_model as dm


@pytest.fixture(scope="module")
def corn():
    inp = dm.load(dm.INPUTS_CORN)
    nodes = dm.load_nodes(dm.NODES_CORN)
    ams = dm.load_truck_rates()
    truck = dm.truck_curve(ams, "North Central", ams["diesel_now"], 950)
    rates = {"pnw": 0.015, "txgulf": 0.05, "mexico": 0.045, "lagulf": 0.09, "mobile": 0.115, "satl": 0.064}
    kw = dict(tau=20.0, nodes=nodes, truck=truck, rail_rates=rates, pnw_reach_miles=60.0)
    return inp, kw


def _new_plant(inp, county_name="Hamilton County", state="IA", need_bu=40e6):
    c = next(c for c in inp["counties"] if c["state"] == state and c["name"] == county_name)
    return {"key": "New: Test (IA)", "state": state, "lat": c["lat"], "lon": c["lon"], "need_bu": need_bu, "city": county_name}


def test_new_plant_adds_a_column_between_plants_and_river_pins(corn):
    inp, _ = corn
    n_p = len(inp["plants"])
    inp2 = dm.with_new_plants(inp, [_new_plant(inp)])
    assert len(inp2["plants"]) == n_p + 1 and len(inp2["miles"][0]) == len(inp["miles"][0]) + 1
    assert inp2["miles"][0][:n_p] == inp["miles"][0][:n_p] and inp2["miles"][0][n_p + 1:] == inp["miles"][0][n_p:]
    i = next(k for k, c in enumerate(inp["counties"]) if c["name"] == "Hamilton County" and c["state"] == "IA")
    assert inp2["miles"][i][n_p] <= 2                                         # the plant sits at that county's centre


def test_new_plant_fills_its_need_from_nearby_counties(corn):
    inp, kw = corn
    inp2 = dm.with_new_plants(inp, [_new_plant(inp)])
    res = dm.solve(inp2, **kw)
    n_p = len(inp["plants"])
    drawn = float(res["flow"][:, n_p].sum())
    assert drawn == pytest.approx(40e6, rel=0.02)
    miles = float(np.nansum(res["flow"][:, n_p] * res["miles"][:, n_p]) / drawn)
    assert miles < 200                                                        # in the middle of Iowa corn, where plants crowd each other: a short haul


def test_new_plant_takes_corn_from_domestic_use_and_neighbours(corn):
    inp, kw = corn
    base = dm.solve(inp, **kw)
    res = dm.solve(dm.with_new_plants(inp, [_new_plant(inp)]), **kw)
    n_p = len(inp["plants"])
    assert res["other_total"] < base["other_total"]                           # the plant's corn comes out of what was left for feed and stocks
    assert float(res["flow"][:, :n_p].sum()) < float(base["flow"][:, :n_p].sum()) + 1.0 + 40e6   # the existing plants still fill: 40M more in total, never less than before
    iowa = np.array([c["state"] == "IA" for c in inp["counties"]])
    assert res["flow"][iowa][:, :n_p].sum() <= base["flow"][iowa][:, :n_p].sum() + 1.0     # neighbours do not gain corn in Iowa; the new plant's draw is counted apart


def test_expansion_is_extra_need_on_an_existing_plant(corn):
    inp, kw = corn
    key = "Archer Daniels Midland Decatur IL"
    j = next(k for k, p in enumerate(inp["plants"]) if p["key"] == key)
    base = dm.solve(inp, **kw)
    res = dm.solve(inp, plant_extra_mbu={key: 25.0}, **kw)
    assert res["flow"][:, j].sum() - base["flow"][:, j].sum() == pytest.approx(25e6, rel=0.02)

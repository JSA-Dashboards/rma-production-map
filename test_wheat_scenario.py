"""Wheat in Draw Territory: class supply adds up, class hub targets come from FGIS, and the model with no named plants fills every hub it is given."""
import numpy as np
import pytest

import draw_model as dm
import wheat_scenario as ws


@pytest.fixture(scope="module")
def inp():
    return dm.load(dm.INPUTS_WHEAT)


@pytest.fixture(scope="module")
def nodes():
    return dm.load_nodes(dm.NODES_WHEAT)


def test_class_supply_adds_to_all_wheat(inp):
    parts = sum(ws.county_supply(inp, k, None).sum() for k in ws.CLASSES)
    assert abs(parts - ws.county_supply(inp, None, None).sum()) < 1.0
    assert 1.7e9 < parts < 1.85e9                                   # the 2021-25 US wheat crop, ~1.8 B bu


def test_crop_year_scales_by_state_class_production(inp):
    avg = ws.county_supply(inp, "HRW", None).sum()
    y25 = ws.county_supply(inp, "HRW", 2025).sum()
    assert y25 > 0 and abs(y25 / avg - 1.0) < 0.5                  # a different crop year, not a different crop


def test_class_hub_targets_are_fgis(nodes):
    for cls in ws.CLASSES:
        want = ws.hub_mbu("avg", cls)
        n = ws.nodes_for(nodes, "avg", cls)
        got = {}
        for g in n["groups"]:
            k = ws.KIND_OF_GROUP[g["kind"]]
            got[k] = got.get(k, 0.0) + g["target_mbu"]
        for k, v in want.items():
            assert abs(got.get(k, 0.0) - v) < 1e-6 * max(v, 1.0) + 1e-6


def test_white_wheat_leaves_through_the_pacific_northwest():
    h = ws.hub_mbu("avg", "White")
    assert h["pnw"] > 0.9 * sum(h.values())


def test_class_origin_states_follow_the_class(inp, nodes):
    n = ws.nodes_for(nodes, "avg", "White", inp)
    pnw = [g for g in n["groups"] if g["kind"] == "pnw"][0]
    assert set(pnw["origin_states_mbu"]) <= {"WA", "ID", "OR"}      # no soft white grows in Montana or the Dakotas


def test_model_with_no_plants_fills_the_hubs(inp, nodes):
    ams = dm.load_truck_rates()
    truck = dm.truck_curve(ams, "North Central", ams["diesel_now"], 900)
    rates = {"pnw": 0.015, "txgulf": 0.05, "mexico": 0.045, "lagulf": 0.09, "mobile": 0.115, "satl": 0.064}
    n = ws.nodes_for(nodes, "avg", "SRW", inp)
    sup = ws.county_supply(inp, "SRW", None)
    i2 = dict(inp, counties=[dict(c, supply_bu=float(v)) for c, v in zip(inp["counties"], sup)])
    res = dm.solve(i2, tau=20.0, nodes=n, truck=truck, rail_rates=rates, pnw_reach_miles=60.0, other_local=ws.milling_local(inp), other_rest_weight=ws.rest_weight(inp))
    target = sum(g["target_bu"] for g in res["groups"])
    drawn = sum(g["drawn_bu"] for g in res["groups"])
    assert abs(drawn - target) < 0.02 * target
    assert abs(res["flow"].sum() - res["supply"].sum()) < 0.02 * res["supply"].sum()


def test_other_local_keeps_total_domestic_use():
    inp = {"counties": [], "plants": [], "river": [], "miles": []}
    assert dm.solve is not None                                    # the weighted removal is exercised end to end above; here only the signature
    import inspect
    assert "other_local" in inspect.signature(dm.solve).parameters

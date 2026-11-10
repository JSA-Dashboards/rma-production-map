"""The all-classes wheat view is one solve made of the class runs: every class keeps its own supply, hub targets and mill needs, and the totals add up."""
import numpy as np
import pytest

import draw_model as dm
import draw_tab as dt
import flour_mills as fm
import wheat_scenario as ws

CSV = "\n".join([
    "state,city,company,multi_company,cap_cwt_day,storage_bu,classes,rail,page,lat,lon",
    "KS,Alphaville,Test Milling A,1,40000,900000,Hard red winter,BNSF;UP,1,38.00,-98.00",
    "OH,Betaton,Test Milling B,1,30000,,Soft red,NS,2,40.50,-83.00",
    "ND,Gammaburg,Test Milling C,1,20000,,Hard spring,BNSF,3,47.00,-97.00",
    "WA,Deltaville,Test Milling D,1,10000,100000,Soft white,UP,4,47.20,-120.00",
]) + "\n"


@pytest.fixture
def mills(tmp_path, monkeypatch):
    plain = tmp_path / "m.csv"
    plain.write_text(CSV, encoding="utf-8")
    monkeypatch.setattr(fm, "PLAIN", str(plain))
    fm.load.cache_clear()
    yield
    fm.load.cache_clear()


def test_class_runs_add_up_to_usda_food_use_and_fgis_hub_targets(mills):
    ams = dm.load_truck_rates()
    truck = dm.truck_curve(ams, "North Central", ams["diesel_now"], 900)
    inp = dm.load(dt.PATHS["wheat"][0])
    nodes = dm.load_nodes(dt.PATHS["wheat"][1])
    kw = dict(rate=0.0, tau=20.0, river_premium=0.0, util=1.0, lock_scale=1.0, area_mbu={}, pin_cap_mbu={}, plant_premium={}, river_scale=1.0, rail_scales={}, rail_rates={},
              pnw_reach_miles=60.0, barge_fed={}, rail_tau={}, plant_extra_mbu={}, plant_run_fraction={}, truck=truck, volume_response=0.0, volume_band=0.10)
    res = dt._solve_wheat_combined(inp, nodes, kw, "avg", None, 0.12)
    n_p = len(ws.mill_plants("avg", None))
    flow = res["flow"]
    assert flow[:, :n_p].sum() == pytest.approx(sum(ws.mill_controls("avg").values()), rel=1e-3)      # the mills grind USDA's food use by class, in the total
    for cls, d in res["by_class"].items():
        mills_cls = d["flow"][:, :n_p].sum()
        assert mills_cls == pytest.approx(ws.mill_controls("avg").get(cls, 0.0), rel=1e-3)             # and class by class (durum has no mills here)
    assert flow[:, n_p:].sum() <= sum(ws.hub_mbu("avg", None).values()) * 1e6 * 1.001                   # hubs never get more than FGIS inspected
    assert 0.0 < res["target_scale"] <= 1.0
    grown = sum(sum(ws.county_supply(inp, c, None)) for c in ws.CLASSES)
    assert flow.sum() + res["other_total"] == pytest.approx(grown, rel=1e-3)                           # every bushel grown is placed or left to feed, seed and stocks


def test_spring_wheat_imports_credit_the_mills_and_free_us_spring_wheat_for_the_hubs(mills):
    ams = dm.load_truck_rates()
    truck = dm.truck_curve(ams, "North Central", ams["diesel_now"], 900)
    inp = dm.load(dt.PATHS["wheat"][0])
    nodes = dm.load_nodes(dt.PATHS["wheat"][1])
    kw = dict(rate=0.0, tau=20.0, river_premium=0.0, util=1.0, lock_scale=1.0, area_mbu={}, pin_cap_mbu={}, plant_premium={}, river_scale=1.0, rail_scales={}, rail_rates={},
              pnw_reach_miles=60.0, barge_fed={}, rail_tau={}, plant_extra_mbu={}, plant_run_fraction={}, truck=truck, volume_response=0.0, volume_band=0.10)
    n_p = len(ws.mill_plants("avg", None))
    base = dt._solve_wheat_combined(inp, nodes, kw, "avg", None, 0.12, 0.0)
    imp = 40e6
    with_imp = dt._solve_wheat_combined(inp, nodes, kw, "avg", None, 0.12, imp)
    drawn0, drawn1 = base["flow"][:, :n_p].sum(), with_imp["flow"][:, :n_p].sum()
    assert drawn0 - drawn1 == pytest.approx(imp, rel=1e-3)                                             # the mills draw that much less from US counties
    assert with_imp["by_class"]["HRS"]["flow"][:, :n_p].sum() == pytest.approx(ws.mill_controls("avg")["HRS"] - imp, rel=1e-3)
    assert with_imp["by_class"]["HRS"]["target_scale"] >= base["by_class"]["HRS"]["target_scale"]       # US spring wheat freed for the export hubs
    assert with_imp["flow"][:, n_p:].sum() >= base["flow"][:, n_p:].sum() - 1.0


def test_durum_goes_to_the_durum_mills_and_imported_durum_is_credited_to_them(mills, tmp_path, monkeypatch):
    durum = tmp_path / "d.csv"
    durum.write_text("state,city,company,multi_company,cap_cwt_day,storage_bu,classes,rail,page,lat,lon\n"
                     "ND,Pastaville,Test Durum A,1,20000,,Durum,BNSF,1,47.40,-99.10\n"
                     "MO,Semolina City,Test Durum B,1,30000,,Durum,UP,2,38.60,-90.20\n", encoding="utf-8")
    monkeypatch.setattr(fm, "DURUM_PLAIN", str(durum))
    fm.load_durum.cache_clear()
    ams = dm.load_truck_rates()
    truck = dm.truck_curve(ams, "North Central", ams["diesel_now"], 900)
    inp = dm.load(dt.PATHS["wheat"][0])
    nodes = dm.load_nodes(dt.PATHS["wheat"][1])
    kw = dict(rate=0.0, tau=20.0, river_premium=0.0, util=1.0, lock_scale=1.0, area_mbu={}, pin_cap_mbu={}, plant_premium={}, river_scale=1.0, rail_scales={}, rail_rates={},
              pnw_reach_miles=60.0, barge_fed={}, rail_tau={}, plant_extra_mbu={}, plant_run_fraction={}, truck=truck, volume_response=0.0, volume_band=0.10)
    plants = ws.mill_plants("avg", None)
    n_p = len(plants)
    is_durum = np.array([p["cls"] == "Durum mill" for p in plants])
    assert is_durum.sum() == 2
    imp = 35e6
    res = dt._solve_wheat_combined(inp, nodes, kw, "avg", None, 0.12, (("Durum", imp),))
    dflow = res["by_class"]["Durum"]["flow"][:, :n_p]
    assert dflow[:, ~is_durum].sum() == 0                                                                  # flour mills grind no durum
    assert dflow[:, is_durum].sum() == pytest.approx(ws.durum_control("avg") - imp, rel=1e-3)               # the durum mills draw what is left after the imports
    for cls in ws.MILL_CLASSES:
        assert res["by_class"][cls]["flow"][:, :n_p][:, is_durum].sum() == 0                                # and the durum mills grind no flour wheat
    fm.load_durum.cache_clear()

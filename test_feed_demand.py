"""Corn feed demand by county and the distillers grains credit: shares add up, totals tie to USDA, the credit is where the plants and the cattle are, the plant keeps its full grind."""
import numpy as np
import pytest

import draw_model as dm
import feed_demand as fd


@pytest.fixture(scope="module")
def inp():
    return dm.load(dm.INPUTS_CORN)


def test_census_shares_add_to_one_in_each_state(inp):
    for sp in ("HOGS", "CATTLE ON FEED", "OTHER CATTLE"):
        sh = fd.census_shares(inp["counties"], sp)
        by: dict = {}
        for c in inp["counties"]:
            by[c["state"]] = by.get(c["state"], 0.0) + sh.get(c["fips"], 0.0)
        assert all(abs(v - 1.0) < 1e-6 for v in by.values() if v > 0), sp
        assert sum(1 for v in by.values() if v > 0) >= 30, sp          # the county list is the corn counties, so the few states with no corn have none


def test_county_heads_add_to_the_state_inventory(inp):
    heads = fd.county_head(inp["counties"], 2024)
    inv = fd.state_inventory(2024, "HOGS")
    st = np.array([c["state"] for c in inp["counties"]])
    for s in ("IA", "MN", "NC"):
        assert abs(heads["HOGS"][st == s].sum() - inv[s]) < 1e-6 * inv[s] + 1.0
    assert 6.0e7 < sum(inv.values()) < 9.5e7                                # about 75 million hogs and pigs


def test_net_feed_ties_to_usda_feed_and_residual(inp):
    plants = inp["plants"]
    grind = np.array([p["need_bu"] for p in plants], float)
    fr = 5.5e9
    out = fd.county_feed(inp["counties"], 2024, plants, grind, fr)
    assert abs(out["net"].sum() / fr - 1.0) < 0.03                           # the county cap leaves a little credit unplaced, hence not exact
    assert 0.5e9 < out["credit"]["county"].sum() < 0.95e9                    # the credit is some 0.6-0.8 billion bushels nationally
    assert out["credit"]["county"].sum() + out["credit"]["export"] + out["credit"]["unplaced"] == pytest.approx(grind.sum() * fd.CORN_CREDIT, rel=1e-6)


def test_wet_credit_stays_near_the_plant_and_goes_to_cattle():
    counties = [{"fips": "A", "state": "NE", "lat": 41.0, "lon": -97.0}, {"fips": "B", "state": "NE", "lat": 41.0, "lon": -97.1},
                {"fips": "C", "state": "IA", "lat": 42.0, "lon": -91.0}]
    gross = {sp: np.zeros(3) for sp in fd.SPECIES}
    gross["CATTLE ON FEED"] = np.array([1e6, 1e6, 1e6])
    plants = [{"lat": 41.0, "lon": -97.05, "state": "NE"}]
    cr = fd.ddgs_credit(plants, [1e7], counties, gross, wet_share={"NE": 1.0}, cap=10.0)
    assert cr["wet"][:2].sum() == pytest.approx(1e7 * fd.CORN_CREDIT, rel=1e-9) and cr["wet"][2] == 0.0
    assert cr["export"] == 0.0


def test_dried_credit_loses_the_export_share():
    counties = [{"fips": "A", "state": "IA", "lat": 42.0, "lon": -93.0}]
    gross = {sp: np.zeros(1) for sp in fd.SPECIES}
    gross["HOGS"] = np.array([1e9])
    cr = fd.ddgs_credit([{"lat": 42.0, "lon": -93.0, "state": "IA"}], [1e8], counties, gross, wet_share={"IA": 0.0}, dried_export=0.33, cap=10.0)
    assert cr["county"][0] == pytest.approx(1e8 * fd.CORN_CREDIT * 0.67, rel=1e-9)
    assert cr["export"] == pytest.approx(1e8 * fd.CORN_CREDIT * 0.33, rel=1e-9)


def test_credit_is_capped_at_a_share_of_the_county_feed():
    counties = [{"fips": "A", "state": "IA", "lat": 42.0, "lon": -93.0}]
    gross = {sp: np.zeros(1) for sp in fd.SPECIES}
    gross["HOGS"] = np.array([1e6])
    cr = fd.ddgs_credit([{"lat": 42.0, "lon": -93.0, "state": "IA"}], [1e9], counties, gross, wet_share={"IA": 0.0}, dried_export=0.0)
    assert cr["county"][0] == pytest.approx(fd.CREDIT_CAP * 1e6)
    assert cr["unplaced"] > 0


def test_export_tonnage_ties_to_census():
    assert 10.5e6 < fd.default_export_tonnes() < 12.5e6                        # Census HS 2303.30, 2023-25 average
    counties = [{"fips": "A", "state": "IA", "lat": 42.0, "lon": -93.0}]
    gross = {sp: np.zeros(1) for sp in fd.SPECIES}
    gross["HOGS"] = np.array([1e10])
    plants = [{"lat": 42.0, "lon": -93.0, "state": "IA"}]
    want = fd.export_credit_bu(fd.default_export_tonnes())
    cr = fd.ddgs_credit(plants, [5.6e9], counties, gross, wet_share={"IA": 0.0}, export_bu=want, cap=10.0)
    assert cr["export"] == pytest.approx(want, rel=1e-9)
    assert cr["county"][0] == pytest.approx(5.6e9 * fd.CORN_CREDIT - want, rel=1e-9)

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


def test_regional_exports_pass_port_states_to_the_states_that_make_it():
    shares = {"LA": 0.4, "IL": 0.3, "IA": 0.3}                                   # Louisiana loads the ships but has no plant
    out = fd.allocate_exports(100.0, shares, {"IL": 1000.0, "IA": 1000.0}, max_frac=1.0)
    assert out["IL"] == pytest.approx(50.0) and out["IA"] == pytest.approx(50.0)   # Louisiana's 40% goes to the others in proportion to their own shares
    capped = fd.allocate_exports(100.0, shares, {"IL": 20.0, "IA": 1000.0}, max_frac=1.0)
    assert capped["IL"] == pytest.approx(20.0) and capped["IA"] == pytest.approx(80.0)
    assert sum(fd.allocate_exports(100.0, shares, {"IL": 10.0}, max_frac=0.5).values()) == pytest.approx(5.0)   # no room: what cannot be placed is left out


def test_census_state_shares_add_to_one_and_louisiana_is_big():
    sh = fd.export_state_shares()
    assert abs(sum(sh.values()) - 1.0) < 1e-6
    assert sh["IL"] > 0.2 and sh["LA"] > 0.1
    rt = fd.export_routes()
    assert abs(rt["container_t"] + rt["bulk_vessel_t"] + rt["land_t"] - rt["total_t"]) < 1.0


def test_big_plants_on_track_carry_the_export_and_small_remote_ones_stay_local():
    plants = [{"key": "big", "state": "IL", "lat": 39.8, "lon": -88.9}, {"key": "small", "state": "IL", "lat": 40.5, "lon": -89.5}]
    grind = np.array([300e6 / 2.9, 20e6 / 2.9])
    counties = [{"fips": "A", "state": "IL", "lat": 40.0, "lon": -89.0}]
    acc = fd.plant_export_propensity(plants, grind, counties, np.array([1e9]), track_mi=[1.0, 40.0], river_mi=[80.0, 80.0])
    assert acc["propensity"][0] > 0.6 and acc["propensity"][1] < 0.12
    assert acc["modes"][0, 0] > 0.8 and acc["modes"][1, 3] > 0.9                    # the big one ships by rail; the small one is local truck
    np.testing.assert_allclose(acc["modes"].sum(axis=1), 1.0)


def test_river_plants_lean_barge_and_chicago_plants_lean_containers():
    plants = [{"key": "river", "state": "IL", "lat": 41.3, "lon": -89.3}, {"key": "chi", "state": "IL", "lat": 41.85, "lon": -87.7}]
    grind = np.array([150e6 / 2.9, 150e6 / 2.9])
    counties = [{"fips": "A", "state": "IL", "lat": 40.0, "lon": -89.0}]
    acc = fd.plant_export_propensity(plants, grind, counties, np.array([1e9]), track_mi=[60.0, 60.0], river_mi=[0.5, 80.0])
    assert acc["modes"][0, 1] > acc["modes"][0, 0] and acc["modes"][0, 1] > 0.5       # at a river terminal, away from track
    assert acc["modes"][1, 2] > 0.5                                                  # in Chicago


def test_less_livestock_around_a_plant_means_more_leaves():
    plants = [{"key": "a", "state": "IA", "lat": 42.0, "lon": -93.0}, {"key": "b", "state": "IA", "lat": 45.0, "lon": -99.0}]
    grind = np.array([150e6 / 2.9, 150e6 / 2.9])
    counties = [{"fips": "1", "state": "IA", "lat": 42.0, "lon": -93.0}, {"fips": "2", "state": "SD", "lat": 45.0, "lon": -99.0}]
    acc = fd.plant_export_propensity(plants, grind, counties, np.array([5e9, 1e7]), track_mi=[1.0, 1.0], river_mi=[90.0, 90.0])
    assert acc["propensity"][1] > acc["propensity"][0]


def test_state_export_goes_to_the_plants_with_access():
    plants = [{"key": "big", "state": "IL", "lat": 39.8, "lon": -88.9}, {"key": "small", "state": "IL", "lat": 40.5, "lon": -89.5}]
    grind = np.array([300e6 / 2.9, 100e6 / 2.9])
    counties = [{"fips": "A", "state": "IL", "lat": 40.0, "lon": -89.0}]
    gross = {sp: np.zeros(1) for sp in fd.SPECIES}
    gross["HOGS"] = np.array([5e9])
    want = 4e6
    cr = fd.ddgs_credit(plants, grind, counties, gross, wet_share={"IL": 0.0}, export_bu=want, export_shares={"IL": 1.0}, cap=10.0, track_mi=np.array([1.0, 40.0]), river_mi=np.array([80.0, 80.0]))
    ex = cr["export_plant"]
    assert ex.sum() == pytest.approx(want, rel=1e-6) and ex[0] > 4 * ex[1]


def test_credit_depends_on_the_animals_but_keeps_its_average():
    counties = [{"fips": "C", "state": "NE", "lat": 41.0, "lon": -97.0}, {"fips": "P", "state": "NE", "lat": 41.0, "lon": -97.1}]
    gross = {sp: np.zeros(2) for sp in fd.SPECIES}
    gross["CATTLE ON FEED"] = np.array([1e9, 0.0])
    gross["BROILERS"] = np.array([0.0, 1e9])
    plants = [{"lat": 41.0, "lon": -97.05, "state": "NE"}]
    flat = fd.ddgs_credit(plants, [1e8], counties, gross, wet_share={"NE": 0.0}, dried_export=0.0, cap=10.0)
    by = fd.ddgs_credit(plants, [1e8], counties, gross, wet_share={"NE": 0.0}, dried_export=0.0, cap=10.0, by_animal=True)
    assert by["displace_lb"][0] > by["displace_lb"][1]                          # a pound of DDGS replaces more corn with cattle than with broilers
    assert by["county"][0] > 5 * by["county"][1]                                 # and cattle country is where it is fed
    assert by["county"].sum() == pytest.approx(flat["county"].sum(), rel=1e-9) or by["county"].sum() < flat["county"].sum() * 1.001
    assert flat["displace_lb"][0] == pytest.approx(fd.CORN_CREDIT * 56 / 15.5)


def test_average_corn_per_pound_of_ddgs_stays_at_the_slider():
    import draw_model as dm
    inp = dm.load(dm.INPUTS_CORN)
    plants = inp["plants"]
    grind = np.array([p["need_bu"] for p in plants], float) * 0.95
    out = fd.county_feed(inp["counties"], 2024, plants, grind, 5.5e9, by_animal=True)
    cr = out["credit"]
    avg = float((cr["county"] * cr["displace_lb"]).sum() / cr["county"].sum())
    assert avg == pytest.approx(fd.CORN_CREDIT * 56 / 15.5, rel=0.06)

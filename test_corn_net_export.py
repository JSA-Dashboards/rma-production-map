"""Net exportable corn supply: supply less processing and feed, by state, district and county."""
import numpy as np
import pandas as pd
import pytest

import corn_net_export as cnx


@pytest.fixture
def demand(tmp_path, monkeypatch):
    f = tmp_path / "d.csv"
    pd.DataFrame({"year": [2025] * 4, "fips": ["19001", "19003", "19005", "31001"], "state": ["IA", "IA", "IA", "NE"], "ethanol_bu": [100.0, 0, 50.0, 30.0],
                  "feed_gross_bu": [20.0, 10, 10, 8], "ddgs_credit_bu": [2.0, 1, 1, 1], "feed_net_bu": [18.0, 9, 9, 7],
                  "feed_dairy_bu": [2.0, 1, 1, 1], "feed_beef_bu": [6.0, 3, 3, 2], "feed_hogs_bu": [4.0, 2, 2, 2], "feed_poultry_bu": [4.0, 2, 2, 1], "residual_bu": [4.0, 2, 2, 2]}).to_csv(f, index=False)
    monkeypatch.setattr(cnx, "DATA", str(f))
    cnx._all.cache_clear()
    yield
    cnx._all.cache_clear()


def test_state_table_nets_demand_off_supply_and_keeps_a_state_that_only_uses(demand):
    t = cnx.state_table({"IA": 500.0}, {"IA": 40.0}, 2025).set_index("State")
    assert t.loc["IA", "Supply"] == 540 and t.loc["IA", "Demand"] == 100 + 50 + 18 + 9 + 9 and t.loc["IA", "Net"] == pytest.approx(540 - 186)
    assert t.loc["NE", "Production"] == 0 and t.loc["NE", "Net"] == -(30 + 7)              # NE has demand but no production: its net is negative, not dropped
    assert t.loc["IA", "Net % of supply"] == pytest.approx(354 / 540 * 100)
    assert np.isnan(t.loc["NE", "Net % of supply"])


def test_county_table_spreads_stocks_by_production_and_keeps_the_demand_of_a_county_without_a_crop_row(demand):
    cty = pd.DataFrame({"fips": ["19001", "19003"], "County": ["Adair", "Adams"], "District": ["Central", "Central"], "DistrictCode": ["10", "10"], "Value": [300.0, 100.0]})
    ct = cnx.county_table(cty, "IA", 40.0, 2025, {"19005": "Southwest"}, {"19005": "Allamakee"}).set_index("fips")
    assert ct.loc["19001", "Stocks"] == pytest.approx(30.0) and ct.loc["19003", "Stocks"] == pytest.approx(10.0)       # in proportion to production
    assert ct.loc["19005", "Production"] == 0 and ct.loc["19005", "County"] == "Allamakee" and ct.loc["19005", "District"] == "Southwest"
    assert ct["Net"].sum() == pytest.approx(440 - (100 + 50 + 18 + 9 + 9))                                            # nothing is lost: all of Iowa's demand is in the table
    a = cnx.asd_table(ct.reset_index()).set_index("District")
    assert a.loc["Central", "Supply"] == 440 and a.loc["Southwest", "Net"] == -(50 + 9)
    assert a["Net"].sum() == pytest.approx(ct["Net"].sum())


def test_the_built_file_ties_to_usda_for_the_ethanol_and_feed_totals():
    import export_scenario as es
    bal = es.balance("corn")
    for y in cnx.years():
        d = cnx.county_demand(y)
        assert d["ethanol_bu"].sum() / 1e6 == pytest.approx(bal[str(y)]["ethanol"], rel=0.002)             # plants scaled to USDA's ethanol corn use
        assert d["feed_net_bu"].sum() / 1e6 == pytest.approx(bal[str(y)]["feed_residual"], rel=0.002)      # net feed ties to USDA feed and residual
        assert d["feed_gross_bu"].sum() >= d["feed_net_bu"].sum()


def test_balance_demand_splits_feed_by_animal_and_spreads_the_residual_either_way(demand):
    d = cnx.balance_demand(2025, ["19001", "19003"], "animals")
    assert (d["dairy"], d["beef"], d["hogs"], d["poultry"]) == (3.0, 9.0, 6.0, 6.0) and d["residual"] == 6.0
    assert d["ethanol"] == 100 and d["displaced"] == 3 and d["ddg_tons"] == pytest.approx(100 * 15.5 / 2000)
    assert d["dairy"] + d["beef"] + d["hogs"] + d["poultry"] + d["residual"] == 30.0                                   # the animals' feed and the residual are the county feed
    p = cnx.balance_demand(2025, ["19001"], "production", prod_area=25.0, prod_us=100.0)
    assert p["residual"] == pytest.approx(cnx.residual_total(2025) * 0.25) and cnx.residual_total(2025) == 10.0         # the national residual, by the area's share of production


def test_balance_table_has_the_years_across_and_supply_less_use_as_the_surplus(demand):
    by_year = {y: {"carry_in": 40.0e6, "planted": 5.0e6, "harvested": 4.5e6, "production": 800.0e6, **cnx.balance_demand(2025, ["19001", "19003"], "animals")} for y in (2025, 2026)}
    t = cnx.balance_table(by_year, 1e6, "M bu", 1e3, "K ac")
    assert list(t.columns) == ["Item", "2025/26", "2026/27"]
    row = dict(zip(t["Item"], t["2025/26"]))
    assert row["Yield (bu/ac)"] == "177.8" and row["Total supply (M bu)"] == "840.0"
    use = cnx._use(by_year[2025]) / 1e6
    assert float(row["Total use (M bu)"].replace(",", "")) == pytest.approx(use, abs=0.06)
    assert float(row["Supply less use (M bu)"].replace(",", "")) == pytest.approx(840.0 - use, abs=0.06)
    assert t.attrs["kinds"][-1] == "net"
    by_year[2026]["production"] = None                                                                                 # a year with no NASS production keeps its column, with dashes
    assert dict(zip(cnx.balance_table(by_year, 1e6, "M bu", 1e3, "K ac")["Item"], cnx.balance_table(by_year, 1e6, "M bu", 1e3, "K ac")["2026/27"]))["Production (M bu)"] == "-"


def test_the_built_file_splits_feed_into_animals_plus_residual():
    for y in cnx.years():
        d = cnx.county_demand(y)
        parts = d[["feed_dairy_bu", "feed_beef_bu", "feed_hogs_bu", "feed_poultry_bu", "residual_bu"]].sum(axis=1)
        assert (parts - d["feed_gross_bu"]).abs().max() < 5                                                            # rounding to whole bushels only
        assert d["residual_bu"].sum() > 0

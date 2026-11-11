"""Net exportable corn supply: supply less processing and feed, by state, district and county."""
import numpy as np
import pandas as pd
import pytest

import corn_net_export as cnx


@pytest.fixture
def demand(tmp_path, monkeypatch):
    f = tmp_path / "d.csv"
    pd.DataFrame({"year": [2025] * 4, "fips": ["19001", "19003", "19005", "31001"], "state": ["IA", "IA", "IA", "NE"], "ethanol_bu": [100.0, 0, 50.0, 30.0],
                  "feed_gross_bu": [20.0, 10, 10, 8], "ddgs_credit_bu": [2.0, 1, 1, 1], "feed_net_bu": [18.0, 9, 9, 7]}).to_csv(f, index=False)
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

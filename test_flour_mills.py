"""Flour mills as named plants: the list is read from an encrypted copy (never the clear text in the public repo), scaled to USDA's food use, and absent without its key."""
import numpy as np
import pytest
from cryptography.fernet import Fernet

import flour_mills as fm
import wheat_scenario as ws

CSV = "\n".join([
    "state,city,company,multi_company,cap_cwt_day,storage_bu,classes,rail,page,lat,lon",
    "KS,Alphaville,Test Milling A,1,20000,900000,Hard red winter,BNSF;UP,1,38.00,-98.00",
    "OH,Betaton,Test Milling B,1,30000,,Soft red,NS,2,40.50,-83.00",
    "NE,Gammaburg,Test Milling C,1,,,Hard red winter,,3,41.00,-97.00",
    "PR,Deltaville,Test Milling D,1,5000,100000,,,4,18.40,-66.00",
]) + "\n"


def test_encrypted_copy_round_trips(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    blob = tmp_path / "mills.enc"
    blob.write_bytes(fm.encrypt(CSV, key))
    assert b"Alphaville" not in blob.read_bytes()                                # nothing readable in the committed file
    monkeypatch.setattr(fm, "PLAIN", str(tmp_path / "none.csv"))
    monkeypatch.setattr(fm, "BLOB", str(blob))
    monkeypatch.setenv("MILLS_KEY", key)
    fm.load.cache_clear()
    got = fm.load()
    assert [m["city"] for m in got] == ["Alphaville", "Betaton", "Gammaburg", "Deltaville"]
    assert got[0]["cap_cwt_day"] == 20000 and got[2]["cap_cwt_day"] is None and got[0]["classes"] == ["Hard red winter"]
    fm.load.cache_clear()


def test_without_the_key_there_are_no_mills(tmp_path, monkeypatch):
    blob = tmp_path / "mills.enc"
    blob.write_bytes(fm.encrypt(CSV, Fernet.generate_key().decode()))
    monkeypatch.setattr(fm, "PLAIN", str(tmp_path / "none.csv"))
    monkeypatch.setattr(fm, "BLOB", str(blob))
    monkeypatch.setenv("MILLS_KEY", Fernet.generate_key().decode())            # the wrong key
    fm.load.cache_clear()
    assert fm.load() == []
    monkeypatch.delenv("MILLS_KEY")
    fm.load.cache_clear()
    assert fm.load() == []
    fm.load.cache_clear()


def test_mills_grind_usdas_food_use_and_skip_what_has_no_capacity(tmp_path, monkeypatch):
    plain = tmp_path / "m.csv"
    plain.write_text(CSV, encoding="utf-8")
    monkeypatch.setattr(fm, "PLAIN", str(plain))
    fm.load.cache_clear()
    mills = ws.mill_plants()
    assert [m["state"] for m in mills] == ["KS", "OH"]                         # Gammaburg reports no capacity, Deltaville is in Puerto Rico
    assert sum(m["need_bu"] for m in mills) == pytest.approx(sum(ws.mill_controls("avg").values()))   # USDA's food use less durum, not the mills' capacity
    assert mills[1]["need_bu"] / mills[0]["need_bu"] == pytest.approx(30000 / 20000)
    hrw = ws.mill_plants("avg", "HRW")
    assert sum(m["need_bu"] for m in hrw) == pytest.approx(ws.mill_controls("avg")["HRW"])
    assert ws.mill_plants("avg", "Durum") == []
    assert len({m["key"] for m in mills}) == 2
    fm.load.cache_clear()


def test_plant_rail_makes_a_long_haul_cheaper_only_for_plants_on_a_railroad():
    import draw_model as dm
    counties = [{"fips": "A", "state": "KS", "lat": 38.0, "lon": -98.0, "supply_bu": 1e8}]
    inputs = {"counties": counties, "plants": [{"key": "rail", "state": "OH", "lat": 41.0, "lon": -83.0, "need_bu": 1e7}, {"key": "norail", "state": "OH", "lat": 41.0, "lon": -83.1, "need_bu": 1e7}],
              "river": [], "miles": [[900, 900]]}
    ams = dm.load_truck_rates()
    truck = dm.truck_curve(ams, "North Central", ams["diesel_now"], 900)
    pr = {"rate": 0.12, "fixed": 15.0, "min_miles": 250.0, "reach": 60.0, "track": [5.0], "mask": [True, False]}
    base = dm.solve(inputs, truck=truck)["cost"]
    with_rail = dm.solve(inputs, truck=truck, plant_rail=pr)["cost"]
    assert with_rail[0, 0] < base[0, 0] and with_rail[0, 1] == base[0, 1]
    far = dict(pr, track=[200.0])                                              # a county 200 miles from track cannot load a railcar
    assert dm.solve(inputs, truck=truck, plant_rail=far)["cost"][0, 0] == base[0, 0]


def test_class_controls_leave_out_durum_and_match_usda_food_use():
    ctl = ws.mill_controls(2026)
    assert set(ctl) == set(ws.MILL_CLASSES)                                    # durum is milled elsewhere
    assert sum(ctl.values()) == pytest.approx(874e6, rel=1e-3)                  # WASDE 2026/27 food use 960 less durum 86
    assert ctl["HRW"] == pytest.approx(370e6)
    avg = ws.mill_controls("avg")
    assert 850e6 < sum(avg.values()) < 900e6


def test_mill_class_needs_add_up_to_the_class_controls_and_follow_capacity():
    mills = [{"cap_cwt_day": 40000, "state": "KS", "classes": []}, {"cap_cwt_day": 20000, "state": "KS", "classes": []},
             {"cap_cwt_day": 30000, "state": "OH", "classes": ["Soft red"]}, {"cap_cwt_day": 10000, "state": "WA", "classes": []}]
    ctl = {"HRW": 400e6, "HRS": 250e6, "SRW": 150e6, "White": 100e6}
    X = ws.mill_class_needs(mills, ctl)
    np.testing.assert_allclose(X.sum(axis=0), [ctl[c] for c in ws.MILL_CLASSES], rtol=1e-6)
    np.testing.assert_allclose(X.sum(axis=1), np.array([40, 20, 30, 10]) / 100 * 900e6, rtol=1e-3)
    share = X / X.sum(axis=1, keepdims=True)
    assert share[0, 0] > 0.6                                                    # a Kansas mill grinds mostly hard red winter
    assert share[2, 2] > share[0, 2] and share[3, 3] > share[0, 3]               # the Ohio mill leans soft red, the Washington mill soft white


def test_mills_that_list_no_railroad_are_on_one_when_big():
    plants = [{"rail": [], "cap_cwt_day": 15000}, {"rail": [], "cap_cwt_day": 800}, {"rail": ["UP"], "cap_cwt_day": 800}]
    assert ws.mill_plant_rail({"counties": [{"fips": "01001"}]}, plants)["mask"] == [True, False, True]


def test_imported_spring_wheat_is_credited_to_the_states_it_is_delivered_to_and_capped():
    states = ["NY", "NY", "OH", "ND", "KS"]
    hrs = np.array([10e6, 30e6, 20e6, 100e6, 5e6])                                # hard red spring grind of each mill
    shares = {"NY": 0.5, "OH": 0.3, "ND": 0.1, "MN": 0.1}                        # MN has no mill: its share goes on to the others
    cr = ws.mill_import_credit(states, hrs, 60e6, shares)
    assert cr.sum() == pytest.approx(60e6)                                        # all of it is placed
    assert cr[0] / cr[1] == pytest.approx(10 / 30)                               # inside a state, by the mill's spring wheat grind
    assert cr[4] == 0                                                              # Kansas is not on the list of delivery states
    assert (cr <= 0.9 * hrs + 1).all()                                             # no mill takes more than the cap
    assert cr[2] == pytest.approx(0.9 * 20e6)                                      # Ohio is full, so the surplus moves to New York and North Dakota
    small = ws.mill_import_credit(states, hrs, 1e12, shares)
    assert small.sum() == pytest.approx(0.9 * (hrs[:4].sum()))                    # more than the mills can use is not credited


def test_mills_take_their_credit_off_what_they_draw_from_counties(tmp_path, monkeypatch):
    plain = tmp_path / "m.csv"
    plain.write_text(CSV, encoding="utf-8")
    monkeypatch.setattr(fm, "PLAIN", str(plain))
    fm.load.cache_clear()
    gross = ws.mill_plants("avg", "HRS")
    net = ws.mill_plants("avg", "HRS", 20e6)
    assert sum(m["import_bu"] for m in net) == pytest.approx(20e6)
    for g, n in zip(gross, net):
        assert n["need_bu"] == pytest.approx(g["need_bu"] - n["import_bu"]) and n["class_need"] == g["class_need"]      # the gross grind is unchanged
    hrw = ws.mill_plants("avg", "HRW", 20e6)
    assert [m["need_bu"] for m in hrw] == pytest.approx([m["need_bu"] for m in ws.mill_plants("avg", "HRW")])            # only the spring wheat need is reduced
    fm.load.cache_clear()


def test_hrs_imports_follow_usda_by_class():
    assert 50e6 < ws.hrs_imports("avg") < 80e6
    assert ws.hrs_imports(2024) == pytest.approx(79e6)
    assert sum(ws.import_state_shares().values()) == pytest.approx(1.0)

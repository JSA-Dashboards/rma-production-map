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
    assert sum(m["need_bu"] for m in mills) == pytest.approx(ws.MILL_FOOD_USE_BU)
    assert mills[1]["need_bu"] / mills[0]["need_bu"] == pytest.approx(30000 / 20000)
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

"""Flour mills as named plants: the list is read from an encrypted copy (never the clear text in the public repo), scaled to USDA's food use, and absent without its key."""
import numpy as np
import pytest
from cryptography.fernet import Fernet

import flour_mills as fm
import wheat_scenario as ws

CSV = ("state,city,company,multi_company,cap_cwt_day,storage_bu,classes,rail,page,lat,lon\n"
       "KS,Wichita,Ardent Mills,1,27000,2500000,Hard red winter,BNSF;UP,129,37.69,-97.34\n"
       "OH,Toledo,Toledo Flour Mill,1,40000,,Soft red,NS,134,41.66,-83.56\n"
       "NE,Lincoln,ADM Milling Co.,1,,,Hard red winter,,131,40.81,-96.68\n"
       "PR,San Juan,Ardent Mills,1,10000,3000000,,,135,18.47,-66.11\n")


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

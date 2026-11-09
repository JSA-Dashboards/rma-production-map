"""
US wheat flour mills by facility, for the wheat Draw Territory model.

Source: Sosland Publishing, Grain & Milling Annual 2027, "Wheat Flour Mills / US" (printed pages 126-136): each mill with its daily flour capacity in cwt, storage, the railroads
that serve it and the wheat classes it lists. The book's notice bars reproduction without written permission (see data/wheat/FLOUR_MILLS_LICENSING.md), and this repository is public,
so the list is NOT stored in the clear: `build_flour_mills_blob.py` encrypts it into data/wheat/flour_mills.enc and the app decrypts it with the MILLS_KEY secret. With neither the
local file nor the key, load() returns [] and the model keeps its regional capacity stand-in.

Columns: state, city, company, multi_company, cap_cwt_day, storage_bu, classes (; separated), rail (; separated), page, lat, lon. A mill with no reported capacity has cap_cwt_day empty.
"""
from __future__ import annotations

import csv
import io
import os
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
PLAIN = os.path.join(HERE, "private", "flour_mills_gma2027.csv")          # on the machine that transcribed it; git-ignored
BLOB = os.path.join(HERE, "data", "wheat", "flour_mills.enc")


def _key() -> str:
    k = os.environ.get("MILLS_KEY", "")
    if k:
        return k
    try:
        import streamlit as st
        return str(st.secrets.get("MILLS_KEY", ""))
    except Exception:
        return ""


def _parse(text: str) -> list:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        out.append({"state": r["state"], "city": r["city"], "company": r["company"], "multi": r["multi_company"] == "1",
                    "cap_cwt_day": float(r["cap_cwt_day"]) if r["cap_cwt_day"] else None, "storage_bu": float(r["storage_bu"]) if r["storage_bu"] else None,
                    "classes": [c for c in r["classes"].split(";") if c], "rail": [c for c in r["rail"].split(";") if c], "page": r["page"],
                    "lat": float(r["lat"]) if r.get("lat") else None, "lon": float(r["lon"]) if r.get("lon") else None})
    return out


def encrypt(text: str, key: str) -> bytes:
    from cryptography.fernet import Fernet
    return Fernet(key.encode()).encrypt(text.encode("utf-8"))


@lru_cache(maxsize=1)
def load() -> list:
    """The mills, or [] when neither the local transcription nor the encrypted copy plus its key is available."""
    if os.path.exists(PLAIN):
        with open(PLAIN, encoding="utf-8", newline="") as fh:
            return _parse(fh.read())
    k = _key()
    if k and os.path.exists(BLOB):
        try:
            from cryptography.fernet import Fernet
            with open(BLOB, "rb") as fh:
                return _parse(Fernet(k.encode()).decrypt(fh.read()).decode("utf-8"))
        except Exception:
            return []
    return []

"""
Export scenario for the Draw Territory model: a US soybean export number (USDA's, or one you type) turned into bushels leaving the East Gulf and the
West Coast using the share history in data/draw/soy_export_shares_by_my.csv, then into scale factors on the river targets.

The model's river volumes are built on the 2021-25 average (BASE_YEARS). A scenario that has the East Gulf loading 10% more than that average scales
every barge-loading target by 1.10; the West Coast ratio scales the Pacific Northwest rail node. Everything else (feed, rail to the Gulf, stocks) is the
residual, so it absorbs the rest.

Pure pandas / numpy; build_soy_export_shares.py refreshes the two data files.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draw")
BASE_YEARS = (2021, 2025)                  # marketing years the river targets are built on (2021/22 ... 2025/26)
BASES = ["Last 5 years' share", "Last 10 years' share", "Since 2011 share", "Marginal (moves with the change from the 2021-25 average)",
         "A single year's share", "Manual shares"]
MARGINAL_FROM = 2011


_CACHE: dict = {}


def _read(name, loader):
    """Reload a data file only when it changed on disk, so a refreshed snapshot shows up without restarting the app."""
    path = os.path.join(D, name)
    m = os.path.getmtime(path)
    hit = _CACHE.get(path)
    if hit and hit[0] == m:
        return hit[1]
    value = loader(path)
    _CACHE[path] = (m, value)
    return value


def history() -> pd.DataFrame:
    """One row per completed marketing year (index = the year it starts): USDA exports, FGIS inspected bushels by region, shares."""
    return _read("soy_export_shares_by_my.csv", lambda p: pd.read_csv(p, index_col=0))


def wasde() -> dict:
    return _read("soy_exports_wasde.json", lambda p: json.load(open(p, encoding="utf8")))


def label(my: int) -> str:
    return f"{my}/{str(my + 1)[2:]}"


def _years(h, a, b):
    return h.loc[(h.index >= a) & (h.index <= b)]


def baseline(h=None) -> dict:
    h = history() if h is None else h
    b = _years(h, *BASE_YEARS)
    return {"east_gulf": float(b.east_gulf_mbu.mean()), "west_coast": float(b.west_coast_mbu.mean()), "interior": float(b.interior_mbu.mean()),
            "west_gulf": float(b.west_gulf_mbu.mean()), "mexico_rail": float(b.mexico_rail_mbu.mean()) if "mexico_rail_mbu" in b else 0.0,
            "south_atlantic": float(b.south_atlantic_mbu.mean()) if "south_atlantic_mbu" in b else 0.0,
            "inspected": float(b.fgis_inspected_mbu.mean()), "usda": float(b.usda_exports_mbu.mean()), "ratio": float(b.inspected_over_usda.mean())}


def _slopes(h) -> dict:
    x = _years(h, MARGINAL_FROM, 9999)
    return {k: float(np.polyfit(x.fgis_inspected_mbu, x[c], 1)[0]) for k, c in (("east_gulf", "east_gulf_mbu"), ("west_coast", "west_coast_mbu"),
                                                                              ("interior", "interior_mbu"), ("west_gulf", "west_gulf_mbu"),
                                                                              ("mexico_rail", "mexico_rail_mbu"), ("south_atlantic", "south_atlantic_mbu")) if c in x}


def scenario(exports_mbu: float, basis: str = BASES[0], *, year: int | None = None, manual: tuple | None = None) -> dict:
    """USDA export number (M bu) -> inspected bushels -> East Gulf / West Coast / interior bushels, and the scale factors for the model.

    The export number is USDA's; inspections run about 3% below it (inspected_over_usda, 2021-25 average), so it is converted first.
    """
    h = history()
    base = baseline(h)
    insp = exports_mbu * base["ratio"]
    if basis == BASES[3]:                                          # marginal: each region moves by its OLS slope times the change in the total
        s = _slopes(h)
        d = insp - base["inspected"]
        eg, wc, it, tg, mx, sa = (max(0.0, base[k] + s.get(k, 0.0) * d) for k in ("east_gulf", "west_coast", "interior", "west_gulf", "mexico_rail", "south_atlantic"))
    else:
        if basis == BASES[4]:
            r = h.loc[year if year in h.index else h.index[-1]]
            egs, wcs, its = r.east_gulf_share_pct, r.west_coast_share_pct, r.interior_share_pct
            tgs, mxs = r.get("west_gulf_share_pct", 0.0), r.get("mexico_rail_share_pct", 0.0)
            sas = r.get("south_atlantic_share_pct", 0.0)
        elif basis == BASES[5]:
            egs, wcs = manual
            its = None
            x = _years(h, BASE_YEARS[0], h.index.max())                  # the two shares you did not type keep their recent average
            tgs, mxs = x.west_gulf_share_pct.mean(), (x.mexico_rail_share_pct.mean() if "mexico_rail_share_pct" in x else 0.0)
            sas = x.south_atlantic_share_pct.mean() if "south_atlantic_share_pct" in x else 0.0
        else:
            a = {BASES[0]: 2021, BASES[1]: 2016, BASES[2]: 2011}[basis]
            x = _years(h, a, h.index.max())
            egs, wcs, its = x.east_gulf_share_pct.mean(), x.west_coast_share_pct.mean(), x.interior_share_pct.mean()
            tgs, mxs = x.west_gulf_share_pct.mean(), (x.mexico_rail_share_pct.mean() if "mexico_rail_share_pct" in x else 0.0)
            sas = x.south_atlantic_share_pct.mean() if "south_atlantic_share_pct" in x else 0.0
        eg, wc, tg, mx, sa = insp * egs / 100, insp * wcs / 100, insp * tgs / 100, insp * mxs / 100, insp * sas / 100
        it = insp * its / 100 if its is not None else max(0.0, insp - eg - wc)
    return {"exports": exports_mbu, "inspected": insp, "east_gulf": eg, "west_coast": wc, "interior": it, "west_gulf": tg, "mexico_rail": mx, "south_atlantic": sa,
            "satl_scale": sa / base["south_atlantic"] if base["south_atlantic"] else 1.0,
            "txgulf_scale": tg / base["west_gulf"] if base["west_gulf"] else 1.0, "mexico_scale": mx / base["mexico_rail"] if base["mexico_rail"] else 1.0,
            "east_gulf_share": eg / insp * 100 if insp else 0.0, "west_coast_share": wc / insp * 100 if insp else 0.0,
            "river_scale": eg / base["east_gulf"], "pnw_scale": wc / base["west_coast"], "base": base}


def sensitivity(exports_mbu: float) -> pd.DataFrame:
    """The same export number under each share basis, so the spread from the share assumption alone is visible."""
    h = history()
    rows = []
    for b in BASES[:4]:
        s = scenario(exports_mbu, b)
        rows.append((b if b != BASES[3] else "Marginal", s["east_gulf_share"], s["east_gulf"], s["west_coast"], s["river_scale"]))
    x = _years(h, MARGINAL_FROM, h.index.max())
    insp = exports_mbu * baseline(h)["ratio"]
    lo, hi = x.east_gulf_share_pct.min(), x.east_gulf_share_pct.max()
    rows.append((f"Lowest year since 2011 ({label(int(x.east_gulf_share_pct.idxmin()))})", lo, insp * lo / 100, np.nan, insp * lo / 100 / baseline(h)["east_gulf"]))
    rows.append((f"Highest year since 2011 ({label(int(x.east_gulf_share_pct.idxmax()))})", hi, insp * hi / 100, np.nan, insp * hi / 100 / baseline(h)["east_gulf"]))
    return pd.DataFrame(rows, columns=["Share basis", "East Gulf share %", "East Gulf (M bu)", "West Coast (M bu)", "River scale"])

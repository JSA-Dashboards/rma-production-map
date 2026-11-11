"""
Net exportable corn supply by state, ASD district and county: the supply of an area less the corn used inside it for processing (ethanol and the other plants of the Draw Territory model)
and for livestock feed (net of the distillers grains credit). A negative number is an area that uses more than it grows.

The demand comes from data/corn_demand_by_county.csv (build_corn_demand_by_county.py): ethanol where the plant stands, feed where the animals are. The supply is NASS production plus the
Sep 1 stocks the state holds (stocks are published by state only, so a state's districts and counties share them in proportion to their production). Everything is in bushels.

Not placed by area: exports, food, seed and other industrial use outside these plants, and the stocks left at the end of the marketing year. The US total of the net figure is therefore about
what is left for exports, those other uses and ending stocks.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "corn_demand_by_county.csv")


@lru_cache(maxsize=1)
def _all() -> pd.DataFrame:
    return pd.read_csv(DATA, dtype={"fips": str})


def years() -> list:
    """Marketing years (the crop year's NASS year) with demand on file."""
    return sorted(int(y) for y in _all()["year"].unique())


def county_demand(year: int) -> pd.DataFrame:
    """Demand of one marketing year by county fips: state, ethanol_bu, feed_gross_bu, ddgs_credit_bu, feed_net_bu."""
    d = _all()
    return d[d["year"] == int(year)].set_index("fips")[["state", "ethanol_bu", "feed_gross_bu", "ddgs_credit_bu", "feed_net_bu", "feed_dairy_bu", "feed_beef_bu", "feed_hogs_bu", "feed_poultry_bu", "residual_bu"]]


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df["Demand"] = df["Ethanol"] + df["Feed"]
    df["Net"] = df["Supply"] - df["Demand"]
    df["Net % of supply"] = np.where(df["Supply"] > 0, df["Net"] / df["Supply"] * 100.0, np.nan)
    return df


def state_table(prod_by_state: dict, stocks_by_state: dict, year: int) -> pd.DataFrame:
    """One row per state: Production, Stocks, Supply, Ethanol, Feed, Demand, Net, Net % of supply. A state with demand but no production still has a row (its net is negative)."""
    dem = county_demand(year).groupby("state")[["ethanol_bu", "feed_net_bu"]].sum()
    states = sorted(set(prod_by_state) | set(dem.index))
    df = pd.DataFrame({"State": states})
    df["Production"] = [float(prod_by_state.get(s, 0.0) or 0.0) for s in states]
    df["Stocks"] = [float(stocks_by_state.get(s, 0.0) or 0.0) for s in states]
    df["Supply"] = df["Production"] + df["Stocks"]
    df["Ethanol"] = [float(dem["ethanol_bu"].get(s, 0.0)) for s in states]
    df["Feed"] = [float(dem["feed_net_bu"].get(s, 0.0)) for s in states]
    return _finish(df)


def county_table(counties: pd.DataFrame, state: str, state_stocks: float, year: int, district_of: dict = None, name_of: dict = None) -> pd.DataFrame:
    """One row per county of a state. `counties` has fips, County, District, DistrictCode and Value (the state's production by county, completed to the state total); the state's stocks are
    spread over them in proportion to production. A county with demand but no production row is added so its demand is not lost: `district_of` {fips: district name} and `name_of` {fips: county
    name} place it, else its district reads '(not in the district list)'."""
    c = counties[["fips", "County", "District", "DistrictCode", "Value"]].copy()
    c["fips"] = c["fips"].astype(str).str.zfill(5)
    c = c.rename(columns={"Value": "Production"})
    c["Production"] = pd.to_numeric(c["Production"], errors="coerce").fillna(0.0)
    dem = county_demand(year)
    dem = dem[dem["state"] == state]
    extra = [f for f in dem.index if f not in set(c["fips"])]
    if extra:
        district_of, name_of = district_of or {}, name_of or {}
        c = pd.concat([c, pd.DataFrame({"fips": extra, "County": [name_of.get(f, f"County {f}") for f in extra], "District": [district_of.get(f) or "(not in the district list)" for f in extra],
                                        "DistrictCode": "", "Production": 0.0})], ignore_index=True)
    tot = c["Production"].sum()
    c["Stocks"] = c["Production"] / tot * float(state_stocks or 0.0) if tot > 0 else 0.0
    c["Supply"] = c["Production"] + c["Stocks"]
    c["Ethanol"] = c["fips"].map(dem["ethanol_bu"]).fillna(0.0)
    c["Feed"] = c["fips"].map(dem["feed_net_bu"]).fillna(0.0)
    c["District"] = c["District"].fillna("(not in the district list)").astype(str).str.strip().str.title()
    return _finish(c)


def asd_table(county_df: pd.DataFrame) -> pd.DataFrame:
    """The county table summed by ASD district."""
    g = county_df.groupby("District", as_index=False)[["Production", "Stocks", "Supply", "Ethanol", "Feed"]].sum()
    return _finish(g)


# ── balance sheet: one area, years in columns ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
DDG_LB_PER_BU = 15.5                    # pounds of distillers grains a bushel of corn makes (0.27 by weight of the corn's 56 lb, the same figure as the soybean meal credit)
RESIDUAL_BASES = {"animals": "with the animals (where the livestock feed is)", "production": "by production (shrink, handling and error follow the corn that is grown)"}


def residual_total(year: int) -> float:
    """USDA's feed and residual that the per-head feed budgets do not cover, summed over every county (bushels)."""
    return float(county_demand(year)["residual_bu"].sum())


def balance_demand(year: int, fips, residual_basis: str = "animals", prod_area: float = 0.0, prod_us: float = 0.0) -> dict:
    """The use side of the balance sheet for a set of counties (fips): feed by kind of animal, the residual, ethanol and processing, distillers grains made and the corn they displace.
    residual_basis 'animals' keeps each county's residual with its animals; 'production' spreads the national residual by the area's share of US production (`prod_area` / `prod_us`)."""
    d = county_demand(year)
    d = d[d.index.isin(set(fips))]
    resid = float(d["residual_bu"].sum()) if residual_basis == "animals" else (residual_total(year) * prod_area / prod_us if prod_us > 0 else 0.0)
    out = {"dairy": float(d["feed_dairy_bu"].sum()), "beef": float(d["feed_beef_bu"].sum()), "hogs": float(d["feed_hogs_bu"].sum()), "poultry": float(d["feed_poultry_bu"].sum()),
           "residual": resid, "ethanol": float(d["ethanol_bu"].sum()), "displaced": float(d["ddgs_credit_bu"].sum())}
    out["ddg_tons"] = out["ethanol"] * DDG_LB_PER_BU / 2000.0
    return out


def us_fips(year: int) -> list:
    return list(county_demand(year).index)


def balance_table(by_year: dict, bu_div: float, bu_lbl: str, ac_div: float, ac_lbl: str) -> pd.DataFrame:
    """A balance sheet with the years across: one column per year of `by_year` {year: {carry_in, planted, harvested, production} + balance_demand()}, one row per line. Returns a frame
    with an 'Item' column, the year columns as text (labelled 2025/26) and a 'kind' list on .attrs['kinds'] ('head', 'total', 'net' or '') for styling."""
    rows = [("SUPPLY", "head", None), (f"Carry-in, Sep 1 stocks ({bu_lbl})", "", lambda d: d["carry_in"] / bu_div), (f"Area planted ({ac_lbl})", "", lambda d: d["planted"] / ac_div),
            (f"Area harvested ({ac_lbl})", "", lambda d: d["harvested"] / ac_div), ("Yield (bu/ac)", "", lambda d: d["production"] / d["harvested"] if d["harvested"] else None),
            (f"Production ({bu_lbl})", "", lambda d: d["production"] / bu_div), (f"Total supply ({bu_lbl})", "total", lambda d: (d["carry_in"] + d["production"]) / bu_div),
            ("USE", "head", None), (f"Feed: dairy ({bu_lbl})", "", lambda d: d["dairy"] / bu_div), (f"Feed: beef ({bu_lbl})", "", lambda d: d["beef"] / bu_div),
            (f"Feed: hogs ({bu_lbl})", "", lambda d: d["hogs"] / bu_div), (f"Feed: poultry ({bu_lbl})", "", lambda d: d["poultry"] / bu_div),
            (f"Residual ({bu_lbl})", "", lambda d: d["residual"] / bu_div),
            (f"Feed and residual, gross ({bu_lbl})", "total", lambda d: (d["dairy"] + d["beef"] + d["hogs"] + d["poultry"] + d["residual"]) / bu_div),
            (f"Less: corn displaced by distillers grains ({bu_lbl})", "", lambda d: -d["displaced"] / bu_div),
            (f"Feed and residual, net of distillers grains ({bu_lbl})", "total", lambda d: (d["dairy"] + d["beef"] + d["hogs"] + d["poultry"] + d["residual"] - d["displaced"]) / bu_div),
            (f"Processing: ethanol and other plants, corn ground ({bu_lbl})", "", lambda d: d["ethanol"] / bu_div),
            ("Memo: distillers grains made (K tons)", "", lambda d: d["ddg_tons"] / 1e3),
            (f"Total use ({bu_lbl})", "total", lambda d: _use(d) / bu_div),
            ("EXPORTABLE SURPLUS", "head", None),
            (f"Supply less use ({bu_lbl})", "net", lambda d: (d["carry_in"] + d["production"] - _use(d)) / bu_div),
            ("% of supply", "net", lambda d: (d["carry_in"] + d["production"] - _use(d)) / (d["carry_in"] + d["production"]) * 100.0 if (d["carry_in"] + d["production"]) > 0 else None)]
    yrs = sorted(by_year)
    out = {"Item": [r[0] for r in rows]}
    for y in yrs:
        col = []
        for lab, kind, fn in rows:
            if fn is None:
                col.append("")
                continue
            try:
                v = fn(by_year[y])
            except Exception:
                v = None
            if v is None or (isinstance(v, float) and np.isnan(v)):
                col.append("-")
            elif "Yield" in lab:
                col.append(f"{v:,.1f}")
            elif lab.startswith("%") or "% of supply" in lab:
                col.append(f"{v:+,.0f}%")
            elif "K tons" in lab:
                col.append(f"{v:,.0f}")
            else:
                col.append(f"{v:,.1f}" if abs(v) < 1000 or "M bu" in lab else f"{v:,.0f}")
        out[f"{y}/{str(y + 1)[2:]}"] = col
    df = pd.DataFrame(out)
    df.attrs["kinds"] = [r[1] for r in rows]
    return df


def _use(d: dict) -> float:
    return d["dairy"] + d["beef"] + d["hogs"] + d["poultry"] + d["residual"] - d["displaced"] + d["ethanol"]

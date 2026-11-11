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
    return d[d["year"] == int(year)].set_index("fips")[["state", "ethanol_bu", "feed_gross_bu", "ddgs_credit_bu", "feed_net_bu"]]


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

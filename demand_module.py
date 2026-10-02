"""
demand_module.py
ASD-level grain demand calculator for the county/ASD S&D dashboard.

Demand = livestock feed (corn + SBM) + ethanol corn + crush soybeans
All output in bushels per ASD per year.

Usage:
    from demand_module import load_asd_demand
    df = load_asd_demand(year=2024, cache_ver="v1")
    # Returns DataFrame indexed by (state, asd_code) with columns:
    #   corn_feed_bu, sbm_feed_lbs, corn_ethanol_bu,
    #   soy_crush_bu, corn_demand_total_bu, soy_demand_total_bu
"""

import json
import re
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
NASS_KEY  = "9A6D1EB8-4D94-3221-BA0C-ADD4533EA0C1"
NASS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"

# ── Feed conversion factors (net of current DDGS inclusion) ─────────────────
# Corn bu/head/yr, SBM lbs/head/yr
FEED_FACTORS = {
    # (commodity_desc, class_desc or None): (corn_bu, sbm_lbs, denominator_note)
    "CATTLE ON FEED":   (50,    0,    "Jan inventory"),
    "CATTLE OTHER":     (6,     0,    "all cattle - COF - dairy"),
    "HOGS":             (13,    150,  "quarterly inventory"),
    "BROILERS":         (0.12,  3.0,  "birds produced"),
    "CHICKENS, LAYERS": (1.1,   20,   "average on hand"),
    "MILK COWS":        (50,    1450, "Jan milk cows"),
    "TURKEYS":          (1.2,   22,   "birds raised"),
}

# CORN BELT + major livestock states
DEMAND_STATES = [
    "IA","IL","IN","MN","NE","OH","MO","SD","ND","KS","WI","MI",
    "TX","OK","KY","TN","AR","MS","AL","GA","NC","VA",
]

LBS_PER_BU_CORN = 56
LBS_PER_BU_SBM  = 2000   # SBM stays in lbs; convert to short tons where needed


# ── NASS helpers ─────────────────────────────────────────────────────────────

def _nass_get(params: dict, timeout: int = 60, retries: int = 4) -> list[dict]:
    """NASS 400 = zero rows (returns []). Throttling/network errors retry, then raise
    so a failed pull is never cached as an empty result."""
    url = NASS_BASE + "?" + urllib.parse.urlencode({**params, "key": NASS_KEY, "format": "json"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.load(r).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return []
            last = e
        except Exception as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"NASS request failed after {retries} tries: {last}")


def _parse_value(v: str) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


# ── Species matching ─────────────────────────────────────────────────────────
# NASS short_desc punctuation varies ("CATTLE, ON FEED - INVENTORY"), so match on
# letters only. (commodity_desc, normalized short_desc, exact?) -> species key.

_SPECIES_RULES = [
    ("CATTLE",   "CATTLEONFEEDINVENTORY",       True,  "CATTLE ON FEED"),
    ("CATTLE",   "CATTLEINCLCALVESINVENTORY",   True,  "CATTLE ALL"),
    ("CATTLE",   "CATTLECOWSMILKINVENTORY",     True,  "MILK COWS"),
    ("HOGS",     "HOGSINVENTORY",               True,  "HOGS"),
    ("CHICKENS", "CHICKENSLAYERSINVENTORY",     True,  "CHICKENS, LAYERS"),
    ("CHICKENS", "CHICKENSBROILERSPRODUCTION",  False, "BROILERS"),
    ("TURKEYS",  "TURKEYSPRODUCTION",           False, "TURKEYS"),
]


def _norm(sd: str) -> str:
    return re.sub(r"[^A-Z]", "", sd.upper())


def _species_for(commodity: str, short_desc: str) -> str | None:
    n = _norm(short_desc)
    for com, pat, exact, key in _SPECIES_RULES:
        if com == commodity and (n == pat if exact else n.startswith(pat)):
            return key
    return None


# ── Step 1: 2022 Census ASD livestock shares ─────────────────────────────────

@st.cache_data(ttl=86400 * 7, show_spinner=False)
def _load_census_asd_shares(cache_ver: str) -> pd.DataFrame:
    """
    2022 Census county livestock for Corn Belt states, aggregated to ASD:
        state_alpha, asd_code, species, head
    Used as spatial allocation weights. Raises if NASS is unreachable (not cached).
    """
    records = []
    for commodity in ("CATTLE", "HOGS", "CHICKENS"):
        for state in DEMAND_STATES:
            rows = _nass_get({
                "commodity_desc":    commodity,
                "statisticcat_desc": "INVENTORY",
                "source_desc":       "CENSUS",
                "year":              "2022",
                "agg_level_desc":    "COUNTY",
                "state_alpha":       state,
                "domain_desc":       "TOTAL",
            })
            time.sleep(0.3)
            for row in rows:
                key = _species_for(commodity, row.get("short_desc", ""))
                if not key:
                    continue
                val = _parse_value(row.get("Value", "0"))
                if val <= 0:
                    continue
                records.append({
                    "state_alpha": row["state_alpha"],
                    "asd_code":    row.get("asd_code", ""),
                    "species":     key,
                    "head":        val,
                })

    df = pd.DataFrame(records)
    if df.empty:
        raise RuntimeError("Census livestock shares came back empty")
    return df.groupby(["state_alpha", "asd_code", "species"])["head"].sum().reset_index()


# ── Step 2: Current annual state totals ──────────────────────────────────────

@st.cache_data(ttl=3600 * 24, show_spinner=False)
def _load_state_livestock(year: int, cache_ver: str) -> pd.DataFrame:
    """
    Annual state-level livestock (SURVEY): state_alpha, species, head.
    Inventories with several reference periods (hogs are quarterly) are averaged.
    Raises if nothing comes back so an empty table is never cached.
    """
    queries = [
        ("CATTLE",   "INVENTORY"),
        ("HOGS",     "INVENTORY"),
        ("CHICKENS", "INVENTORY"),
        ("CHICKENS", "PRODUCTION"),
        ("TURKEYS",  "PRODUCTION"),
    ]
    records = []
    for commodity, statcat in queries:
        rows = _nass_get({
            "commodity_desc":    commodity,
            "statisticcat_desc": statcat,
            "source_desc":       "SURVEY",
            "year":              str(year),
            "agg_level_desc":    "STATE",
            "domain_desc":       "TOTAL",
            "unit_desc":         "HEAD",
        })
        time.sleep(0.3)
        for row in rows:
            key = _species_for(commodity, row.get("short_desc", ""))
            if not key:
                continue
            val = _parse_value(row.get("Value", "0"))
            if val <= 0:
                continue
            records.append({
                "state_alpha": row["state_alpha"],
                "species":     key,
                "period":      row.get("reference_period_desc", ""),
                "head":        val,
            })

    df = pd.DataFrame(records)
    if df.empty:
        raise RuntimeError(f"State livestock for {year} came back empty")
    per = df.groupby(["state_alpha", "species", "period"])["head"].sum().reset_index()
    return per.groupby(["state_alpha", "species"])["head"].mean().reset_index()


# ── Step 3: Distribute state livestock to ASDs using Census shares ────────────

def _allocate_to_asd(
    state_df: pd.DataFrame,
    shares_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Multiply state totals by ASD census shares to estimate ASD-level inventory.
    Returns DataFrame: state_alpha, asd_code, species, head_est
    """
    if shares_df.empty or state_df.empty:
        return pd.DataFrame()

    # Compute each ASD's share of state total per species
    state_totals = shares_df.groupby(["state_alpha", "species"])["head"].sum().reset_index()
    state_totals = state_totals.rename(columns={"head": "state_census_total"})
    shares = shares_df.merge(state_totals, on=["state_alpha", "species"])
    shares["asd_share"] = shares["head"] / shares["state_census_total"].clip(lower=1)

    # Merge with current state totals
    merged = shares.merge(
        state_df.rename(columns={"head": "state_current"}),
        on=["state_alpha", "species"],
        how="left",
    )
    merged["state_current"] = merged["state_current"].fillna(0)

    # Map census cattle_all → cattle_other (all cattle - COF - dairy)
    # We'll handle this after assembling per-species columns
    merged["head_est"] = merged["asd_share"] * merged["state_current"]

    return merged[["state_alpha", "asd_code", "species", "head_est"]].copy()


# ── Step 4: Compute feed demand per ASD ──────────────────────────────────────

def _compute_feed_demand(asd_inv: pd.DataFrame) -> pd.DataFrame:
    """
    Apply feed conversion factors → corn_bu, sbm_lbs per ASD.
    """
    if asd_inv.empty:
        return pd.DataFrame()

    pivot = asd_inv.pivot_table(
        index=["state_alpha", "asd_code"],
        columns="species",
        values="head_est",
        aggfunc="sum",
    ).fillna(0).reset_index()

    # "Other cattle" = all cattle − COF − dairy
    pivot["CATTLE OTHER"] = (
        pivot.get("CATTLE ALL", pd.Series(0, index=pivot.index))
        - pivot.get("CATTLE ON FEED", pd.Series(0, index=pivot.index))
        - pivot.get("MILK COWS", pd.Series(0, index=pivot.index))
    ).clip(lower=0)

    corn_cols, sbm_cols = [], []
    for species_key, (corn_bu, sbm_lbs, _) in FEED_FACTORS.items():
        col = species_key
        if col not in pivot.columns:
            pivot[col] = 0
        pivot[f"_corn_{col}"] = pivot[col] * corn_bu
        pivot[f"_sbm_{col}"]  = pivot[col] * sbm_lbs
        corn_cols.append(f"_corn_{col}")
        sbm_cols.append(f"_sbm_{col}")

    pivot["corn_feed_bu"]  = pivot[corn_cols].sum(axis=1)
    pivot["sbm_feed_lbs"]  = pivot[sbm_cols].sum(axis=1)
    pivot = pivot.rename(columns={
        **{f"_corn_{k}": f"corn_feed__{k}" for k in FEED_FACTORS},
        **{f"_sbm_{k}":  f"sbm_feed__{k}"  for k in FEED_FACTORS},
    })
    per_species = [f"{p}__{k}" for k in FEED_FACTORS for p in ("corn_feed", "sbm_feed")]

    return pivot[["state_alpha", "asd_code", "corn_feed_bu", "sbm_feed_lbs", *per_species]].copy()


# ── Step 5: Facility demand from CSVs ─────────────────────────────────────────

def _load_facility_demand() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (eth_asd, crush_asd):
        eth_asd:   state, asd_code, corn_ethanol_bu, ddgs_dry_lbs
        crush_asd: state, asd_code, soy_crush_bu
    """
    eth_path   = DATA_DIR / "ethanol_plants_asd.csv"
    crush_path = DATA_DIR / "crush_plants_asd.csv"

    eth_df = pd.DataFrame()
    if eth_path.exists():
        e = pd.read_csv(eth_path, dtype=str)
        e["corn_ethanol_bu"] = pd.to_numeric(e["corn_input_bu_yr"], errors="coerce").fillna(0)
        e["ddgs_dry_lbs"]    = pd.to_numeric(e["ddgs_dry_lbs_yr"],  errors="coerce").fillna(0)
        eth_df = (
            e[e["asd_code"].notna() & (e["asd_code"] != "")]
            .groupby(["state", "asd_code"])[["corn_ethanol_bu", "ddgs_dry_lbs"]]
            .sum()
            .reset_index()
            .rename(columns={"state": "state_alpha"})
        )

    crush_df = pd.DataFrame()
    if crush_path.exists():
        c = pd.read_csv(crush_path, dtype=str)
        c["soy_crush_bu"] = pd.to_numeric(c["plate_annual_bu"], errors="coerce").fillna(0)
        crush_df = (
            c[c["asd_code"].notna() & (c["asd_code"] != "")]
            .groupby(["state", "asd_code"])[["soy_crush_bu"]]
            .sum()
            .reset_index()
            .rename(columns={"state": "state_alpha"})
        )

    return eth_df, crush_df


# ── Public API ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600 * 6, show_spinner=False)
def load_asd_demand(year: int, cache_ver: str) -> pd.DataFrame:
    """
    Full ASD-level demand table.

    Returns DataFrame with index (state_alpha, asd_code) and columns:
        corn_feed_bu        — livestock feed corn demand
        sbm_feed_lbs        — livestock feed SBM demand
        corn_ethanol_bu     — ethanol plant corn demand
        ddgs_dry_lbs        — dry DDGS produced (available for local rations)
        soy_crush_bu        — crush plant soybean throughput
        corn_demand_total_bu  — corn_feed_bu + corn_ethanol_bu
        soy_demand_total_bu   — soy_crush_bu
    """
    with st.spinner("Loading ASD livestock shares (Census 2022)..."):
        shares = _load_census_asd_shares(cache_ver)

    with st.spinner(f"Loading {year} state livestock..."):
        state_lv = _load_state_livestock(year, cache_ver)

    asd_inv     = _allocate_to_asd(state_lv, shares)
    feed_demand = _compute_feed_demand(asd_inv)

    eth_asd, crush_asd = _load_facility_demand()

    # Merge all demand components
    base = feed_demand.copy() if not feed_demand.empty else pd.DataFrame(
        columns=["state_alpha", "asd_code", "corn_feed_bu", "sbm_feed_lbs"]
    )

    if not eth_asd.empty:
        base = base.merge(eth_asd, on=["state_alpha", "asd_code"], how="outer")
    else:
        base["corn_ethanol_bu"] = 0
        base["ddgs_dry_lbs"]    = 0

    if not crush_asd.empty:
        base = base.merge(crush_asd, on=["state_alpha", "asd_code"], how="outer")
    else:
        base["soy_crush_bu"] = 0

    species_cols = [c for c in base.columns if c.startswith(("corn_feed__", "sbm_feed__"))]
    for col in ["corn_feed_bu","sbm_feed_lbs","corn_ethanol_bu","ddgs_dry_lbs","soy_crush_bu", *species_cols]:
        base[col] = pd.to_numeric(base.get(col, 0), errors="coerce").fillna(0)

    base["corn_demand_total_bu"] = base["corn_feed_bu"] + base["corn_ethanol_bu"]
    base["soy_demand_total_bu"]  = base["soy_crush_bu"]

    return base.sort_values(["state_alpha", "asd_code"]).reset_index(drop=True)

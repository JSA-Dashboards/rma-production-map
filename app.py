import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import json
import urllib.request
import urllib.parse
import numpy as np
import base64
import datetime
from pathlib import Path
from PIL import Image
import geopandas as gpd
from shapely.geometry import shape

_HERE = Path(__file__).parent
st.set_page_config(
    page_title="JSA Agricultural Intelligence Dashboard",
    page_icon=Image.open(_HERE / "assets" / "Transparent Smal logo.png"),
    layout="wide",
)
_CACHE_VERSION = "v21"  # bump to invalidate all @st.cache_data on deploy

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE       = Path(__file__).parent
DATA_PATH  = HERE / "data" / "2025 RMA Production Data.xlsx"
LOGO_50YR  = HERE / "assets" / "50 Year logo JSA.png"
LOGO_TRANS = HERE / "assets" / "Transparent Smal logo.png"
LOGO_FULL  = HERE / "assets" / "logo-full.png"

# ── NASS API ───────────────────────────────────────────────────────────────────
NASS_API_KEY  = "9A6D1EB8-4D94-3221-BA0C-ADD4533EA0C1"
NASS_BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

# ── EIA API ────────────────────────────────────────────────────────────────────
EIA_API_KEY   = "byhccqGIo65WWSSfpry5n3o3tMA66Z4Wf4oOwHpk"
EIA_BASE_URL  = "https://api.eia.gov/v2/"
NASS_YEARS             = list(range(2026, 2014, -1))   # 2026 → 2015
_NASS_BENCHMARK_YEAR   = 2023   # most-complete county year — used for % reporting KPI

# ── Grain Stocks ───────────────────────────────────────────────────────────────
STOCKS_PERIODS = {           # display name → NASS reference_period_desc
    "Sep 1":  "FIRST OF SEP",
    "Dec 1":  "FIRST OF DEC",
    "Mar 1":  "FIRST OF MAR",
    "Jun 1":  "FIRST OF JUN",
}
STOCKS_VIEWS   = ["Total Stocks", "On-Farm", "Off-Farm", "% On-Farm", "% Off-Farm",
                  "Production", "Total Supply"]
STOCKS_CROPS   = ["Corn", "Soybeans", "Wheat", "Sorghum"]
STOCKS_CROP_PARAMS = {
    "Corn":     {"commodity_desc": "CORN"},
    "Soybeans": {"commodity_desc": "SOYBEANS"},
    "Wheat":    {"commodity_desc": "WHEAT"},
    "Sorghum":  {"commodity_desc": "SORGHUM"},
}

# Metrics available in the NASS tab
NASS_METRICS     = ["Production (bu)", "Planted Acres", "Harvested Acres",
                    "% Harvested", "Yield (bu/ac)", "Prevent Plant Acres"]
NASS_CHANGE_OPTS = ["Current Year", "vs Prior Year", "vs Selected Year", "vs 3-Yr Avg"]

_METRIC_TO_STAT = {
    "Production (bu)":    "production",
    "Planted Acres":      "planted",
    "Harvested Acres":    "harvested",
    "% Harvested":        "pct_harvested",   # derived: harvested / planted * 100
    "Yield (bu/ac)":      "yield",
    "Prevent Plant Acres": "prevent_plant",
}

# Per-crop API params for each stat type
NASS_CROP_STAT_PARAMS = {
    "Corn": {
        "production":    {"commodity_desc": "CORN", "util_practice_desc": "GRAIN"},
        "planted":       {"commodity_desc": "CORN"},
        "harvested":     {"commodity_desc": "CORN", "util_practice_desc": "GRAIN"},
        "yield":         {"commodity_desc": "CORN", "util_practice_desc": "GRAIN"},
        "prevent_plant": {"commodity_desc": "CORN"},
    },
    "Soybeans": {
        "production":    {"commodity_desc": "SOYBEANS"},
        "planted":       {"commodity_desc": "SOYBEANS"},
        "harvested":     {"commodity_desc": "SOYBEANS"},
        "yield":         {"commodity_desc": "SOYBEANS"},
        "prevent_plant": {"commodity_desc": "SOYBEANS"},
    },
    "Wheat": {
        "production":    {"commodity_desc": "WHEAT", "class_desc": "ALL CLASSES"},
        "planted":       {"commodity_desc": "WHEAT", "class_desc": "ALL CLASSES"},
        "harvested":     {"commodity_desc": "WHEAT", "class_desc": "ALL CLASSES"},
        "yield":         {"commodity_desc": "WHEAT", "class_desc": "ALL CLASSES"},
        "prevent_plant": {"commodity_desc": "WHEAT", "class_desc": "ALL CLASSES"},
    },
    "Sorghum": {
        "production":    {"commodity_desc": "SORGHUM", "util_practice_desc": "GRAIN"},
        "planted":       {"commodity_desc": "SORGHUM"},
        "harvested":     {"commodity_desc": "SORGHUM", "util_practice_desc": "GRAIN"},
        "yield":         {"commodity_desc": "SORGHUM", "util_practice_desc": "GRAIN"},
        "prevent_plant": {"commodity_desc": "SORGHUM"},
    },
}

# Base API params per stat type
# prevent_plant uses AREA PLANTED — rows are filtered to "PREVENTED" in load_nass_stat
NASS_STAT_BASE = {
    "production":    {"statisticcat_desc": "PRODUCTION",     "unit_desc": "BU"},
    "planted":       {"statisticcat_desc": "AREA PLANTED",    "unit_desc": "ACRES"},
    "harvested":     {"statisticcat_desc": "AREA HARVESTED",  "unit_desc": "ACRES"},
    "yield":         {"statisticcat_desc": "YIELD",           "unit_desc": "BU / ACRE"},
    "prevent_plant": {"statisticcat_desc": "AREA PLANTED",    "unit_desc": "ACRES"},
}

# ── Acreage Summary — principal crop params ─────────────────────────────────
# Tuples so they're hashable for @st.cache_data
_ACR_PARAMS: dict = {
    "Wheat":      (("commodity_desc","WHEAT"),("class_desc","ALL CLASSES")),
    "Corn":       (("commodity_desc","CORN"),),
    "Sorghum":    (("commodity_desc","SORGHUM"),),
    "Barley":     (("commodity_desc","BARLEY"),),
    "Oats":       (("commodity_desc","OATS"),),
    "Soybeans":   (("commodity_desc","SOYBEANS"),),
    "Sunflowers": (("commodity_desc","SUNFLOWERS"),("class_desc","ALL CLASSES")),
    "Canola":     (("commodity_desc","CANOLA"),),
    "Cotton":     (("commodity_desc","COTTON"),("class_desc","ALL CLASSES")),
    "Rice":       (("commodity_desc","RICE"),),
    "Peanuts":    (("commodity_desc","PEANUTS"),),
    "SugarBeets": (("commodity_desc","SUGARBEETS"),),
    "DryBeans":   (("commodity_desc","BEANS, DRY EDIBLE"),("class_desc","ALL CLASSES")),
    "Hay":        (("commodity_desc","HAY"),("class_desc","ALL CLASSES")),
}
# crops that produce prevent-plant data through NASS
_ACR_PP_CROPS = ("Corn","Soybeans","Wheat","Cotton","Sorghum","Rice","Peanuts","SugarBeets","DryBeans","Barley","Oats","Sunflowers","Canola")

_LIVESTOCK_SPECIES: dict = {
    # Livestock inventory (HEAD, INVENTORY)
    "Cattle, All":        {"commodity_desc": "CATTLE",        "class_desc": "INCL CALVES"},
    "Cattle, Beef Cows":  {"commodity_desc": "CATTLE",        "class_desc": "COWS, BEEF"},
    "Cattle, Milk Cows":  {"commodity_desc": "CATTLE",        "class_desc": "COWS, MILK"},
    "Hogs & Pigs":        {"commodity_desc": "HOGS"},
    # "SHEEP & LAMBS" is not a NASS commodity - the commodity is SHEEP and
    # the lamb inclusion is a class. The class is required: without it all 12
    # sheep classes come back mixed together and inventory double-counts.
    "Sheep & Lambs":      {"commodity_desc": "SHEEP", "class_desc": "INCL LAMBS"},
    # Dairy production
    # County milk production was discontinued after 2009, so the county
    # drill-down has no modern data; state history still reaches back.
    "Milk Production":    {"commodity_desc": "MILK",
                           "_stat": "PRODUCTION", "_unit": "LB",
                           "_stale_agg": ("COUNTY",)},
    # Poultry inventory / production — use _stat/_unit to override defaults
    # "LAYERS - INCL PULLETS" is not a CHICKENS class; replacement pullets are
    # their own class and there is no combined one. NATIONAL layers is MONTHLY
    # (12 rows a year) while STATE and COUNTY are annual.
    "Chickens, Layers":   {"commodity_desc": "CHICKENS", "class_desc": "LAYERS"},
    "Chickens, Broilers": {"commodity_desc": "CHICKENS", "class_desc": "BROILERS",
                           "_stat": "PRODUCTION"},
    # County table-egg production does not exist under any source or year. A
    # county EGGS series exists under CENSUS + ALL CLASSES, but every row is
    # prodn_practice_desc PRODUCTION CONTRACT - contract eggs, a different
    # series. Do not substitute it.
    "Eggs, Table":        {"commodity_desc": "EGGS",     "class_desc": "TABLE",
                           "_stat": "PRODUCTION", "_unit": "DOZEN",
                           "_no_agg": ("COUNTY",)},
    # Turkey INVENTORY is Census-only, so the SURVEY default can never satisfy
    # it. domain_desc is required: without it NATIONAL returns 49 rows across
    # AREA OPERATED / ECONOMIC CLASS / FARM SALES / NAICS / TOTAL, and summing
    # them multiplies the flock fivefold.
    "Turkeys":            {"commodity_desc": "TURKEYS",
                           "_source": "CENSUS", "_domain": "TOTAL",
                           "_years": ("2017", "2022")},
}
# Standard survey reference period per species for consistent year-over-year comparison

def _livestock_spec(species_key: str) -> tuple:
    """Split a _LIVESTOCK_SPECIES entry into (NASS query params, local flags).

    Every underscore key is a LOCAL CONTROL FLAG and must never reach NASS:
        _stat/_unit  override the INVENTORY/HEAD defaults
        _source      override source_desc (turkeys are Census-only)
        _domain      pin domain_desc (Census fans out across five domains)
        _years       the only years that can return rows (Census cadence)
        _no_agg      agg levels NASS never publishes for this species
        _stale_agg   agg levels whose series ended before the year picker starts

    Popping only _stat/_unit - as this code used to - leaves the rest in the
    dict, and the later params.update(spec) sends them to NASS as bogus fields.
    """
    raw = _LIVESTOCK_SPECIES[species_key]
    return ({k: v for k, v in raw.items() if not k.startswith("_")},
            {k: v for k, v in raw.items() if k.startswith("_")})


def _livestock_base_params(species_key: str) -> tuple:
    """The shared NASS params for one species, plus its local flags."""
    spec, over = _livestock_spec(species_key)
    params = {
        "key":               NASS_API_KEY,
        "source_desc":       over.get("_source", "SURVEY"),
        "sector_desc":       "ANIMALS & PRODUCTS",
        "statisticcat_desc": over.get("_stat", "INVENTORY"),
        "unit_desc":         over.get("_unit", "HEAD"),
        "format":            "JSON",
    }
    if "_domain" in over:
        params["domain_desc"] = over["_domain"]
    params.update(spec)
    return params, over


def livestock_coverage_note(species_key: str, agg_level: str, year=None) -> str:
    """Why this species/level/year has no data, or "" if it should have data.

    Lets the UI say what is actually true instead of "try an earlier year",
    which was wrong for every species whose series does not exist at all.
    """
    _, over = _livestock_spec(species_key)
    if agg_level in over.get("_no_agg", ()):
        return (f"NASS does not publish {species_key} at {agg_level.lower()} "
                f"level in any year. National and state figures are available.")
    if agg_level in over.get("_stale_agg", ()):
        return (f"NASS discontinued {species_key} at {agg_level.lower()} level; "
                f"only historical years are available, not recent ones.")
    years = over.get("_years")
    if years and year is not None and str(year) not in years:
        return (f"{species_key} inventory comes from the Census of Agriculture, "
                f"published only in {' and '.join(years)}.")
    return ""


_LIVESTOCK_PERIOD: dict = {
    "Cattle, All":        "JAN 1",
    "Cattle, Beef Cows":  "JAN 1",
    "Cattle, Milk Cows":  "JAN 1",
    "Hogs & Pigs":        "DEC 1",
    "Sheep & Lambs":      "JAN 1",
    "Milk Production":    "YEAR",
    "Chickens, Layers":   "JAN 1",
    "Chickens, Broilers": "YEAR",
    "Eggs, Table":        "YEAR",
    "Turkeys":            "DEC 1",
}
# Base display unit per species (used for auto-scaling labels)
_LIVESTOCK_UNIT: dict = {
    "Cattle, All":        "head",
    "Cattle, Beef Cows":  "head",
    "Cattle, Milk Cows":  "head",
    "Hogs & Pigs":        "head",
    "Sheep & Lambs":      "head",
    "Milk Production":    "lb",
    "Chickens, Layers":   "head",
    "Chickens, Broilers": "head",
    "Eggs, Table":        "dz",
    "Turkeys":            "head",
}
# Species where county-level data is unavailable or very sparse
_LIVESTOCK_POULTRY: set = {
    "Milk Production",
    "Chickens, Layers", "Chickens, Broilers", "Eggs, Table", "Turkeys",
}
_LIVESTOCK_YEARS: list = list(range(2025, 2011, -1))

_AQUA_SPECIES: dict = {
    # NASS Census of Aquaculture taxonomy (group_desc=AQUACULTURE, statisticcat=SALES & DISTRIBUTION)
    "All Aquaculture":  {"commodity_desc": "AQUACULTURE TOTALS"},
    "Food Fish":        {"commodity_desc": "FOOD FISH"},
    "Catfish":          {"commodity_desc": "FOOD FISH",  "class_desc": "CATFISH"},
    "Trout":            {"commodity_desc": "FOOD FISH",  "class_desc": "TROUT"},
    "Crustaceans":      {"commodity_desc": "CRUSTACEANS"},
    "Mollusks":         {"commodity_desc": "MOLLUSKS"},
    "Sport Fish":       {"commodity_desc": "SPORT FISH"},
    "Ornamental Fish":  {"commodity_desc": "ORNAMENTAL FISH"},
    "Baitfish":         {"commodity_desc": "BAITFISH"},
}
_AQUA_YEARS: list = [2022, 2017, 2012, 2007]
_ECHO_AQUA_URL = (
    "https://echodata.epa.gov/echo/cwa_rest_services.get_facilities"
    "?output=JSON&p_sic=0921,0273&p_act=Y&p_limit=10000"
)

_CORN_PLANTS = [
    {'co': 'Pinal Energy', 'st': 'AZ', 'city': 'Casa Grande', 'county': 'Pinal', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 19643000, 'start_yr': 2007, 'lat': 32.8773, 'lon': -111.7537},
    {'co': 'Alto Ingredients, Inc.', 'st': 'CA', 'city': 'Calipatria', 'county': 'Imperial', 'status': 'Hold', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 17857000, 'start_yr': 0, 'lat': 33.1256, 'lon': -115.514},
    {'co': 'AltraBiofuels Phoenix Bio Industries, LLC', 'st': 'CA', 'city': 'Goshen', 'county': 'Tulare', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 31500000, 'corn_bu': 11250000, 'start_yr': 2005, 'lat': 36.3526, 'lon': -119.4258},
    {'co': 'Aemetis', 'st': 'CA', 'city': 'Keyes', 'county': 'Stanislaus', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 65900000, 'corn_bu': None, 'start_yr': 2008, 'lat': 37.5605, 'lon': -120.9072},
    {'co': 'Azteca Milling(Gruma)', 'st': 'CA', 'city': 'Madera', 'county': 'Madera', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 9450000, 'start_yr': 1996, 'lat': 37.1716, 'lon': -119.7738},
    {'co': 'Madera Renewable Energy One', 'st': 'CA', 'city': 'Madera', 'county': 'Madera', 'status': 'Hold', 'cls': 'Ethanol', 'typ': 'Cellulosic', 'eth_gal': None, 'corn_bu': None, 'start_yr': None, 'lat': 37.1716, 'lon': -119.7738},
    {'co': 'Seaboard Energy California, LLC', 'st': 'CA', 'city': 'Madera', 'county': 'Madera', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 40000000, 'corn_bu': 14286000, 'start_yr': 2006, 'lat': 37.1716, 'lon': -119.7738},
    {'co': 'Calgren Renewable Fuels, LLC', 'st': 'CA', 'city': 'Pixley', 'county': 'Tulare', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 58000000, 'corn_bu': 20714000, 'start_yr': 2008, 'lat': 35.9788, 'lon': -119.2948},
    {'co': 'Parallel Products Inc', 'st': 'CA', 'city': 'Rancho Cucamonga', 'county': 'San Bernardino', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 3000000, 'corn_bu': None, 'start_yr': None, 'lat': 34.1064, 'lon': -117.5931},
    {'co': 'Aemetis', 'st': 'CA', 'city': 'Riverbank', 'county': 'Stanislaus', 'status': 'Repurposed', 'cls': 'SAF/Renewable Diesel', 'typ': 'Cellulosic', 'eth_gal': None, 'corn_bu': None, 'start_yr': None, 'lat': 37.7308, 'lon': -120.9353},
    {'co': 'Ingredion Inc.', 'st': 'CA', 'city': 'Stockton', 'county': 'San Joaquin', 'status': 'Idled', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 8050000, 'start_yr': 1980, 'lat': 37.9577, 'lon': -121.2908},
    {'co': 'Pelican Acquisition LLC', 'st': 'CA', 'city': 'Stockton', 'county': 'San Joaquin', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2008, 'lat': 37.9577, 'lon': -121.2908},
    {'co': 'MillerCoors/Merrick & Company', 'st': 'CO', 'city': 'Golden', 'county': 'Jefferson', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 2000000, 'corn_bu': None, 'start_yr': 1996, 'lat': 39.7555, 'lon': -105.2211},
    {'co': 'A.L. Gilbert Company (Colorado Sweet Gold)', 'st': 'CO', 'city': 'Johnstown', 'county': 'Weld', 'status': 'Closed', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 3150000, 'start_yr': 1983, 'lat': 40.337, 'lon': -104.913},
    {'co': 'Sterling Ethanol, LLC', 'st': 'CO', 'city': 'Sterling', 'county': 'Logan', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 19666000, 'start_yr': 2005, 'lat': 40.6255, 'lon': -103.2077},
    {'co': 'Front Range Energy, LLC', 'st': 'CO', 'city': 'Windsor', 'county': 'Weld', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 48000000, 'corn_bu': 17143000, 'start_yr': 2006, 'lat': 40.4775, 'lon': -104.901},
    {'co': 'Yuma Ethanol', 'st': 'CO', 'city': 'Yuma', 'county': 'Yuma', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 19666000, 'start_yr': 2007, 'lat': 40.1247, 'lon': -102.7238},
    {'co': 'U.N.O.I. Grain Mill', 'st': 'DE', 'city': 'Seaford', 'county': 'Sussex', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1060000, 'start_yr': 2008, 'lat': 38.6395, 'lon': -75.611},
    {'co': 'Frankens Energy LLC', 'st': 'FL', 'city': 'Vero Beach', 'county': 'Indian River', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Cellulosic', 'eth_gal': 8000000, 'corn_bu': None, 'start_yr': 2012, 'lat': 27.6386, 'lon': -80.3973},
    {'co': 'Alltech Baconton', 'st': 'GA', 'city': 'Baconton', 'county': 'Mitchell', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 500000, 'corn_bu': None, 'start_yr': 1988, 'lat': 31.3735, 'lon': -84.1427},
    {'co': 'POET (formerly Flint Hills Resources LP)', 'st': 'GA', 'city': 'Camilla', 'county': 'Mitchell', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 120000000, 'corn_bu': 10169500, 'start_yr': 2008, 'lat': 31.231, 'lon': -84.2107},
    {'co': 'Synergy Solutions Crisp County', 'st': 'GA', 'city': 'Cordele', 'county': 'Crisp', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 4000000, 'corn_bu': None, 'start_yr': 2015, 'lat': 31.9638, 'lon': -83.7827},
    {'co': 'Southeastern Mills, Inc.', 'st': 'GA', 'city': 'Rome', 'county': 'Floyd', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1650000, 'start_yr': 1947, 'lat': 34.257, 'lon': -85.1647},
    {'co': 'LanzaTech Freedom Pines Fuels LLC', 'st': 'GA', 'city': 'Soperton', 'county': 'Treutlen', 'status': 'Run', 'cls': 'SAF/Renewable Diesel', 'typ': 'ATJ', 'eth_gal': None, 'corn_bu': None, 'start_yr': None, 'lat': 32.3763, 'lon': -82.5954},
    {'co': 'Valero Renewable Fuels', 'st': 'IA', 'city': 'Albert City', 'county': 'Buena Vista', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 135000000, 'corn_bu': 47000000, 'start_yr': 2006, 'lat': 42.7794, 'lon': -94.9622},
    {'co': 'POET Biorefining (formerly Flint Hills Resources LP)', 'st': 'IA', 'city': 'Arthur', 'county': 'Ida', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 132000000, 'corn_bu': 44746000, 'start_yr': 2008, 'lat': 41.9847, 'lon': -95.34},
    {'co': 'POET Biorefining (Otter Creek Ethanol)', 'st': 'IA', 'city': 'Ashton', 'county': 'Osceola', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 68000000, 'corn_bu': 24286000, 'start_yr': 2004, 'lat': 43.313, 'lon': -95.7816},
    {'co': 'Elite Octane', 'st': 'IA', 'city': 'Atlantic', 'county': 'Cass', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 175000000, 'corn_bu': 58333000, 'start_yr': 2018, 'lat': 41.403, 'lon': -95.0152},
    {'co': 'Archer Daniels Midland', 'st': 'IA', 'city': 'Cedar Rapids', 'county': 'Linn', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 240000000, 'corn_bu': 60600000, 'start_yr': 1971, 'lat': 42.0084, 'lon': -91.6441},
    {'co': 'Cargill Inc.', 'st': 'IA', 'city': 'Cedar Rapids', 'county': 'Linn', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 31500000, 'start_yr': 1968, 'lat': 42.0084, 'lon': -91.6441},
    {'co': 'Ingredion, Inc.', 'st': 'IA', 'city': 'Cedar Rapids', 'county': 'Linn', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 17750000, 'start_yr': 1899, 'lat': 42.0084, 'lon': -91.6441},
    {'co': 'Quaker Foods', 'st': 'IA', 'city': 'Cedar Rapids', 'county': 'Linn', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 9360000, 'start_yr': 1873, 'lat': 42.0084, 'lon': -91.6441},
    {'co': 'Vantage Corn Processors (Archer Daniels Midland)', 'st': 'IA', 'city': 'Cedar Rapids', 'county': 'Linn', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 300000000, 'corn_bu': 72400000, 'start_yr': 2010, 'lat': 42.0084, 'lon': -91.6441},
    {'co': 'Valero Renewable Fuels', 'st': 'IA', 'city': 'Charles City', 'county': 'Floyd', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 49000000, 'start_yr': 2007, 'lat': 43.0666, 'lon': -92.6722},
    {'co': 'Archer Daniels Midland', 'st': 'IA', 'city': 'Clinton', 'county': 'Clinton', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 237000000, 'corn_bu': 78000000, 'start_yr': 1982, 'lat': 41.8447, 'lon': -90.1887},
    {'co': 'POET Biorefining (Tall Corn Ethanol Co-op)', 'st': 'IA', 'city': 'Coon Rapids', 'county': 'Carroll', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 65000000, 'corn_bu': 23214000, 'start_yr': 2002, 'lat': 41.8741, 'lon': -94.6808},
    {'co': 'POET Biorefining', 'st': 'IA', 'city': 'Corning', 'county': 'Adams', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 31846000, 'start_yr': 2007, 'lat': 40.9908, 'lon': -94.7375},
    {'co': 'Southwest Iowa Renewable Energy, LLC', 'st': 'IA', 'city': 'Council Bluffs', 'county': 'Pottawattamie', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 43430000, 'start_yr': 2009, 'lat': 41.2619, 'lon': -95.8608},
    {'co': 'The Andersons Marathon Holdings LLC', 'st': 'IA', 'city': 'Denison', 'county': 'Crawford', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': None, 'start_yr': 2005, 'lat': 42.0178, 'lon': -95.3553},
    {'co': 'Big River United Energy', 'st': 'IA', 'city': 'Dyersville', 'county': 'Dubuque', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 50000000, 'start_yr': 2008, 'lat': 42.4845, 'lon': -91.1211},
    {'co': 'Cargill Inc.', 'st': 'IA', 'city': 'Eddyville', 'county': 'Wapello', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 71000000, 'corn_bu': 43750000, 'start_yr': 1985, 'lat': 41.1562, 'lon': -92.6382},
    {'co': 'POET Biorefining (Voyager Ethanol)', 'st': 'IA', 'city': 'Emmetsburg', 'county': 'Palo Alto', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 68000000, 'corn_bu': 23491000, 'start_yr': 2005, 'lat': 43.1108, 'lon': -94.6782},
    {'co': 'POET-DSM Advanced Biofuel, LLC', 'st': 'IA', 'city': 'Emmetsburg', 'county': 'Palo Alto', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Cellulosic', 'eth_gal': 20000000, 'corn_bu': None, 'start_yr': 2014, 'lat': 43.1108, 'lon': -94.6782},
    {'co': 'POET Biorefining (formerly Flint Hills Resources LP)', 'st': 'IA', 'city': 'Fairbank', 'county': 'Buchanan', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 132000000, 'corn_bu': 44746000, 'start_yr': 2006, 'lat': 42.6392, 'lon': -92.0489},
    {'co': 'Cargill Inc.', 'st': 'IA', 'city': 'Fort Dodge', 'county': 'Webster', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 115000000, 'corn_bu': 54000000, 'start_yr': 2013, 'lat': 42.5044, 'lon': -94.191},
    {'co': 'Valero Renewable Fuels', 'st': 'IA', 'city': 'Fort Dodge', 'county': 'Webster', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 49000000, 'start_yr': 2005, 'lat': 42.5044, 'lon': -94.191},
    {'co': 'Quad-County Corn Processors', 'st': 'IA', 'city': 'Galva', 'county': 'Ida', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 37000000, 'corn_bu': 12500000, 'start_yr': 2002, 'lat': 42.5069, 'lon': -95.4172},
    {'co': 'Iowa Corn Processors LC', 'st': 'IA', 'city': 'Glidden', 'county': 'Carroll', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 7350000, 'start_yr': 2005, 'lat': 42.0569, 'lon': -94.7289},
    {'co': 'Corn, LP', 'st': 'IA', 'city': 'Goldfield', 'county': 'Wright', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 75000000, 'corn_bu': 25000000, 'start_yr': 2005, 'lat': 42.7355, 'lon': -93.9198},
    {'co': 'POET Biorefining', 'st': 'IA', 'city': 'Gowrie', 'county': 'Webster', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 31304000, 'start_yr': 2006, 'lat': 42.2807, 'lon': -94.2911},
    {'co': 'Louis Dreyfus Commodities', 'st': 'IA', 'city': 'Grand Junction', 'county': 'Greene', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 122000000, 'corn_bu': 43571000, 'start_yr': 2009, 'lat': 42.0389, 'lon': -94.2361},
    {'co': 'POET Biorefining', 'st': 'IA', 'city': 'Hanlontown', 'county': 'Worth', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80000000, 'corn_bu': 28571000, 'start_yr': 2004, 'lat': 43.2863, 'lon': -93.3583},
    {'co': 'Valero Renewable Fuels', 'st': 'IA', 'city': 'Hartley', 'county': 'Clay', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 49000000, 'start_yr': 2008, 'lat': 43.1791, 'lon': -95.4744},
    {'co': 'POET Biorefining (formerly Flint Hills Resources LP)', 'st': 'IA', 'city': 'Iowa Falls', 'county': 'Hardin', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 112000000, 'corn_bu': 37966000, 'start_yr': 2004, 'lat': 42.5224, 'lon': -93.2613},
    {'co': 'POET Biorefining', 'st': 'IA', 'city': 'Jewell', 'county': 'Hamilton', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 31304000, 'start_yr': 2006, 'lat': 42.3096, 'lon': -93.6388},
    {'co': 'Roquette', 'st': 'IA', 'city': 'Keokuk', 'county': 'Lee', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 36400000, 'start_yr': 1991, 'lat': 40.3984, 'lon': -91.3848},
    {'co': 'Valero Renewable Fuels', 'st': 'IA', 'city': 'Lakota', 'county': 'Kossuth', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 110000000, 'corn_bu': 38000000, 'start_yr': 2002, 'lat': 43.3774, 'lon': -94.0963},
    {'co': 'Homeland Energy Solutions', 'st': 'IA', 'city': 'Lawler', 'county': 'Chickasaw', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 200000000, 'corn_bu': 66000000, 'start_yr': 2009, 'lat': 43.0949, 'lon': -92.1577},
    {'co': 'Little Sioux Corn Processors, LP', 'st': 'IA', 'city': 'Marcus', 'county': 'Cherokee', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 165000000, 'corn_bu': 56897000, 'start_yr': 2003, 'lat': 42.8272, 'lon': -95.8075},
    {'co': 'Golden Grain Energy, LLC', 'st': 'IA', 'city': 'Mason City', 'county': 'Cerro Gordo', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 120000000, 'corn_bu': 42857000, 'start_yr': 2004, 'lat': 43.1536, 'lon': -93.201},
    {'co': 'New Energy Freedom', 'st': 'IA', 'city': 'Mason City', 'county': 'Stutsman', 'status': 'Build', 'cls': 'Ethanol/Ethylene', 'typ': 'Cellulosic', 'eth_gal': None, 'corn_bu': None, 'start_yr': None, 'lat': 43.1536, 'lon': -93.201},
    {'co': 'POET Biorefining (formerly Flint Hills Resources LP)', 'st': 'IA', 'city': 'Menlo', 'county': 'Guthrie', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 132000000, 'corn_bu': 44746000, 'start_yr': 2008, 'lat': 41.5188, 'lon': -94.3969},
    {'co': 'Plymouth Energy, LLC', 'st': 'IA', 'city': 'Merrill', 'county': 'Plymouth', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 18500000, 'start_yr': 2008, 'lat': 42.7219, 'lon': -96.2452},
    {'co': 'Grain Processing Corp.', 'st': 'IA', 'city': 'Muscatine', 'county': 'Muscatine', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 83000000, 'corn_bu': 60000000, 'start_yr': 2002, 'lat': 41.4245, 'lon': -91.0429},
    {'co': 'Lincolnway Energy, LLC', 'st': 'IA', 'city': 'Nevada', 'county': 'Story', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2006, 'lat': 42.0225, 'lon': -93.4541},
    {'co': 'Verbio North America Corp', 'st': 'IA', 'city': 'Nevada', 'county': 'Story', 'status': 'Expand', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 20000000, 'start_yr': 2015, 'lat': 42.0225, 'lon': -93.4541},
    {'co': 'POET Biorefining (formerly Flint Hills Resources LP)', 'st': 'IA', 'city': 'Shell Rock', 'county': 'Butler', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 128000000, 'corn_bu': 43390000, 'start_yr': 2008, 'lat': 42.7088, 'lon': -92.5838},
    {'co': 'Green Plains Renewable Energy', 'st': 'IA', 'city': 'Shenandoah', 'county': 'Page', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 82000000, 'corn_bu': 28258000, 'start_yr': 2007, 'lat': 40.7659, 'lon': -95.3733},
    {'co': 'Siouxland Energy & Livestock Coop', 'st': 'IA', 'city': 'Sioux Center', 'county': 'Sioux', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 31428600, 'start_yr': 2001, 'lat': 43.0791, 'lon': -96.1756},
    {'co': 'Absolute Energy, LLC', 'st': 'IA', 'city': 'St. Ansgar', 'county': 'Mitchell', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 46429000, 'start_yr': 2008, 'lat': 43.378, 'lon': -92.9183},
    {'co': 'Pine Lake Corn Processors, LLC', 'st': 'IA', 'city': 'Steamboat Rock', 'county': 'Hardin', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80700000, 'corn_bu': 26990000, 'start_yr': 2005, 'lat': 42.5225, 'lon': -93.0641},
    {'co': 'Green Plains Renewable Energy', 'st': 'IA', 'city': 'Superior', 'county': 'Dickinson', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2008, 'lat': 43.4477, 'lon': -95.1378},
    {'co': 'Big River Resources West Burlington, LLC', 'st': 'IA', 'city': 'West Burlington', 'county': 'Des Moines', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 46429000, 'start_yr': 2004, 'lat': 40.8211, 'lon': -91.165},
    {'co': 'Alto Ingredients, Inc.', 'st': 'ID', 'city': 'Burley', 'county': 'Cassia', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 61000000, 'corn_bu': 21786000, 'start_yr': 2008, 'lat': 42.5352, 'lon': -113.7924},
    {'co': 'Wyoming Ethanol', 'st': 'ID', 'city': 'Heyburn', 'county': 'Minidoka', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 20000000, 'corn_bu': 7143000, 'start_yr': 0, 'lat': 42.5583, 'lon': -113.7729},
    {'co': 'CHS Inc. (Patriot Renewable Fuels LLC)', 'st': 'IL', 'city': 'Annawan', 'county': 'Henry', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 46429000, 'start_yr': 2008, 'lat': 41.39, 'lon': -89.899},
    {'co': 'Ingredion Inc.', 'st': 'IL', 'city': 'Bedford Park', 'county': 'Cook', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 43750000, 'start_yr': 1910, 'lat': 41.7614, 'lon': -87.8575},
    {'co': 'Mano Metate Grain & Energy Commodities Plant', 'st': 'IL', 'city': 'Benton', 'county': 'Franklin', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 7000000, 'corn_bu': 2500000, 'start_yr': 2009, 'lat': 37.9967, 'lon': -88.9203},
    {'co': 'Alto Ingredients, Inc.', 'st': 'IL', 'city': 'Canton', 'county': 'Fulton', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 37000000, 'corn_bu': 13214000, 'start_yr': 2007, 'lat': 40.5589, 'lon': -90.0318},
    {'co': 'Bunge Milling & Danville Milling', 'st': 'IL', 'city': 'Danville', 'county': 'Vermilion', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 93000000, 'start_yr': 1979, 'lat': 40.1245, 'lon': -87.63},
    {'co': 'Archer Daniels Midland', 'st': 'IL', 'city': 'Decatur', 'county': 'Macon', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 375000000, 'corn_bu': 125000000, 'start_yr': 1967, 'lat': 39.8403, 'lon': -88.9548},
    {'co': 'Tate & Lyle North Amer.', 'st': 'IL', 'city': 'Decatur', 'county': 'Macon', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 50000000, 'start_yr': 1989, 'lat': 39.8403, 'lon': -88.9548},
    {'co': 'Big River Resources Galva, LLC', 'st': 'IL', 'city': 'Galva', 'county': 'Henry', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 128800000, 'corn_bu': 46000000, 'start_yr': 2009, 'lat': 41.1648, 'lon': -90.0426},
    {'co': 'One Earth Energy', 'st': 'IL', 'city': 'Gibson City', 'county': 'Ford', 'status': 'Expand', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 175000000, 'corn_bu': 60000000, 'start_yr': 2009, 'lat': 40.4586, 'lon': -88.3595},
    {'co': 'Marquis Energy, LLC', 'st': 'IL', 'city': 'Hennepin', 'county': 'Putnam', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 395000000, 'corn_bu': 132107000, 'start_yr': 2008, 'lat': 41.2542, 'lon': -89.3493},
    {'co': 'Marquis Sustainable Aviation Fuel', 'st': 'IL', 'city': 'Hennepin', 'county': 'Putnam', 'status': 'Proposed', 'cls': 'SAF/Renewable Diesel', 'typ': 'ATJ', 'eth_gal': None, 'corn_bu': 66666666, 'start_yr': None, 'lat': 41.2542, 'lon': -89.3493},
    {'co': 'Bunge Milling', 'st': 'IL', 'city': 'Kankakee', 'county': 'Kankakee', 'status': 'Idled', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 16250000, 'start_yr': 1963, 'lat': 41.12, 'lon': -87.8612},
    {'co': 'Adkins Energy, LLC', 'st': 'IL', 'city': 'Lena', 'county': 'Stephenson', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2002, 'lat': 42.3792, 'lon': -89.8218},
    {'co': 'Green Plains Renewable Energy', 'st': 'IL', 'city': 'Madison', 'county': 'Madison', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 31015000, 'start_yr': 2010, 'lat': 38.6803, 'lon': -90.1512},
    {'co': 'Lincolnland Agri-Energy, LLC', 'st': 'IL', 'city': 'Palestine', 'county': 'Crawford', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 66000000, 'corn_bu': 23571000, 'start_yr': 2004, 'lat': 39.0011, 'lon': -87.6117},
    {'co': 'Cargill Illinois Cereal Mills Div.', 'st': 'IL', 'city': 'Paris', 'county': 'Edgar', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 17500000, 'start_yr': 1934, 'lat': 39.6112, 'lon': -87.6967},
    {'co': 'Alto Ingredients, Inc.', 'st': 'IL', 'city': 'Pekin', 'county': 'Tazewell', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2007, 'lat': 40.5678, 'lon': -89.6498},
    {'co': 'Alto Ingredients, Inc.', 'st': 'IL', 'city': 'Pekin', 'county': 'Tazewell', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 100000000, 'corn_bu': 31429000, 'start_yr': 1981, 'lat': 40.5678, 'lon': -89.6498},
    {'co': 'Alto Ingredients, Inc.', 'st': 'IL', 'city': 'Pekin', 'county': 'Tazewell', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 1980, 'lat': 40.5678, 'lon': -89.6498},
    {'co': 'BioUrja Group', 'st': 'IL', 'city': 'Peoria', 'county': 'Peoria', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 1980, 'lat': 40.6936, 'lon': -89.589},
    {'co': 'CHS Inc. (Illinois River Energy LLC)', 'st': 'IL', 'city': 'Rochelle', 'county': 'Ogle', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 138000000, 'corn_bu': 49286000, 'start_yr': 2006, 'lat': 41.9231, 'lon': -89.0695},
    {'co': 'Center Ethanol Company', 'st': 'IL', 'city': 'Sauget', 'county': 'St. Clair', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 54000000, 'corn_bu': 19286000, 'start_yr': 2008, 'lat': 38.5912, 'lon': -90.1576},
    {'co': 'POET Biorefining', 'st': 'IN', 'city': 'Alexandria', 'county': 'Madison', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2008, 'lat': 40.2625, 'lon': -85.6775},
    {'co': 'Valero Renewable Fuels', 'st': 'IN', 'city': 'Bluffton', 'county': 'Wells', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 135000000, 'corn_bu': 47000000, 'start_yr': 2008, 'lat': 40.7381, 'lon': -85.1722},
    {'co': 'POET Biorefining', 'st': 'IN', 'city': 'Cloverdale', 'county': 'Putnam', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 92000000, 'corn_bu': 32857000, 'start_yr': 2011, 'lat': 39.5136, 'lon': -86.7941},
    {'co': 'The Andersons Marathon Holdings LLC', 'st': 'IN', 'city': 'Clymers', 'county': 'Cass', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': None, 'start_yr': 2007, 'lat': 40.8203, 'lon': -86.1444},
    {'co': 'Azteca Milling', 'st': 'IN', 'city': 'Evansville', 'county': 'Vanderburgh', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 9450000, 'start_yr': 1996, 'lat': 37.9748, 'lon': -87.5558},
    {'co': 'Nunn Milling Co.', 'st': 'IN', 'city': 'Evansville', 'county': 'Vanderburgh', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3180000, 'start_yr': 1926, 'lat': 37.9748, 'lon': -87.5558},
    {'co': 'Cargill Inc.', 'st': 'IN', 'city': 'Hammond', 'county': 'Lake', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 14350000, 'start_yr': 1908, 'lat': 41.5831, 'lon': -87.5001},
    {'co': 'Cardinal Ethanol', 'st': 'IN', 'city': 'Harrisville', 'county': 'Randolph', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 135000000, 'corn_bu': 48214000, 'start_yr': 2008, 'lat': 40.3158, 'lon': -84.953},
    {'co': 'Cargill Illinois Cereal Mills Div.', 'st': 'IN', 'city': 'Indianapolis', 'county': 'Marion', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 17500000, 'start_yr': 1976, 'lat': 39.7684, 'lon': -86.1581},
    {'co': 'Ingredion Inc.', 'st': 'IN', 'city': 'Indianapolis', 'county': 'Marion', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 8750000, 'start_yr': 1939, 'lat': 39.7684, 'lon': -86.1581},
    {'co': 'Tate & Lyle North Amer.', 'st': 'IN', 'city': 'Lafayette', 'county': 'Tippecanoe', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 25375000, 'start_yr': 1979, 'lat': 40.4191, 'lon': -86.8919},
    {'co': 'MGPI of Indiana, LLC', 'st': 'IN', 'city': 'Lawrenceburg', 'county': 'Dearborn', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 35000000, 'corn_bu': 12500000, 'start_yr': 1847, 'lat': 39.0909, 'lon': -84.85},
    {'co': 'Valero Renewable Fuels', 'st': 'IN', 'city': 'Linden', 'county': 'Montgomery', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 135000000, 'corn_bu': 47000000, 'start_yr': 2007, 'lat': 40.1881, 'lon': -86.9039},
    {'co': 'Agricor Inc.', 'st': 'IN', 'city': 'Marion', 'county': 'Grant', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 8400000, 'start_yr': 1983, 'lat': 40.5584, 'lon': -85.6591},
    {'co': 'Central Indiana Ethanol, LLC', 'st': 'IN', 'city': 'Marion', 'county': 'Grant', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2007, 'lat': 40.5584, 'lon': -85.6591},
    {'co': 'Green Plains Renewable Energy', 'st': 'IN', 'city': 'Mount Vernon', 'county': 'Posey', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 31015000, 'start_yr': 2010, 'lat': 37.9318, 'lon': -87.8948},
    {'co': 'Valero Renewable Fuels', 'st': 'IN', 'city': 'Mount Vernon', 'county': 'Posey', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 100000000, 'corn_bu': 35000000, 'start_yr': 2010, 'lat': 37.9318, 'lon': -87.8948},
    {'co': 'POET Biorefining', 'st': 'IN', 'city': 'North Manchester', 'county': 'Wabash', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2008, 'lat': 41.0006, 'lon': -85.7686},
    {'co': 'POET Biorefining', 'st': 'IN', 'city': 'Portland', 'county': 'Jay', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2007, 'lat': 40.4342, 'lon': -84.9771},
    {'co': 'Harvestone Iroquois Bio-Energy Company', 'st': 'IN', 'city': 'Rensselaer', 'county': 'Jasper', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2007, 'lat': 40.9364, 'lon': -87.1528},
    {'co': 'Prairie Mills Products', 'st': 'IN', 'city': 'Rochester', 'county': 'Fulton', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3120000, 'start_yr': 1920, 'lat': 41.0664, 'lon': -86.2158},
    {'co': 'POET Biorefining', 'st': 'IN', 'city': 'Shelbyville', 'county': 'Shelby', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 94000000, 'corn_bu': 33571000, 'start_yr': 2020, 'lat': 39.5214, 'lon': -85.7766},
    {'co': 'Verbio North America Corp (formerly South Bend Ethanol, LLC)', 'st': 'IN', 'city': 'South Bend', 'county': 'St. Joseph', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 85000000, 'corn_bu': 28000000, 'start_yr': 1984, 'lat': 41.6764, 'lon': -86.252},
    {'co': 'Grain Processing Corp.', 'st': 'IN', 'city': 'Washington', 'county': 'Daviess', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 37000000, 'corn_bu': 36000000, 'start_yr': 1997, 'lat': 38.6592, 'lon': -87.1722},
    {'co': 'Bunge Milling', 'st': 'KS', 'city': 'Atchison', 'county': 'Atchison', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 18000000, 'start_yr': 1950, 'lat': 39.5631, 'lon': -95.1216},
    {'co': 'MGPI Processing, Inc.', 'st': 'KS', 'city': 'Atchison', 'county': 'Atchison', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 25000000, 'corn_bu': 8929000, 'start_yr': 1941, 'lat': 39.5631, 'lon': -95.1216},
    {'co': 'Cereal Food Processors Inc.', 'st': 'KS', 'city': 'Bonner Springs', 'county': 'Wyandotte', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3400000, 'start_yr': 1989, 'lat': 39.0595, 'lon': -94.8858},
    {'co': 'Western Plains Energy, LLC', 'st': 'KS', 'city': 'Campus', 'county': 'Logan', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 52000000, 'corn_bu': 18571000, 'start_yr': 2004, 'lat': 38.9584, 'lon': -99.083},
    {'co': 'Cardinal Colwich LLC (formerly Element-ICM/Andersons)', 'st': 'KS', 'city': 'Colwich', 'county': 'Sedgwick', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 70000000, 'corn_bu': 25000000, 'start_yr': 2019, 'lat': 37.7786, 'lon': -97.5339},
    {'co': 'Bonanza BioEnergy, LLC', 'st': 'KS', 'city': 'Garden City', 'county': 'Finney', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 62000000, 'corn_bu': 22143000, 'start_yr': 2007, 'lat': 37.9717, 'lon': -100.8726},
    {'co': 'Reeve Agri-Energy, Inc.', 'st': 'KS', 'city': 'Garden City', 'county': 'Finney', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 13000000, 'corn_bu': 4643000, 'start_yr': 2005, 'lat': 37.9717, 'lon': -100.8726},
    {'co': 'East Kansas Agri - Energy, LLC', 'st': 'KS', 'city': 'Garnett', 'county': 'Anderson', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 48000000, 'corn_bu': 17143000, 'start_yr': 2005, 'lat': 38.2808, 'lon': -95.2433},
    {'co': 'E Caruso LLC', 'st': 'KS', 'city': 'Goodland', 'county': 'Sherman', 'status': 'Hold', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 20000000, 'corn_bu': 7143000, 'start_yr': 0, 'lat': 39.3486, 'lon': -101.7101},
    {'co': 'Seaboard Energy Kansas (formerly Synata Bio Inc.)', 'st': 'KS', 'city': 'Hugoton', 'county': 'Stevens', 'status': 'Repurposed', 'cls': 'Renewable Diesel', 'typ': 'Waste', 'eth_gal': 25000000, 'corn_bu': None, 'start_yr': 2014, 'lat': 37.1747, 'lon': -101.3493},
    {'co': 'ESE Alcohol Inc.', 'st': 'KS', 'city': 'Leoti', 'county': 'Wichita', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 2000000, 'corn_bu': 714000, 'start_yr': 1991, 'lat': 38.4839, 'lon': -101.3559},
    {'co': 'Arkalon Energy, LLC', 'st': 'KS', 'city': 'Liberal', 'county': 'Seward', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 110000000, 'corn_bu': 39286000, 'start_yr': 2007, 'lat': 37.0431, 'lon': -100.9237},
    {'co': 'Kansas Ethanol, LLC', 'st': 'KS', 'city': 'Lyons', 'county': 'Rice', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80000000, 'corn_bu': 28571000, 'start_yr': 2008, 'lat': 38.3439, 'lon': -98.2045},
    {'co': 'Amber Wave (owned by Summit Agricultural Group formerly Prairie Horizon Agri-Energy, LLC)', 'st': 'KS', 'city': 'Phillipsburg', 'county': 'Phillips', 'status': 'Repurposed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': None, 'start_yr': 2007, 'lat': 39.7589, 'lon': -99.3228},
    {'co': 'Pratt Energy', 'st': 'KS', 'city': 'Pratt', 'county': 'Pratt', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 57000000, 'corn_bu': 20357000, 'start_yr': 2007, 'lat': 37.6437, 'lon': -98.737},
    {'co': 'Purefield Ingredients Llc (SVPGlobal)', 'st': 'KS', 'city': 'Russell', 'county': 'Russell', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': None, 'start_yr': 2001, 'lat': 38.8992, 'lon': -98.8585},
    {'co': 'Butamax Advanced Biofuels LLC (formerly Nesika Energy LLC)', 'st': 'KS', 'city': 'Scandia', 'county': 'Republic', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 10000000, 'corn_bu': 3571000, 'start_yr': 2008, 'lat': 39.7875, 'lon': -97.7739},
    {'co': "Scott's Auburn Mills Inc.", 'st': 'KY', 'city': 'Auburn', 'county': 'Logan', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 2100000, 'start_yr': 1876, 'lat': 36.8622, 'lon': -86.7103},
    {'co': 'Burkmann Feeds', 'st': 'KY', 'city': 'Bowling Green', 'county': 'Warren', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1530000, 'start_yr': 1950, 'lat': 36.9903, 'lon': -86.4436},
    {'co': 'Azteca Milling', 'st': 'KY', 'city': 'Henderson', 'county': 'Henderson', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 9450000, 'start_yr': 2001, 'lat': 37.8361, 'lon': -87.59},
    {'co': 'Commonwealth Agri-Energy', 'st': 'KY', 'city': 'Hopkinsville', 'county': 'Christian', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 48000000, 'corn_bu': 17143000, 'start_yr': 2004, 'lat': 36.8656, 'lon': -87.4886},
    {'co': 'Hopkinsville Milling Co.', 'st': 'KY', 'city': 'Hopkinsville', 'county': 'Christian', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 6500000, 'start_yr': 1908, 'lat': 36.8656, 'lon': -87.4886},
    {'co': 'Parallel Products Inc', 'st': 'KY', 'city': 'Louisville', 'county': 'Jefferson', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 5000000, 'corn_bu': None, 'start_yr': None, 'lat': 38.2527, 'lon': -85.7585},
    {'co': 'Weisenberger Mills Inc.', 'st': 'KY', 'city': 'Midway', 'county': 'Woodford', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 122500, 'start_yr': 1865, 'lat': 38.1512, 'lon': -84.6908},
    {'co': 'Alltech Springfield', 'st': 'KY', 'city': 'Springfield', 'county': 'Washington', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Waste', 'eth_gal': 1040000, 'corn_bu': None, 'start_yr': 2012, 'lat': 37.687, 'lon': -85.2191},
    {'co': 'Washington Quality Foods', 'st': 'MD', 'city': 'Ellicott City', 'county': 'Howard', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 2820000, 'start_yr': 1974, 'lat': 39.2676, 'lon': -76.7985},
    {'co': 'The Andersons Marathon Holdings LLC', 'st': 'MI', 'city': 'Albion', 'county': 'Calhoun', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 147000000, 'corn_bu': 52500000, 'start_yr': 2006, 'lat': 42.2431, 'lon': -84.7519},
    {'co': 'GranBio (formerly American Process Inc. - Alpena Biorefinery)', 'st': 'MI', 'city': 'Alpena', 'county': 'Aplena', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Cellulosic', 'eth_gal': None, 'corn_bu': None, 'start_yr': 2013, 'lat': 45.0617, 'lon': -83.4327},
    {'co': 'POET Biorefining', 'st': 'MI', 'city': 'Caro', 'county': 'Tuscola', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80000000, 'corn_bu': 28571000, 'start_yr': 2002, 'lat': 43.4878, 'lon': -83.3966},
    {'co': 'Liberty Renewable Fuels LLC', 'st': 'MI', 'city': 'Ithaca', 'county': 'Gratiot', 'status': 'Hold', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 100000000, 'corn_bu': 35714000, 'start_yr': 0, 'lat': 43.2945, 'lon': -84.6069},
    {'co': 'Marysville Ethanol, LLC', 'st': 'MI', 'city': 'Marysville', 'county': 'St. Clair', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2007, 'lat': 42.9123, 'lon': -82.7297},
    {'co': 'Valero Renewable Fuels', 'st': 'MI', 'city': 'Riga', 'county': 'Lenawee', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 19000000, 'start_yr': 2007, 'lat': 41.8122, 'lon': -83.9102},
    {'co': 'Carbon Green Bioenergy', 'st': 'MI', 'city': 'Woodbury', 'county': 'Eaton', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 59000000, 'corn_bu': 21071000, 'start_yr': 2006, 'lat': 42.65, 'lon': -84.9786},
    {'co': 'POET Biorefining (Agra Resources Co-op)', 'st': 'MN', 'city': 'Albert Lea', 'county': 'Freeborn', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 48000000, 'corn_bu': 17143000, 'start_yr': 1999, 'lat': 43.648, 'lon': -93.3683},
    {'co': 'Bushmills Ethanol, Inc.', 'st': 'MN', 'city': 'Atwater', 'county': 'Kandiyohi', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2005, 'lat': 45.1344, 'lon': -94.7804},
    {'co': 'Chippewa Valley Ethanol Co.', 'st': 'MN', 'city': 'Benson', 'county': 'Swift', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 18519000, 'start_yr': 1996, 'lat': 45.3152, 'lon': -95.6011},
    {'co': 'POET Biorefining (Ethanol2000 LLP)', 'st': 'MN', 'city': 'Bingham Lake', 'county': 'Cottonwood', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 35000000, 'corn_bu': 12500000, 'start_yr': 1997, 'lat': 43.9083, 'lon': -95.04},
    {'co': 'Buffalo Lake Advanced Biofuels', 'st': 'MN', 'city': 'Buffalo Lake', 'county': 'Renville', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 18000000, 'corn_bu': 6429000, 'start_yr': 1997, 'lat': 44.733, 'lon': -94.6117},
    {'co': 'Al-Corn Clean Fuel', 'st': 'MN', 'city': 'Claremont', 'county': 'Dodge', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 46429000, 'start_yr': 1996, 'lat': 44.0372, 'lon': -92.9897},
    {'co': 'Homestead Mills', 'st': 'MN', 'city': 'Cook', 'county': 'St. Louis', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1300000, 'start_yr': 1939, 'lat': 47.8527, 'lon': -92.688},
    {'co': 'Green Plains Renewable Energy (Buffalo Lake Energy, LLC)', 'st': 'MN', 'city': 'Fairmont', 'county': 'Martin', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 119000000, 'corn_bu': 41006000, 'start_yr': 2008, 'lat': 43.6516, 'lon': -94.4608},
    {'co': 'Green Plains Renewable Energy - Otter Tail', 'st': 'MN', 'city': 'Fergus Falls', 'county': 'Otter Tail', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 20429000, 'start_yr': 2008, 'lat': 46.283, 'lon': -96.0778},
    {'co': 'Granite Falls Energy, LLC', 'st': 'MN', 'city': 'Granite Falls', 'county': 'Yellow Medicine', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 64000000, 'corn_bu': 22069000, 'start_yr': 2005, 'lat': 44.8127, 'lon': -95.5458},
    {'co': 'Heron Lake BioEnergy LLC', 'st': 'MN', 'city': 'Heron Lake', 'county': 'Jackson', 'status': 'Expansion', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 66036000, 'corn_bu': 22615000, 'start_yr': 2008, 'lat': 43.7919, 'lon': -95.3219},
    {'co': 'Guardian Energy LLC', 'st': 'MN', 'city': 'Janesville', 'county': 'Waseca', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 164000000, 'corn_bu': 55593220, 'start_yr': 2008, 'lat': 44.1194, 'lon': -93.7138},
    {'co': 'POET Biorefining', 'st': 'MN', 'city': 'Lake Crystal', 'county': 'Blue Earth', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 68000000, 'corn_bu': 24286000, 'start_yr': 2005, 'lat': 44.1038, 'lon': -94.2188},
    {'co': 'Highwater Ethanol LLC', 'st': 'MN', 'city': 'Lamberton', 'county': 'Redwood', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 67000000, 'corn_bu': 22112211, 'start_yr': 2009, 'lat': 44.2333, 'lon': -95.2647},
    {'co': 'White Dog Labs', 'st': 'MN', 'city': 'Little Falls', 'county': 'Morrison', 'status': 'Run', 'cls': 'Ethanol/Aquaculture Feed', 'typ': 'Dry', 'eth_gal': 18000000, 'corn_bu': 6429000, 'start_yr': 1999, 'lat': 45.9763, 'lon': -94.3622},
    {'co': 'Gevo (Agri-Energy LLC)', 'st': 'MN', 'city': 'Luverne', 'county': 'Rock', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 19000000, 'corn_bu': 6786000, 'start_yr': 1999, 'lat': 43.6539, 'lon': -96.2133},
    {'co': 'Archer Daniels Midland', 'st': 'MN', 'city': 'Marshall', 'county': 'Lyon', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 48000000, 'corn_bu': 42000000, 'start_yr': 2002, 'lat': 44.4475, 'lon': -95.7915},
    {'co': 'DENCO II, LLC', 'st': 'MN', 'city': 'Morris', 'county': 'Stevens', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 30000000, 'corn_bu': 11538000, 'start_yr': 1999, 'lat': 45.5861, 'lon': -95.9139},
    {'co': 'POET Biorefining (Pro Corn LLC)', 'st': 'MN', 'city': 'Preston', 'county': 'Fillmore', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 19643000, 'start_yr': 1998, 'lat': 43.6702, 'lon': -92.0832},
    {'co': 'Valero Renewable Fuels', 'st': 'MN', 'city': 'Welcome', 'county': 'Martin', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 49000000, 'start_yr': 2009, 'lat': 43.6671, 'lon': -94.6188},
    {'co': 'Greenfield Global (formerly Corn Plus)', 'st': 'MN', 'city': 'Winnebago', 'county': 'Faribault', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 43000000, 'corn_bu': 16538000, 'start_yr': 1994, 'lat': 43.7677, 'lon': -94.1659},
    {'co': 'Heartland Corn Products', 'st': 'MN', 'city': 'Winthrop', 'county': 'Sibley', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 50000000, 'start_yr': 1994, 'lat': 44.543, 'lon': -94.3664},
    {'co': 'Show Me Ethanol', 'st': 'MO', 'city': 'Carrollton', 'county': 'Carroll', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 65000000, 'corn_bu': 22429000, 'start_yr': 2008, 'lat': 39.3582, 'lon': -93.4965},
    {'co': 'Golden Triangle Energy, LLC', 'st': 'MO', 'city': 'Craig', 'county': 'Holt', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 21000000, 'corn_bu': 7500000, 'start_yr': 2001, 'lat': 40.1947, 'lon': -95.3711},
    {'co': 'Hodgson Mill Inc.', 'st': 'MO', 'city': 'Gainesville', 'county': 'Ozark', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3050000, 'start_yr': 1883, 'lat': 36.6031, 'lon': -92.4282},
    {'co': 'Ingredion Inc.', 'st': 'MO', 'city': 'Kansas City', 'county': 'Jackson', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 8750000, 'start_yr': 1925, 'lat': 39.1001, 'lon': -94.5781},
    {'co': 'POET Biorefining', 'st': 'MO', 'city': 'Laddonia', 'county': 'Audrain', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80000000, 'corn_bu': 28571000, 'start_yr': 2006, 'lat': 39.2425, 'lon': -91.6454},
    {'co': 'POET Biorefining (Northeast Missouri Grain LLC)', 'st': 'MO', 'city': 'Macon', 'county': 'Macon', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 19643000, 'start_yr': 2000, 'lat': 39.8416, 'lon': -92.5675},
    {'co': 'Mid-Missouri Energy, Inc.', 'st': 'MO', 'city': 'Malta Bend', 'county': 'Saline', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 2005, 'lat': 39.1934, 'lon': -93.3631},
    {'co': 'SEMO Milling, LLC', 'st': 'MO', 'city': 'Scott City', 'county': 'Scott', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 8660960, 'start_yr': 2007, 'lat': 37.2163, 'lon': -89.5261},
    {'co': 'Lifeline Foods, LLC', 'st': 'MO', 'city': 'St. Joseph', 'county': 'Buchanan', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 7200000, 'start_yr': 2001, 'lat': 39.7686, 'lon': -94.8466},
    {'co': 'Lifeline Foods, LLC - Ethanol (ICM Biofuels LLC)', 'st': 'MO', 'city': 'St. Joseph', 'county': 'Buchanan', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 17857000, 'start_yr': 2007, 'lat': 39.7686, 'lon': -94.8466},
    {'co': 'The Attala Co.', 'st': 'MS', 'city': 'Kosciusko', 'county': 'Attala', 'status': 'Closed', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 4300000, 'start_yr': 1937, 'lat': 33.0576, 'lon': -89.5876},
    {'co': 'Ergon Ethanol', 'st': 'MS', 'city': 'Vicksburg', 'county': 'Warren', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 54000000, 'corn_bu': 19286000, 'start_yr': 2008, 'lat': 32.3528, 'lon': -90.8777},
    {'co': 'Alltech Eden', 'st': 'NC', 'city': 'Eden', 'county': 'Rockingham', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Waste', 'eth_gal': 1040000, 'corn_bu': None, 'start_yr': None, 'lat': 36.4885, 'lon': -79.7667},
    {'co': 'House-Autry Mills, Inc.', 'st': 'NC', 'city': 'Four Oaks', 'county': 'Johnston', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 250000, 'start_yr': 2001, 'lat': 35.4456, 'lon': -78.4295},
    {'co': 'King Milling Co.', 'st': 'NC', 'city': 'King', 'county': 'Stokes', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3120000, 'start_yr': 1920, 'lat': 36.2807, 'lon': -80.3592},
    {'co': 'Renwood Mills', 'st': 'NC', 'city': 'Newton', 'county': 'Catawba', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3060000, 'start_yr': 1935, 'lat': 35.6631, 'lon': -81.2219},
    {'co': 'Benchmark Renewable Energy (formerly Tyton Biofuels LLC)', 'st': 'NC', 'city': 'Raeford', 'county': 'Hoke', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 4285800, 'start_yr': 2010, 'lat': 34.981, 'lon': -79.2242},
    {'co': 'Lakeside Mills, Inc.', 'st': 'NC', 'city': 'Seven Springs', 'county': 'Wayne', 'status': 'Closed', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3060000, 'start_yr': 1736, 'lat': 35.2266, 'lon': -77.8465},
    {'co': 'Lakeside Mills, Inc.', 'st': 'NC', 'city': 'Spindale', 'county': 'Rutherford', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3060000, 'start_yr': 1736, 'lat': 35.3575, 'lon': -81.9312},
    {'co': 'Tharaldson Ethanol Plant', 'st': 'ND', 'city': 'Casselton', 'county': 'Cass', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 185000000, 'corn_bu': 66071000, 'start_yr': 2011, 'lat': 46.9005, 'lon': -97.2112},
    {'co': 'Alchem Ltd. LLP', 'st': 'ND', 'city': 'Grafton', 'county': 'Walsh', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 10500000, 'corn_bu': 3750000, 'start_yr': 1995, 'lat': 48.4122, 'lon': -97.4106},
    {'co': 'Fufeng Group Ltd.', 'st': 'ND', 'city': 'Grand Forks', 'county': 'Grand Forks', 'status': 'Hold', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 25000000, 'start_yr': None, 'lat': 47.9252, 'lon': -97.0306},
    {'co': 'Red River Biorefinery, LLC', 'st': 'ND', 'city': 'Grand Forks', 'county': 'Grand Forks', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 16500000, 'corn_bu': None, 'start_yr': 2020, 'lat': 47.9252, 'lon': -97.0306},
    {'co': 'Guardian Hankinson, LLC - Doubling', 'st': 'ND', 'city': 'Hankinson', 'county': 'Richland', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 150000000, 'corn_bu': 52000000, 'start_yr': 1995, 'lat': 46.0697, 'lon': -96.9017},
    {'co': 'Harvestone Dakota Spirit', 'st': 'ND', 'city': 'Jamestown', 'county': 'Stutsman', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 77000000, 'corn_bu': 27500000, 'start_yr': 2015, 'lat': 46.9105, 'lon': -98.7084},
    {'co': 'Net Zero Holdings (Gevo)', 'st': 'ND', 'city': 'Richardton', 'county': 'Stark', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 65000000, 'corn_bu': 22821000, 'start_yr': 2008, 'lat': 46.8839, 'lon': -102.3157},
    {'co': 'Harvestone Blue Flint', 'st': 'ND', 'city': 'Underwood', 'county': 'McLean', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 74500000, 'corn_bu': 26607000, 'start_yr': 2008, 'lat': 47.4564, 'lon': -101.1371},
    {'co': 'Cargill Inc.', 'st': 'ND', 'city': 'Wahpeton', 'county': 'Richland', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 30000000, 'start_yr': 2004, 'lat': 46.2659, 'lon': -96.6089},
    {'co': 'SweetPro Feeds', 'st': 'ND', 'city': 'Walhalla', 'county': 'Pembina', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 30000000, 'corn_bu': 10714000, 'start_yr': 2009, 'lat': 48.9233, 'lon': -97.9181},
    {'co': 'E Energy Adams, LLC', 'st': 'NE', 'city': 'Adams', 'county': 'Gage', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 101000000, 'corn_bu': 36071000, 'start_yr': 1992, 'lat': 40.5126, 'lon': -98.5149},
    {'co': 'Valero Renewable Fuels', 'st': 'NE', 'city': 'Albion', 'county': 'Boone', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 135000000, 'corn_bu': 47000000, 'start_yr': 1979, 'lat': 41.6929, 'lon': -98.0012},
    {'co': 'Sandhills Renewable Energy LLC (formerly Green Plains Renewable Energy)', 'st': 'NE', 'city': 'Atkinson', 'county': 'Holt', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 18000000, 'start_yr': 2008, 'lat': 42.5314, 'lon': -98.9782},
    {'co': 'KAAPA Partners Aurora, LLC', 'st': 'NE', 'city': 'Aurora', 'county': 'Hamilton', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 45000000, 'corn_bu': 16071000, 'start_yr': 1995, 'lat': 40.8669, 'lon': -98.0045},
    {'co': 'KAAPA Partners Aurora, LLC', 'st': 'NE', 'city': 'Aurora', 'county': 'Hamilton', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 150000000, 'corn_bu': 53571000, 'start_yr': 2011, 'lat': 40.8669, 'lon': -98.0045},
    {'co': 'Cargill Inc.', 'st': 'NE', 'city': 'Blair', 'county': 'Washington', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 210000000, 'corn_bu': 70000000, 'start_yr': 1995, 'lat': 41.5438, 'lon': -96.136},
    {'co': 'Bridgeport Ethanol LLC', 'st': 'NE', 'city': 'Bridgeport', 'county': 'Morrill', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 54000000, 'corn_bu': 19286000, 'start_yr': 2008, 'lat': 41.6666, 'lon': -103.0977},
    {'co': 'Nebraska Corn Processing LLC', 'st': 'NE', 'city': 'Cambridge', 'county': 'Furnas', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 17857000, 'start_yr': 2008, 'lat': 40.282, 'lon': -100.166},
    {'co': 'Green Plains Renewable Energy', 'st': 'NE', 'city': 'Central City', 'county': 'Merrick', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 116000000, 'corn_bu': 41429000, 'start_yr': 2004, 'lat': 41.1158, 'lon': -98.0017},
    {'co': 'Archer Daniels Midland', 'st': 'NE', 'city': 'Columbus', 'county': 'Platte', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': 100000000, 'corn_bu': 48000000, 'start_yr': 1992, 'lat': 41.4293, 'lon': -97.3581},
    {'co': 'Vantage Corn Processors (Archer Daniels Midland)', 'st': 'NE', 'city': 'Columbus', 'county': 'Platte', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 313000000, 'corn_bu': 54000000, 'start_yr': 2009, 'lat': 41.4293, 'lon': -97.3581},
    {'co': 'Bunge Milling', 'st': 'NE', 'city': 'Crete', 'county': 'Saline', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 17500000, 'start_yr': 1979, 'lat': 40.6257, 'lon': -96.9614},
    {'co': 'POET Biorefining (formerly Flint Hills Resources LP)', 'st': 'NE', 'city': 'Fairmont', 'county': 'Fillmore', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 128000000, 'corn_bu': 45714000, 'start_yr': 2007, 'lat': 40.6361, 'lon': -97.5853},
    {'co': 'Lincoln Premium Poultry', 'st': 'NE', 'city': 'Fremont', 'county': 'Dodge', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 18200000, 'start_yr': 2019, 'lat': 41.4338, 'lon': -96.496},
    {'co': 'AGP', 'st': 'NE', 'city': 'Hastings', 'county': 'Adams', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 52000000, 'corn_bu': 18571000, 'start_yr': 1995, 'lat': 40.5861, 'lon': -98.3899},
    {'co': 'Chief Ethanol Fuels, Inc.', 'st': 'NE', 'city': 'Hastings', 'county': 'Adams', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 70000000, 'corn_bu': 25000000, 'start_yr': 1985, 'lat': 40.5861, 'lon': -98.3899},
    {'co': 'Siouxland Ethanol, LLC', 'st': 'NE', 'city': 'Jackson', 'county': 'Dakota', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 96400000, 'corn_bu': 34429000, 'start_yr': 2007, 'lat': 42.4483, 'lon': -96.5655},
    {'co': 'Chief Ethanol Fuels Inc.', 'st': 'NE', 'city': 'Lexington', 'county': 'Dawson', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 17857000, 'start_yr': 2005, 'lat': 40.7788, 'lon': -99.7415},
    {'co': 'ADM Milling', 'st': 'NE', 'city': 'Lincoln', 'county': 'Lancaster', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3640000, 'start_yr': 1908, 'lat': 40.8089, 'lon': -96.7078},
    {'co': 'Mid America Agri Products/Wheatland, LLC', 'st': 'NE', 'city': 'Madrid', 'county': 'Perkins', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 51000000, 'corn_bu': 18214000, 'start_yr': 2007, 'lat': 40.8502, 'lon': -101.5437},
    {'co': 'Alten LLC', 'st': 'NE', 'city': 'Mead', 'county': 'Saunders', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 24100000, 'corn_bu': 8607000, 'start_yr': 2015, 'lat': 41.2266, 'lon': -96.4893},
    {'co': 'KAAPA Ethanol, LLC', 'st': 'NE', 'city': 'Minden', 'county': 'Kearney', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 85000000, 'corn_bu': 29825000, 'start_yr': 2003, 'lat': 40.4985, 'lon': -98.9477},
    {'co': 'Central Indiana Ethanol, LLC (formerly Louis Dreyfus Commodities [Elkhorn Valley Ethanol])', 'st': 'NE', 'city': 'Norfolk', 'county': 'Madison', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 17857000, 'start_yr': 2007, 'lat': 42.0283, 'lon': -97.417},
    {'co': 'GreenAmerica Biofuels Ord LLC', 'st': 'NE', 'city': 'Ord', 'county': 'Valley', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 68000000, 'corn_bu': 24286000, 'start_yr': 2007, 'lat': 41.6033, 'lon': -98.9262},
    {'co': 'Husker Ag, LLC', 'st': 'NE', 'city': 'Plainview', 'county': 'Pierce', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 109000000, 'corn_bu': 37586000, 'start_yr': 2003, 'lat': 42.3496, 'lon': -97.7935},
    {'co': 'KAAPA Ethanol Ravenna LLC', 'st': 'NE', 'city': 'Ravenna', 'county': 'Buffalo', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 125000000, 'corn_bu': 43103000, 'start_yr': 2007, 'lat': 41.0254, 'lon': -98.9126},
    {'co': 'Midwest Renewable Energy, LLC', 'st': 'NE', 'city': 'Sutherland', 'county': 'Lincoln', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 28000000, 'corn_bu': 10000000, 'start_yr': 2000, 'lat': 41.1571, 'lon': -101.1262},
    {'co': 'Trenton Agri Products, LLC', 'st': 'NE', 'city': 'Trenton', 'county': 'Hitchcock', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 19643000, 'start_yr': 2004, 'lat': 40.1758, 'lon': -101.0132},
    {'co': 'Green Plains Renewable Energy', 'st': 'NE', 'city': 'Wood River', 'county': 'Hall', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 120000000, 'corn_bu': 41351000, 'start_yr': 2008, 'lat': 40.8211, 'lon': -98.6006},
    {'co': 'Green Plains Renewable Energy', 'st': 'NE', 'city': 'York', 'county': 'York', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 18519000, 'start_yr': 1994, 'lat': 40.8716, 'lon': -97.6001},
    {'co': 'Natural Chem Group LLC', 'st': 'NM', 'city': 'Portales', 'county': 'Roosevelt', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 30000000, 'corn_bu': 10714000, 'start_yr': 1985, 'lat': 34.186, 'lon': -103.3373},
    {'co': 'Attis Industries (formerly Sunoco)', 'st': 'NY', 'city': 'Fulton', 'county': 'Oswego', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 100000000, 'corn_bu': 11904666, 'start_yr': 2008, 'lat': 43.1062, 'lon': -74.4462},
    {'co': 'Western New York Energy, LLC', 'st': 'NY', 'city': 'Medina', 'county': 'Niagara', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 65000000, 'corn_bu': 11607000, 'start_yr': 1985, 'lat': 43.2203, 'lon': -78.3866},
    {'co': 'Champlain Valley Milling Corp.', 'st': 'NY', 'city': 'Willsboro', 'county': 'Essex', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 510000, 'start_yr': 1985, 'lat': 44.3621, 'lon': -73.3913},
    {'co': 'Valero Renewable Fuels', 'st': 'OH', 'city': 'Bloomingburg', 'county': 'Fayette', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 135000000, 'corn_bu': 47000000, 'start_yr': 2008, 'lat': 39.6051, 'lon': -83.3955},
    {'co': 'Clifton Mills Co.', 'st': 'OH', 'city': 'Clifton', 'county': 'Greene', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3060000, 'start_yr': 1802, 'lat': 39.7968, 'lon': -83.8259},
    {'co': 'Three Rivers Energy (CE Acquisitions Co LLC)', 'st': 'OH', 'city': 'Coshocton', 'county': 'Coshocton', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 50000000, 'corn_bu': 17857000, 'start_yr': 2008, 'lat': 40.2906, 'lon': -81.9271},
    {'co': 'Cargill Inc.', 'st': 'OH', 'city': 'Dayton', 'county': 'Montgomery', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 17150000, 'start_yr': 1973, 'lat': 39.7589, 'lon': -84.1916},
    {'co': 'POET Biorefining', 'st': 'OH', 'city': 'Fostoria', 'county': 'Seneca', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2008, 'lat': 41.1574, 'lon': -83.4141},
    {'co': 'The Andersons Marathon Holdings LLC', 'st': 'OH', 'city': 'Greenville', 'county': 'Darke', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 154000000, 'corn_bu': 55000000, 'start_yr': 2008, 'lat': 40.1024, 'lon': -84.6333},
    {'co': 'POET Biorefining', 'st': 'OH', 'city': 'Leipsic', 'county': 'Putnam', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2008, 'lat': 41.0984, 'lon': -83.9847},
    {'co': 'Guardian Lima, LLC', 'st': 'OH', 'city': 'Lima', 'county': 'Allen', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 84000000, 'corn_bu': 30000000, 'start_yr': 2008, 'lat': 40.74, 'lon': -84.105},
    {'co': 'POET Biorefining', 'st': 'OH', 'city': 'Marion', 'county': 'Marion', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 154000000, 'corn_bu': 51505000, 'start_yr': 2008, 'lat': 40.5885, 'lon': -83.1895},
    {'co': 'Shawnee Milling Co.', 'st': 'OK', 'city': 'Shawnee', 'county': 'Pottawatomie', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 2750000, 'start_yr': 1891, 'lat': 35.3273, 'lon': -96.9253},
    {'co': 'Alto Ingredients, Inc.', 'st': 'OR', 'city': 'Boardman', 'county': 'Morrow', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 40000000, 'corn_bu': 3571500, 'start_yr': 2007, 'lat': 45.8399, 'lon': -119.7006},
    {'co': 'ZeaChem Inc.', 'st': 'OR', 'city': 'Boardman', 'county': 'Morrow', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Cellulosic', 'eth_gal': None, 'corn_bu': None, 'start_yr': None, 'lat': 45.8399, 'lon': -119.7006},
    {'co': 'Columbia Pacific Bio-Refinery', 'st': 'OR', 'city': 'Clatskanie', 'county': 'Columbia', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 108000000, 'corn_bu': 38571000, 'start_yr': 2008, 'lat': 46.1037, 'lon': -123.2048},
    {'co': 'Summit Natural Energy, Inc.', 'st': 'OR', 'city': 'Cornelius', 'county': 'Washington', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 1000000, 'corn_bu': None, 'start_yr': 2008, 'lat': 45.5198, 'lon': -123.0556},
    {'co': 'Pennsylvania Grain Processing LLC', 'st': 'PA', 'city': 'Clearfield', 'county': 'Clearfield', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 128000000, 'corn_bu': 22857000, 'start_yr': 2010, 'lat': 40.9909, 'lon': -78.4457},
    {'co': 'H.R. Wentzel Sons, Inc.', 'st': 'PA', 'city': 'Newport', 'county': 'Perry', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1530000, 'start_yr': 1805, 'lat': 40.4779, 'lon': -77.1305},
    {'co': 'Vermont Milling LTD', 'st': 'PA', 'city': 'Pottsgrove', 'county': 'Montgomery', 'status': 'Closed', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1530000, 'start_yr': 1950, 'lat': 40.2626, 'lon': -75.6108},
    {'co': 'Allen Brothers Milling Co.', 'st': 'SC', 'city': 'Columbia', 'county': 'Richland', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1250000, 'start_yr': 1900, 'lat': 34.0008, 'lon': -81.0352},
    {'co': 'Glacial Lakes Energy, LLC (Hub City Energy Llc)', 'st': 'SD', 'city': 'Aberdeen', 'county': 'Brown', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 48000000, 'corn_bu': 17143000, 'start_yr': 2008, 'lat': 45.465, 'lon': -98.4878},
    {'co': 'Valero Renewable Fuels', 'st': 'SD', 'city': 'Aurora', 'county': 'Brookings', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 140000000, 'corn_bu': 49000000, 'start_yr': 2003, 'lat': 43.6963, 'lon': -98.5722},
    {'co': 'POET Biorefining (Northern Lights Ethanol LLC)', 'st': 'SD', 'city': 'Big Stone City', 'county': 'Grant', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 105000000, 'corn_bu': 37500000, 'start_yr': 2002, 'lat': 45.2916, 'lon': -96.4628},
    {'co': 'POET Biorefining (Great Plains Ethanol LLC)', 'st': 'SD', 'city': 'Chancellor', 'county': 'Turner', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 125000000, 'corn_bu': 44643000, 'start_yr': 2003, 'lat': 43.3721, 'lon': -96.9872},
    {'co': 'POET Biorefining (James Valley Ethanol LLC)', 'st': 'SD', 'city': 'Groton', 'county': 'Brown', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 68000000, 'corn_bu': 24286000, 'start_yr': 2003, 'lat': 45.4473, 'lon': -98.0988},
    {'co': 'POET Biorefining (Sioux River Ethanol)', 'st': 'SD', 'city': 'Hudson', 'county': 'Lincoln', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80000000, 'corn_bu': 28571000, 'start_yr': 2004, 'lat': 43.1302, 'lon': -96.4543},
    {'co': 'Glacial Lakes Energy, LLC (Huron Energy Llc)', 'st': 'SD', 'city': 'Huron', 'county': 'Beadle', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 34600000, 'corn_bu': 12357000, 'start_yr': 1999, 'lat': 44.3631, 'lon': -98.2144},
    {'co': 'Gevo', 'st': 'SD', 'city': 'Lake Preston', 'county': 'Kingsbury', 'status': 'Build', 'cls': 'SAF/Renewable Diesel', 'typ': 'ATJ', 'eth_gal': 62000000, 'corn_bu': 41333333, 'start_yr': None, 'lat': 44.3636, 'lon': -97.3764},
    {'co': 'NuGen Energy', 'st': 'SD', 'city': 'Marion', 'county': 'Turner', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 150000000, 'corn_bu': 53571000, 'start_yr': 2008, 'lat': 43.4231, 'lon': -97.2604},
    {'co': 'Missouri Valley Energy LLC', 'st': 'SD', 'city': 'Meckling', 'county': 'Clay', 'status': 'Hold', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 21429000, 'start_yr': 0, 'lat': 42.8425, 'lon': -97.0695},
    {'co': 'Glacial Lakes Energy, LLC', 'st': 'SD', 'city': 'Mina', 'county': 'Edmunds', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 46429000, 'start_yr': 2008, 'lat': 45.4384, 'lon': -98.756},
    {'co': 'Ringneck Energy', 'st': 'SD', 'city': 'Onida', 'county': 'Sully', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 80000000, 'corn_bu': 28571000, 'start_yr': 2019, 'lat': 44.7056, 'lon': -100.0655},
    {'co': 'Redfield Energy, LLC', 'st': 'SD', 'city': 'Redfield', 'county': 'Spink', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 65000000, 'corn_bu': 22919000, 'start_yr': 2007, 'lat': 44.8758, 'lon': -98.5187},
    {'co': 'Red River Energy, LLC', 'st': 'SD', 'city': 'Rosholt', 'county': 'Roberts', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 36000000, 'corn_bu': 12857000, 'start_yr': 2005, 'lat': 45.8666, 'lon': -96.7315},
    {'co': 'POET Biorefining - Research Center', 'st': 'SD', 'city': 'Scotland', 'county': 'Bon Homme', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 12000000, 'corn_bu': 4286000, 'start_yr': 1988, 'lat': 43.1497, 'lon': -97.7176},
    {'co': 'Glacial Lakes Energy, LLC', 'st': 'SD', 'city': 'Watertown', 'county': 'Codington', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 46429000, 'start_yr': 2002, 'lat': 44.8992, 'lon': -97.1153},
    {'co': 'Dakota Ethanol, LLC', 'st': 'SD', 'city': 'Wentworth', 'county': 'Lake', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 100000000, 'corn_bu': 35714000, 'start_yr': 2001, 'lat': 43.9972, 'lon': -96.9642},
    {'co': 'Dynamic Recycling LLC', 'st': 'TN', 'city': 'Bristol', 'county': 'Sullivan', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 4000000, 'corn_bu': None, 'start_yr': None, 'lat': 36.5945, 'lon': -82.1885},
    {'co': 'ADM Milling Co.', 'st': 'TN', 'city': 'Jackson', 'county': 'Madison', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3600000, 'start_yr': 1989, 'lat': 35.6144, 'lon': -88.8177},
    {'co': 'White Lily (owned by Hometown Food Company)', 'st': 'TN', 'city': 'Knoxville', 'county': 'Knox', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 4950000, 'start_yr': 1883, 'lat': 35.9604, 'lon': -83.921},
    {'co': 'Tate & Lyle', 'st': 'TN', 'city': 'Loudon', 'county': 'Loudon', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 110000000, 'corn_bu': 39286000, 'start_yr': 2006, 'lat': 35.749, 'lon': -84.3203},
    {'co': 'Clover Hill Milling Company', 'st': 'TN', 'city': 'Maryville', 'county': 'Blount', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 2100000, 'start_yr': 1921, 'lat': 35.7565, 'lon': -83.9705},
    {'co': 'Cargill Inc.', 'st': 'TN', 'city': 'Memphis', 'county': 'Shelby', 'status': 'Idled', 'cls': 'Mixed', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 20825000, 'start_yr': 1951, 'lat': 35.146, 'lon': -90.0518},
    {'co': 'Green Plains Renewable Energy', 'st': 'TN', 'city': 'Obion', 'county': 'Obion', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 120000000, 'corn_bu': 41354000, 'start_yr': 2008, 'lat': 36.3556, 'lon': -89.1749},
    {'co': 'Azteca Milling (Gruma)', 'st': 'TX', 'city': 'Amarillo', 'county': 'Potter', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 9450000, 'start_yr': 1996, 'lat': 35.2073, 'lon': -101.8371},
    {'co': 'International Ingredient Corporation', 'st': 'TX', 'city': 'Cleburne', 'county': 'Johnson', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Waste', 'eth_gal': 840000, 'corn_bu': None, 'start_yr': None, 'lat': 32.3474, 'lon': -97.3865},
    {'co': 'The Morrison Milling Co. Div Guenther & Sons', 'st': 'TX', 'city': 'Denton', 'county': 'Denton', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 5200000, 'start_yr': 1886, 'lat': 33.1839, 'lon': -97.1413},
    {'co': 'High Plains Milling', 'st': 'TX', 'city': 'Dimmitt', 'county': 'Castro', 'status': 'Run', 'cls': 'Food', 'typ': 'Wet', 'eth_gal': None, 'corn_bu': 11375000, 'start_yr': 1984, 'lat': 34.5488, 'lon': -102.3153},
    {'co': 'Azteca Milling (Gruma)', 'st': 'TX', 'city': 'Edinburg', 'county': 'Hidalgo', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 9450000, 'start_yr': 1982, 'lat': 26.3014, 'lon': -98.1625},
    {'co': 'Arrowhead Mills, Inc (owned by Hometown Food Company)', 'st': 'TX', 'city': 'Hereford', 'county': 'Deaf Smith', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3060000, 'start_yr': 1960, 'lat': 34.8246, 'lon': -102.3988},
    {'co': 'Hereford Ethanol Partners, L.P.', 'st': 'TX', 'city': 'Hereford', 'county': 'Deaf Smith', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 120000000, 'corn_bu': None, 'start_yr': 2011, 'lat': 34.8246, 'lon': -102.3988},
    {'co': 'White Energy', 'st': 'TX', 'city': 'Hereford', 'county': 'Deaf Smith', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 130000000, 'corn_bu': 42857000, 'start_yr': 2008, 'lat': 34.8246, 'lon': -102.3988},
    {'co': 'Diamond Ethanol', 'st': 'TX', 'city': 'Levelland', 'county': 'Hockley', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 40000000, 'corn_bu': 14286000, 'start_yr': 2012, 'lat': 33.5871, 'lon': -102.3777},
    {'co': 'Azteca Milling (Gruma)', 'st': 'TX', 'city': 'Plainview', 'county': 'Hale', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3150000, 'start_yr': 1989, 'lat': 34.1848, 'lon': -101.7068},
    {'co': 'White Energy', 'st': 'TX', 'city': 'Plainview', 'county': 'Hale', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 122800000, 'corn_bu': 43857000, 'start_yr': 2008, 'lat': 34.1848, 'lon': -101.7068},
    {'co': 'Pioneer Flour Mills Div Guenther & Son', 'st': 'TX', 'city': 'San Antonio', 'county': 'Bexar', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1430000, 'start_yr': 1899, 'lat': 29.4246, 'lon': -98.4951},
    {'co': 'MXI Environmental Services LLC (Maumee Express, Inc.)', 'st': 'VA', 'city': 'Abingdon', 'county': 'Washington', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Waste', 'eth_gal': 2000000, 'corn_bu': None, 'start_yr': 2001, 'lat': 36.7104, 'lon': -81.9752},
    {'co': 'Amherst Milling Co.', 'st': 'VA', 'city': 'Amherst', 'county': 'Amherst', 'status': 'Closed', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 3120000, 'start_yr': 1920, 'lat': 37.5998, 'lon': -79.1484},
    {'co': 'Ashland Milling Co.', 'st': 'VA', 'city': 'Ashland', 'county': 'Hanover', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 2600000, 'start_yr': 1968, 'lat': 37.7594, 'lon': -77.4807},
    {'co': 'Green Plains Renewable Energy', 'st': 'VA', 'city': 'Hopewell', 'county': 'Prince Geroge', 'status': 'Closed', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 60000000, 'corn_bu': 2142900, 'start_yr': 2014, 'lat': 37.3043, 'lon': -77.2872},
    {'co': 'Northwest Renewable, LLC', 'st': 'WA', 'city': 'Longview', 'county': 'Cowlitz', 'status': 'Hold', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55000000, 'corn_bu': 19643000, 'start_yr': 0, 'lat': 46.1377, 'lon': -122.9345},
    {'co': 'Big River Boyceville, LLC', 'st': 'WI', 'city': 'Boyceville', 'county': 'Dunn', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 70000000, 'corn_bu': 24964336, 'start_yr': 2006, 'lat': 45.0445, 'lon': -92.0394},
    {'co': 'Didion Ethanol', 'st': 'WI', 'city': 'Cambria', 'county': 'Columbia', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 49300000, 'corn_bu': 23755832, 'start_yr': 2008, 'lat': 43.5433, 'lon': -89.1085},
    {'co': 'Didion Milling', 'st': 'WI', 'city': 'Cambria', 'county': 'Columbia', 'status': 'Run', 'cls': 'Food', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1225000, 'start_yr': 1991, 'lat': 43.5433, 'lon': -89.1085},
    {'co': 'United Wisconsin Grain Producers, LLC', 'st': 'WI', 'city': 'Friesland', 'county': 'Columbia', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 62500000, 'corn_bu': 22876138, 'start_yr': 2005, 'lat': 43.5887, 'lon': -89.0672},
    {'co': 'Azatlan Bio LLC (formerly Valero Renewable Fuels)', 'st': 'WI', 'city': 'Jefferson', 'county': 'Jefferson', 'status': 'Expand', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': 110000000, 'corn_bu': 50000000, 'start_yr': 2008, 'lat': 43.0225, 'lon': -88.7673},
    {'co': 'United Ethanol', 'st': 'WI', 'city': 'Milton', 'county': 'Rock', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 61000000, 'corn_bu': 4195669, 'start_yr': 2007, 'lat': 42.7754, 'lon': -88.939},
    {'co': 'ADM Milling Company', 'st': 'WI', 'city': 'Milwaukee', 'county': 'Milwaukee', 'status': 'Run', 'cls': 'Mixed', 'typ': 'Dry', 'eth_gal': None, 'corn_bu': 1530000, 'start_yr': 1950, 'lat': 43.0386, 'lon': -87.9091},
    {'co': 'Badger State Ethanol, LLC', 'st': 'WI', 'city': 'Monroe', 'county': 'Green', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 90000000, 'corn_bu': 32143000, 'start_yr': 2002, 'lat': 43.9417, 'lon': -90.6397},
    {'co': 'United Energy Necedah LLC (formerly Marquis Energy - Wisconsin, LLC)', 'st': 'WI', 'city': 'Necedah', 'county': 'Juneau', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 106000000, 'corn_bu': 36000000, 'start_yr': 2008, 'lat': 44.0254, 'lon': -90.0721},
    {'co': 'Fox River Valley', 'st': 'WI', 'city': 'Oshkosh', 'county': 'Winnebago', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 64000000, 'corn_bu': 23396677, 'start_yr': 2003, 'lat': 44.0207, 'lon': -88.5409},
    {'co': 'Ace Ethanol, LLC', 'st': 'WI', 'city': 'Stanley', 'county': 'Chippewa', 'status': 'Run', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 55500000, 'corn_bu': 13541017, 'start_yr': 2002, 'lat': 44.9601, 'lon': -90.9375},
    {'co': 'Renova Energy', 'st': 'WY', 'city': 'Torrington', 'county': 'Goshen', 'status': 'Idled', 'cls': 'Ethanol', 'typ': 'Dry', 'eth_gal': 10000000, 'corn_bu': 3571000, 'start_yr': 2006, 'lat': 42.0625, 'lon': -104.1844},
]

_CRUSH_PLANTS = [
    {'co': 'ADM', 'st': 'IL', 'city': 'Decatur', 'nopa': 'Illinois', 'census': 'Illinois', 'rr': 'NS / CN *', 'daily_bu': 310000, 'lat': 39.8403, 'lon': -88.9548},
    {'co': 'ADM', 'st': 'MO', 'city': 'Deerfield', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'BNSF', 'daily_bu': 100000, 'lat': 37.8384, 'lon': -94.508},
    {'co': 'ADM', 'st': 'IA', 'city': 'Des Moines', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP / IAIS', 'daily_bu': 177000, 'lat': 41.5869, 'lon': -93.6249},
    {'co': 'ADM (Swing Plant)', 'st': 'ND', 'city': 'Enderlin', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'CPKC', 'daily_bu': 40000, 'lat': 46.623, 'lon': -97.6015},
    {'co': 'ADM', 'st': 'OH', 'city': 'Fostoria', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'CSXT / NS', 'daily_bu': 75000, 'lat': 41.1574, 'lon': -83.4141},
    {'co': 'ADM', 'st': 'IN', 'city': 'Frankfort', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'NS / CN *', 'daily_bu': 125000, 'lat': 40.2795, 'lon': -86.5122},
    {'co': 'ADM', 'st': 'NE', 'city': 'Freemont', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP', 'daily_bu': 70000, 'lat': 42.4674, 'lon': -96.4281},
    {'co': 'ADM', 'st': 'NE', 'city': 'Lincoln', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP / BNSF *', 'daily_bu': 175000, 'lat': 40.8089, 'lon': -96.7078},
    {'co': 'ADM', 'st': 'MN', 'city': 'Mankato', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'CPKC', 'daily_bu': 165000, 'lat': 44.1636, 'lon': -94.0067},
    {'co': 'ADM', 'st': 'MO', 'city': 'Mexico', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'CPKC / NS', 'daily_bu': 75000, 'lat': 39.1698, 'lon': -91.8829},
    {'co': 'ADM', 'st': 'IL', 'city': 'Quincy', 'nopa': 'Illinois', 'census': 'Illinois', 'rr': 'BNSF / NS *', 'daily_bu': 245000, 'lat': 39.9356, 'lon': -91.4099},
    {'co': 'ADM/Marathon', 'st': 'ND', 'city': 'Spiritwood', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'BNSF', 'daily_bu': 150000, 'lat': 46.9336, 'lon': -98.4923},
    {'co': 'ADM', 'st': 'GA', 'city': 'Valdosta', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'CSXT / NS *', 'daily_bu': 105000, 'lat': 30.8327, 'lon': -83.2785},
    {'co': 'AGP', 'st': 'MN', 'city': 'Dawson', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'BNSF', 'daily_bu': 110000, 'lat': 44.9355, 'lon': -96.0558},
    {'co': 'AGP', 'st': 'IA', 'city': 'Eagle Grove', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP', 'daily_bu': 120000, 'lat': 42.6641, 'lon': -93.9046},
    {'co': 'AGP', 'st': 'IA', 'city': 'Emmetsburg', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP / CP', 'daily_bu': 90000, 'lat': 43.1108, 'lon': -94.6782},
    {'co': 'AGP', 'st': 'NE', 'city': 'Hastings', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'BNSF / UP', 'daily_bu': 200000, 'lat': 40.5861, 'lon': -98.3899},
    {'co': 'AGP', 'st': 'IA', 'city': 'Manning', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP', 'daily_bu': 90000, 'lat': 41.9094, 'lon': -95.0594},
    {'co': 'AGP', 'st': 'IA', 'city': 'Mason City', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP / CP *', 'daily_bu': 90000, 'lat': 43.1536, 'lon': -93.201},
    {'co': 'AGP', 'st': 'IA', 'city': 'Sgt. Bluff', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'BNSF / UP', 'daily_bu': 150000, 'lat': 42.4025, 'lon': -96.325},
    {'co': 'AGP', 'st': 'IA', 'city': 'Sheldon', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP', 'daily_bu': 90000, 'lat': 43.183, 'lon': -95.8519},
    {'co': 'AGP', 'st': 'MO', 'city': 'St. Joe', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP / BNSF', 'daily_bu': 150000, 'lat': 37.8013, 'lon': -90.5},
    {'co': 'AGP', 'st': 'SD', 'city': 'Aberdeen', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'BNSF', 'daily_bu': 150000, 'lat': 45.465, 'lon': -98.4878},
    {'co': 'AGP', 'st': 'NE', 'city': 'David City', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP / BNSF', 'daily_bu': 150000, 'lat': 41.2527, 'lon': -97.1301},
    {'co': 'Bartlett Grain', 'st': 'KS', 'city': 'Cherryvale', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP / BNSF *', 'daily_bu': 130000, 'lat': 37.2694, 'lon': -95.553},
    {'co': 'Bunge', 'st': 'OH', 'city': 'Bellevue', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'NS', 'daily_bu': 92000, 'lat': 41.2742, 'lon': -82.841},
    {'co': 'Bunge', 'st': 'IL', 'city': 'Cairo', 'nopa': 'Illinois', 'census': 'Illinois', 'rr': 'CN', 'daily_bu': 135000, 'lat': 37.0053, 'lon': -89.1765},
    {'co': 'Bunge', 'st': 'IA', 'city': 'Council Bluffs', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'BNSF / UP', 'daily_bu': 240000, 'lat': 41.2619, 'lon': -95.8608},
    {'co': 'Bunge', 'st': 'IN', 'city': 'Decatur', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'CSXT / NS *', 'daily_bu': 135000, 'lat': 40.8303, 'lon': -84.9288},
    {'co': 'Bunge', 'st': 'AL', 'city': 'Decatur', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'NS / CSXT', 'daily_bu': 150000, 'lat': 34.606, 'lon': -86.9838},
    {'co': 'Bunge', 'st': 'OH', 'city': 'Delphos', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'BNSF / CN *', 'daily_bu': 65000, 'lat': 40.8437, 'lon': -84.3398},
    {'co': 'Bunge', 'st': 'LA', 'city': 'Destrehan', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'UP / CPKC', 'daily_bu': 130000, 'lat': 29.9427, 'lon': -90.3665},
    {'co': 'Bunge', 'st': 'KS', 'city': 'Emporia', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'BNSF', 'daily_bu': 90000, 'lat': 38.4039, 'lon': -96.1817},
    {'co': 'Bunge', 'st': 'IN', 'city': 'Morristown', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'CSXT', 'daily_bu': 205000, 'lat': 39.6732, 'lon': -85.699},
    {'co': 'Bunge', 'st': 'IL', 'city': 'Gibson City', 'nopa': 'Illinois', 'census': 'Illinois', 'rr': 'NS / CN *', 'daily_bu': 55000, 'lat': 40.4586, 'lon': -88.3595},
    {'co': 'White River Soy Proc', 'st': 'IA', 'city': 'Creston', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'BNSF', 'daily_bu': 28000, 'lat': 41.0597, 'lon': -94.3619},
    {'co': 'White River Soy Proc', 'st': 'IN', 'city': 'Seymour', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'N/A', 'daily_bu': 30000, 'lat': 38.9585, 'lon': -85.8903},
    {'co': 'Cargill', 'st': 'IL', 'city': 'Bloomington', 'nopa': 'Illinois', 'census': 'Illinois', 'rr': 'UP', 'daily_bu': 50000, 'lat': 40.4842, 'lon': -88.9937},
    {'co': 'Cargill', 'st': 'IA', 'city': 'Cedar Rapids', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP *', 'daily_bu': 150000, 'lat': 42.0084, 'lon': -91.6441},
    {'co': 'Cargill', 'st': 'NC', 'city': 'Fayetteville', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'CSXT', 'daily_bu': 125000, 'lat': 35.0526, 'lon': -78.8783},
    {'co': 'Cargill', 'st': 'GA', 'city': 'Gainesville', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'CSXT / NS', 'daily_bu': 100000, 'lat': 34.2979, 'lon': -83.8241},
    {'co': 'Cargill', 'st': 'AL', 'city': 'Guntersville', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'CSXT / NS *', 'daily_bu': 95000, 'lat': 34.3581, 'lon': -86.2947},
    {'co': 'Cargill', 'st': 'IA', 'city': 'Iowa Falls', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'CN / UP *', 'daily_bu': 95000, 'lat': 42.5224, 'lon': -93.2613},
    {'co': 'Cargill', 'st': 'MO', 'city': 'Kansas City', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP/BNSF/NS *', 'daily_bu': 200000, 'lat': 39.1001, 'lon': -94.5781},
    {'co': 'Cargill', 'st': 'IN', 'city': 'Lafayette', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'NS / CSXT', 'daily_bu': 85000, 'lat': 40.4191, 'lon': -86.8919},
    {'co': 'Cargill', 'st': 'OH', 'city': 'Sidney', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'CSXT', 'daily_bu': 195000, 'lat': 40.2842, 'lon': -84.1555},
    {'co': 'Cargill', 'st': 'IA', 'city': 'Sioux City', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'UP/BNSF/CN', 'daily_bu': 160000, 'lat': 42.4995, 'lon': -96.4003},
    {'co': 'Cargill', 'st': 'KS', 'city': 'Wichita', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'BNSF / UP *', 'daily_bu': 105000, 'lat': 37.6872, 'lon': -97.3301},
    {'co': 'Cargill', 'st': 'KY', 'city': 'Owensboro', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'CSXT', 'daily_bu': 150000, 'lat': 37.7719, 'lon': -87.1111},
    {'co': 'NDSP / CGB', 'st': 'ND', 'city': 'Casselton', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'BNSF *', 'daily_bu': 125000, 'lat': 46.9005, 'lon': -97.2112},
    {'co': 'CGB', 'st': 'IN', 'city': 'Mt Vernon', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'CSX/NS/BNSF*', 'daily_bu': 135000, 'lat': 37.9318, 'lon': -87.8948},
    {'co': 'CHS', 'st': 'MN', 'city': 'Fairmont', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'UP', 'daily_bu': 240000, 'lat': 43.6516, 'lon': -94.4608},
    {'co': 'CHS', 'st': 'MN', 'city': 'Mankato', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'CPKC', 'daily_bu': 140000, 'lat': 44.1636, 'lon': -94.0067},
    {'co': 'Dreyfus', 'st': 'IN', 'city': 'Claypool', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'NS / CSXT', 'daily_bu': 175000, 'lat': 41.1258, 'lon': -85.8775},
    {'co': 'Incobrasa', 'st': 'IL', 'city': 'Gilman', 'nopa': 'Illinois', 'census': 'Illinois', 'rr': 'CN *', 'daily_bu': 120000, 'lat': 40.7683, 'lon': -87.9956},
    {'co': 'MnSP', 'st': 'MN', 'city': 'Brewster', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'UP', 'daily_bu': 115000, 'lat': 43.6939, 'lon': -95.4661},
    {'co': 'Norfolk Crush', 'st': 'NE', 'city': 'Norfolk', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP *', 'daily_bu': 110000, 'lat': 42.0283, 'lon': -97.417},
    {'co': 'Perdue', 'st': 'NC', 'city': 'Cofield', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'CSXT *', 'daily_bu': 40000, 'lat': 36.3565, 'lon': -76.91},
    {'co': 'Perdue', 'st': 'PA', 'city': 'Bainbridge', 'nopa': 'IN, KY, OH, MI', 'census': 'North and East', 'rr': 'NS', 'daily_bu': 60000, 'lat': 40.0909, 'lon': -76.6675},
    {'co': 'Perdue', 'st': 'VA', 'city': 'Chesapeake', 'nopa': 'Southeast', 'census': 'North and East', 'rr': 'CSXT / NS *', 'daily_bu': 85000, 'lat': 36.7168, 'lon': -76.2494},
    {'co': 'Perdue', 'st': 'MD', 'city': 'Salisbury', 'nopa': 'Southeast', 'census': 'North and East', 'rr': 'NS *', 'daily_bu': 63000, 'lat': 38.3607, 'lon': -75.5996},
    {'co': 'Platinum', 'st': 'IA', 'city': 'Alta', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'CN', 'daily_bu': 110000, 'lat': 42.6714, 'lon': -95.2947},
    {'co': 'Riceland', 'st': 'AR', 'city': 'Stuttgart', 'nopa': 'Southeast', 'census': 'South, West, and Pacific', 'rr': 'UP *', 'daily_bu': 90000, 'lat': 34.5004, 'lon': -91.5526},
    {'co': 'Scoular (Swing Plant)', 'st': 'KS', 'city': 'Goodland', 'nopa': 'Southwest', 'census': 'West Central', 'rr': 'UP / BNSF *', 'daily_bu': 35000, 'lat': 39.3486, 'lon': -101.7101},
    {'co': 'Shell Rock/P66', 'st': 'IA', 'city': 'Shell Rock', 'nopa': 'Iowa', 'census': 'Iowa', 'rr': 'CN *', 'daily_bu': 110000, 'lat': 42.7088, 'lon': -92.5838},
    {'co': 'SDSP', 'st': 'SD', 'city': 'Volga', 'nopa': 'MN, ND, SD, MT', 'census': 'North Central', 'rr': 'CPKC *', 'daily_bu': 95000, 'lat': 44.3236, 'lon': -96.9264},
    {'co': 'Zeeland Farms', 'st': 'MI', 'city': 'Ithaca', 'nopa': 'IN, KY, OH, MI', 'census': 'North Central', 'rr': 'CN/CSXT/NS *', 'daily_bu': 135000, 'lat': 43.2945, 'lon': -84.6069},
    {'co': 'Zeeland Farms', 'st': 'MI', 'city': 'Zeeland', 'nopa': 'IN, KY, OH, MI', 'census': 'North Central', 'rr': 'N/A', 'daily_bu': 35000, 'lat': 42.8128, 'lon': -86.0168},
]

# FSA Conservation Reserve Program enrollment — million acres by state abbreviation (or "US")
# Source: USDA FSA CRPHistoryState86-25.xlsx (fiscal year = planting calendar year)
_CRP_DATA: dict = {
    'US': {1986: 1.9291, 1987: 15.3488, 1988: 23.8706, 1989: 28.8762, 1990: 32.5224, 1991: 32.9958, 1992: 33.9933, 1993: 35.015, 1994: 35.015, 1995: 34.9777, 1996: 34.5038, 1997: 32.8171, 1998: 30.1346, 1999: 29.8234, 2000: 31.4256, 2001: 33.6083, 2002: 33.9644, 2003: 34.1111, 2004: 34.7073, 2005: 34.9023, 2006: 36.0033, 2007: 36.771, 2008: 34.6127, 2009: 33.7213, 2010: 31.2982, 2011: 31.1245, 2012: 29.5256, 2013: 26.8387, 2014: 25.4488, 2015: 24.1805, 2016: 23.8806, 2017: 23.4337, 2018: 22.6097, 2019: 22.3254, 2020: 21.9248, 2021: 20.5132, 2022: 21.9972, 2023: 22.9333, 2024: 24.6019, 2025: 25.7661},
    'AK': {1986: 0.0023, 1987: 0.0166, 1988: 0.0251, 1989: 0.0253, 1990: 0.0253, 1991: 0.0261, 1992: 0.0261, 1993: 0.0261, 1994: 0.0261, 1995: 0.0249, 1996: 0.0249, 1997: 0.0247, 1998: 0.0252, 1999: 0.025, 2000: 0.03, 2001: 0.0295, 2002: 0.0295, 2003: 0.0295, 2004: 0.0295, 2005: 0.0298, 2006: 0.0297, 2007: 0.0297, 2008: 0.0265, 2009: 0.0265, 2010: 0.0258, 2011: 0.019, 2012: 0.019, 2013: 0.018, 2014: 0.018, 2015: 0.0175, 2016: 0.0174, 2017: 0.0174, 2018: 0.0027, 2019: 0.0027, 2020: 0.0025, 2021: 0.0082, 2022: 0.0108, 2023: 0.0115, 2024: 0.0125, 2025: 0.0125},
    'AL': {1986: 0.0685, 1987: 0.3018, 1988: 0.4119, 1989: 0.4837, 1990: 0.5019, 1991: 0.5181, 1992: 0.5331, 1993: 0.5555, 1994: 0.5555, 1995: 0.5544, 1996: 0.5439, 1997: 0.5222, 1998: 0.4261, 1999: 0.4103, 2000: 0.4558, 2001: 0.4801, 2002: 0.4837, 2003: 0.4835, 2004: 0.4842, 2005: 0.4851, 2006: 0.4916, 2007: 0.4925, 2008: 0.4637, 2009: 0.4458, 2010: 0.4175, 2011: 0.3966, 2012: 0.3603, 2013: 0.3248, 2014: 0.3078, 2015: 0.2779, 2016: 0.2552, 2017: 0.242, 2018: 0.2127, 2019: 0.2, 2020: 0.1947, 2021: 0.1683, 2022: 0.1392, 2023: 0.107, 2024: 0.0936, 2025: 0.0899},
    'AR': {1986: 0.02, 1987: 0.0916, 1988: 0.1406, 1989: 0.1846, 1990: 0.2123, 1991: 0.2217, 1992: 0.233, 1993: 0.2465, 1994: 0.2465, 1995: 0.2458, 1996: 0.2391, 1997: 0.2304, 1998: 0.1808, 1999: 0.1484, 2000: 0.1452, 2001: 0.1573, 2002: 0.1614, 2003: 0.1712, 2004: 0.1902, 2005: 0.2025, 2006: 0.2203, 2007: 0.2379, 2008: 0.2338, 2009: 0.247, 2010: 0.2486, 2011: 0.2494, 2012: 0.2512, 2013: 0.2396, 2014: 0.2361, 2015: 0.2339, 2016: 0.2315, 2017: 0.2303, 2018: 0.2306, 2019: 0.2199, 2020: 0.2152, 2021: 0.2053, 2022: 0.2003, 2023: 0.193, 2024: 0.1871, 2025: 0.1854},
    'AZ': {1988: 0.0, 1989: 0.0, 1990: 0.0, 1991: 0.0, 1992: 0.0, 1993: 0.0, 1994: 0.0, 1995: 0.0, 1996: 0.0, 1997: 0.0, 1998: 0.0, 1999: 0.0, 2000: 0.0, 2001: 0.0, 2002: 0.0, 2003: 0.0, 2004: 0.0, 2005: 0.0, 2022: 0.0103, 2023: 0.0152, 2024: 0.0615, 2025: 0.0994},
    'CA': {1986: 0.022, 1987: 0.1197, 1988: 0.1516, 1989: 0.1695, 1990: 0.1762, 1991: 0.1762, 1992: 0.1818, 1993: 0.1822, 1994: 0.1822, 1995: 0.1805, 1996: 0.179, 1997: 0.173, 1998: 0.1331, 1999: 0.129, 2000: 0.1301, 2001: 0.1372, 2002: 0.139, 2003: 0.1429, 2004: 0.147, 2005: 0.1444, 2006: 0.1474, 2007: 0.1489, 2008: 0.1325, 2009: 0.1281, 2010: 0.1257, 2011: 0.1218, 2012: 0.1012, 2013: 0.0877, 2014: 0.0831, 2015: 0.0815, 2016: 0.0796, 2017: 0.0743, 2018: 0.0428, 2019: 0.0427, 2020: 0.0424, 2021: 0.0417, 2022: 0.0564, 2023: 0.0638, 2024: 0.0797, 2025: 0.082},
    'CO': {1986: 0.3547, 1987: 1.2865, 1988: 1.5998, 1989: 1.7516, 1990: 1.9085, 1991: 1.9101, 1992: 1.9243, 1993: 1.9339, 1994: 1.9339, 1995: 1.9332, 1996: 1.9171, 1997: 1.8901, 1998: 1.7985, 1999: 1.9429, 2000: 2.0935, 2001: 2.2048, 2002: 2.2094, 2003: 2.2035, 2004: 2.2759, 2005: 2.2848, 2006: 2.3728, 2007: 2.4721, 2008: 2.4379, 2009: 2.4196, 2010: 2.0221, 2011: 2.239, 2012: 2.1759, 2013: 2.0671, 2014: 1.9881, 2015: 1.9123, 2016: 1.8533, 2017: 1.7791, 2018: 1.8138, 2019: 1.8389, 2020: 1.8305, 2021: 1.5528, 2022: 2.0348, 2023: 2.4434, 2024: 2.7793, 2025: 2.9584},
    'CT': {1998: 0.0002, 1999: 0.0002, 2000: 0.0003, 2001: 0.0003, 2002: 0.0003, 2003: 0.0003, 2004: 0.0003, 2005: 0.0003, 2006: 0.0003, 2007: 0.0003, 2008: 0.0002, 2009: 0.0002, 2010: 0.0002, 2011: 0.0001, 2012: 0.0001, 2013: 0.0001, 2014: 0.0001, 2015: 0.0, 2016: 0.0, 2017: 0.0, 2018: 0.0, 2019: 0.0, 2020: 0.0, 2021: 0.0},
    'DE': {1987: 0.0001, 1988: 0.0003, 1989: 0.0008, 1990: 0.0009, 1991: 0.0009, 1992: 0.0009, 1993: 0.0009, 1994: 0.0009, 1995: 0.0009, 1996: 0.0009, 1997: 0.0008, 1998: 0.0016, 1999: 0.0016, 2000: 0.0025, 2001: 0.0047, 2002: 0.0066, 2003: 0.0071, 2004: 0.0076, 2005: 0.0077, 2006: 0.0077, 2007: 0.0079, 2008: 0.0077, 2009: 0.0073, 2010: 0.0068, 2011: 0.0067, 2012: 0.0065, 2013: 0.0063, 2014: 0.0062, 2015: 0.006, 2016: 0.0046, 2017: 0.004, 2018: 0.0038, 2019: 0.0036, 2020: 0.0035, 2021: 0.0033, 2022: 0.0032, 2023: 0.003, 2024: 0.0029, 2025: 0.0028},
    'FL': {1986: 0.012, 1987: 0.0488, 1988: 0.0848, 1989: 0.1066, 1990: 0.1167, 1991: 0.119, 1992: 0.123, 1993: 0.1286, 1994: 0.1286, 1995: 0.1284, 1996: 0.1241, 1997: 0.1199, 1998: 0.0981, 1999: 0.0835, 2000: 0.0862, 2001: 0.0903, 2002: 0.0883, 2003: 0.0862, 2004: 0.0884, 2005: 0.0856, 2006: 0.0843, 2007: 0.0826, 2008: 0.0694, 2009: 0.066, 2010: 0.0621, 2011: 0.0563, 2012: 0.0514, 2013: 0.0463, 2014: 0.043, 2015: 0.0361, 2016: 0.0315, 2017: 0.0298, 2018: 0.0253, 2019: 0.023, 2020: 0.0201, 2021: 0.0144, 2022: 0.0114, 2023: 0.0077, 2024: 0.0065, 2025: 0.0062},
    'GA': {1986: 0.0471, 1987: 0.2541, 1988: 0.3982, 1989: 0.5193, 1990: 0.5734, 1991: 0.5845, 1992: 0.5992, 1993: 0.6165, 1994: 0.6165, 1995: 0.6156, 1996: 0.609, 1997: 0.5953, 1998: 0.3526, 1999: 0.254, 2000: 0.2842, 2001: 0.3189, 2002: 0.3137, 2003: 0.307, 2004: 0.3082, 2005: 0.305, 2006: 0.3063, 2007: 0.3125, 2008: 0.3299, 2009: 0.3342, 2010: 0.3209, 2011: 0.3197, 2012: 0.3173, 2013: 0.31, 2014: 0.3051, 2015: 0.2603, 2016: 0.2386, 2017: 0.2385, 2018: 0.2316, 2019: 0.2098, 2020: 0.2067, 2021: 0.1951, 2022: 0.1831, 2023: 0.1598, 2024: 0.149, 2025: 0.1398},
    'HI': {1986: 0.0001, 1987: 0.0001, 1988: 0.0001, 1989: 0.0001, 1990: 0.0001, 1991: 0.0007, 1992: 0.0007, 1993: 0.0007, 1994: 0.0007, 1995: 0.0007, 2000: 0.0, 2001: 0.0, 2002: 0.0, 2003: 0.0, 2004: 0.0, 2005: 0.0, 2006: 0.0, 2007: 0.0, 2008: 0.0, 2009: 0.0, 2010: 0.0002, 2011: 0.0002, 2012: 0.0005, 2013: 0.0005, 2014: 0.0008, 2015: 0.001, 2016: 0.001, 2017: 0.0012, 2018: 0.0012, 2019: 0.0013, 2020: 0.0013, 2021: 0.0049, 2022: 0.0049, 2023: 0.005, 2024: 0.0064, 2025: 0.0063},
    'IA': {1986: 0.0765, 1987: 1.2391, 1988: 1.4728, 1989: 1.7601, 1990: 1.9511, 1991: 1.9878, 1992: 2.0872, 1993: 2.2038, 1994: 2.2038, 1995: 2.1994, 1996: 2.1762, 1997: 1.7577, 1998: 1.5036, 1999: 1.4841, 2000: 1.5995, 2001: 1.8029, 2002: 1.8657, 2003: 1.8827, 2004: 1.8945, 2005: 1.9175, 2006: 1.9589, 2007: 1.9705, 2008: 1.8096, 2009: 1.7039, 2010: 1.6371, 2011: 1.6624, 2012: 1.6444, 2013: 1.525, 2014: 1.4575, 2015: 1.4844, 2016: 1.689, 2017: 1.7865, 2018: 1.8001, 2019: 1.7459, 2020: 1.7052, 2021: 1.6625, 2022: 1.6939, 2023: 1.6881, 2024: 1.6742, 2025: 1.6689},
    'ID': {1986: 0.039, 1987: 0.4643, 1988: 0.6323, 1989: 0.7182, 1990: 0.7633, 1991: 0.7828, 1992: 0.8177, 1993: 0.8486, 1994: 0.8486, 1995: 0.847, 1996: 0.8214, 1997: 0.7847, 1998: 0.7585, 1999: 0.7373, 2000: 0.7788, 2001: 0.7927, 2002: 0.792, 2003: 0.7879, 2004: 0.7895, 2005: 0.7869, 2006: 0.8003, 2007: 0.8241, 2008: 0.7697, 2009: 0.7549, 2010: 0.7164, 2011: 0.6648, 2012: 0.6488, 2013: 0.6146, 2014: 0.604, 2015: 0.5835, 2016: 0.5691, 2017: 0.5616, 2018: 0.5391, 2019: 0.5403, 2020: 0.5344, 2021: 0.4492, 2022: 0.4257, 2023: 0.377, 2024: 0.3629, 2025: 0.3565},
    'IL': {1986: 0.0312, 1987: 0.2628, 1988: 0.3741, 1989: 0.5144, 1990: 0.6087, 1991: 0.6367, 1992: 0.7092, 1993: 0.7864, 1994: 0.7864, 1995: 0.7851, 1996: 0.7845, 1997: 0.7465, 1998: 0.7141, 1999: 0.7154, 2000: 0.7942, 2001: 0.9155, 2002: 0.9643, 2003: 0.9751, 2004: 0.9985, 2005: 1.0277, 2006: 1.0499, 2007: 1.0866, 2008: 1.0623, 2009: 1.0365, 2010: 1.0224, 2011: 1.0372, 2012: 1.0305, 2013: 0.9929, 2014: 0.9203, 2015: 0.8948, 2016: 0.8943, 2017: 0.8954, 2018: 0.8788, 2019: 0.8537, 2020: 0.8417, 2021: 0.8286, 2022: 0.823, 2023: 0.8131, 2024: 0.7902, 2025: 0.7882},
    'IN': {1986: 0.0106, 1987: 0.1448, 1988: 0.2104, 1989: 0.3038, 1990: 0.3566, 1991: 0.3715, 1992: 0.4113, 1993: 0.4535, 1994: 0.4535, 1995: 0.4522, 1996: 0.4296, 1997: 0.3832, 1998: 0.3306, 1999: 0.2827, 2000: 0.2729, 2001: 0.2935, 2002: 0.3017, 2003: 0.2985, 2004: 0.2838, 2005: 0.2932, 2006: 0.308, 2007: 0.3166, 2008: 0.295, 2009: 0.2907, 2010: 0.2877, 2011: 0.2861, 2012: 0.2804, 2013: 0.2632, 2014: 0.2395, 2015: 0.232, 2016: 0.2361, 2017: 0.2313, 2018: 0.2253, 2019: 0.2187, 2020: 0.2136, 2021: 0.2021, 2022: 0.1997, 2023: 0.192, 2024: 0.1829, 2025: 0.1803},
    'KS': {1986: 0.1029, 1987: 0.9655, 1988: 1.9995, 1989: 2.4122, 1990: 2.8098, 1991: 2.8183, 1992: 2.8591, 1993: 2.8856, 1994: 2.8856, 1995: 2.8835, 1996: 2.8676, 1997: 2.8489, 1998: 2.5998, 1999: 2.5234, 2000: 2.5194, 2001: 2.6527, 2002: 2.6588, 2003: 2.6603, 2004: 2.8289, 2005: 2.8788, 2006: 3.0852, 2007: 3.259, 2008: 3.1248, 2009: 3.0982, 2010: 2.7828, 2011: 2.7322, 2012: 2.5229, 2013: 2.3559, 2014: 2.2715, 2015: 2.1819, 2016: 2.1328, 2017: 2.0716, 2018: 2.0079, 2019: 1.9409, 2020: 1.8961, 2021: 1.7488, 2022: 1.7206, 2023: 1.8222, 2024: 1.9503, 2025: 2.0314},
    'KY': {1986: 0.0423, 1987: 0.2769, 1988: 0.3476, 1989: 0.3846, 1990: 0.4027, 1991: 0.4102, 1992: 0.4206, 1993: 0.4376, 1994: 0.4376, 1995: 0.4363, 1996: 0.4132, 1997: 0.3321, 1998: 0.2538, 1999: 0.2528, 2000: 0.2695, 2001: 0.3055, 2002: 0.3129, 2003: 0.316, 2004: 0.3339, 2005: 0.3414, 2006: 0.3541, 2007: 0.3584, 2008: 0.385, 2009: 0.3898, 2010: 0.3826, 2011: 0.3582, 2012: 0.3323, 2013: 0.3007, 2014: 0.2769, 2015: 0.2624, 2016: 0.251, 2017: 0.2488, 2018: 0.2429, 2019: 0.2081, 2020: 0.1979, 2021: 0.1902, 2022: 0.1891, 2023: 0.1487, 2024: 0.1358, 2025: 0.1346},
    'LA': {1986: 0.0071, 1987: 0.0443, 1988: 0.0774, 1989: 0.1058, 1990: 0.1317, 1991: 0.1365, 1992: 0.1412, 1993: 0.1457, 1994: 0.1457, 1995: 0.1453, 1996: 0.144, 1997: 0.1401, 1998: 0.1466, 1999: 0.1439, 2000: 0.1819, 2001: 0.2071, 2002: 0.2058, 2003: 0.2046, 2004: 0.2373, 2005: 0.2434, 2006: 0.2885, 2007: 0.3098, 2008: 0.3044, 2009: 0.3065, 2010: 0.3181, 2011: 0.3274, 2012: 0.3254, 2013: 0.3125, 2014: 0.3084, 2015: 0.2951, 2016: 0.2893, 2017: 0.2871, 2018: 0.2827, 2019: 0.2756, 2020: 0.2721, 2021: 0.258, 2022: 0.256, 2023: 0.2517, 2024: 0.2493, 2025: 0.2481},
    'MA': {1986: 0.0, 1987: 0.0, 1988: 0.0, 1989: 0.0, 1990: 0.0, 1991: 0.0, 1992: 0.0, 1993: 0.0, 1994: 0.0, 1995: 0.0, 1996: 0.0001, 1997: 0.0001, 1998: 0.0001, 1999: 0.0001, 2000: 0.0001, 2001: 0.0001, 2002: 0.0001, 2003: 0.0001, 2004: 0.0001, 2005: 0.0001, 2006: 0.0001, 2007: 0.0001, 2008: 0.0001, 2009: 0.0001, 2010: 0.0, 2011: 0.0, 2012: 0.0, 2013: 0.0, 2014: 0.0, 2015: 0.0, 2016: 0.0, 2017: 0.0, 2018: 0.0, 2019: 0.0, 2020: 0.0, 2021: 0.0, 2022: 0.0, 2023: 0.0, 2024: 0.0, 2025: 0.0},
    'MD': {1986: 0.0004, 1987: 0.0025, 1988: 0.0061, 1989: 0.0109, 1990: 0.0151, 1991: 0.0167, 1992: 0.0185, 1993: 0.0194, 1994: 0.0194, 1995: 0.0194, 1996: 0.0196, 1997: 0.0192, 1998: 0.0233, 1999: 0.0289, 2000: 0.0345, 2001: 0.0451, 2002: 0.0609, 2003: 0.0777, 2004: 0.0843, 2005: 0.0849, 2006: 0.0857, 2007: 0.0857, 2008: 0.0832, 2009: 0.0808, 2010: 0.0796, 2011: 0.0791, 2012: 0.0788, 2013: 0.075, 2014: 0.0701, 2015: 0.0673, 2016: 0.0633, 2017: 0.0582, 2018: 0.0536, 2019: 0.0504, 2020: 0.0486, 2021: 0.0464, 2022: 0.0451, 2023: 0.043, 2024: 0.041, 2025: 0.0394},
    'ME': {1986: 0.0024, 1987: 0.0133, 1988: 0.0266, 1989: 0.0332, 1990: 0.0346, 1991: 0.0348, 1992: 0.0355, 1993: 0.0358, 1994: 0.0358, 1995: 0.0357, 1996: 0.032, 1997: 0.0297, 1998: 0.0253, 1999: 0.0238, 2000: 0.0243, 2001: 0.0245, 2002: 0.0243, 2003: 0.0241, 2004: 0.0233, 2005: 0.0233, 2006: 0.0242, 2007: 0.0236, 2008: 0.0208, 2009: 0.0217, 2010: 0.0206, 2011: 0.0179, 2012: 0.0136, 2013: 0.0092, 2014: 0.0086, 2015: 0.0083, 2016: 0.008, 2017: 0.0077, 2018: 0.0057, 2019: 0.0056, 2020: 0.0056, 2021: 0.0045, 2022: 0.0039, 2023: 0.0032, 2024: 0.0022, 2025: 0.0021},
    'MI': {1986: 0.0074, 1987: 0.0701, 1988: 0.1225, 1989: 0.1686, 1990: 0.1928, 1991: 0.2119, 1992: 0.2563, 1993: 0.3316, 1994: 0.3316, 1995: 0.3309, 1996: 0.3346, 1997: 0.3221, 1998: 0.2857, 1999: 0.2744, 2000: 0.2743, 2001: 0.2877, 2002: 0.3101, 2003: 0.3097, 2004: 0.2583, 2005: 0.2632, 2006: 0.2717, 2007: 0.2762, 2008: 0.2595, 2009: 0.2402, 2010: 0.2332, 2011: 0.2284, 2012: 0.2217, 2013: 0.2078, 2014: 0.1752, 2015: 0.1666, 2016: 0.1581, 2017: 0.1272, 2018: 0.12, 2019: 0.1211, 2020: 0.1152, 2021: 0.1135, 2022: 0.1168, 2023: 0.1142, 2024: 0.1088, 2025: 0.1073},
    'MN': {1986: 0.1322, 1987: 1.1219, 1988: 1.4414, 1989: 1.6345, 1990: 1.7392, 1991: 1.7589, 1992: 1.8074, 1993: 1.8368, 1994: 1.8368, 1995: 1.8344, 1996: 1.7787, 1997: 1.5583, 1998: 1.081, 1999: 1.1609, 2000: 1.4591, 2001: 1.596, 2002: 1.6694, 2003: 1.7142, 2004: 1.7651, 2005: 1.7629, 2006: 1.7965, 2007: 1.8294, 2008: 1.7739, 2009: 1.6947, 2010: 1.6419, 2011: 1.6351, 2012: 1.5557, 2013: 1.3802, 2014: 1.2995, 2015: 1.1509, 2016: 1.1531, 2017: 1.1281, 2018: 1.1372, 2019: 1.0586, 2020: 1.0252, 2021: 0.9947, 2022: 0.9966, 2023: 0.9761, 2024: 0.9645, 2025: 0.9628},
    'MO': {1986: 0.1015, 1987: 0.8708, 1988: 1.2583, 1989: 1.4084, 1990: 1.4792, 1991: 1.5121, 1992: 1.5994, 1993: 1.7002, 1994: 1.7002, 1995: 1.6982, 1996: 1.7017, 1997: 1.6074, 1998: 1.3805, 1999: 1.3563, 2000: 1.4239, 2001: 1.5397, 2002: 1.5518, 2003: 1.5505, 2004: 1.5542, 2005: 1.5511, 2006: 1.5752, 2007: 1.5929, 2008: 1.453, 2009: 1.4204, 2010: 1.3919, 2011: 1.3607, 2012: 1.2828, 2013: 1.1247, 2014: 1.0397, 2015: 1.011, 2016: 0.9903, 2017: 0.9655, 2018: 0.8756, 2019: 0.8385, 2020: 0.8258, 2021: 0.7858, 2022: 0.7664, 2023: 0.7042, 2024: 0.6686, 2025: 0.6663},
    'MS': {1986: 0.0807, 1987: 0.3845, 1988: 0.5241, 1989: 0.6253, 1990: 0.7013, 1991: 0.7334, 1992: 0.7736, 1993: 0.8156, 1994: 0.8156, 1995: 0.8134, 1996: 0.8202, 1997: 0.7974, 1998: 0.775, 1999: 0.7566, 2000: 0.7886, 2001: 0.8518, 2002: 0.8649, 2003: 0.8886, 2004: 0.9305, 2005: 0.9408, 2006: 0.9513, 2007: 0.9551, 2008: 0.8949, 2009: 0.8846, 2010: 0.8614, 2011: 0.8495, 2012: 0.8278, 2013: 0.7771, 2014: 0.7591, 2015: 0.743, 2016: 0.7244, 2017: 0.6995, 2018: 0.6122, 2019: 0.5978, 2020: 0.5893, 2021: 0.5488, 2022: 0.5158, 2023: 0.4708, 2024: 0.4515, 2025: 0.4462},
    'MT': {1986: 0.0546, 1987: 0.8052, 1988: 1.8272, 1989: 2.3415, 1990: 2.6829, 1991: 2.7308, 1992: 2.7758, 1993: 2.8147, 1994: 2.8147, 1995: 2.8153, 1996: 2.7862, 1997: 2.7351, 1998: 2.797, 1999: 2.9556, 2000: 3.2189, 2001: 3.4169, 2002: 3.4115, 2003: 3.4112, 2004: 3.4191, 2005: 3.4016, 2006: 3.4815, 2007: 3.4809, 2008: 3.2912, 2009: 3.2024, 2010: 3.0771, 2011: 2.8536, 2012: 2.4925, 2013: 1.9896, 2014: 1.755, 2015: 1.4992, 2016: 1.4087, 2017: 1.3641, 2018: 1.1149, 2019: 1.0599, 2020: 1.0199, 2021: 0.799, 2022: 0.786, 2023: 0.8077, 2024: 0.8788, 2025: 0.9277},
    'NC': {1986: 0.0097, 1987: 0.0591, 1988: 0.097, 1989: 0.1186, 1990: 0.1299, 1991: 0.133, 1992: 0.1386, 1993: 0.1437, 1994: 0.1437, 1995: 0.143, 1996: 0.1393, 1997: 0.1312, 1998: 0.0968, 1999: 0.0849, 2000: 0.0953, 2001: 0.1104, 2002: 0.1135, 2003: 0.1159, 2004: 0.1224, 2005: 0.1265, 2006: 0.1337, 2007: 0.1376, 2008: 0.1318, 2009: 0.1282, 2010: 0.1229, 2011: 0.1174, 2012: 0.1111, 2013: 0.1059, 2014: 0.1015, 2015: 0.0893, 2016: 0.0757, 2017: 0.0667, 2018: 0.0589, 2019: 0.0503, 2020: 0.0456, 2021: 0.0352, 2022: 0.0279, 2023: 0.0215, 2024: 0.0189, 2025: 0.018},
    'ND': {1986: 0.037, 1987: 0.5992, 1988: 1.5618, 1989: 2.276, 1990: 2.8658, 1991: 2.8791, 1992: 2.8979, 1993: 2.9084, 1994: 2.9084, 1995: 2.9086, 1996: 2.8365, 1997: 2.8049, 1998: 3.2906, 1999: 3.0599, 2000: 3.1642, 2001: 3.3209, 2002: 3.3269, 2003: 3.3365, 2004: 3.3664, 2005: 3.3413, 2006: 3.3717, 2007: 3.3886, 2008: 2.9765, 2009: 2.8526, 2010: 2.7194, 2011: 2.648, 2012: 2.3873, 2013: 1.781, 2014: 1.6193, 2015: 1.5237, 2016: 1.5391, 2017: 1.5287, 2018: 1.3063, 2019: 1.2951, 2020: 1.2721, 2021: 1.2352, 2022: 1.2442, 2023: 1.1246, 2024: 1.1454, 2025: 1.1902},
    'NE': {1986: 0.0694, 1987: 0.6739, 1988: 0.9815, 1989: 1.163, 1990: 1.3046, 1991: 1.3141, 1992: 1.3501, 1993: 1.3797, 1994: 1.3797, 1995: 1.3772, 1996: 1.3707, 1997: 1.2454, 1998: 1.0434, 1999: 0.9979, 2000: 1.0448, 2001: 1.1333, 2002: 1.1409, 2003: 1.1496, 2004: 1.1914, 2005: 1.2005, 2006: 1.2916, 2007: 1.3412, 2008: 1.2336, 2009: 1.2041, 2010: 1.0928, 2011: 1.0714, 2012: 0.9939, 2013: 0.8874, 2014: 0.8437, 2015: 0.7847, 2016: 0.782, 2017: 0.8004, 2018: 0.9159, 2019: 1.0641, 2020: 1.0541, 2021: 1.2684, 2022: 1.5218, 2023: 1.8376, 2024: 2.2092, 2025: 2.4032},
    'NH': {1996: 0.0, 1997: 0.0002, 1998: 0.0002, 1999: 0.0002, 2000: 0.0002, 2001: 0.0002, 2002: 0.0002, 2003: 0.0002, 2004: 0.0002, 2005: 0.0002, 2006: 0.0002, 2007: 0.0002, 2008: 0.0001, 2009: 0.0001, 2010: 0.0001, 2011: 0.0001, 2012: 0.0, 2013: 0.0, 2014: 0.0, 2015: 0.0, 2016: 0.0, 2017: 0.0, 2018: 0.0, 2019: 0.0, 2020: 0.0},
    'NJ': {1986: 0.0001, 1987: 0.0002, 1988: 0.0003, 1989: 0.0005, 1990: 0.0006, 1991: 0.0006, 1992: 0.0006, 1993: 0.0007, 1994: 0.0007, 1995: 0.0007, 1996: 0.0006, 1997: 0.0005, 1998: 0.0017, 1999: 0.0023, 2000: 0.0021, 2001: 0.0022, 2002: 0.0023, 2003: 0.0023, 2004: 0.0023, 2005: 0.0023, 2006: 0.0025, 2007: 0.0026, 2008: 0.0024, 2009: 0.0024, 2010: 0.0025, 2011: 0.0026, 2012: 0.0024, 2013: 0.0022, 2014: 0.0021, 2015: 0.002, 2016: 0.0021, 2017: 0.002, 2018: 0.0019, 2019: 0.0019, 2020: 0.0019, 2021: 0.0018, 2022: 0.0018, 2023: 0.0016, 2024: 0.0021, 2025: 0.0021},
    'NM': {1986: 0.0945, 1987: 0.424, 1988: 0.4621, 1989: 0.4772, 1990: 0.4805, 1991: 0.4806, 1992: 0.4824, 1993: 0.4829, 1994: 0.4829, 1995: 0.4834, 1996: 0.4765, 1997: 0.4672, 1998: 0.5618, 1999: 0.5842, 2000: 0.5912, 2001: 0.59, 2002: 0.594, 2003: 0.5933, 2004: 0.5965, 2005: 0.5967, 2006: 0.5975, 2007: 0.5904, 2008: 0.5701, 2009: 0.5665, 2010: 0.5418, 2011: 0.4542, 2012: 0.4143, 2013: 0.4165, 2014: 0.4347, 2015: 0.4307, 2016: 0.4355, 2017: 0.4469, 2018: 0.4088, 2019: 0.4272, 2020: 0.4241, 2021: 0.4275, 2022: 0.6168, 2023: 0.857, 2024: 1.0552, 2025: 1.1985},
    'NV': {1988: 0.0018, 1989: 0.0021, 1990: 0.0028, 1991: 0.0028, 1992: 0.0028, 1993: 0.0028, 1994: 0.0028, 1995: 0.0028, 1996: 0.0024, 1997: 0.0024, 1998: 0.0009, 1999: 0.0009, 2000: 0.0002, 2001: 0.0002, 2002: 0.0002, 2003: 0.0002, 2004: 0.0002, 2005: 0.0002, 2006: 0.0002, 2007: 0.0001, 2008: 0.0001, 2009: 0.0001, 2010: 0.0001, 2011: 0.0001, 2012: 0.0001, 2013: 0.0001, 2014: 0.0001, 2015: 0.0001, 2016: 0.0001, 2017: 0.0001, 2018: 0.0001, 2019: 0.0001, 2020: 0.0001, 2021: 0.0016, 2022: 0.0027, 2023: 0.003, 2024: 0.003, 2025: 0.003},
    'NY': {1986: 0.0056, 1987: 0.0241, 1988: 0.0388, 1989: 0.047, 1990: 0.0501, 1991: 0.053, 1992: 0.0563, 1993: 0.0598, 1994: 0.0598, 1995: 0.0598, 1996: 0.0577, 1997: 0.0538, 1998: 0.0506, 1999: 0.0526, 2000: 0.0538, 2001: 0.0573, 2002: 0.0603, 2003: 0.0607, 2004: 0.0581, 2005: 0.0614, 2006: 0.0645, 2007: 0.0665, 2008: 0.0597, 2009: 0.0544, 2010: 0.0536, 2011: 0.0529, 2012: 0.0507, 2013: 0.0476, 2014: 0.0446, 2015: 0.0428, 2016: 0.0385, 2017: 0.0342, 2018: 0.027, 2019: 0.0246, 2020: 0.0209, 2021: 0.0173, 2022: 0.0163, 2023: 0.0155, 2024: 0.0146, 2025: 0.014},
    'OH': {1986: 0.0082, 1987: 0.0997, 1988: 0.1392, 1989: 0.1957, 1990: 0.2432, 1991: 0.2627, 1992: 0.3132, 1993: 0.366, 1994: 0.366, 1995: 0.3645, 1996: 0.353, 1997: 0.3272, 1998: 0.3201, 1999: 0.2961, 2000: 0.2806, 2001: 0.3005, 2002: 0.3048, 2003: 0.3016, 2004: 0.2765, 2005: 0.288, 2006: 0.3296, 2007: 0.3623, 2008: 0.3522, 2009: 0.3475, 2010: 0.3436, 2011: 0.3436, 2012: 0.3362, 2013: 0.3171, 2014: 0.2784, 2015: 0.2672, 2016: 0.2634, 2017: 0.2575, 2018: 0.2475, 2019: 0.2423, 2020: 0.2371, 2021: 0.2277, 2022: 0.2275, 2023: 0.227, 2024: 0.2271, 2025: 0.2271},
    'OK': {1986: 0.058, 1987: 0.5151, 1988: 0.8754, 1989: 1.0183, 1990: 1.1338, 1991: 1.1391, 1992: 1.1611, 1993: 1.1704, 1994: 1.1704, 1995: 1.1696, 1996: 1.1551, 1997: 1.1376, 1998: 0.9718, 1999: 0.9769, 2000: 0.997, 2001: 1.0277, 2002: 1.0253, 2003: 1.0236, 2004: 1.0356, 2005: 1.0305, 2006: 1.056, 2007: 1.074, 2008: 0.9816, 2009: 0.959, 2010: 0.8604, 2011: 0.8582, 2012: 0.819, 2013: 0.7845, 2014: 0.7569, 2015: 0.7357, 2016: 0.7173, 2017: 0.6853, 2018: 0.6339, 2019: 0.6417, 2020: 0.635, 2021: 0.5195, 2022: 0.6141, 2023: 0.6231, 2024: 0.6589, 2025: 0.7155},
    'OR': {1986: 0.0629, 1987: 0.381, 1988: 0.4779, 1989: 0.5011, 1990: 0.5101, 1991: 0.513, 1992: 0.521, 1993: 0.5237, 1994: 0.5237, 1995: 0.5227, 1996: 0.5114, 1997: 0.4828, 1998: 0.3737, 1999: 0.3867, 2000: 0.4172, 2001: 0.4526, 2002: 0.4555, 2003: 0.4581, 2004: 0.491, 2005: 0.508, 2006: 0.5414, 2007: 0.5676, 2008: 0.5633, 2009: 0.5603, 2010: 0.5479, 2011: 0.5509, 2012: 0.5464, 2013: 0.5453, 2014: 0.5511, 2015: 0.5414, 2016: 0.5233, 2017: 0.4858, 2018: 0.4443, 2019: 0.4418, 2020: 0.4408, 2021: 0.5024, 2022: 0.531, 2023: 0.5997, 2024: 0.909, 2025: 0.9534},
    'PA': {1986: 0.0052, 1987: 0.0337, 1988: 0.0555, 1989: 0.0744, 1990: 0.0863, 1991: 0.0883, 1992: 0.092, 1993: 0.0949, 1994: 0.0949, 1995: 0.0944, 1996: 0.095, 1997: 0.0903, 1998: 0.0778, 1999: 0.0711, 2000: 0.0661, 2001: 0.0898, 2002: 0.1181, 2003: 0.1339, 2004: 0.1609, 2005: 0.2013, 2006: 0.2213, 2007: 0.2302, 2008: 0.2289, 2009: 0.2226, 2010: 0.2216, 2011: 0.2201, 2012: 0.2056, 2013: 0.1861, 2014: 0.1747, 2015: 0.1645, 2016: 0.1557, 2017: 0.1508, 2018: 0.1409, 2019: 0.1271, 2020: 0.1109, 2021: 0.101, 2022: 0.0928, 2023: 0.0825, 2024: 0.0789, 2025: 0.075},
    'RI': {2004: 0.0, 2005: 0.0, 2006: 0.0, 2007: 0.0, 2008: 0.0, 2009: 0.0, 2010: 0.0, 2011: 0.0, 2012: 0.0, 2013: 0.0, 2014: 0.0, 2015: 0.0, 2016: 0.0, 2017: 0.0, 2018: 0.0, 2019: 0.0, 2020: 0.0, 2021: 0.0, 2022: 0.0, 2023: 0.0, 2024: 0.0},
    'SC': {1986: 0.0184, 1987: 0.1294, 1988: 0.187, 1989: 0.233, 1990: 0.2547, 1991: 0.2571, 1992: 0.2612, 1993: 0.267, 1994: 0.267, 1995: 0.2665, 1996: 0.2677, 1997: 0.2627, 1998: 0.2096, 1999: 0.1934, 2000: 0.201, 2001: 0.2179, 2002: 0.2188, 2003: 0.2174, 2004: 0.2131, 2005: 0.214, 2006: 0.2132, 2007: 0.211, 2008: 0.1943, 2009: 0.1889, 2010: 0.1729, 2011: 0.1588, 2012: 0.1432, 2013: 0.1217, 2014: 0.1123, 2015: 0.0951, 2016: 0.08, 2017: 0.0771, 2018: 0.0624, 2019: 0.0583, 2020: 0.0557, 2021: 0.0436, 2022: 0.0376, 2023: 0.0274, 2024: 0.0242, 2025: 0.0221},
    'SD': {1986: 0.0358, 1987: 0.3918, 1988: 0.8713, 1989: 1.2834, 1990: 1.7372, 1991: 1.7412, 1992: 1.7518, 1993: 1.7723, 1994: 1.7723, 1995: 1.7725, 1996: 1.7337, 1997: 1.6989, 1998: 1.7333, 1999: 1.4727, 2000: 1.3259, 2001: 1.4175, 2002: 1.4321, 2003: 1.4343, 2004: 1.4621, 2005: 1.4732, 2006: 1.5152, 2007: 1.5593, 2008: 1.3017, 2009: 1.2489, 2010: 1.1136, 2011: 1.1622, 2012: 1.1103, 2013: 0.9723, 2014: 0.9326, 2015: 0.9261, 2016: 0.9774, 2017: 0.9769, 2018: 1.1095, 2019: 1.143, 2020: 1.1289, 2021: 1.3793, 2022: 1.7646, 2023: 2.0991, 2024: 2.3908, 2025: 2.6239},
    'TN': {1986: 0.0521, 1987: 0.2441, 1988: 0.3338, 1989: 0.3878, 1990: 0.41, 1991: 0.4206, 1992: 0.4379, 1993: 0.455, 1994: 0.455, 1995: 0.4538, 1996: 0.4283, 1997: 0.3746, 1998: 0.2632, 1999: 0.2291, 2000: 0.2312, 2001: 0.2485, 2002: 0.2488, 2003: 0.2482, 2004: 0.2714, 2005: 0.2739, 2006: 0.2765, 2007: 0.278, 2008: 0.2346, 2009: 0.2253, 2010: 0.2174, 2011: 0.2049, 2012: 0.1902, 2013: 0.1763, 2014: 0.1482, 2015: 0.1428, 2016: 0.1391, 2017: 0.1377, 2018: 0.1321, 2019: 0.1283, 2020: 0.1253, 2021: 0.1113, 2022: 0.1032, 2023: 0.0988, 2024: 0.0916, 2025: 0.0905},
    'TX': {1986: 0.1492, 1987: 1.9327, 1988: 2.9801, 1989: 3.5391, 1990: 3.8336, 1991: 3.8753, 1992: 3.9644, 1993: 4.068, 1994: 4.068, 1995: 4.0655, 1996: 4.0386, 1997: 3.9052, 1998: 3.5523, 1999: 3.6581, 2000: 3.897, 2001: 4.0434, 2002: 4.0436, 2003: 4.0341, 2004: 3.9817, 2005: 3.9569, 2006: 4.0449, 2007: 4.0741, 2008: 3.9383, 2009: 3.8468, 2010: 3.3051, 2011: 3.4559, 2012: 3.3542, 2013: 3.2529, 2014: 3.1766, 2015: 3.0404, 2016: 2.9886, 2017: 2.8964, 2018: 2.828, 2019: 2.8116, 2020: 2.7744, 2021: 2.3418, 2022: 2.3076, 2023: 2.1553, 2024: 2.1516, 2025: 2.1916},
    'UT': {1986: 0.0198, 1987: 0.1625, 1988: 0.2077, 1989: 0.2208, 1990: 0.2237, 1991: 0.2237, 1992: 0.2249, 1993: 0.2253, 1994: 0.2253, 1995: 0.2275, 1996: 0.2243, 1997: 0.2164, 1998: 0.1793, 1999: 0.1885, 2000: 0.1919, 2001: 0.1998, 2002: 0.2005, 2003: 0.2013, 2004: 0.2028, 2005: 0.2027, 2006: 0.2055, 2007: 0.2087, 2008: 0.199, 2009: 0.1948, 2010: 0.1453, 2011: 0.1675, 2012: 0.1784, 2013: 0.177, 2014: 0.1769, 2015: 0.1762, 2016: 0.1709, 2017: 0.165, 2018: 0.1619, 2019: 0.1616, 2020: 0.1614, 2021: 0.1164, 2022: 0.1365, 2023: 0.1578, 2024: 0.1625, 2025: 0.1621},
    'VA': {1986: 0.0047, 1987: 0.0256, 1988: 0.0475, 1989: 0.0628, 1990: 0.07, 1991: 0.0714, 1992: 0.0732, 1993: 0.0755, 1994: 0.0755, 1995: 0.0751, 1996: 0.0725, 1997: 0.0702, 1998: 0.0529, 1999: 0.0454, 2000: 0.0441, 2001: 0.0495, 2002: 0.0558, 2003: 0.0583, 2004: 0.0621, 2005: 0.0637, 2006: 0.0657, 2007: 0.0697, 2008: 0.0661, 2009: 0.0645, 2010: 0.063, 2011: 0.063, 2012: 0.0612, 2013: 0.0588, 2014: 0.0561, 2015: 0.0545, 2016: 0.0514, 2017: 0.0447, 2018: 0.0378, 2019: 0.0347, 2020: 0.0331, 2021: 0.032, 2022: 0.031, 2023: 0.0295, 2024: 0.0282, 2025: 0.0282},
    'VT': {1986: 0.0, 1987: 0.0002, 1988: 0.0002, 1989: 0.0002, 1990: 0.0002, 1991: 0.0002, 1992: 0.0002, 1993: 0.0002, 1994: 0.0002, 1995: 0.0002, 1996: 0.0002, 1997: 0.0002, 1998: 0.0002, 1999: 0.0003, 2000: 0.0003, 2001: 0.0005, 2002: 0.001, 2003: 0.0012, 2004: 0.0014, 2005: 0.0016, 2006: 0.0017, 2007: 0.002, 2008: 0.0023, 2009: 0.0025, 2010: 0.0027, 2011: 0.0028, 2012: 0.0028, 2013: 0.0028, 2014: 0.0029, 2015: 0.0029, 2016: 0.0029, 2017: 0.0027, 2018: 0.0026, 2019: 0.0024, 2020: 0.0023, 2021: 0.0023, 2022: 0.0021, 2023: 0.002, 2024: 0.0018, 2025: 0.0017},
    'WA': {1986: 0.0546, 1987: 0.5303, 1988: 0.8235, 1989: 0.8953, 1990: 0.9747, 1991: 0.9829, 1992: 1.0167, 1993: 1.0465, 1994: 1.0465, 1995: 1.0446, 1996: 1.0331, 1997: 1.017, 1998: 0.8158, 1999: 0.9584, 2000: 1.0832, 2001: 1.2754, 2002: 1.2813, 2003: 1.2864, 2004: 1.3736, 2005: 1.3925, 2006: 1.4723, 2007: 1.5572, 2008: 1.5376, 2009: 1.5141, 2010: 1.4442, 2011: 1.458, 2012: 1.4886, 2013: 1.4545, 2014: 1.3957, 2015: 1.3105, 2016: 1.2234, 2017: 1.1956, 2018: 1.2057, 2019: 1.1917, 2020: 1.1774, 2021: 0.974, 2022: 1.0339, 2023: 0.9692, 2024: 0.9205, 2025: 0.9143},
    'WI': {1986: 0.0209, 1987: 0.2251, 1988: 0.3892, 1989: 0.4888, 1990: 0.5736, 1991: 0.6044, 1992: 0.6595, 1993: 0.713, 1994: 0.713, 1995: 0.7099, 1996: 0.7069, 1997: 0.662, 1998: 0.5973, 1999: 0.5986, 2000: 0.5923, 2001: 0.6365, 2002: 0.6349, 2003: 0.6404, 2004: 0.6198, 2005: 0.6201, 2006: 0.6167, 2007: 0.6069, 2008: 0.526, 2009: 0.4597, 2010: 0.4282, 2011: 0.3986, 2012: 0.3682, 2013: 0.3171, 2014: 0.2624, 2015: 0.244, 2016: 0.2383, 2017: 0.2313, 2018: 0.2197, 2019: 0.2053, 2020: 0.2021, 2021: 0.1927, 2022: 0.1959, 2023: 0.1934, 2024: 0.1887, 2025: 0.1884},
    'WV': {1986: 0.0001, 1987: 0.0003, 1988: 0.0005, 1989: 0.0006, 1990: 0.0006, 1991: 0.0006, 1992: 0.0006, 1993: 0.0006, 1994: 0.0006, 1995: 0.0006, 1996: 0.0005, 1997: 0.0004, 1998: 0.0008, 1999: 0.0008, 2000: 0.001, 2001: 0.001, 2002: 0.0011, 2003: 0.0017, 2004: 0.0022, 2005: 0.0026, 2006: 0.0034, 2007: 0.0043, 2008: 0.0049, 2009: 0.0052, 2010: 0.0054, 2011: 0.0059, 2012: 0.0062, 2013: 0.0063, 2014: 0.0064, 2015: 0.0065, 2016: 0.0066, 2017: 0.0064, 2018: 0.0079, 2019: 0.0078, 2020: 0.0078, 2021: 0.0077, 2022: 0.0074, 2023: 0.0096, 2024: 0.0137, 2025: 0.0158},
    'WY': {1986: 0.0076, 1987: 0.1111, 1988: 0.2054, 1989: 0.2272, 1990: 0.2516, 1991: 0.2516, 1992: 0.2516, 1993: 0.2518, 1994: 0.2518, 1995: 0.2537, 1996: 0.2517, 1997: 0.2469, 1998: 0.2486, 1999: 0.2723, 2000: 0.2768, 2001: 0.2773, 2002: 0.278, 2003: 0.279, 2004: 0.2817, 2005: 0.2811, 2006: 0.2852, 2007: 0.2843, 2008: 0.2762, 2009: 0.2708, 2010: 0.2088, 2011: 0.224, 2012: 0.213, 2013: 0.1955, 2014: 0.1968, 2015: 0.1916, 2016: 0.1883, 2017: 0.1904, 2018: 0.1951, 2019: 0.2066, 2020: 0.2056, 2021: 0.1479, 2022: 0.2869, 2023: 0.3761, 2024: 0.4662, 2025: 0.585},
}

# Legacy — kept for backward compat with any cached references
NASS_CROP_PARAMS = {
    "Corn":     {"commodity_desc": "CORN",    "util_practice_desc": "GRAIN"},
    "Soybeans": {"commodity_desc": "SOYBEANS"},
    "Wheat":    {"commodity_desc": "WHEAT",   "class_desc": "ALL CLASSES"},
    "Sorghum":  {"commodity_desc": "SORGHUM", "util_practice_desc": "GRAIN"},
}

# ── State lookups ─────────────────────────────────────────────────────────────
# RMA subset (used for county FIPS name-lookup only)
STATE_FIPS = {
    "AL": "01", "AR": "05", "CO": "08", "GA": "13", "IA": "19",
    "ID": "16", "IL": "17", "IN": "18", "KS": "20", "KY": "21",
    "MD": "24", "MI": "26", "MN": "27", "MO": "29", "MS": "28",
    "MT": "30", "NC": "37", "ND": "38", "NE": "31", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "VA": "51", "WA": "53", "WI": "55",
    "WY": "56",
}

# Full 50-state FIPS — used for NASS county map lookups
STATE_FIPS_ALL = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06",
    "CO": "08", "CT": "09", "DE": "10", "FL": "12", "GA": "13",
    "HI": "15", "ID": "16", "IL": "17", "IN": "18", "IA": "19",
    "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29",
    "MT": "30", "NE": "31", "NV": "32", "NH": "33", "NJ": "34",
    "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45",
    "SD": "46", "TN": "47", "TX": "48", "UT": "49", "VT": "50",
    "VA": "51", "WA": "53", "WV": "54", "WI": "55", "WY": "56",
}

ABBR_TO_NAME = {
    "AL": "Alabama",        "AK": "Alaska",         "AZ": "Arizona",
    "AR": "Arkansas",       "CA": "California",     "CO": "Colorado",
    "CT": "Connecticut",    "DE": "Delaware",       "FL": "Florida",
    "GA": "Georgia",        "HI": "Hawaii",         "ID": "Idaho",
    "IL": "Illinois",       "IN": "Indiana",        "IA": "Iowa",
    "KS": "Kansas",         "KY": "Kentucky",       "LA": "Louisiana",
    "ME": "Maine",          "MD": "Maryland",       "MA": "Massachusetts",
    "MI": "Michigan",       "MN": "Minnesota",      "MS": "Mississippi",
    "MO": "Missouri",       "MT": "Montana",        "NE": "Nebraska",
    "NV": "Nevada",         "NH": "New Hampshire",  "NJ": "New Jersey",
    "NM": "New Mexico",     "NY": "New York",       "NC": "North Carolina",
    "ND": "North Dakota",   "OH": "Ohio",           "OK": "Oklahoma",
    "OR": "Oregon",         "PA": "Pennsylvania",   "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota",   "TN": "Tennessee",
    "TX": "Texas",          "UT": "Utah",           "VT": "Vermont",
    "VA": "Virginia",       "WA": "Washington",     "WV": "West Virginia",
    "WI": "Wisconsin",      "WY": "Wyoming",
}

STATE_CENTROIDS = {
    "AL": (-86.8,  32.8), "AR": (-92.4,  34.9), "AZ": (-111.6, 34.3),
    "CA": (-119.4, 37.2), "CO": (-105.5, 39.0), "CT": (-72.7,  41.6),
    "DE": (-75.5,  38.9), "FL": (-81.5,  27.8), "GA": (-83.4,  32.7),
    "IA": (-93.1,  42.0), "ID": (-114.5, 44.4), "IL": (-89.2,  40.0),
    "IN": (-86.3,  40.3), "KS": (-98.4,  38.5), "KY": (-84.9,  37.5),
    "LA": (-92.1,  30.5), "MD": (-76.8,  39.0), "ME": (-69.2,  44.7),
    "MI": (-84.5,  44.3), "MN": (-94.3,  46.4), "MO": (-92.5,  38.4),
    "MS": (-89.7,  32.7), "MT": (-110.5, 46.9), "NC": (-79.4,  35.6),
    "ND": (-100.5, 47.5), "NE": (-99.9,  41.5), "NJ": (-74.4,  40.1),
    "NM": (-106.0, 34.5), "NY": (-75.5,  42.9), "OH": (-82.8,  40.4),
    "OK": (-97.5,  35.5), "OR": (-120.6, 44.1), "PA": (-77.2,  40.9),
    "SC": (-80.9,  33.8), "SD": (-100.2, 44.4), "TN": (-86.7,  35.8),
    "TX": (-99.3,  31.5), "UT": (-111.1, 39.3), "VA": (-78.7,  37.5),
    "WA": (-120.5, 47.4), "WI": (-89.8,  44.5), "WV": (-80.4,  38.7),
    "WY": (-107.6, 43.0),
}

# ── RMA metric mappings ───────────────────────────────────────────────────────
METRIC_COL = {
    "Production":            "Reported Production",
    "Production Acres":      "Reported Production Acres",
    "Yield":                 "Reported Yield Mean",
    "Prevent Planted Acres": "Prev Plant Acres",
}
METRIC_UNIT = {
    "Production": "bu", "Production Acres": "ac",
    "Yield": "bu/ac",   "Prevent Planted Acres": "ac",
}
METRIC_FMT = {
    "Production": ",.0f", "Production Acres": ",.0f",
    "Yield": ".1f",        "Prevent Planted Acres": ",.0f",
}
COLOR_SCALE = {
    "Production": "YlOrBr", "Production Acres": "YlGn",
    "Yield": "RdYlGn",      "Prevent Planted Acres": "OrRd",
}

DISPLAY_DIVISOR = {
    "Production": 1_000_000, "Production Acres": 100_000,
    "Yield": 1,               "Prevent Planted Acres": 100_000,
}
DISPLAY_UNIT = {
    "Production": "M bu",     "Production Acres": "×100K ac",
    "Yield": "bu/ac",          "Prevent Planted Acres": "×100K ac",
}

# ── JPSI brand palette — light theme, matched to jpsi.com ───────────────────
# Site white nav + dark green hero: #425248  |  Teal CTA: #5ba5af  |  Lime: #88b131
DARK    = "#f4f8f5"   # near-white with green tint (app background)
PANEL   = "#e8f0eb"   # light green-gray (sidebar, cards)
SURFACE = "#ddeae1"   # slightly deeper green-gray (hover/alternates)
BORDER  = "#b8d0be"   # soft green border
TEXT    = "#1e2e22"   # near-black with green tint (body text)
MUTED   = "#4a6a54"   # medium green-gray (muted text)
ACCENT  = "#3d8f99"   # JPSI teal (slightly deepened for light bg readability)
ACCENT2 = "#6a9a20"   # JPSI lime green (deepened for light bg contrast)
LAND    = "#c8dccb"   # light sage green land mass (maps)


# ── RMA Data ──────────────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    out = {}
    for crop in ["Corn", "Soybeans", "Wheat"]:
        try:
            df = pd.read_excel(DATA_PATH, sheet_name=crop)
        except Exception:
            continue
        df.columns  = df.columns.str.strip()
        df["State"]    = df["State"].str.strip()
        df["County"]   = df["County"].str.strip()
        df["Practice"] = df["Practice"].str.strip()
        df["PG"] = np.where(
            df["Practice"].str.startswith("Irrigated"),          "Irrigated",
            np.where(df["Practice"].str.startswith("Non-Irrigated"), "Non-Irrigated", "Invalid"),
        )
        df = df[df["PG"] != "Invalid"].copy()
        if "Type" in df.columns:
            df["Type"] = df["Type"].str.strip()
        for col in METRIC_COL.values():
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        key_cols = [c for c in ["State", "County", "PG", "Type", "Yield Year"] if c in df.columns]
        idx_keep = df.groupby(key_cols)["Reported Production"].idxmax()
        df = df.loc[idx_keep].reset_index(drop=True)
        out[crop] = df
    return out


# ── NASS Data ─────────────────────────────────────────────────────────────────
@st.cache_data
def load_nass_stat(crop: str, year: int, stat_type: str,
                   cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Generic NASS county-level loader for any stat type.
    stat_type: 'production' | 'planted' | 'harvested' | 'yield' | 'prevent_plant'
    Returns DataFrame with [State, County, fips, Value].
    """
    params = {
        "key":            NASS_API_KEY,
        "source_desc":    "SURVEY",
        "sector_desc":    "CROPS",
        "agg_level_desc": "COUNTY",
        "year":           str(year),
        "format":         "JSON",
    }
    params.update(NASS_STAT_BASE[stat_type])
    params.update(NASS_CROP_STAT_PARAMS[crop][stat_type])
    # NASS doesn't publish class_desc=ALL CLASSES at county level for wheat
    if crop == "Wheat":
        params.pop("class_desc", None)
    url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            raw = json.load(r)
    except Exception as e:
        return pd.DataFrame(columns=["State", "County", "fips", "Value"])
        return pd.DataFrame(columns=["State", "County", "fips", "Value"])

    records = raw.get("data", [])
    if not records:
        return pd.DataFrame(columns=["State", "County", "fips", "Value"])

    df = pd.DataFrame(records)
    needed = ["state_alpha", "county_name", "state_fips_code",
              "county_ansi", "prodn_practice_desc", "short_desc", "Value"]
    df = df[[c for c in needed if c in df.columns]].copy()

    # For prevent_plant: keep only rows whose short_desc contains "PREVENT"
    # (AREA PLANTED queries return both regular planted and prevented-planted rows)
    if stat_type == "prevent_plant":
        if "short_desc" in df.columns:
            df = df[df["short_desc"].str.upper().str.contains("PREVENT", na=False)]
        if df.empty:
            return pd.DataFrame(columns=["State", "County", "fips", "Value"])

    # Drop state-level and aggregate rows
    df = df[~df["county_ansi"].isin(["998", "000", "999"])]
    df = df[~df["county_name"].str.strip().str.lower().str.startswith("other")]

    df["Value"] = pd.to_numeric(
        df["Value"].str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    ).fillna(0)
    df["fips"]   = df["state_fips_code"].str.zfill(2) + df["county_ansi"].str.zfill(3)
    df["State"]  = df["state_alpha"].str.strip()
    df["County"] = df["county_name"].str.strip().str.title()

    # Dedup: prefer ALL PRODUCTION PRACTICES row; fallback to max-value row
    key      = ["State", "County", "fips"]
    all_prac = "ALL PRODUCTION PRACTICES"
    if "prodn_practice_desc" in df.columns:
        has_all = df[df["prodn_practice_desc"] == all_prac].copy()
        no_all  = df[~df["fips"].isin(has_all["fips"].unique())].copy()
        if not no_all.empty:
            no_all = no_all.loc[no_all.groupby(key)["Value"].idxmax()]
        df = pd.concat([has_all, no_all], ignore_index=True)

    # Wheat is published by class (WINTER / SPRING) at county level — aggregate
    if crop == "Wheat":
        if stat_type == "yield":
            df = df.loc[df.groupby(key)["Value"].idxmax()].reset_index(drop=True)
        else:
            df = df.groupby(key, as_index=False)["Value"].sum()

    return df[key + ["Value"]].reset_index(drop=True)


@st.cache_data
def load_nass_state(crop: str, year: int, stat_type: str,
                    cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Query NASS at STATE level for official state-reported totals.
    Uses domain_desc=TOTAL so we get the main survey figures, not organic
    or other sub-domain breakdowns.
    Returns DataFrame with [State, Value] — one row per state.
    """
    params = {
        "key":                   NASS_API_KEY,
        "source_desc":           "SURVEY",
        "sector_desc":           "CROPS",
        "agg_level_desc":        "STATE",
        "domain_desc":           "TOTAL",
        # NASS stores multiple estimates per crop year (Aug/Sep/Nov forecasts
        # plus the January Annual Summary).  reference_period_desc=YEAR
        # isolates the final Annual Summary and ignores the in-season forecasts.
        "reference_period_desc": "YEAR",
        "year":                  str(year),
        "format":                "JSON",
    }
    params.update(NASS_STAT_BASE[stat_type])
    params.update(NASS_CROP_STAT_PARAMS[crop][stat_type])
    url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            raw = json.load(r)
    except Exception as e:
        return pd.DataFrame(columns=["State", "Value"])
        return pd.DataFrame(columns=["State", "Value"])

    records = raw.get("data", [])
    if not records:
        return pd.DataFrame(columns=["State", "Value"])

    df = pd.DataFrame(records)
    needed = ["state_alpha", "prodn_practice_desc", "short_desc", "Value"]
    df = df[[c for c in needed if c in df.columns]].copy()

    # For prevent_plant: keep only rows whose short_desc contains "PREVENT"
    if stat_type == "prevent_plant":
        if "short_desc" in df.columns:
            df = df[df["short_desc"].str.upper().str.contains("PREVENT", na=False)]
        if df.empty:
            return pd.DataFrame(columns=["State", "Value"])

    df["Value"] = pd.to_numeric(
        df["Value"].str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    ).fillna(0)
    df["State"] = df["state_alpha"].str.strip()

    # Keep only known 50-state abbreviations — explicitly excludes "US" (national
    # total), "PR", "GU", "VI", "OTHER STATES", etc.  If we kept "US" it would be
    # summed alongside the 50 states and inflate the national total ~2×.
    df = df[df["State"].isin(set(STATE_FIPS_ALL.keys()))]

    # Dedup step 1: prefer ALL PRODUCTION PRACTICES row; fallback to max-value row
    all_prac = "ALL PRODUCTION PRACTICES"
    if "prodn_practice_desc" in df.columns:
        has_all = df[df["prodn_practice_desc"] == all_prac].copy()
        no_all  = df[~df["State"].isin(has_all["State"].unique())].copy()
        if not no_all.empty:
            no_all = no_all.loc[no_all.groupby("State")["Value"].idxmax()]
        df = pd.concat([has_all, no_all], ignore_index=True)

    # Dedup step 2: guarantee exactly ONE row per state.  NASS can return multiple
    # "ALL PRODUCTION PRACTICES" rows per state (e.g. different short_desc values);
    # keeping them all causes duplicate map labels and an inflated national total.
    df = df.loc[df.groupby("State")["Value"].idxmax()].reset_index(drop=True)

    return df[["State", "Value"]].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _load_wheat_class_prod(year: int, class_desc: str, cache_ver: str) -> float:
    """Return national total wheat production (bushels) for a specific class_desc.
    Used to split winter vs. spring/durum for the June-based marketing year."""
    params = {
        "key":                   NASS_API_KEY,
        "source_desc":           "SURVEY",
        "sector_desc":           "CROPS",
        "agg_level_desc":        "STATE",
        "domain_desc":           "TOTAL",
        "reference_period_desc": "YEAR",
        "year":                  str(year),
        "commodity_desc":        "WHEAT",
        "class_desc":            class_desc,
        "prodn_practice_desc":   "ALL PRODUCTION PRACTICES",
        "statisticcat_desc":     "PRODUCTION",
        "unit_desc":             "BU",
        "format":                "JSON",
    }
    try:
        with urllib.request.urlopen(
            NASS_BASE_URL + "?" + urllib.parse.urlencode(params), timeout=45
        ) as r:
            records = json.load(r).get("data", [])
    except Exception:
        return 0.0
    total = 0.0
    for rec in records:
        try:
            v = float(str(rec.get("Value", "0")).replace(",", ""))
            if v > 0:
                total += v
        except (ValueError, TypeError):
            pass
    return total


@st.cache_data(show_spinner=False)
def _load_yr_jun(crop: str, year: int, cache_ver: str) -> float:
    """Return national Jun 1 stocks total (bushels) for a crop-year. 0.0 if unavailable."""
    df = load_grain_stocks(crop, year, "FIRST OF JUN", cache_ver)
    return float(df["Total"].sum()) if not df.empty else 0.0


# FAS PSD commodity codes and lbs-per-bushel for WASDE ending-stocks lookup
_PSD_BASE = "https://apps.fas.usda.gov/psdonline/api"
_PSD_CROP = {
    "Corn":     ("0440000", 56),
    "Soybeans": ("2222000", 60),
    "Wheat":    ("0410000", 60),
    "Sorghum":  ("0459100", 56),
}
_PSD_ENDING_STOCKS_ID = 176


@st.cache_data(ttl=3600, show_spinner=False)
def load_wasde_ending_stocks(crop: str, market_year: int, cache_ver: str) -> float:
    """Return US WASDE ending stocks (bushels) for a crop's marketing year.
    Uses the USDA FAS PSD OpenData API (no key required); attribute 176 = Ending Stocks.
    Returns 0.0 if crop not mapped or request fails."""
    if crop not in _PSD_CROP:
        return 0.0
    code, lbs_per_bu = _PSD_CROP[crop]
    try:
        params = {"commodityCode": code, "countryCode": "0000US"}
        r = urllib.request.urlopen(
            _PSD_BASE + "/data/get?" + urllib.parse.urlencode(params), timeout=30
        )
        rows = json.load(r)
    except Exception:
        return 0.0
    for row in rows:
        if (row.get("marketYear") == market_year and
                row.get("attributeId") == _PSD_ENDING_STOCKS_ID):
            try:
                val_1000mt = float(row["value"])
                # 1000 MT × 1000 kg/MT × 2.20462 lb/kg ÷ lbs_per_bu = bushels
                return val_1000mt * 1000 * 2204.62 / lbs_per_bu
            except (TypeError, ValueError):
                return 0.0
    return 0.0


@st.cache_data
def load_nass_county(crop: str, year: int = 2025,
                     cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Load county-level production data.  Kept as a standalone function
    (not delegating to load_nass_stat) to avoid Streamlit cache-within-cache
    issues that can cause stale or incorrect return values.
    Returns [State, County, fips, Production].
    """
    params = {
        "key":               NASS_API_KEY,
        "source_desc":       "SURVEY",
        "sector_desc":       "CROPS",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc":         "BU",
        "agg_level_desc":    "COUNTY",
        "year":              str(year),
        "format":            "JSON",
    }
    params.update(NASS_CROP_PARAMS[crop])
    if crop == "Wheat":
        params.pop("class_desc", None)
    url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            raw = json.load(r)
    except Exception as e:
        return pd.DataFrame(columns=["State", "County", "fips", "Production"])
        return pd.DataFrame(columns=["State", "County", "fips", "Production"])

    records = raw.get("data", [])
    if not records:
        return pd.DataFrame(columns=["State", "County", "fips", "Production"])

    df = pd.DataFrame(records)
    needed = ["state_alpha", "county_name", "state_fips_code",
              "county_ansi", "prodn_practice_desc", "asd_desc", "asd_code", "Value"]
    df = df[[c for c in needed if c in df.columns]].copy()

    df = df[~df["county_ansi"].isin(["998", "000", "999"])]
    df = df[~df["county_name"].str.strip().str.lower().str.startswith("other")]

    df["Production"] = pd.to_numeric(
        df["Value"].str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    ).fillna(0)
    df["fips"]   = df["state_fips_code"].str.zfill(2) + df["county_ansi"].str.zfill(3)
    df["State"]  = df["state_alpha"].str.strip()
    df["County"] = df["county_name"].str.strip().str.title()

    key      = ["State", "County", "fips"]
    all_prac = "ALL PRODUCTION PRACTICES"
    if "prodn_practice_desc" in df.columns:
        has_all = df[df["prodn_practice_desc"] == all_prac].copy()
        no_all  = df[~df["fips"].isin(has_all["fips"].unique())].copy()
        if not no_all.empty:
            no_all = no_all.loc[no_all.groupby(key)["Production"].idxmax()]
        df = pd.concat([has_all, no_all], ignore_index=True)

    # Include district fields when present in the API response
    extra = [c for c in ["asd_desc", "asd_code"] if c in df.columns]

    # Wheat is published by class at county level — sum across classes per county
    if crop == "Wheat":
        df_agg = df.groupby(key, as_index=False)["Production"].sum()
        if extra:
            df_extra = df.groupby(key)[extra].first().reset_index()
            df_agg = df_agg.merge(df_extra, on=key, how="left")
        df = df_agg

    return df[key + ["Production"] + extra].reset_index(drop=True)


# ── Tier-1 county estimation (production, planted acres, harvested acres, yield)
# Missing counties are estimated via district-multiplier applied to olympic-avg
# historical shares, then scaled to reconcile with NASS OTHER COUNTIES totals.

@st.cache_data(show_spinner=False)
def _load_county_raw_for_est(crop: str, state: str, year: int,
                              stat_type: str, cache_ver: str) -> pd.DataFrame:
    """County rows INCLUDING the OTHER COUNTIES catch-all for any stat type.
    Used only for the estimation pipeline."""
    params = {
        "key": NASS_API_KEY, "source_desc": "SURVEY", "sector_desc": "CROPS",
        "agg_level_desc": "COUNTY", "domain_desc": "TOTAL",
        "state_alpha": state, "year": str(year), "format": "JSON",
    }
    params.update(NASS_STAT_BASE[stat_type])
    params.update(NASS_CROP_STAT_PARAMS[crop][stat_type])
    if crop == "Wheat":
        params.pop("class_desc", None)
    try:
        url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=45) as r:
            raw = json.load(r)
    except Exception:
        return pd.DataFrame()
    records = raw.get("data", [])
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    needed = ["state_alpha", "county_name", "county_ansi", "state_fips_code",
              "asd_desc", "asd_code", "prodn_practice_desc", "Value"]
    df = df[[c for c in needed if c in df.columns]].copy()
    df["Value_num"] = pd.to_numeric(
        df["Value"].str.replace(",", "", regex=False).str.strip(), errors="coerce"
    ).fillna(0)
    df["fips"]    = df["state_fips_code"].str.zfill(2) + df["county_ansi"].str.zfill(3)
    df["State"]   = df["state_alpha"].str.strip()
    df["County"]  = df["county_name"].str.strip().str.title()
    df["ANSI"]    = df["county_ansi"].str.strip().str.zfill(3)
    df["District"]     = df["asd_desc"].str.strip().str.title() if "asd_desc"  in df.columns else ""
    df["DistrictCode"] = df["asd_code"].str.strip()              if "asd_code"  in df.columns else ""

    # Wheat is published by class at county level — aggregate across classes
    if crop == "Wheat":
        agg_key = [c for c in ["State", "County", "ANSI", "fips", "District", "DistrictCode"]
                   if c in df.columns]
        if stat_type == "yield":
            df = df.loc[df.groupby(agg_key)["Value_num"].idxmax()].reset_index(drop=True)
        else:
            df_sum = df.groupby(agg_key, as_index=False)["Value_num"].sum()
            if "prodn_practice_desc" in df.columns:
                df_prac = df.groupby(agg_key)["prodn_practice_desc"].first().reset_index()
                df = df_sum.merge(df_prac, on=agg_key, how="left")
            else:
                df = df_sum

    return df


@st.cache_data(show_spinner=False)
def _build_hist_shares(crop: str, state: str, stat_type: str,
                       history_years: tuple, cache_ver: str) -> pd.DataFrame:
    """Olympic-average historical share (county_val / state_total) per county.
    Works for any stat_type (production / planted / harvested / yield).
    history_years is a tuple so it's hashable as a cache key."""
    rows = []
    for yr in history_years:
        st_total = load_nass_state(crop, yr, stat_type, cache_ver)
        if st_total.empty or "State" not in st_total.columns:
            continue
        st_row = st_total[st_total["State"] == state]
        if st_row.empty:
            continue
        st_val = float(st_row["Value"].iloc[0])
        if st_val <= 0:
            continue
        df = _load_county_raw_for_est(crop, state, yr, stat_type, cache_ver)
        if df.empty:
            continue
        named = df[
            (~df["ANSI"].isin(["998", "000", "999"])) &
            (~df["County"].str.lower().str.startswith("other", na=False))
        ].copy()
        if "prodn_practice_desc" in named.columns:
            named = named[named["prodn_practice_desc"] == "ALL PRODUCTION PRACTICES"]
        named = named.loc[named.groupby("fips")["Value_num"].idxmax()]
        for _, row in named.iterrows():
            rows.append({
                "fips":         row["fips"],
                "County":       row["County"],
                "District":     row.get("District", ""),
                "DistrictCode": row.get("DistrictCode", ""),
                "year":         yr,
                "share":        row["Value_num"] / st_val,
            })
    if not rows:
        return pd.DataFrame()
    hist = pd.DataFrame(rows)

    def _olympic(vals):
        v = sorted(vals)
        return float(np.mean(v[1:-1] if len(v) >= 4 else v))

    result = (
        hist.groupby(["fips", "County"])
        .apply(lambda g: pd.Series({
            "hist_share":    _olympic(g["share"].tolist()),
            "n_obs":         len(g),
            "District":      g["District"].mode().iloc[0]     if len(g) > 0 else "",
            "DistrictCode":  g["DistrictCode"].mode().iloc[0] if len(g) > 0 else "",
        }), include_groups=False)
        .reset_index()
    )
    return result


@st.cache_data(show_spinner=False)
def _build_adj(state_fips: str, cache_ver: str, _geo: dict) -> dict:
    """County adjacency map {fips: [neighbor_fips,...]} via geopandas."""
    feats = [f for f in _geo["features"] if f["properties"]["STATE"] == state_fips]
    if not feats:
        return {}
    rows, geoms = [], []
    for f in feats:
        try:
            geoms.append(shape(f["geometry"]).buffer(0.001))
            rows.append({"fips": f["properties"]["STATE"] + f["properties"]["COUNTY"]})
        except Exception:
            continue
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    joined = gpd.sjoin(gdf, gdf, how="left", predicate="intersects")
    joined = joined[joined["fips_left"] != joined["fips_right"]]
    adj: dict = {}
    for _, row in joined.iterrows():
        adj.setdefault(row["fips_left"], []).append(row["fips_right"])
    return adj


@st.cache_data(show_spinner=False)
def get_completed_county_data(crop: str, state: str, year: int,
                               stat_type: str, cache_ver: str) -> pd.DataFrame:
    """
    Generic Tier-1 estimation for any additive stat type:
      production / planted / harvested

    For yield: share = county_yield / state_yield; district multiplier applied
    but NO OTHER-COUNTIES reconciliation (yield is a ratio, not additive).

    Returns [fips, County, District, DistrictCode, Value, is_estimated]
    where  named + estimated  sums (or means for yield) match the state total.
    """
    raw = _load_county_raw_for_est(crop, state, year, stat_type, cache_ver)
    if raw.empty:
        return pd.DataFrame()

    state_total_df = load_nass_state(crop, year, stat_type, cache_ver)
    if state_total_df.empty or "State" not in state_total_df.columns:
        return pd.DataFrame()
    st_row = state_total_df[state_total_df["State"] == state]
    if st_row.empty:
        return pd.DataFrame()
    state_total = float(st_row["Value"].iloc[0])

    is_other  = raw["County"].str.lower().str.startswith("other", na=False)
    other_tot = raw[is_other]["Value_num"].sum()
    named     = raw[~is_other & ~raw["ANSI"].isin(["998","000","999"])].copy()
    if "prodn_practice_desc" in named.columns:
        named = named[named["prodn_practice_desc"] == "ALL PRODUCTION PRACTICES"]
    named = named.loc[named.groupby("fips")["Value_num"].idxmax()]
    named["is_estimated"] = False

    # For yield: OTHER COUNTIES doesn't exist as an additive bucket.
    # Fall back to raw if nothing is missing.
    is_yield = (stat_type == "yield")
    if not is_yield and other_tot <= 0:
        named["Value"] = named["Value_num"]
        return named[["fips","County","District","DistrictCode","Value","is_estimated"]]
    if is_yield and named.empty:
        named["Value"] = named["Value_num"]
        return named[["fips","County","District","DistrictCode","Value","is_estimated"]]

    hist_yrs    = tuple(range(max(year - 8, 2015), year))
    hist_shares = _build_hist_shares(crop, state, stat_type, hist_yrs, cache_ver)
    if hist_shares.empty:
        named["Value"] = named["Value_num"]
        return named[["fips","County","District","DistrictCode","Value","is_estimated"]]

    named_fips = set(named["fips"])
    missing = hist_shares[~hist_shares["fips"].isin(named_fips)].copy()
    if missing.empty:
        named["Value"] = named["Value_num"]
        return named[["fips","County","District","DistrictCode","Value","is_estimated"]]

    # Deviation ratios from reported counties
    hs_map = dict(zip(hist_shares["fips"], hist_shares["hist_share"]))
    dev = {
        row["fips"]: row["Value_num"] / (hs_map[row["fips"]] * state_total)
        for _, row in named.iterrows()
        if row["fips"] in hs_map and hs_map[row["fips"]] > 0
    }
    named_r    = named.copy(); named_r["ratio"] = named_r["fips"].map(dev)
    dist_ratio = named_r.dropna(subset=["ratio"]).groupby("District")["ratio"].mean().to_dict()
    state_ratio= float(np.mean(list(dev.values()))) if dev else 1.0

    missing = missing.copy()
    missing["mult"]    = missing["District"].map(lambda d: dist_ratio.get(d, state_ratio))
    missing["raw_est"] = missing["hist_share"] * state_total * missing["mult"]

    if not is_yield:
        # Scale estimates to sum exactly to OTHER COUNTIES
        raw_sum = missing["raw_est"].sum()
        scale   = (other_tot / raw_sum) if raw_sum > 0 else 1.0
        missing["Value"] = missing["raw_est"] * scale
    else:
        # Yield: no reconciliation — use raw estimate directly
        missing["Value"] = missing["raw_est"]

    missing["is_estimated"] = True
    named["Value"] = named["Value_num"]

    return pd.concat([
        named[["fips","County","District","DistrictCode","Value","is_estimated"]],
        missing[["fips","County","District","DistrictCode","Value","is_estimated"]],
    ], ignore_index=True)


def get_completed_county_production(crop: str, state: str, year: int,
                                    cache_ver: str) -> pd.DataFrame:
    """Backward-compat wrapper — returns Production column instead of Value."""
    df = get_completed_county_data(crop, state, year, "production", cache_ver)
    if df.empty:
        return df
    return df.rename(columns={"Value": "Production"})


# ── Grain Stocks loader ───────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_grain_stocks(crop: str, year: int, reference_period: str,
                      cache_ver: str) -> pd.DataFrame:
    """
    Load state-level grain stocks, returning one row per state with columns:
    State, Total, OnFarm, OffFarm, PctOnFarm, PctOffFarm.
    All values in bushels; Pct columns in percent (0-100).
    """
    params = {
        "key":                   NASS_API_KEY,
        "source_desc":           "SURVEY",
        "sector_desc":           "CROPS",
        "statisticcat_desc":     "STOCKS",
        "unit_desc":             "BU",
        "agg_level_desc":        "STATE",
        "domain_desc":           "TOTAL",
        "reference_period_desc": reference_period,
        "year":                  str(year),
        "format":                "JSON",
    }
    params.update(STOCKS_CROP_PARAMS.get(crop, {"commodity_desc": crop.upper()}))
    try:
        url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=45) as r:
            raw = json.load(r)
    except Exception:
        return pd.DataFrame()

    records = raw.get("data", [])
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["Value_num"] = pd.to_numeric(
        df["Value"].str.replace(",", "", regex=False).str.strip(), errors="coerce"
    )
    df = df.dropna(subset=["Value_num"])
    df["State"] = df["state_alpha"].str.strip()
    df = df[df["State"].isin(set(STATE_FIPS_ALL.keys()))]

    # Classify rows by short_desc
    sdu = df["short_desc"].str.upper()
    df_on   = df[sdu.str.contains("ON FARM",  na=False)]
    df_off  = df[sdu.str.contains("OFF FARM", na=False)]
    df_tot  = df[~sdu.str.contains("ON FARM|OFF FARM", na=False)]

    on_map  = df_on.groupby("State")["Value_num"].sum().to_dict()
    off_map = df_off.groupby("State")["Value_num"].sum().to_dict()
    tot_map = df_tot.groupby("State")["Value_num"].sum().to_dict()

    states  = set(tot_map) | set(on_map) | set(off_map)
    rows    = []
    for st in states:
        tot = tot_map.get(st, 0)
        on  = on_map.get(st, 0)
        off = off_map.get(st, 0)
        rows.append({
            "State":      st,
            "Total":      tot,
            "OnFarm":     on,
            "OffFarm":    off,
            "PctOnFarm":  (on  / tot * 100) if tot > 0 else None,
            "PctOffFarm": (off / tot * 100) if tot > 0 else None,
        })
    return pd.DataFrame(rows).sort_values("State").reset_index(drop=True)


# ── ASD district boundary helpers ────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_boundary_fips_map(crop: str, state_fips: str,
                           cache_ver: str, _geo: dict) -> dict:
    """Walk back from 2023 to build a complete fips→(District, DistrictCode) map.
    Uses historical years so the district boundaries are full regardless of
    how many counties have reported in the currently-selected year.
    Returns { fips_5: (district_name, district_code) }
    """
    all_geo_fips = {
        f["properties"]["STATE"] + f["properties"]["COUNTY"]
        for f in _geo["features"]
        if f["properties"]["STATE"] == state_fips
    }
    inv_fips = {v: k for k, v in STATE_FIPS_ALL.items()}
    state_alpha = inv_fips.get(state_fips, "")

    fips_map: dict = {}
    for yr in range(2023, 2016, -1):
        if len(fips_map) >= len(all_geo_fips):
            break
        df = load_nass_county(crop, yr, cache_ver)
        if df.empty or "State" not in df.columns:
            continue
        df_s = df[df["State"] == state_alpha]
        if df_s.empty or "asd_desc" not in df_s.columns:
            continue
        for _, row in df_s.iterrows():
            fips = row.get("fips", "")
            dist = str(row.get("asd_desc", "")).strip().title()
            code = str(row.get("asd_code", "")).strip()
            if fips and dist and dist.lower() not in ("", "nan") and fips not in fips_map:
                fips_map[fips] = (dist, code)
    return fips_map


@st.cache_data(show_spinner=False)
def build_nass_district_gdf(state_fips: str, cache_ver: str,
                            _fips_map: dict, _geo: dict) -> gpd.GeoDataFrame:
    """Dissolve county polygons → district polygons using the static fips_map."""
    rows, geoms = [], []
    for feat in _geo["features"]:
        if feat["properties"]["STATE"] != state_fips:
            continue
        fips  = feat["properties"]["STATE"] + feat["properties"]["COUNTY"]
        entry = _fips_map.get(fips)
        if entry is None:
            continue
        dist_name, dist_code = entry
        try:
            geoms.append(shape(feat["geometry"]))
            rows.append({"District": dist_name, "DistrictCode": dist_code})
        except Exception:
            continue

    if not rows:
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")
    dissolved = (
        gdf.dissolve(by=["District", "DistrictCode"])
        .reset_index()[["District", "DistrictCode", "geometry"]]
    )
    dissolved["centroid_lon"] = dissolved.geometry.centroid.x
    dissolved["centroid_lat"] = dissolved.geometry.centroid.y
    return dissolved.sort_values("DistrictCode").reset_index(drop=True)


def get_nass_district_view_data(crop: str, year: int, metric: str,
                                 change_view: str, fips_map: dict,
                                 state: str, comp_year=None,
                                 _geo=None) -> pd.DataFrame:
    """
    Returns [District, Value] aggregated from county-level NASS data,
    computing proper district-level % change for non-absolute views.
    For Current Year, uses Tier-1 estimated county data for all metrics so
    district totals reconcile to the official state total.
    """
    stat_type = _METRIC_TO_STAT[metric]
    _estimable = {"production", "planted", "harvested", "yield"}
    # Use estimation for ALL views so comparison years also use completed
    # county data — without this, prior-year district totals are understated
    # by the fraction of unreported counties (e.g. 2024 Iowa at 75% coverage
    # shows ~302M bu instead of ~394M bu, inflating % change by ~30%).
    use_est    = stat_type in _estimable

    def _load_state(yr, use_estimation=False):
        if use_estimation:
            df = get_completed_county_data(crop, state, yr, stat_type, _CACHE_VERSION)
            if df.empty:
                return pd.DataFrame(columns=["fips", "Value", "District"])
        else:
            df = _load_for_metric(crop, yr, stat_type)
            if df.empty or "State" not in df.columns:
                return pd.DataFrame(columns=["fips", "Value", "District"])
            df = df[df["State"] == state].copy()
        df["District"] = df["fips"].map(lambda f: fips_map.get(f, (None, None))[0])
        return df.dropna(subset=["District"])

    def _agg(df):
        if "District" not in df.columns or df.empty:
            return pd.Series(dtype=float)
        if metric in ("Yield (bu/ac)", "% Harvested"):
            return df.groupby("District")["Value"].mean()
        return df.groupby("District")["Value"].sum()

    cur = _agg(_load_state(year, use_estimation=use_est))

    if change_view == "Current Year" or cur.empty:
        return cur.reset_index()

    def _pct(cur_s, base_s):
        return ((cur_s - base_s) / base_s.replace(0, np.nan) * 100).dropna()

    if change_view == "vs Prior Year":
        base = _agg(_load_state(year - 1))
    elif change_view == "vs Selected Year" and comp_year:
        base = _agg(_load_state(comp_year))
    else:  # vs 3-Yr Avg — average each prior year's DISTRICT totals
        prior = [y for y in [year-1, year-2, year-3] if y >= 2015]
        frames = [_agg(_load_state(y)) for y in prior]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return cur.reset_index()
        base = pd.concat(frames, axis=1).mean(axis=1)

    result = _pct(cur, base)
    return result.reset_index().rename(columns={0: "Value"})


def build_nass_district_fig(dist_view_df: pd.DataFrame,
                             dist_raw_df,          # absolute metric values for labels
                             dist_gdf: gpd.GeoDataFrame,
                             state: str, crop: str, year: int,
                             metric: str, change_view: str,
                             logo_50yr, geo=None,
                             estimated_districts: set = None) -> go.Figure:
    """Build a state choropleth coloured by ASD district with county outlines,
    bold district boundaries, and labels showing metric value + % change."""
    if dist_gdf.empty or dist_view_df.empty:
        return None

    cfg        = _nass_view_cfg(metric, change_view)
    view_label = metric if change_view == "Current Year" else f"{change_view} — {metric}"
    state_name = ABBR_TO_NAME.get(state, state)

    dist_val_map = dict(zip(dist_view_df["District"], dist_view_df["Value"]))
    # Raw (absolute) values for the metric label — always show regardless of view
    raw_map = (
        dict(zip(dist_raw_df["District"], dist_raw_df["Value"]))
        if dist_raw_df is not None and not dist_raw_df.empty else {}
    )
    # Always use the absolute-metric config for formatting raw values so we
    # never accidentally apply the % change formatter to a bushel/acre figure
    abs_cfg    = _nass_view_cfg(metric, "Current Year")
    state_fips = STATE_FIPS_ALL.get(state)

    # Convert dissolved GeoDataFrame to GeoJSON for Plotly
    dist_geojson = json.loads(dist_gdf.to_json())

    districts = dist_gdf["District"].tolist()
    z_vals    = [dist_val_map.get(d, 0) for d in districts]

    # Hover: always show raw metric value + % change in comparison modes
    def _hover(d, z):
        rv   = raw_map.get(d)
        rv_s = abs_cfg["label_fn"](rv) if rv is not None else ""
        if change_view != "Current Year" and z:
            return f"<b>{d}</b><br>{rv_s}<br>{'+'if z>=0 else ''}{z:.1f}%"
        return f"<b>{d}</b><br>{rv_s}"

    hover_texts = [_hover(d, z) for d, z in zip(districts, z_vals)]

    _z_pos = [v for v in z_vals if v > 0]
    if cfg["diverging"]:
        _abs = max((abs(v) for v in z_vals), default=1.0)
        z_min, z_max = -max(_abs, 1.0), max(_abs, 1.0)
    else:
        z_min = 0
        z_max = max(_z_pos) if _z_pos else 1

    fig = go.Figure()

    # Layer 1 — district fill (coloured polygons)
    fig.add_trace(go.Choropleth(
        geojson=dist_geojson,
        featureidkey="properties.District",
        locations=districts, z=z_vals,
        colorscale=cfg["cscale"], zmin=z_min, zmax=z_max,
        colorbar=dict(
            title=dict(text=cfg["clabel"], font=dict(color=TEXT)),
            tickfont=dict(color=TEXT),
        ),
        marker=dict(line=dict(color=BORDER, width=0.3)),
        text=hover_texts,
        hovertemplate="%{text}<extra></extra>",
    ))

    # Layer 2 — county outlines (transparent fill, faint grid lines within districts)
    if geo is not None and state_fips:
        _county_feats = [f for f in geo["features"]
                         if f["properties"]["STATE"] == state_fips]
        _county_fips  = [f["properties"]["STATE"] + f["properties"]["COUNTY"]
                         for f in _county_feats]
        _county_geo   = {"type": "FeatureCollection", "features": _county_feats}
        fig.add_trace(go.Choropleth(
            geojson=_county_geo, featureidkey="id",
            locations=_county_fips, z=[0] * len(_county_fips),
            colorscale=[[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
            showscale=False,
            marker=dict(line=dict(color="rgba(90,90,90,0.55)", width=0.5)),
            hoverinfo="skip",
        ))

    # Layer 3 — district boundary lines (bold white, drawn over county grid)
    all_lons, all_lats   = [], []
    lbl_lons, lbl_lats, lbl_texts = [], [], []
    for _, row in dist_gdf.iterrows():
        geom  = row.geometry
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        for poly in polys:
            xs, ys = poly.exterior.coords.xy
            all_lons.extend(list(xs) + [None])
            all_lats.extend(list(ys) + [None])

        _dn  = row["District"]
        _dv  = dist_val_map.get(_dn)
        _rv  = raw_map.get(_dn)
        _rv_s = abs_cfg["label_fn"](_rv) if _rv is not None else ""

        # Label line 1: district name
        # Label line 2: metric value (always)
        # Add "Est" marker to districts with estimated counties
        _est_flag = (
            "<br>Est" if (estimated_districts and _dn in estimated_districts) else ""
        )
        if change_view != "Current Year" and _dv is not None:
            _sign = "+" if _dv >= 0 else ""
            _lbl  = f"{_dn.upper()}{_est_flag}<br>{_sign}{_dv:.1f}%"
        else:
            _lbl  = f"{_dn.upper()}{_est_flag}<br>{_rv_s}"

        lbl_lons.append(row["centroid_lon"])
        lbl_lats.append(row["centroid_lat"])
        lbl_texts.append(_lbl)

    fig.add_trace(go.Scattergeo(
        lon=all_lons, lat=all_lats, mode="lines",
        line=dict(color="white", width=1.8),
        showlegend=False, hoverinfo="skip",
    ))
    # Layer 4 — district name + value labels
    fig.add_trace(go.Scattergeo(
        lon=lbl_lons, lat=lbl_lats, mode="text",
        text=lbl_texts,
        textfont=dict(color="black", size=9, family="Arial Black"),
        showlegend=False, hoverinfo="skip",
    ))

    fig.update_geos(fitbounds="locations", visible=False,
                    bgcolor=DARK, landcolor=LAND, showframe=False)
    _layout = _base_layout(
        f"NASS {year} {crop} — {view_label} | {state_name} AG Districts"
    )
    _layout.update(
        height=_state_map_height(STATE_FIPS_ALL.get(state, ""), geo) if geo else 620,
        geo=dict(showlakes=False),
    )
    fig.update_layout(**_layout)
    _add_logo(fig, logo_50yr)
    return fig


@st.cache_data(show_spinner=False)
def cached_nass_district_fig(state: str, crop: str, year: int,
                              metric: str, change_view: str,
                              comp_year: int, cache_ver: str,
                              _geo, _logo_50yr, _fips_map):
    # View data (absolute or % change depending on change_view)
    # Pass _geo so production "Current Year" can use Tier-2 estimated counties
    dist_view_df = get_nass_district_view_data(
        crop, year, metric, change_view, _fips_map, state,
        comp_year if comp_year > 0 else None,
        _geo=_geo,
    )
    # Raw absolute values for labels — always "Current Year" absolute
    dist_raw_df = get_nass_district_view_data(
        crop, year, metric, "Current Year", _fips_map, state, None,
        _geo=_geo,
    ) if change_view != "Current Year" else dist_view_df

    dist_gdf = build_nass_district_gdf(
        STATE_FIPS_ALL.get(state, ""), cache_ver, _fips_map, _geo
    )
    # Build set of districts that contain at least one estimated county
    if metric == "Production (bu)":
        _comp = get_completed_county_production(crop, state, year, cache_ver)
        estimated_districts = (
            set(_comp[_comp["is_estimated"]]["District"].dropna().unique())
            if not _comp.empty else set()
        )
    else:
        estimated_districts = set()

    return build_nass_district_fig(
        dist_view_df, dist_raw_df, dist_gdf,
        state, crop, year, metric, change_view, _logo_50yr, _geo,
        estimated_districts=estimated_districts,
    )


# ── GeoJSON & lookups ─────────────────────────────────────────────────────────
@st.cache_data
def load_geojson():
    url = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
    with urllib.request.urlopen(url) as r:
        return json.load(r)


@st.cache_data
def get_state_geojson(_geo, sfips: str) -> dict:
    """Return a cached FeatureCollection filtered to one state.
    Returns the original feature objects unmodified so that every field
    (including the top-level 'id' that Plotly uses to match choropleth
    locations) is preserved exactly as it is in the source GeoJSON.
    """
    return {
        "type": "FeatureCollection",
        "features": [f for f in _geo["features"]
                     if f["properties"]["STATE"] == sfips],
    }


@st.cache_data
def build_fips_lookup(_geo):
    inv = {v: k for k, v in STATE_FIPS_ALL.items()}  # all 50 states — needed for NASS
    lk  = {}
    for feat in _geo["features"]:
        p = feat["properties"]
        if p["STATE"] not in inv:
            continue
        abbr  = inv[p["STATE"]]
        name  = p["NAME"]
        fips5 = p["STATE"] + p["COUNTY"]
        lk[(abbr, name.lower())] = fips5
        for suf in [" county", " parish", " borough", " city", " census area"]:
            if name.lower().endswith(suf):
                lk[(abbr, name.lower()[: -len(suf)])] = fips5
    return lk


def resolve_fips(state, county, lk):
    c = county.lower()
    return (
        lk.get((state, c))
        or lk.get((state, c + " county"))
        or lk.get((state, c.replace("st.", "saint").replace(".", "").strip()))
        or lk.get((state, c.replace(" ", "")))
    )


def _poly_centroid_area(coords):
    x, y = coords[:, 0], coords[:, 1]
    a    = x[:-1] * y[1:] - x[1:] * y[:-1]
    A    = 0.5 * a.sum()
    area = abs(A)
    if area < 1e-10:
        return float(x.mean()), float(y.mean()), area
    cx = float(((x[:-1] + x[1:]) * a).sum() / (6 * A))
    cy = float(((y[:-1] + y[1:]) * a).sum() / (6 * A))
    return cx, cy, area


@st.cache_data
def build_centroid_lookup(_geo):
    centroids = {}
    for feat in _geo["features"]:
        fips5 = feat["properties"]["STATE"] + feat["properties"]["COUNTY"]
        geom  = feat["geometry"]
        try:
            if geom["type"] == "Polygon":
                coords = np.array(geom["coordinates"][0])
                centroids[fips5] = _poly_centroid_area(coords)
            elif geom["type"] == "MultiPolygon":
                best, best_area = None, 0
                for part in geom["coordinates"]:
                    c = np.array(part[0])
                    a = abs(0.5 * (c[:-1, 0] * c[1:, 1] - c[1:, 0] * c[:-1, 1]).sum())
                    if a > best_area:
                        best, best_area = c, a
                if best is not None:
                    centroids[fips5] = _poly_centroid_area(best)
        except Exception:
            pass
    return centroids


# ── Formatting helpers ────────────────────────────────────────────────────────
def format_label(val, metric):
    if pd.isna(val) or val == 0:
        return ""
    if metric == "Yield":
        return f"{val:.0f}"
    if metric == "Production":
        m = val / 1_000_000
        return f"{m:.2f}" if m >= 0.005 else f"{val / 100_000:.2f}"
    return f"{val / 100_000:.2f}"


def format_state_label(val, metric):
    if pd.isna(val) or val == 0:
        return ""
    if metric == "Yield":
        return f"{val:.0f}"
    if metric == "Production":
        return f"{val / 1_000_000:.1f}"
    return f"{val / 100_000:.1f}"


def format_nass_label(val):
    if pd.isna(val) or val == 0:
        return ""
    return f"{val / 1_000_000:.1f}"


def format_nass_chg_label(val):
    """Format a % change value for map labels, e.g. '+12.3%'."""
    if pd.isna(val) or abs(val) < 0.05:
        return ""
    return f"{val:+.1f}%"


def format_nass_acres_label(val):
    if pd.isna(val) or val < 500:
        return ""
    return f"{val / 1_000:,.0f}K"


def format_nass_yield_label(val):
    if pd.isna(val) or val == 0:
        return ""
    return f"{val:.0f}"


# ── NASS view helpers ─────────────────────────────────────────────────────────
def _nass_view_cfg(metric: str, change_view: str) -> dict:
    """Return render-config dict given a metric and change_view."""
    if change_view != "Current Year":
        return {
            "cscale": "RdYlGn", "diverging": True, "clabel": "Change (%)",
            "hover_fmt": ":+.1f", "hover_sfx": "%", "label_unit": "% chg",
            "label_fn": format_nass_chg_label,
            "rank_unit": "%", "rank_div": 1, "rank_fmt": "+.1f",
        }
    _abs_cfgs = {
        "Production (bu)": {
            "cscale": "YlOrBr", "diverging": False, "clabel": "Production<br>(bu)",
            "hover_fmt": ":,.0f", "hover_sfx": " bu", "label_unit": "M bu",
            "label_fn": format_nass_label,
            "rank_unit": "M bu", "rank_div": 1_000_000, "rank_fmt": ",.2f",
        },
        "Planted Acres": {
            "cscale": "YlGn", "diverging": False, "clabel": "Planted<br>Acres",
            "hover_fmt": ":,.0f", "hover_sfx": " ac", "label_unit": "K ac",
            "label_fn": format_nass_acres_label,
            "rank_unit": "K ac", "rank_div": 1_000, "rank_fmt": ",.1f",
        },
        "Harvested Acres": {
            "cscale": "BuGn", "diverging": False, "clabel": "Harvested<br>Acres",
            "hover_fmt": ":,.0f", "hover_sfx": " ac", "label_unit": "K ac",
            "label_fn": format_nass_acres_label,
            "rank_unit": "K ac", "rank_div": 1_000, "rank_fmt": ",.1f",
        },
        "Yield (bu/ac)": {
            "cscale": "RdYlGn", "diverging": False, "clabel": "Yield<br>(bu/ac)",
            "hover_fmt": ":.1f", "hover_sfx": " bu/ac", "label_unit": "bu/ac",
            "label_fn": format_nass_yield_label,
            "rank_unit": "bu/ac", "rank_div": 1, "rank_fmt": ".1f",
        },
        "% Harvested": {
            "cscale": "RdYlGn", "diverging": False, "clabel": "% Harvested",
            "hover_fmt": ":.1f", "hover_sfx": "%", "label_unit": "%",
            "label_fn": format_nass_yield_label,
            "rank_unit": "%", "rank_div": 1, "rank_fmt": ".1f",
        },
        "Prevent Plant Acres": {
            "cscale": "OrRd", "diverging": False, "clabel": "Prevent<br>Plant Acres",
            "hover_fmt": ":,.0f", "hover_sfx": " ac", "label_unit": "K ac",
            "label_fn": format_nass_acres_label,
            "rank_unit": "K ac", "rank_div": 1_000, "rank_fmt": ",.1f",
        },
    }
    return _abs_cfgs[metric]


@st.cache_data(show_spinner=False)
def load_nass_state_pct_harvested(crop: str, year: int,
                                   cache_ver: str) -> pd.DataFrame:
    """State-level % harvested = harvested / planted × 100. Returns [State, Value]."""
    planted   = load_nass_state(crop, year, "planted",   cache_ver)
    harvested = load_nass_state(crop, year, "harvested", cache_ver)
    if planted.empty or harvested.empty:
        return pd.DataFrame(columns=["State", "Value"])
    merged = planted.merge(
        harvested.rename(columns={"Value": "Harv"}), on="State", how="inner"
    )
    merged["Value"] = (merged["Harv"] / merged["Value"].replace(0, np.nan) * 100).round(2)
    return merged[["State", "Value"]].dropna(subset=["Value"])


def _load_state_for_stat(crop: str, year: int, stat_type: str,
                          cache_ver: str) -> pd.DataFrame:
    """Wrapper that handles pct_harvested on top of load_nass_state."""
    if stat_type == "pct_harvested":
        return load_nass_state_pct_harvested(crop, year, cache_ver)
    return load_nass_state(crop, year, stat_type, cache_ver)


# ── ASD Forecast Pipeline (current year before NASS publishes yield/production) ─
FORECAST_YEAR = 2026


@st.cache_data(show_spinner=False)
def _load_nass_state_forecast(crop: str, stat_type: str, cache_ver: str) -> pd.DataFrame:
    """State-level planted/harvested for FORECAST_YEAR — accepts any reference period."""
    params = {
        "key": NASS_API_KEY, "source_desc": "SURVEY", "sector_desc": "CROPS",
        "agg_level_desc": "STATE", "domain_desc": "TOTAL",
        "year": str(FORECAST_YEAR), "format": "JSON",
    }
    params.update(NASS_STAT_BASE[stat_type])
    params.update(NASS_CROP_STAT_PARAMS[crop][stat_type])
    try:
        with urllib.request.urlopen(
            NASS_BASE_URL + "?" + urllib.parse.urlencode(params), timeout=45
        ) as r:
            records = json.load(r).get("data", [])
    except Exception:
        return pd.DataFrame(columns=["State", "Value"])
    if not records:
        return pd.DataFrame(columns=["State", "Value"])
    df = pd.DataFrame(records)
    df["Value"] = pd.to_numeric(
        df["Value"].str.replace(",", "", regex=False).str.strip(), errors="coerce"
    )
    df["State"] = df["state_alpha"].str.strip()
    df = df[df["State"].isin(set(STATE_FIPS_ALL.keys()))].dropna(subset=["Value"])
    _pref = {"YEAR - JUN ACREAGE": 0, "YEAR": 1, "YEAR - MAR ACREAGE": 2}
    if "reference_period_desc" in df.columns:
        df["_p"] = df["reference_period_desc"].map(lambda x: _pref.get(x, 99))
        df = df.sort_values("_p").drop_duplicates(subset=["State"], keep="first")
    else:
        df = df.loc[df.groupby("State")["Value"].idxmax()]
    return df[["State", "Value"]].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _build_asd_hist_shares(crop: str, state: str, stat_type: str,
                            history_years: tuple, cache_ver: str) -> dict:
    """Olympic-average ASD share of state total per district. Returns {district: share}."""
    yearly: dict = {}
    for yr in history_years:
        cdf = get_completed_county_data(crop, state, yr, stat_type, cache_ver)
        if cdf.empty or "District" not in cdf.columns:
            continue
        sdf = _load_state_for_stat(crop, yr, stat_type, cache_ver)
        if sdf.empty or state not in sdf["State"].values:
            continue
        srow = sdf[sdf["State"] == state]
        st_val = float(srow["Value"].mean() if stat_type in ("yield", "pct_harvested")
                       else srow["Value"].sum())
        if st_val <= 0:
            continue
        dist_vals = (cdf.groupby("District")["Value"].mean()
                     if stat_type in ("yield", "pct_harvested")
                     else cdf.groupby("District")["Value"].sum())
        for dist, dval in dist_vals.items():
            yearly.setdefault(dist, []).append(float(dval) / st_val)
    if not yearly:
        return {}
    def _olympic(vals):
        v = sorted(vals)
        return float(np.mean(v[1:-1] if len(v) >= 4 else v))
    return {d: _olympic(v) for d, v in yearly.items() if v}


@st.cache_data(show_spinner=False)
def _get_default_yield_est(crop: str, state: str, cache_ver: str) -> float:
    """Prior-year NASS state yield as the default forecast estimate."""
    for yr in [FORECAST_YEAR - 1, FORECAST_YEAR - 2]:
        sdf = _load_state_for_stat(crop, yr, "yield", cache_ver)
        if not sdf.empty and state in sdf["State"].values:
            return round(float(sdf[sdf["State"] == state]["Value"].iloc[0]), 1)
    return 0.0


@st.cache_data(show_spinner=False)
def get_asd_forecast_data(crop: str, state: str, yield_est: float,
                           cache_ver: str) -> tuple:
    """
    Build FORECAST_YEAR ASD-level estimates for all metrics.
    Uses NASS state planted/harvested (already published) and a manual yield input.
    Returns (stats_dict, state_totals_dict).
      stats_dict      = {stat_type: {district: value}}
      state_totals    = {stat_type: state_total}
    """
    _hist = tuple(range(FORECAST_YEAR - 5, FORECAST_YEAR))

    # State anchors: NASS has 2026 planted + harvested
    p_df  = _load_nass_state_forecast(crop, "planted",   cache_ver)
    h_df  = _load_nass_state_forecast(crop, "harvested", cache_ver)
    st_pl = float(p_df[p_df["State"] == state]["Value"].iloc[0]) if state in p_df["State"].values else 0.0
    st_hv = float(h_df[h_df["State"] == state]["Value"].iloc[0]) if state in h_df["State"].values else 0.0
    st_pr = yield_est * st_hv if (yield_est and st_hv) else 0.0

    # Historical ASD shares
    sh_pl = _build_asd_hist_shares(crop, state, "planted",    _hist, cache_ver)
    sh_hv = _build_asd_hist_shares(crop, state, "harvested",  _hist, cache_ver)
    sh_pr = _build_asd_hist_shares(crop, state, "production", _hist, cache_ver)

    stats: dict = {k: {} for k in ("planted", "harvested", "pct_harvested",
                                    "production", "yield")}
    for d in set(sh_pl) | set(sh_hv) | set(sh_pr):
        apl = st_pl * sh_pl[d] if (d in sh_pl and st_pl) else None
        ahv = st_hv * sh_hv[d] if (d in sh_hv and st_hv) else None
        apr = st_pr * sh_pr[d] if (d in sh_pr and st_pr) else None
        stats["planted"][d]    = apl
        stats["harvested"][d]  = ahv
        stats["production"][d] = apr
        if apl and ahv and apl > 0:
            stats["pct_harvested"][d] = ahv / apl * 100
        if ahv and apr and ahv > 0:
            stats["yield"][d] = apr / ahv

    state_totals = {
        "planted":       st_pl,
        "harvested":     st_hv,
        "production":    st_pr,
        "yield":         yield_est if yield_est else 0.0,
        "pct_harvested": st_hv / st_pl * 100 if st_pl else 0.0,
    }
    return stats, state_totals


def _nass_has_official(crop: str, state: str, year: int, stat_type: str,
                        cache_ver: str) -> bool:
    """True if NASS has published official state data for this crop/year/stat."""
    df = _load_state_for_stat(crop, year, stat_type, cache_ver)
    return not df.empty and state in df["State"].values


@st.cache_data(show_spinner=False)
def load_acreage_crop_hist(
    crop_key: str,
    crop_params: tuple,          # tuple of (k,v) pairs — hashable
    start_yr: int, end_yr: int,
    state_abbr: str,             # "" = national
    prevent_plant: bool,
    cache_ver: str,
) -> dict:
    """
    Fetch planted (or prevent-plant) acres from NASS for a crop across a year range.
    Returns {calendar_year: million_acres}.
    """
    params = {
        "key": NASS_API_KEY,
        "source_desc": "SURVEY",
        "sector_desc": "CROPS",
        "agg_level_desc": "STATE" if state_abbr else "NATIONAL",
        "domain_desc": "TOTAL",
        "statisticcat_desc": "AREA PLANTED",
        "unit_desc": "ACRES",
        "year__GE": str(start_yr),
        "year__LE": str(end_yr),
        "format": "JSON",
    }
    for k, v in crop_params:
        params[k] = v
    if state_abbr:
        params["state_alpha"] = state_abbr
    try:
        with urllib.request.urlopen(
            NASS_BASE_URL + "?" + urllib.parse.urlencode(params), timeout=60
        ) as r:
            records = json.load(r).get("data", [])
    except Exception:
        return {}
    if not records:
        return {}
    df = pd.DataFrame(records)
    # Filter prevent-plant or regular planted
    if "short_desc" in df.columns:
        if prevent_plant:
            df = df[df["short_desc"].str.upper().str.contains("PREVENT", na=False)]
        else:
            df = df[~df["short_desc"].str.upper().str.contains("PREVENT", na=False)]
    if df.empty:
        return {}
    df["val"] = pd.to_numeric(
        df["Value"].str.replace(",","",regex=False), errors="coerce"
    )
    df["yr"] = pd.to_numeric(df["year"], errors="coerce")
    # Keep only "YEAR" reference period when column present
    if "reference_period_desc" in df.columns:
        yr_rows = df[df["reference_period_desc"] == "YEAR"]
        if not yr_rows.empty:
            df = yr_rows
    # Keep ALL PRODUCTION PRACTICES when available, else use all
    if "prodn_practice_desc" in df.columns:
        all_p = df[df["prodn_practice_desc"] == "ALL PRODUCTION PRACTICES"]
        if not all_p.empty:
            df = all_p
    df = df.dropna(subset=["yr","val"])
    out: dict = {}
    for yr_val, grp in df.groupby("yr"):
        out[int(yr_val)] = float(grp["val"].sum()) / 1_000_000   # → mil ac
    return out


@st.cache_data(show_spinner=False)
def load_livestock(agg_level: str, species_key: str, year: int,
                   state_alpha: str = "",
                   cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Fetch NASS livestock inventory at STATE, AG DISTRICT, or COUNTY level."""
    params, _ = _livestock_base_params(species_key)
    params["agg_level_desc"] = agg_level
    params["year"] = str(year)
    if state_alpha:
        params["state_alpha"] = state_alpha
    try:
        url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=25) as r:
            data = json.loads(r.read())
        rows = data.get("data", [])
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "Value" not in df.columns:
        return pd.DataFrame()
    df["Value"] = pd.to_numeric(
        df["Value"].astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )
    return df.dropna(subset=["Value"])


@st.cache_data(show_spinner=False)
def load_livestock_hist(species_key: str,
                        cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Fetch all-years STATE-level livestock inventory (2000-present).

    Tries with the canonical reference_period_desc first; if NASS returns
    nothing (discontinued surveys, poultry census-only series, etc.) retries
    without the period filter to capture whatever periods exist.
    """
    period = _LIVESTOCK_PERIOD.get(species_key, "JAN 1")
    base_params, _ = _livestock_base_params(species_key)
    base_params["agg_level_desc"] = "STATE"
    base_params["year__GE"] = "2000"

    def _fetch(params: dict) -> pd.DataFrame:
        try:
            url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.loads(r.read())
            rows = data.get("data", [])
        except Exception:
            return pd.DataFrame()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        if "Value" not in df.columns:
            return pd.DataFrame()
        df["Value"] = pd.to_numeric(
            df["Value"].astype(str).str.replace(",", "", regex=False).str.strip(),
            errors="coerce",
        )
        df["year"] = pd.to_numeric(
            df.get("year", pd.Series(dtype=float)), errors="coerce"
        )
        return df.dropna(subset=["Value", "year"])

    # First attempt: with canonical period
    df = _fetch({**base_params, "reference_period_desc": period})
    if not df.empty:
        return df

    # Retry without period constraint (catches annual/discontinued series)
    df = _fetch(base_params)
    if not df.empty:
        # Keep only the most common reference period to avoid double-counting
        if "reference_period_desc" in df.columns:
            top_period = df["reference_period_desc"].value_counts().idxmax()
            df = df[df["reference_period_desc"] == top_period]
        return df

    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_livestock_county_hist(species_key: str, state_alpha: str,
                               cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Fetch all-years county-level livestock data for one state (2000-present)."""
    params, _ = _livestock_base_params(species_key)
    params["agg_level_desc"] = "COUNTY"
    params["state_alpha"] = state_alpha
    params["year__GE"] = "2000"
    try:
        url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        rows = data.get("data", [])
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "Value" not in df.columns:
        return pd.DataFrame()
    df["Value"] = pd.to_numeric(
        df["Value"].astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )
    df["year"] = pd.to_numeric(df.get("year", pd.Series(dtype=float)), errors="coerce")
    return df.dropna(subset=["Value", "year"])


@st.cache_data(show_spinner=False)
def load_aquaculture_nass(species_key: str, year: int,
                          agg_level: str = "STATE",
                          state_alpha: str = "",
                          cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Fetch NASS Census of Aquaculture sales data by state (dollars — weight not available via API)."""
    params: dict = {
        "key":               NASS_API_KEY,
        "source_desc":       "CENSUS",
        "sector_desc":       "ANIMALS & PRODUCTS",
        "group_desc":        "AQUACULTURE",
        "statisticcat_desc": "SALES & DISTRIBUTION",
        "unit_desc":         "$",
        "domain_desc":       "TOTAL",
        "agg_level_desc":    agg_level,
        "year":              str(year),
        "format":            "JSON",
    }
    params.update(_AQUA_SPECIES[species_key])
    if state_alpha:
        params["state_alpha"] = state_alpha
    try:
        url = NASS_BASE_URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=25) as r:
            data = json.loads(r.read())
        rows = data.get("data", [])
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "Value" not in df.columns:
        return pd.DataFrame()
    df["Value"] = pd.to_numeric(
        df["Value"].astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )
    return df.dropna(subset=["Value"])


@st.cache_data(show_spinner=False)
def load_echo_aquaculture(cache_ver: str = _CACHE_VERSION) -> pd.DataFrame:
    """Fetch EPA ECHO NPDES-permitted aquaculture facilities (SIC 0921, 0273)."""
    try:
        with urllib.request.urlopen(_ECHO_AQUA_URL, timeout=30) as r:
            data = json.loads(r.read())
    except Exception:
        return pd.DataFrame()
    facilities: list = []
    if isinstance(data, dict):
        results = data.get("Results", data)
        if isinstance(results, dict):
            for _k in ("Facilities", "FacilityList", "facilities"):
                if _k in results:
                    facilities = results[_k]
                    break
        elif isinstance(results, list):
            facilities = results
    elif isinstance(data, list):
        facilities = data
    if not facilities:
        return pd.DataFrame()
    rows: list = []
    for f in facilities:
        if not isinstance(f, dict):
            continue
        try:
            lat = float(f.get("FacLat") or f.get("FAC_LAT") or 0)
            lon = float(f.get("FacLong") or f.get("FAC_LONG") or 0)
        except (TypeError, ValueError):
            continue
        if not lat or not lon:
            continue
        rows.append({
            "name":   str(f.get("FacName") or f.get("FAC_NAME", "")),
            "state":  str(f.get("FacState") or f.get("FAC_STATE", "")),
            "county": str(f.get("FacCounty") or f.get("FAC_COUNTY", "")),
            "lat":    lat,
            "lon":    lon,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _render_acreage_html(rows: list, years: list,
                          title: str, scope_label: str,
                          unit_mul: float = 1.0,
                          unit_lbl: str = "mil ac") -> str:
    """
    Build PRX-style HTML table.
    rows: list of {col_key: value_or_None} dicts, one per year (oldest→newest).
    years: matching calendar years.
    unit_mul: multiply stored M-ac values before display (1.0=M ac, 1000=K ac).
    Returns full HTML string.
    """
    # ── style constants ──────────────────────────────────────────────────────
    HDR1  = ACCENT          # group header bg
    HDR2  = "#0578c4"       # sub-header bg (slightly darker)
    UNIT  = "#e8f4fd"       # units row bg
    ROW_E = DARK            # even rows
    ROW_O = "#ffffff"       # odd rows
    CUR   = "#dbeafe"       # current-year highlight
    CHG_P = "#dcfce7"       # change row positive bg
    CHG_N = "#fee2e2"       # change row negative bg
    CHG_0 = SURFACE         # change row neutral bg
    FP    = "#166534"       # positive text
    FN    = "#991b1b"       # negative text
    TXT   = TEXT
    MUT   = MUTED
    W     = "white"

    def _fmt(v, decimals=1):
        if v is None or (isinstance(v, float) and (np.isnan(v) or v == 0)):
            return "—"
        return f"{v * unit_mul:,.{decimals}f}"

    # ── column schema ────────────────────────────────────────────────────────
    # (key, display, group, group_span_start)
    cols = [
        # key,              label,          group,       span_of_group
        ("mkt_yr",          "Crop<br>Year",  None,        0),
        ("Wheat",           "All<br>Wheat",  None,        0),
        # ── Feedgrains
        ("Corn",            "Corn",          "Feedgrains", 5),
        ("Sorghum",         "Sorg",          "Feedgrains", 0),
        ("Barley",          "Barley",        "Feedgrains", 0),
        ("Oats",            "Oats",          "Feedgrains", 0),
        ("FG_Total",        "Total",         "Feedgrains", 0),
        # ── Oilseeds
        ("Soybeans",        "Soy",           "Oilseeds",   4),
        ("Sunflowers",      "Sunseed",       "Oilseeds",   0),
        ("Canola",          "Canola",        "Oilseeds",   0),
        ("OS_Total",        "Total",         "Oilseeds",   0),
        # ── stand-alone
        ("CornSoy",         "Corn+Soy",      None,         0),
        ("Cotton",          "Cotton",        None,         0),
        ("Total_Major",     "Total<br>Major",None,         0),
        ("HayOther",        "All Hay<br>& Other", None,   0),
        ("Principal",       "Principal<br>Crops", None,   0),
        ("CRP",             "CRP*",          None,         0),
        ("TotalCRP",        "Total<br>w/CRP",None,        0),
        # ── Prevent Plant
        ("PP_Major",        "Cn/Soy/<br>Wht",  "Prevent Acres", 3),
        ("PP_Other",        "All<br>Other",     "Prevent Acres", 0),
        ("PP_Total",        "Total<br>w/PP",    "Prevent Acres", 0),
    ]

    def _th(text, bg, fg=W, rowspan=1, colspan=1, extra=""):
        rs = f' rowspan="{rowspan}"' if rowspan > 1 else ""
        cs = f' colspan="{colspan}"' if colspan > 1 else ""
        return (f'<th{rs}{cs} style="background:{bg};color:{fg};'
                f'padding:4px 6px;text-align:center;font-size:0.72rem;'
                f'border:1px solid #c8d5e3;white-space:nowrap;{extra}">'
                f'{text}</th>')

    def _td(text, bg=ROW_E, fg=TXT, bold=False, align="right", extra=""):
        b = "font-weight:600;" if bold else ""
        return (f'<td style="background:{bg};color:{fg};{b}'
                f'padding:3px 7px;text-align:{align};font-size:0.72rem;'
                f'border:1px solid #dde8f0;white-space:nowrap;{extra}">'
                f'{text}</td>')

    # ── build header rows ────────────────────────────────────────────────────
    # Row 1: group headers
    h1 = ""
    skip_until = 0
    for i,(key,lbl,grp,span) in enumerate(cols):
        if i < skip_until:
            continue
        if grp is None:
            h1 += _th(lbl, HDR1, rowspan=2)
        else:
            h1 += _th(grp, HDR2, colspan=span)
            skip_until = i + span

    # Row 2: sub-column headers (only for grouped cols)
    h2 = ""
    for key,lbl,grp,span in cols:
        if grp is not None:
            h2 += _th(lbl, HDR2)

    # Units row
    h3 = ""
    for key,lbl,grp,span in cols:
        if key == "mkt_yr":
            h3 += _td("", bg=UNIT, fg=MUT, align="center")
        else:
            h3 += _td(unit_lbl, bg=UNIT, fg=MUT, align="center")

    thead = (f'<thead><tr>{h1}</tr><tr>{h2}</tr>'
             f'<tr>{h3}</tr></thead>')

    # ── build body rows ──────────────────────────────────────────────────────
    tbody = "<tbody>"
    cur_yr = years[-1] if years else None
    for i,(yr, row) in enumerate(zip(years, rows)):
        is_cur = (yr == cur_yr)
        bg = CUR if is_cur else (ROW_O if i % 2 else ROW_E)
        tr = f'<tr style="background:{bg};">'
        for key,lbl,grp,span in cols:
            v = row.get(key)
            if key == "mkt_yr":
                txt = str(v) if v else "—"
                tr += _td(txt, bg=bg, fg=ACCENT if is_cur else TXT,
                          bold=is_cur, align="center")
            else:
                tr += _td(_fmt(v), bg=bg, fg=TXT, bold=is_cur)
        tr += "</tr>"
        tbody += tr

    # ── year-over-year change row ────────────────────────────────────────────
    if len(rows) >= 2:
        prev_row = rows[-2]
        cur_row  = rows[-1]
        tbody += f'<tr style="border-top:2px solid {BORDER};">'
        for key,lbl,grp,span in cols:
            if key == "mkt_yr":
                tbody += _td("Chg vs Prior Yr", bg=SURFACE, fg=MUT,
                             bold=True, align="center")
                continue
            cv = cur_row.get(key)
            pv = prev_row.get(key)
            if cv is None or pv is None:
                tbody += _td("—", bg=SURFACE, fg=MUT)
                continue
            delta = cv - pv
            if abs(delta) < 0.05:
                chg_bg, chg_fg = CHG_0, MUT
            elif delta > 0:
                chg_bg, chg_fg = CHG_P, FP
            else:
                chg_bg, chg_fg = CHG_N, FN
            sign = "+" if delta > 0 else ""
            tbody += _td(f"{sign}{delta * unit_mul:.1f}", bg=chg_bg, fg=chg_fg, bold=True)
        tbody += "</tr>"

    tbody += "</tbody>"

    scope_note = f" — {scope_label}" if scope_label and scope_label != "National" else ""
    html = f"""
<div style="overflow-x:auto;">
<p style="font-size:0.9rem;font-weight:700;color:{TXT};margin:0 0 4px 0;text-align:center;">
  US Major Field Crops Area Planted{scope_note}</p>
<p style="font-size:0.72rem;color:{MUT};margin:0 0 8px 0;text-align:center;">
  {title} &nbsp;|&nbsp; {unit_lbl}</p>
<table style="border-collapse:collapse;width:100%;font-family:sans-serif;">
{thead}{tbody}
</table>
<p style="font-size:0.7rem;color:{MUT};margin:4px 0 0 0;">
  * CRP = Conservation Reserve Program (FSA). Prevent Plant from USDA RMA. Values in {unit_lbl}.</p>
</div>"""
    return html


def _state_pct_change(cur_df: pd.DataFrame, cmp_df: pd.DataFrame) -> pd.DataFrame:
    """Compute state-level % change: (cur - cmp) / cmp * 100.
    Both inputs are [State, Value]. Returns [State, Value] with % change."""
    mc = cur_df.merge(cmp_df.rename(columns={"Value": "Base"}), on="State", how="inner")
    mc["Value"] = (mc["Value"] - mc["Base"]) / mc["Base"].replace(0, np.nan) * 100
    return mc[["State", "Value"]].dropna(subset=["Value"])


def _state_pct_change_avg(cur_df: pd.DataFrame, frames: list) -> pd.DataFrame:
    """Compute state-level % change vs average of a list of prior-year DataFrames."""
    if not frames:
        return pd.DataFrame(columns=["State", "Value"])
    avg = (pd.concat(frames).groupby("State")["Value"].mean()
           .reset_index().rename(columns={"Value": "Base"}))
    mc = cur_df.merge(avg, on="State", how="inner")
    mc["Value"] = (mc["Value"] - mc["Base"]) / mc["Base"].replace(0, np.nan) * 100
    return mc[["State", "Value"]].dropna(subset=["Value"])


def _load_for_metric(crop: str, year: int, stat_type: str) -> pd.DataFrame:
    """Return [State, County, fips, Value] for any stat type.
    Production → load_nass_county; pct_harvested → derived; all others → load_nass_stat.
    """
    if stat_type == "production":
        return load_nass_county(crop, year, _CACHE_VERSION).rename(columns={"Production": "Value"})
    if stat_type == "pct_harvested":
        planted   = load_nass_stat(crop, year, "planted",   _CACHE_VERSION)
        harvested = load_nass_stat(crop, year, "harvested", _CACHE_VERSION)
        if planted.empty or harvested.empty:
            return pd.DataFrame(columns=["State", "County", "fips", "Value"])
        merged = planted.merge(
            harvested[["fips", "Value"]].rename(columns={"Value": "Harv"}),
            on="fips", how="inner",
        )
        merged["Value"] = merged["Harv"] / merged["Value"].replace(0, np.nan) * 100
        return merged[["State", "County", "fips", "Value"]].dropna(subset=["Value"])
    return load_nass_stat(crop, year, stat_type, _CACHE_VERSION)


def get_nass_view_data(crop: str, year: int, metric: str, change_view: str, comp_year=None):
    """
    Load and compute the view metric for any metric + change_view combination.
    Returns (county_df [State, County, Value], state_df [State, Value]).
    Absolute view  → Value = raw stat (bu / acres / bu per ac).
    Change views   → Value = % change vs comparison period.
    """
    stat_type = _METRIC_TO_STAT[metric]
    df_cur    = _load_for_metric(crop, year, stat_type)

    def _agg_c(df):
        if metric in ("Yield (bu/ac)", "% Harvested"):
            return df.groupby(["State", "County"])["Value"].mean().reset_index()
        return df.groupby(["State", "County"])["Value"].sum().reset_index()

    def _agg_s(df):
        if metric in ("Yield (bu/ac)", "% Harvested"):
            return df.groupby("State")["Value"].mean().reset_index()
        return df.groupby("State")["Value"].sum().reset_index()

    if change_view == "Current Year" or df_cur.empty:
        return _agg_c(df_cur), _agg_s(df_cur)

    def _pct(cur_s, cmp_s):
        return (cur_s - cmp_s) / cmp_s.replace(0, np.nan) * 100

    if change_view == "vs Prior Year":
        df_cmp = _load_for_metric(crop, year - 1, stat_type)
    elif change_view == "vs Selected Year":
        df_cmp = _load_for_metric(crop, comp_year, stat_type) if comp_year else df_cur
    else:  # vs 3-Yr Avg
        prior_years = [y for y in [year - 1, year - 2, year - 3] if y >= 2015]
        frames = [_load_for_metric(crop, y, stat_type) for y in prior_years]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=["State", "County", "Value"]), pd.DataFrame(columns=["State", "Value"])
        avg_c = (pd.concat([_agg_c(d) for d in frames])
                 .groupby(["State", "County"])["Value"].mean()
                 .reset_index().rename(columns={"Value": "Base"}))
        cur_c = _agg_c(df_cur)
        mc = cur_c.merge(avg_c, on=["State", "County"], how="inner")
        mc["Value"] = _pct(mc["Value"], mc["Base"])
        avg_s = (pd.concat([_agg_s(d) for d in frames])
                 .groupby("State")["Value"].mean()
                 .reset_index().rename(columns={"Value": "Base"}))
        cur_s = _agg_s(df_cur)
        ms = cur_s.merge(avg_s, on="State", how="inner")
        ms["Value"] = _pct(ms["Value"], ms["Base"])
        return mc[["State", "County", "Value"]].dropna(subset=["Value"]), ms[["State", "Value"]].dropna(subset=["Value"])

    # Prior Year / Selected Year shared path
    cur_c = _agg_c(df_cur)
    cmp_c = _agg_c(df_cmp).rename(columns={"Value": "Base"})
    mc    = cur_c.merge(cmp_c, on=["State", "County"], how="inner")
    mc["Value"] = _pct(mc["Value"], mc["Base"])

    cur_s = _agg_s(df_cur)
    cmp_s = _agg_s(df_cmp).rename(columns={"Value": "Base"})
    ms    = cur_s.merge(cmp_s, on="State", how="inner")
    ms["Value"] = _pct(ms["Value"], ms["Base"])
    return mc[["State", "County", "Value"]].dropna(subset=["Value"]), ms[["State", "Value"]].dropna(subset=["Value"])


# ── Aggregation ───────────────────────────────────────────────────────────────
def filter_practice(df, practice):
    return df if practice == "All" else df[df["PG"] == practice]


def agg_data(df, practice, metric, group_cols):
    col = METRIC_COL[metric]
    df  = filter_practice(df, practice)
    if metric == "Yield":
        prod   = df.groupby(group_cols)["Reported Production"].sum()
        acres  = df.groupby(group_cols)["Reported Production Acres"].sum()
        result = (prod / acres.replace(0, np.nan)).reset_index()
        result.columns = group_cols + [col]
    else:
        result = df.groupby(group_cols)[col].sum().reset_index()
    return result


# ── Logo helpers ──────────────────────────────────────────────────────────────
@st.cache_data
def load_logo(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _add_logo(fig, logo_src, size=0.25, opacity=0.12, x=0.5, y=0.5,
              yanchor="middle", layer="above"):
    fig.add_layout_image(
        source=logo_src, xref="paper", yref="paper",
        x=x, y=y, xanchor="center", yanchor=yanchor,
        sizex=size, sizey=size, sizing="contain",
        opacity=opacity, layer=layer,
    )


def _base_layout(title):
    return dict(
        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Arial"),
        title=dict(text=title, font=dict(size=15, color=ACCENT)),
        margin=dict(l=0, r=0, t=50, b=0),
    )


# ── Shared county-label placement ────────────────────────────────────────────
def _place_labels(fig, fips_list, value_series, centroids, metric_or_fn):
    """Add adaptive Scattergeo text labels to a county fig.
    metric_or_fn: RMA metric string OR callable(val)->str for NASS.
    """
    county_areas = [centroids[f][2] for f in fips_list if f in centroids]
    if county_areas:
        avg_area   = float(np.mean(county_areas))
        label_size = int(np.clip(9 + np.log(max(avg_area, 0.01) / 0.05) * 2.0, 9, 15))
    else:
        avg_area   = 0.1
        label_size = 10

    fmt_fn = (lambda v: format_label(v, metric_or_fn)) if isinstance(metric_or_fn, str) \
             else metric_or_fn

    candidates = []
    for fips, val in zip(fips_list, value_series):
        label = fmt_fn(val)
        if label and fips in centroids:
            cx, cy, area = centroids[fips]
            candidates.append((area, cx, cy, label))
    candidates.sort(reverse=True)

    min_sep = float(np.clip(0.15 + avg_area * 0.8, 0.20, 0.45))
    placed, lons, lats, texts = [], [], [], []
    for area, cx, cy, label in candidates:
        if not any((cx - px) ** 2 + (cy - py) ** 2 < min_sep ** 2 for px, py in placed):
            placed.append((cx, cy))
            lons.append(cx); lats.append(cy); texts.append(label)

    if lons:
        fig.add_trace(go.Scattergeo(
            lon=lons, lat=lats, text=texts, mode="text",
            textfont=dict(color="#4b5563", size=label_size, family="Arial Black"),
            showlegend=False, hoverinfo="skip",
        ))


# ── RMA figure builders ───────────────────────────────────────────────────────
def build_state_fig(agg, metric, crop_label, practice, logo_50yr):
    col       = METRIC_COL[metric]
    unit      = METRIC_UNIT[metric]
    fmt       = METRIC_FMT[metric]
    disp_unit = DISPLAY_UNIT[metric]
    df = agg.copy()
    df["StateName"] = df["State"].map(ABBR_TO_NAME)

    title_text = (
        f"{crop_label} — {metric} | Practice: {practice}"
        f"<br><sup>Map labels in {disp_unit}</sup>"
    )
    fig = px.choropleth(
        df, locations="State", locationmode="USA-states", color=col,
        scope="usa", color_continuous_scale=COLOR_SCALE[metric],
        hover_name="StateName",
        hover_data={col: f":{fmt}", "State": False},
        labels={col: f"{metric} ({unit})"},
    )
    fig.update_layout(
        **_base_layout(title_text), height=520,
        geo=dict(showlakes=False, bgcolor=DARK, landcolor=LAND, showland=True, showframe=False),
        coloraxis_colorbar=dict(
            title=dict(text=f"{metric}<br>({unit})", font=dict(color=TEXT)),
            tickfont=dict(color=TEXT),
        ),
    )
    lons, lats, texts = [], [], []
    for _, row in df.iterrows():
        label = format_state_label(row[col], metric)
        if label and row["State"] in STATE_CENTROIDS:
            lon, lat = STATE_CENTROIDS[row["State"]]
            lons.append(lon); lats.append(lat); texts.append(label)
    if lons:
        fig.add_trace(go.Scattergeo(
            lon=lons, lat=lats, text=texts, mode="text", geo="geo",
            textfont=dict(color="#374151", size=11, family="Arial Black"),
            showlegend=False, hoverinfo="skip",
        ))
    _add_logo(fig, logo_50yr)
    return fig


def build_county_fig(agg, geo, fips_lk, centroids, state, metric, crop_label, practice, logo_50yr):
    col   = METRIC_COL[metric]
    unit  = METRIC_UNIT[metric]
    fmt   = METRIC_FMT[metric]
    sfips = STATE_FIPS.get(state)
    if sfips is None:
        return None

    df = agg.copy()
    df["fips"] = df["County"].apply(lambda c: resolve_fips(state, c, fips_lk))
    df = df.dropna(subset=["fips"])

    state_geo = get_state_geojson(geo, sfips)  # cached
    state_name = ABBR_TO_NAME.get(state, state)
    all_fips   = [f["properties"]["STATE"] + f["properties"]["COUNTY"]
                  for f in state_geo["features"]]

    z_vals = df[col].tolist()
    z_min  = df[col].min() if z_vals else 0
    z_max  = df[col].max() if z_vals else 1
    if z_min == z_max:
        z_min = 0

    county_line = dict(color="#a0b5c8", width=0.8)
    fig = go.Figure()
    fig.add_trace(go.Choropleth(
        geojson=state_geo, featureidkey="id",
        locations=all_fips, z=[0] * len(all_fips),
        colorscale=[[0, PANEL], [1, PANEL]], showscale=False,
        marker=dict(line=county_line), hoverinfo="skip",
    ))
    fig.add_trace(go.Choropleth(
        geojson=state_geo, featureidkey="id",
        locations=df["fips"].tolist(), z=z_vals,
        colorscale=COLOR_SCALE[metric], zmin=z_min, zmax=z_max,
        colorbar=dict(
            title=dict(text=f"{metric}<br>({unit})", font=dict(color=TEXT)),
            tickfont=dict(color=TEXT),
        ),
        marker=dict(line=county_line),
        text=df["County"].tolist(),
        hovertemplate=f"%{{text}}: %{{z:{fmt}}}<extra></extra>",
    ))

    disp_unit  = DISPLAY_UNIT[metric]
    title_text = (
        f"{crop_label} — {metric} | {state_name} Counties | Practice: {practice}"
        f"<br><sup>Map labels in {disp_unit}</sup>"
    )
    fig.update_geos(fitbounds="locations", visible=False,
                    bgcolor=DARK, landcolor=LAND)
    fig.update_layout(**_base_layout(title_text),
                      height=_state_map_height(sfips, geo))
    _add_logo(fig, logo_50yr)
    _place_labels(fig, df["fips"].tolist(), df[col].tolist(), centroids, metric)
    return fig


def build_ranking_chart(agg, metric, state):
    col        = METRIC_COL[metric]
    state_name = ABBR_TO_NAME.get(state, state)
    divisor    = DISPLAY_DIVISOR[metric]
    disp_unit  = DISPLAY_UNIT[metric]

    ranked   = agg.dropna(subset=[col]).sort_values(col, ascending=True)
    raw_avg  = ranked[col].mean()
    x_vals   = ranked[col] / divisor
    avg_disp = raw_avg / divisor

    colors = [ACCENT if v >= raw_avg else "#e05252" for v in ranked[col]]
    fmt    = ".1f" if metric == "Yield" else ",.2f"
    labels = [f"{v:{fmt}}" for v in x_vals]

    fig = go.Figure(go.Bar(
        x=x_vals, y=ranked["County"], orientation="h",
        marker_color=colors, marker_line_width=0,
        text=labels, textposition="outside",
        textfont=dict(color=TEXT, size=8), cliponaxis=False,
        hovertemplate=f"%{{y}}: %{{x:{fmt}}} {disp_unit}<extra></extra>",
    ))
    fig.add_vline(
        x=avg_disp, line_color="#d97706", line_width=1.5, line_dash="dash",
        annotation_text=f"  Avg: {avg_disp:{fmt}} {disp_unit}",
        annotation_position="top left",
        annotation_font=dict(color="#d97706", size=10),
    )
    fig.update_layout(
        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Arial"),
        title=dict(text=f"{state_name} County Rankings — {metric}", font=dict(size=14, color=ACCENT)),
        height=max(380, len(ranked) * 22 + 80),
        margin=dict(l=10, r=90, t=50, b=20), bargap=0.18,
        xaxis=dict(title=f"{metric} ({disp_unit})", gridcolor=BORDER,
                   tickfont=dict(color=MUTED), title_font=dict(color=MUTED), zeroline=False),
        yaxis=dict(gridcolor=BORDER, tickfont=dict(color=TEXT, size=9), automargin=True),
    )
    return fig


# ── NASS figure builders ──────────────────────────────────────────────────────
def build_nass_state_fig(state_vdf, crop, year, metric, change_view, logo_50yr):
    """state_vdf has columns [State, Value] — pre-computed by get_nass_view_data."""
    cfg        = _nass_view_cfg(metric, change_view)
    view_label = metric if change_view == "Current Year" else f"{change_view} — {metric}"
    agg        = state_vdf.copy()
    agg["StateName"] = agg["State"].map(ABBR_TO_NAME)

    title_text = (
        f"NASS {year} {crop} — {view_label}"
        f"<br><sup>Map labels in {cfg['label_unit']}</sup>"
    )

    px_kwargs = {}
    if cfg["diverging"] and not agg["Value"].empty:
        abs_max = max(float(agg["Value"].abs().max()), 1.0)
        px_kwargs["range_color"]               = [-abs_max, abs_max]
        px_kwargs["color_continuous_midpoint"] = 0.0

    fig = px.choropleth(
        agg, locations="State", locationmode="USA-states", color="Value",
        scope="usa", color_continuous_scale=cfg["cscale"],
        hover_name="StateName",
        hover_data={"Value": cfg["hover_fmt"], "State": False},
        labels={"Value": view_label},
        **px_kwargs,
    )
    fig.update_layout(
        **_base_layout(title_text), height=520,
        geo=dict(showlakes=False, bgcolor=DARK, landcolor=LAND, showland=True, showframe=False),
        coloraxis_colorbar=dict(
            title=dict(text=cfg["clabel"], font=dict(color=TEXT)),
            tickfont=dict(color=TEXT),
        ),
    )
    lons, lats, texts = [], [], []
    for _, row in agg.iterrows():
        label = cfg["label_fn"](row["Value"])
        if label and row["State"] in STATE_CENTROIDS:
            lon, lat = STATE_CENTROIDS[row["State"]]
            lons.append(lon); lats.append(lat); texts.append(label)
    if lons:
        fig.add_trace(go.Scattergeo(
            lon=lons, lat=lats, text=texts, mode="text", geo="geo",
            textfont=dict(color="#374151", size=11, family="Arial Black"),
            showlegend=False, hoverinfo="skip",
        ))
    _add_logo(fig, logo_50yr)
    return fig


def _state_map_height(sfips: str, geo: dict,
                       min_h: int = 420, max_h: int = 820) -> int:
    """
    Compute a figure height that fits the state's geographic shape.
    Reads the lat/lon bounding box from the county GeoJSON, corrects
    longitude for latitude, and scales a nominal 900-px-wide container.
    Tall/narrow states (IL, VA) get more height; wide/compact states (IA,
    MN) get less so the state fills the figure rather than being dwarfed.
    """
    import math
    feats = [f for f in geo["features"] if f["properties"]["STATE"] == sfips]
    if not feats:
        return 620
    lats, lons = [], []

    def _coords(obj):
        if isinstance(obj, list):
            if obj and isinstance(obj[0], (int, float)):
                lons.append(obj[0]); lats.append(obj[1])
            else:
                for item in obj:
                    _coords(item)

    for f in feats:
        _coords(f.get("geometry", {}).get("coordinates", []))

    if not lats or not lons:
        return 620

    lat_r = max(lats) - min(lats)
    lon_r = max(lons) - min(lons)
    mean_lat = (max(lats) + min(lats)) / 2
    adj_lon = lon_r * math.cos(math.radians(mean_lat))
    if adj_lon == 0:
        return 620

    aspect = lat_r / adj_lon          # > 1 = tall/narrow; < 1 = wide/flat
    height = int(900 * aspect * 1.05) # 5 % padding
    return max(min_h, min(max_h, height))


def build_nass_county_fig(county_vdf, geo, state, crop, year, metric, change_view, logo_50yr, centroids, fips_lk):
    """county_vdf has columns [State, County, Value] — pre-computed by get_nass_view_data."""
    cfg        = _nass_view_cfg(metric, change_view)
    view_label = metric if change_view == "Current Year" else f"{change_view} — {metric}"
    state_df   = county_vdf[county_vdf["State"] == state].copy()
    if state_df.empty:
        return None

    sfips = STATE_FIPS_ALL.get(state)
    if sfips is None:
        return None

    # Resolve FIPS from county names via GeoJSON lookup — guarantees the codes
    # match the feature "id" that Plotly uses to locate choropleth polygons.
    state_df["fips"] = state_df["County"].apply(lambda c: resolve_fips(state, c, fips_lk))
    state_df = state_df.dropna(subset=["fips"])
    if state_df.empty:
        return None

    state_geo = get_state_geojson(geo, sfips)  # cached
    all_fips  = [f["properties"]["STATE"] + f["properties"]["COUNTY"]
                 for f in state_geo["features"]]

    z_vals = state_df["Value"].tolist()
    if cfg["diverging"]:
        abs_max = max((abs(v) for v in z_vals if not pd.isna(v)), default=1.0)
        abs_max = max(abs_max, 1.0)
        z_min, z_max = -abs_max, abs_max
    else:
        z_min = min(z_vals) if z_vals else 0
        z_max = max(z_vals) if z_vals else 1
        if z_min == z_max:
            z_min = 0

    state_name = ABBR_TO_NAME.get(state, state)
    title_text = (
        f"NASS {year} {crop} — {view_label} | {state_name} Counties"
        f"<br><sup>Map labels in {cfg['label_unit']}</sup>"
    )

    county_line = dict(color="#a0b5c8", width=0.8)
    fig = go.Figure()
    fig.add_trace(go.Choropleth(
        geojson=state_geo, featureidkey="id",
        locations=all_fips, z=[0] * len(all_fips),
        colorscale=[[0, PANEL], [1, PANEL]], showscale=False,
        marker=dict(line=county_line), hoverinfo="skip",
    ))
    fig.add_trace(go.Choropleth(
        geojson=state_geo, featureidkey="id",
        locations=state_df["fips"].tolist(), z=z_vals,
        colorscale=cfg["cscale"], zmin=z_min, zmax=z_max,
        colorbar=dict(
            title=dict(text=cfg["clabel"], font=dict(color=TEXT)),
            tickfont=dict(color=TEXT),
        ),
        marker=dict(line=county_line),
        text=state_df["County"].tolist(),
        hovertemplate=f"%{{text}}: %{{z{cfg['hover_fmt']}}}{cfg['hover_sfx']}<extra></extra>",
    ))
    fig.update_geos(fitbounds="locations", visible=False, bgcolor=DARK, landcolor=LAND)
    fig.update_layout(**_base_layout(title_text),
                      height=_state_map_height(sfips, geo))
    _add_logo(fig, logo_50yr)
    _place_labels(fig, state_df["fips"].tolist(), state_df["Value"].tolist(),
                  centroids, cfg["label_fn"])
    return fig


def build_nass_county_fig_with_est(completed_df: pd.DataFrame, geo, state: str,
                                    crop: str, year: int, metric: str,
                                    logo_50yr, centroids) -> go.Figure:
    """
    County choropleth using completed (reported + Tier-1 estimated) data for
    any metric. Estimated counties get a small 'Est' label and hover suffix.
    """
    if completed_df.empty:
        return None

    sfips = STATE_FIPS_ALL.get(state)
    if not sfips:
        return None

    cfg        = _nass_view_cfg(metric, "Current Year")
    state_name = ABBR_TO_NAME.get(state, state)
    state_geo  = get_state_geojson(geo, sfips)
    all_fips   = [f["properties"]["STATE"] + f["properties"]["COUNTY"]
                  for f in state_geo["features"]]

    fips_val = dict(zip(completed_df["fips"], completed_df["Value"]))
    fips_cty = dict(zip(completed_df["fips"], completed_df["County"]))
    fips_est = dict(zip(completed_df["fips"], completed_df["is_estimated"]))

    z_vals, hover_texts = [], []
    for fips in all_fips:
        val = fips_val.get(fips, 0)
        z_vals.append(val)
        cty = fips_cty.get(fips, fips)
        sfx = " (Est)" if fips_est.get(fips, False) else ""
        hover_texts.append(
            f"{cty}{sfx}: {cfg['label_fn'](val)}{cfg['hover_sfx']}" if val
            else f"{cty}: No data"
        )

    _pos = [v for v in z_vals if v > 0]
    z_min = min(_pos) if _pos else 0
    z_max = max(_pos) if _pos else 1

    county_line = dict(color="#a0b5c8", width=0.8)
    fig = go.Figure()

    # Background (uncoloured county outlines)
    fig.add_trace(go.Choropleth(
        geojson=state_geo, featureidkey="id",
        locations=all_fips, z=[0] * len(all_fips),
        colorscale=[[0, PANEL], [1, PANEL]], showscale=False,
        marker=dict(line=county_line), hoverinfo="skip",
    ))

    # Production values (reported + estimated, same colour scale)
    fig.add_trace(go.Choropleth(
        geojson=state_geo, featureidkey="id",
        locations=all_fips, z=z_vals,
        colorscale=cfg["cscale"], zmin=z_min, zmax=z_max,
        colorbar=dict(
            title=dict(text=cfg["clabel"], font=dict(color=TEXT)),
            tickfont=dict(color=TEXT),
        ),
        marker=dict(line=county_line),
        text=hover_texts,
        hovertemplate="%{text}<extra></extra>",
    ))

    # "Est" labels on estimated county centroids
    est_fips = [f for f in all_fips if fips_est.get(f, False)]
    if est_fips and centroids:
        _elons = [centroids[f][0] for f in est_fips if f in centroids]
        _elats = [centroids[f][1] for f in est_fips if f in centroids]
        if _elons:
            fig.add_trace(go.Scattergeo(
                lon=_elons, lat=_elats, mode="text",
                text=["Est"] * len(_elons),
                textfont=dict(color="#374151", size=7, family="Arial Bold"),
                showlegend=False, hoverinfo="skip",
            ))

    title_text = (
        f"NASS {year} {crop} — {metric} | {state_name} Counties"
        f"<br><sup>Map labels in {cfg['label_unit']}  ·  Est = county value estimated</sup>"
    )
    fig.update_geos(fitbounds="locations", visible=False, bgcolor=DARK, landcolor=LAND)
    fig.update_layout(**_base_layout(title_text),
                      height=_state_map_height(sfips, geo))
    _add_logo(fig, logo_50yr)

    # Production labels for reported counties only (same as existing county fig)
    rep_fips = [f for f in all_fips if f in fips_val and not fips_est.get(f, False)]
    rep_vals = [fips_val[f] for f in rep_fips]
    _place_labels(fig, rep_fips, rep_vals, centroids, cfg["label_fn"])

    return fig


NASS_REGIONAL_GROUPS = {
    "Eastern Corn Belt":       ["IL", "IN", "OH", "MI", "KY"],
    "Central Plains (UP States)": ["IA", "NE", "KS"],
    "Upper Plains (BN States)":   ["MN", "SD", "ND"],
    "Delta":                   ["MS", "AR", "LA", "TN"],
    "SE States":               ["AL", "GA", "FL", "SC", "NC", "VA", "WV"],
    "NE States":               ["PA", "NY", "MD", "VT", "NH", "MA", "NJ", "DE", "ME"],
    "Other":                   ["WI", "MO", "TX", "CO", "MT", "WY", "OK"],
}

def _row_bg_text(rank: int, n: int):
    """Return (bg_hex, text_hex) for a value at given rank in a row of n."""
    if n == 0: return SURFACE, TEXT
    p = rank / max(n - 1, 1)
    if   rank == 0:         return "#fecaca", "#991b1b"
    elif rank == 1:         return "#fee2e2", "#b91c1c"
    elif rank == n - 1:     return "#bbf7d0", "#166534"
    elif rank == n - 2:     return "#dcfce7", "#15803d"
    elif p < 0.35:          return "#fef2f2", TEXT
    elif p > 0.65:          return "#f0fdf4", TEXT
    else:                   return SURFACE, TEXT


def build_heatmap_table(
    state_year_data: dict,   # {state_alpha: {year: float_or_None}}
    years: list,             # sorted list of years to show
    title: str,
    unit: str = "M bu",      # shown in column header
    divisor: float = 1e6,    # raw → display (1 if already display units)
    is_ratio: bool = False,  # True for %, yield — use mean for region totals
    us_totals: dict = None,  # {year: raw_us_total} for % of US column
    total_label: str = "US Total",  # label for the bottom totals row
    regions: dict = None,    # use NASS_REGIONAL_GROUPS if None
    fmt: str = ",.0f",
    top_row: dict = None,           # {year: raw_value} — summary row pinned at top
    top_row_label: str = "",        # label for the top summary row
    cell_status: dict = None,       # {row_label: {year: bool}} True=append " E"
    top_row_cell_status: dict = None,  # {year: bool} for the top summary row
) -> go.Figure:
    """
    Styled Plotly table with:
      • Per-row heat-map (top 2 = green, bottom 2 = red, gradient between)
      • Regional subtotal rows
      • Stats: % vs LY · Olympic Avg · % of Avg · Min · Max · % of US
    """
    _D  = "#f5f7fa"; _P = "#edf1f7"; _S = "#e2e8f2"
    _BR = "#c8d5e3"; _T = "#1a2332"; _M = "#64748b"
    _A  = "#0693e3"; _G = "#d97706"

    def _disp(v):
        if v is None or (isinstance(v, float) and np.isnan(v)): return None
        return v / divisor

    def _fmt_num(v):
        if v is None: return "—"
        return f"{v:{fmt}}"

    def _fmt_pct(v, arrow=True):
        if v is None: return "—"
        sign = "▲" if v >= 0 else "▼"
        return f"{sign} {abs(v):.1f}%" if arrow else f"{v:.1f}%"

    def _olympic(vals):
        c = [v for v in vals if v is not None]
        if len(c) >= 4: c = sorted(c)[1:-1]
        return sum(c)/len(c) if c else None

    def _row_colors(vals):
        """Return lists of (bg, fg) per cell based on within-row rank."""
        valid = [(i, v) for i, v in enumerate(vals) if v is not None]
        n = len(valid)
        rank_of = {idx: rank for rank, (idx, _) in enumerate(sorted(valid, key=lambda x: x[1]))}
        bgs, fgs = [], []
        for i, v in enumerate(vals):
            if i not in rank_of: bgs.append(_S); fgs.append(_M)
            else:
                bg, fg = _row_bg_text(rank_of[i], n)
                bgs.append(bg); fgs.append(fg)
        return bgs, fgs

    # ── Build rows ────────────────────────────────────────────────────────────
    if regions is None:
        regions = NASS_REGIONAL_GROUPS

    # Collect what states we actually have data for
    have_states = set(state_year_data.keys())

    row_labels, row_data, row_bg, row_fg, row_is_region = [], [], [], [], []
    seen = set()

    # ── Optional pinned summary row at top (e.g. district total) ─────────────
    if top_row and top_row_label:
        _tr_vals = [_disp(top_row.get(y)) for y in years]
        row_labels.append(f"▶  {top_row_label}")
        row_data.append(_tr_vals)
        row_bg.append(["#dbeafe"] * len(years))   # pinned row — light blue
        row_fg.append([_G] * len(years))           # amber text — stands out
        row_is_region.append(True)

    for region_name, members in regions.items():
        region_members = [s for s in members if s in have_states]
        if not region_members:
            continue
        # Individual state rows
        for st in region_members:
            seen.add(st)
            vals = [_disp(state_year_data[st].get(y)) for y in years]
            bgs, fgs = _row_colors(vals)
            row_labels.append(st)
            row_data.append(vals)
            row_bg.append(bgs); row_fg.append(fgs)
            row_is_region.append(False)
        # Region subtotal row
        r_vals = []
        for y in years:
            yr_raw = [state_year_data[s].get(y) for s in region_members
                      if state_year_data[s].get(y) is not None]
            if yr_raw:
                r_vals.append((np.mean(yr_raw) if is_ratio else sum(yr_raw)) / divisor)
            else:
                r_vals.append(None)
        row_labels.append(region_name)
        row_data.append(r_vals)
        row_bg.append([_BR] * len(years))
        row_fg.append([_G] * len(years))
        row_is_region.append(True)

    # Remaining states not in any region
    for st in sorted(have_states - seen):
        vals = [_disp(state_year_data[st].get(y)) for y in years]
        bgs, fgs = _row_colors(vals)
        row_labels.append(st)
        row_data.append(vals)
        row_bg.append(bgs); row_fg.append(fgs)
        row_is_region.append(False)

    # US Total row
    if us_totals:
        us_vals = [_disp(us_totals.get(y)) for y in years]
        bgs, fgs = _row_colors(us_vals)
        row_labels.append(total_label)
        row_data.append(us_vals)
        row_bg.append(bgs); row_fg.append(fgs)
        row_is_region.append(True)

    n_rows = len(row_labels)

    # ── Compute stats columns ─────────────────────────────────────────────────
    latest_yr = years[-1]
    prev_yr   = years[-2] if len(years) >= 2 else None

    stats_pct_vs_ly, stats_avg, stats_pct_avg = [], [], []
    stats_min, stats_max, stats_pct_us = [], [], []
    bg_pct_ly, fg_pct_ly = [], []
    bg_pct_avg, fg_pct_avg = [], []
    bg_pct_us, fg_pct_us = [], []

    for i, (label, vals) in enumerate(zip(row_labels, row_data)):
        is_reg = row_is_region[i]
        clean  = [v for v in vals if v is not None]

        # % vs LY
        cur  = vals[years.index(latest_yr)] if latest_yr in years else None
        prev = vals[years.index(prev_yr)]   if prev_yr and prev_yr in years else None
        if cur and prev and prev != 0:
            p = (cur - prev) / abs(prev) * 100
            stats_pct_vs_ly.append(_fmt_pct(p))
            c = "#dcfce7" if p >= 0 else "#fee2e2"
            bg_pct_ly.append(c); fg_pct_ly.append("#166534" if p >= 0 else "#991b1b")
        else:
            stats_pct_vs_ly.append("—")
            bg_pct_ly.append(_S); fg_pct_ly.append(_M)

        # Olympic Avg
        avg = _olympic(clean)
        stats_avg.append(_fmt_num(avg))

        # % of Avg
        if cur and avg and avg != 0:
            pa = (cur - avg) / abs(avg) * 100
            stats_pct_avg.append(_fmt_pct(pa))
            c = "#dcfce7" if pa >= 0 else "#fee2e2"
            bg_pct_avg.append(c); fg_pct_avg.append("#166534" if pa >= 0 else "#991b1b")
        else:
            stats_pct_avg.append("—")
            bg_pct_avg.append(_S); fg_pct_avg.append(_M)

        # Min / Max
        stats_min.append(_fmt_num(min(clean)) if clean else "—")
        stats_max.append(_fmt_num(max(clean)) if clean else "—")

        # % of US
        if not is_ratio and us_totals and cur:
            us_d = _disp(us_totals.get(latest_yr))
            if us_d and us_d > 0:
                pu = cur / us_d * 100
                stats_pct_us.append(f"{pu:.1f}%")
                bg_pct_us.append(_S); fg_pct_us.append(_G)
            else:
                stats_pct_us.append("—"); bg_pct_us.append(_S); fg_pct_us.append(_M)
        else:
            stats_pct_us.append("—"); bg_pct_us.append(_S); fg_pct_us.append(_M)

    # Merge top_row_cell_status into cell_status under the top row's label key
    if top_row_cell_status and top_row_label:
        _full_cs = dict(cell_status or {})
        _full_cs[f"▶  {top_row_label}"] = top_row_cell_status
    else:
        _full_cs = cell_status or {}

    def _cell_str(r, c):
        """Format cell value, appending ' E' if that year is estimated."""
        val_str = _fmt_num(row_data[r][c])
        if _full_cs:
            lbl = row_labels[r]
            yr  = years[c]
            if _full_cs.get(lbl, {}).get(yr, False):
                return f"{val_str} E"
        return val_str

    # ── Build column arrays for Plotly (column-major) ─────────────────────────
    hdr_vals = ["State / Region"] + [str(y) for y in years] + \
               ["% vs LY", f"Olympic Avg\n({unit})", "% of Avg",
                "Min", "Max", "% of U.S."]
    hdr_bg   = [_P] * len(hdr_vals)
    hdr_fg   = [_M] + [_T] * len(years) + [_M] * 6

    # Row label column
    lbl_bg   = [(_BR if row_is_region[i] else _P) for i in range(n_rows)]
    lbl_fg   = [(_G  if row_is_region[i] else _T) for i in range(n_rows)]

    # Year columns — values carry ' E' suffix where estimated
    yr_cell_vals = [[_cell_str(r, c) for r in range(n_rows)]
                    for c in range(len(years))]
    yr_cell_bgs  = [[row_bg[r][c] for r in range(n_rows)] for c in range(len(years))]
    yr_cell_fgs  = [[row_fg[r][c] for r in range(n_rows)] for c in range(len(years))]

    all_vals = ([row_labels]
                + yr_cell_vals
                + [stats_pct_vs_ly, stats_avg, stats_pct_avg,
                   stats_min, stats_max, stats_pct_us])
    all_bgs  = ([lbl_bg]
                + yr_cell_bgs
                + [bg_pct_ly, [_S]*n_rows, bg_pct_avg,
                   [_S]*n_rows, [_S]*n_rows, bg_pct_us])
    all_fgs  = ([lbl_fg]
                + yr_cell_fgs
                + [fg_pct_ly, [_T]*n_rows, fg_pct_avg,
                   [_T]*n_rows, [_A]*n_rows, fg_pct_us])

    col_widths = [130] + [60]*len(years) + [70, 90, 70, 55, 55, 65]

    fig = go.Figure(go.Table(
        columnwidth=col_widths,
        header=dict(
            values=hdr_vals,
            fill_color=hdr_bg,
            font=dict(color=hdr_fg, size=11, family="Arial"),
            align="center", height=28,
            line_color=_BR,
        ),
        cells=dict(
            values=all_vals,
            fill_color=all_bgs,
            font=dict(color=all_fgs, size=11, family="Arial"),
            align=["left"] + ["right"]*len(years) + ["right"]*6,
            height=24,
            line_color=_BR,
        ),
    ))
    fig.update_layout(
        paper_bgcolor=_D,
        margin=dict(l=0, r=0, t=30, b=0),
        height=max(300, n_rows * 26 + 60),
        title=dict(text=title, font=dict(color=_A, size=13)),
    )
    return fig


def build_history_bar(series_dict: dict, years: list, title: str,
                       y_label: str = "", stacked: bool = False) -> go.Figure:
    """
    Build a stacked or grouped bar chart with data labels at column tops.
    series_dict: {series_name: [value_per_year, ...]}  — values in display units
    years: list of year ints (x-axis)
    """
    _SERIES_COLORS = ["#0693e3", "#f59e0b", "#ef4444", "#a78bfa",
                      "#16a34a", "#fb923c", "#e879f9"]
    series_list = list(series_dict.items())
    n_series     = len(series_list)
    # Pre-compute column totals for top labels
    col_totals = [sum(series_list[s][1][c] or 0 for s in range(n_series))
                  for c in range(len(years))]

    fig = go.Figure()
    for i, (name, vals) in enumerate(series_list):
        is_top = (i == n_series - 1)   # last trace = top of stack
        fig.add_trace(go.Bar(
            x=years, y=vals, name=name,
            marker_color=_SERIES_COLORS[i % len(_SERIES_COLORS)],
            marker_line_width=0,
            # Show total label on top of last trace (stacked) or every bar (grouped)
            text=[f"{t:.2f}" for t in (col_totals if stacked and is_top else vals)],
            textposition="outside" if (not stacked or is_top) else "none",
            textfont=dict(color=TEXT, size=9),
            cliponaxis=False,
        ))
    layout = _base_layout(title)
    layout.update(
        height=320,
        barmode="stack" if stacked else "group",
        legend=dict(orientation="h", y=1.08, x=0.5, xanchor="center",
                    font=dict(color=TEXT, size=10)),
        xaxis=dict(tickvals=years, ticktext=[str(y) for y in years],
                   gridcolor=BORDER, tickfont=dict(color=MUTED)),
        yaxis=dict(title=y_label, gridcolor=BORDER,
                   tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
        margin=dict(l=60, r=20, t=50, b=30),
    )
    fig.update_layout(**layout)
    return fig


def build_nass_ranking_chart(ranked_df, state, crop, year, metric, change_view):
    """ranked_df: DataFrame with [County, Value] pre-filtered to a single state."""
    cfg        = _nass_view_cfg(metric, change_view)
    view_label = metric if change_view == "Current Year" else f"{change_view} — {metric}"
    state_name = ABBR_TO_NAME.get(state, state)
    ranked     = ranked_df.dropna(subset=["Value"]).sort_values("Value", ascending=True)
    if ranked.empty:
        return go.Figure()

    raw_avg  = ranked["Value"].mean()
    x_vals   = ranked["Value"] / cfg["rank_div"]
    avg_disp = raw_avg / cfg["rank_div"]
    fmt      = cfg["rank_fmt"]

    colors = [ACCENT if v >= raw_avg else "#e05252" for v in ranked["Value"]]
    labels = [f"{v:{fmt}}" for v in x_vals]

    fig = go.Figure(go.Bar(
        x=x_vals, y=ranked["County"], orientation="h",
        marker_color=colors, marker_line_width=0,
        text=labels, textposition="outside",
        textfont=dict(color=TEXT, size=8), cliponaxis=False,
        hovertemplate=f"%{{y}}: %{{x:{fmt}}} {cfg['rank_unit']}<extra></extra>",
    ))
    fig.add_vline(
        x=avg_disp, line_color="#d97706", line_width=1.5, line_dash="dash",
        annotation_text=f"  Avg: {avg_disp:{fmt}} {cfg['rank_unit']}",
        annotation_position="top left",
        annotation_font=dict(color="#d97706", size=10),
    )
    fig.update_layout(
        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Arial"),
        title=dict(
            text=f"{state_name} County Rankings — {crop} {view_label} (NASS {year})",
            font=dict(size=14, color=ACCENT),
        ),
        height=max(380, len(ranked) * 22 + 80),
        margin=dict(l=10, r=90, t=50, b=20), bargap=0.18,
        xaxis=dict(
            title=f"{view_label} ({cfg['rank_unit']})", gridcolor=BORDER,
            tickfont=dict(color=MUTED), title_font=dict(color=MUTED),
            zeroline=True, zerolinecolor=MUTED,
        ),
        yaxis=dict(gridcolor=BORDER, tickfont=dict(color=TEXT, size=9), automargin=True),
    )
    return fig


# One color per ASD district code (10–90)
_ASD_PALETTE = {
    "10": "#0693e3", "20": "#f59e0b", "30": "#ef4444",
    "40": "#a78bfa", "50": "#16a34a", "60": "#fb923c",
    "70": "#38bdf8", "80": "#e879f9", "90": "#6366f1",
}


def build_nass_asd_ranking_chart(state_county_v, fips_map, fips_lk,
                                  state, crop, year, metric, change_view):
    """Bar chart of all counties in a state, sorted and coloured by ASD district."""
    cfg        = _nass_view_cfg(metric, change_view)
    view_label = metric if change_view == "Current Year" else f"{change_view} — {metric}"
    state_name = ABBR_TO_NAME.get(state, state)

    df = state_county_v.dropna(subset=["Value"]).copy()
    if df.empty:
        return go.Figure()

    # Attach district info via fips_map
    df["fips"]         = df["County"].apply(lambda c: resolve_fips(state, c, fips_lk))
    df["District"]     = df["fips"].map(lambda f: (fips_map.get(f) or (None,None))[0])
    df["DistrictCode"] = df["fips"].map(lambda f: (fips_map.get(f) or (None,""))[1])

    # Sort: district code ascending, then value ascending within district
    df = df.sort_values(
        ["DistrictCode", "Value"], ascending=[True, True]
    ).reset_index(drop=True)

    df["Color"] = df["DistrictCode"].map(_ASD_PALETTE).fillna(MUTED)

    x_vals   = df["Value"] / cfg["rank_div"]
    raw_avg  = df["Value"].mean()
    avg_disp = raw_avg / cfg["rank_div"]
    fmt      = cfg["rank_fmt"]

    fig = go.Figure(go.Bar(
        x=x_vals, y=df["County"], orientation="h",
        marker_color=df["Color"].tolist(), marker_line_width=0,
        text=[f"{v:{fmt}}" for v in x_vals],
        textposition="outside",
        textfont=dict(color=TEXT, size=8), cliponaxis=False,
        customdata=df["District"].fillna("—").tolist(),
        hovertemplate=(
            f"%{{y}} — %{{customdata}}: %{{x:{fmt}}} {cfg['rank_unit']}<extra></extra>"
        ),
    ))

    # State-average reference line
    fig.add_vline(
        x=avg_disp, line_color="#d97706", line_width=1.5, line_dash="dash",
        annotation_text=f"  Avg: {avg_disp:{fmt}} {cfg['rank_unit']}",
        annotation_position="top left",
        annotation_font=dict(color="#d97706", size=10),
    )

    # Horizontal separator lines + district label between groups
    cumulative = 0
    for code in sorted(df["DistrictCode"].dropna().unique()):
        grp = df[df["DistrictCode"] == code]
        if grp.empty:
            continue
        dist_name = grp.iloc[0]["District"] or code
        color     = _ASD_PALETTE.get(code, MUTED)

        if cumulative > 0:
            fig.add_hline(y=cumulative - 0.5,
                          line_color=BORDER, line_width=1.5, line_dash="dot")

        fig.add_annotation(
            x=0, y=cumulative + len(grp) / 2 - 0.5,
            xref="paper", yref="y",
            text=f"<b>{dist_name}</b>",
            font=dict(color=color, size=8),
            showarrow=False, xanchor="right", xshift=-4,
        )
        cumulative += len(grp)

    fig.update_layout(
        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Arial"),
        title=dict(
            text=f"{state_name} County Rankings by AG District — {crop} {view_label} (NASS {year})",
            font=dict(size=14, color=ACCENT),
        ),
        height=max(400, len(df) * 22 + 80),
        margin=dict(l=110, r=90, t=50, b=20), bargap=0.18,
        xaxis=dict(
            title=f"{view_label} ({cfg['rank_unit']})", gridcolor=BORDER,
            tickfont=dict(color=MUTED), title_font=dict(color=MUTED),
            zeroline=True, zerolinecolor=MUTED,
        ),
        yaxis=dict(gridcolor=BORDER, tickfont=dict(color=TEXT, size=9), automargin=True),
    )
    return fig


# ── Cached county figure wrappers ─────────────────────────────────────────────
# Cache key = hashable args only.  _geo / _centroids / _logo / _fips_lk are
# excluded (underscore prefix) so large objects aren't hashed into the key.

@st.cache_data(show_spinner=False)
def cached_nass_county_fig(state: str, crop: str, year: int,
                            metric: str, change_view: str,
                            comp_year: int, cache_ver: str,
                            _geo, _centroids, _logo_50yr, _fips_lk):
    county_vdf, _ = get_nass_view_data(
        crop, year, metric, change_view,
        comp_year if comp_year > 0 else None,
    )
    return build_nass_county_fig(
        county_vdf, _geo, state, crop, year, metric, change_view, _logo_50yr, _centroids, _fips_lk
    )


@st.cache_data(show_spinner=False)
def cached_rma_county_fig(state: str, crop: str, metric: str,
                           practice: str, wheat_type, cache_ver: str,
                           _geo, _fips_lk, _centroids, _logo_50yr):
    rma_data = load_data()
    if crop not in rma_data:
        return None
    df = rma_data[crop].copy()
    if crop == "Wheat" and wheat_type:
        df = df[df["Type"] == wheat_type]
    agg        = agg_data(df[df["State"] == state], practice, metric, ["County"])
    crop_label = f"Wheat — {wheat_type}" if crop == "Wheat" else crop
    return build_county_fig(agg, _geo, _fips_lk, _centroids, state,
                            metric, crop_label, practice, _logo_50yr)


def _auto_bu(mx: float) -> tuple:
    """Return (divisor, label) to scale a bushel quantity to a readable unit."""
    if mx >= 500e6: return 1e9, "B bu"
    if mx >= 1e6:   return 1e6, "M bu"
    if mx >= 1e3:   return 1e3, "K bu"
    return 1, "bu"


def _auto_ac(mx: float) -> tuple:
    """Return (divisor, label) to scale an acreage quantity to a readable unit."""
    if mx >= 500e3: return 1e6, "M ac"
    if mx >= 1e3:   return 1e3, "K ac"
    return 1, "ac"


def _lv_auto_scale(mx: float, base_unit: str) -> tuple:
    """Return (divisor, label) to scale livestock quantities to a readable unit."""
    if mx >= 500e6: return 1e9,     f"B {base_unit}"
    if mx >= 500e3: return 1e6,     f"M {base_unit}"
    if mx >= 500:   return 1e3,     f"K {base_unit}"
    return 1, base_unit


# ── Watermark helpers ─────────────────────────────────────────────────────────
_st_plotly_chart = st.plotly_chart  # saved before _chart shadows the call site


def _chart(fig, **kw) -> None:
    _st_plotly_chart(fig, **kw)


# ── App ────────────────────────────────────────────────────────────────────────
def main():
    st.markdown(
        f"""
        <style>
        /* ── JPSI brand theme — jpsi.com palette ─────────────────── */
        .stApp {{ background-color: {DARK}; color: {TEXT}; }}
        [data-testid="stSidebar"] {{
            background-color: {PANEL};
            border-right: 1px solid {BORDER};
        }}
        .block-container {{ padding-top: 1rem; max-width: 1400px; }}
        /* headings — teal accent */
        h1, h2, h3 {{ color: {ACCENT} !important; letter-spacing: 0.02em; }}
        h4, h5, h6 {{ color: {TEXT} !important; }}
        p, label, .stCaption, [data-testid="stCaptionContainer"] {{ color: {MUTED} !important; }}
        /* dropdowns / selects */
        [data-testid="stSelectbox"] label {{ color: {MUTED} !important; font-size: 0.8rem; }}
        div[data-baseweb="select"] > div {{
            background-color: {PANEL} !important;
            border-color: {BORDER} !important;
            color: {TEXT} !important;
        }}
        div[data-baseweb="popover"] * {{ background-color: {PANEL} !important; color: {TEXT} !important; }}
        /* multiselect tags */
        [data-baseweb="tag"] {{
            background-color: {SURFACE} !important;
            border: 1px solid {BORDER} !important;
        }}
        /* metric cards */
        [data-testid="metric-container"] {{
            background-color: {PANEL};
            border: 1px solid {BORDER};
            border-left: 3px solid {ACCENT};
            border-radius: 8px;
            padding: 12px 16px;
        }}
        [data-testid="stMetricValue"] {{ color: {ACCENT} !important; font-size: 1.35rem; font-weight: 700; }}
        [data-testid="stMetricLabel"] {{
            color: {MUTED} !important; font-size: 0.78rem;
            text-transform: uppercase; letter-spacing: 0.06em;
        }}
        [data-testid="stMetricDelta"] svg {{ fill: {ACCENT2} !important; }}
        /* expanders & tables */
        [data-testid="stExpander"] {{ background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
        [data-testid="stDataFrame"] {{
            background-color: {PANEL};
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='130'%3E%3Ctext transform='rotate(-30 130 65)' x='55' y='88' font-family='Arial Black%2CArial' font-size='44' font-weight='900' fill='rgba(66%2C82%2C72%2C0.04)'%3EJSA%3C/text%3E%3C/svg%3E");
            background-repeat: repeat;
        }}
        hr {{ border-color: {BORDER}; }}
        [data-testid="stSpinner"] p {{ color: {MUTED} !important; }}
        /* tabs — active tab gets lime underline, text switches to teal */
        .stTabs [data-baseweb="tab-list"] {{
            background-color: {PANEL};
            border-radius: 6px 6px 0 0;
            gap: 4px;
            border-bottom: 2px solid {BORDER};
        }}
        .stTabs [data-baseweb="tab"] {{ color: {MUTED}; font-size: 0.92rem; padding: 8px 20px; }}
        .stTabs [aria-selected="true"] {{
            color: {ACCENT} !important;
            border-bottom: 3px solid {ACCENT2} !important;
            font-weight: 600 !important;
        }}
        /* radio button-group toggle */
        div[data-testid="stRadio"] > label {{ display: none; }}
        div[data-testid="stRadio"] > div[role="radiogroup"] {{
            display: flex; flex-direction: row; gap: 6px; flex-wrap: wrap;
        }}
        div[data-testid="stRadio"] > div[role="radiogroup"] > label {{
            background-color: {PANEL} !important;
            border: 1px solid {BORDER} !important;
            border-radius: 6px !important;
            padding: 6px 16px !important;
            cursor: pointer !important;
            color: {MUTED} !important;
            font-size: 0.84rem !important;
            font-weight: 500 !important;
            margin: 0 !important;
        }}
        div[data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {{
            border-color: {ACCENT2} !important;
            color: {ACCENT2} !important;
            background-color: {SURFACE} !important;
        }}
        div[data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {{
            display: none !important;
        }}
        /* app title bar */
        [data-testid="stHeader"] {{ background-color: {DARK} !important; border-bottom: 1px solid {BORDER}; }}
        /* info/warning boxes */
        [data-testid="stAlert"] {{ background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
        /* scrollbar */
        ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
        ::-webkit-scrollbar-track {{ background: {DARK}; }}
        ::-webkit-scrollbar-thumb {{ background: {BORDER}; border-radius: 3px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: {ACCENT}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("JSA Agricultural Intelligence Dashboard")
    st.markdown(
        f"""<p style='color:{MUTED};font-size:0.80rem;margin-top:-10px;margin-bottom:6px;line-height:1.6;'>
        ℹ️ <b style='color:{TEXT};'>NASS tab</b>: USDA survey-based final production figures (post-harvest, all acres). &nbsp;|&nbsp;
        <b style='color:{TEXT};'>RMA tab</b>: Estimated production for federally insured acres (insured acres × projected yield) — figures will differ from NASS.
        </p>""",
        unsafe_allow_html=True,
    )

    # Load base resources shared across both tabs
    with st.spinner("Loading..."):
        rma_data   = load_data()
        geo        = load_geojson()
        fips_lk    = build_fips_lookup(geo)
        centroids  = build_centroid_lookup(geo)
        logo_50yr  = load_logo(LOGO_50YR)
        logo_trans = load_logo(LOGO_TRANS)
        logo_full  = load_logo(LOGO_FULL)

    st.markdown(
        f"""<style>
        [data-testid="stHeader"] {{
            background-image: url('{logo_full}');
            background-repeat: no-repeat;
            background-position: right 90px center;
            background-size: auto 68%;
        }}
        </style>""",
        unsafe_allow_html=True,
    )

    tab_nass, tab_rma, tab_stocks, tab_acreage, tab_livestock, tab_aqua, tab_proc, tab_wcmd, tab_storage_cmp, tab_eia, tab_about = st.tabs([
        "🌾  NASS Production", "📋  RMA",
        "📦  Grain Stocks", "🌱  Acreage Summary", "🐄  Livestock",
        "🐟  Aquaculture", "🏭  Processing",
        "🏦  Grain Warehouses", "⚖️  Storage vs. Production",
        "🔋  Biofuels (EIA)",
        "📖  About the Data",
    ])

    # ══════════════════════════════════════════════════════════════════════════
    # NASS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_nass:
        if "nass_sel_state" not in st.session_state:
            st.session_state.nass_sel_state = None
        if "nass_map_view" not in st.session_state:
            st.session_state.nass_map_view = "ASD District"

        # Row 1 — Crop, Year, State drill-down, Refresh
        nc1, nc2, nc3, nc4 = st.columns([1, 0.75, 1.8, 0.55])
        with nc1:
            nass_crop = st.selectbox("Crop", list(NASS_CROP_STAT_PARAMS.keys()), key="nass_crop")
        with nc2:
            nass_year = st.selectbox("Year", NASS_YEARS, index=0, key="nass_year")
        with nc4:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Refresh", use_container_width=True, key="nass_refresh"):
                st.cache_data.clear()
                st.rerun()

        # Row 2 — Metric selector
        st.markdown(
            f"<p style='color:{MUTED};font-size:0.78rem;margin:4px 0 2px 0;'>Metric</p>",
            unsafe_allow_html=True,
        )
        nass_metric = st.radio(
            "Metric", NASS_METRICS, horizontal=True,
            key="nass_metric", label_visibility="collapsed",
        )

        # Row 3 — Change view selector
        st.markdown(
            f"<p style='color:{MUTED};font-size:0.78rem;margin:4px 0 2px 0;'>View</p>",
            unsafe_allow_html=True,
        )
        nass_change = st.radio(
            "Change View", NASS_CHANGE_OPTS, horizontal=True,
            key="nass_change", label_visibility="collapsed",
        )

        # Row 4 (conditional) — Compare-year picker
        if nass_change == "vs Selected Year":
            cy_col, _ = st.columns([0.55, 2.6])
            with cy_col:
                avail_comp   = [y for y in NASS_YEARS if y != nass_year]
                nass_comp_yr = st.selectbox("Compare to Year", avail_comp, key="nass_comp_year")
        else:
            nass_comp_yr = None

        stat_type = _METRIC_TO_STAT[nass_metric]

        with st.spinner(f"Loading NASS {nass_year} {nass_crop} {nass_metric}..."):
            nass_df = load_nass_county(nass_crop, nass_year, _CACHE_VERSION)   # county data — for drill-down & coverage KPI
            # Pre-warm 2023 benchmark county count
            load_nass_county(nass_crop, _NASS_BENCHMARK_YEAR, _CACHE_VERSION)
            # Pre-warm comparison years for county and state data
            if nass_change == "vs Prior Year":
                _load_for_metric(nass_crop, nass_year - 1, stat_type)
                _load_state_for_stat(nass_crop, nass_year - 1, stat_type, _CACHE_VERSION)
            elif nass_change == "vs Selected Year" and nass_comp_yr:
                _load_for_metric(nass_crop, nass_comp_yr, stat_type)
                _load_state_for_stat(nass_crop, nass_comp_yr, stat_type, _CACHE_VERSION)
            elif nass_change == "vs 3-Yr Avg":
                for _y in [nass_year - 1, nass_year - 2, nass_year - 3]:
                    if _y >= 2015:
                        _load_for_metric(nass_crop, _y, stat_type)
                        _load_state_for_stat(nass_crop, _y, stat_type, _CACHE_VERSION)
            # County-level data for the county map
            county_vdf, _ = get_nass_view_data(
                nass_crop, nass_year, nass_metric, nass_change, nass_comp_yr
            )
            # Official state-level data for the state choropleth map
            _st_cur = _load_state_for_stat(nass_crop, nass_year, stat_type, _CACHE_VERSION)
            if nass_change == "Current Year" or _st_cur.empty:
                state_vdf = _st_cur
            elif nass_change == "vs Prior Year":
                state_vdf = _state_pct_change(
                    _st_cur,
                    _load_state_for_stat(nass_crop, nass_year - 1, stat_type, _CACHE_VERSION),
                )
            elif nass_change == "vs Selected Year" and nass_comp_yr:
                state_vdf = _state_pct_change(
                    _st_cur,
                    _load_state_for_stat(nass_crop, nass_comp_yr, stat_type, _CACHE_VERSION),
                )
            else:  # vs 3-Yr Avg
                _sy = [y for y in [nass_year - 1, nass_year - 2, nass_year - 3] if y >= 2015]
                state_vdf = _state_pct_change_avg(
                    _st_cur,
                    [_load_state_for_stat(nass_crop, y, stat_type, _CACHE_VERSION) for y in _sy],
                )

        if nass_df.empty and nass_year != FORECAST_YEAR:
            st.warning(
                f"No NASS {nass_year} county-level production data returned for {nass_crop}. "
                "The data may not yet be published or the API parameters may need adjustment."
            )
        else:
            # Forecast year: build state list from NASS state planted data (already published)
            if nass_year == FORECAST_YEAR and nass_df.empty:
                _fc_st_avail = _load_nass_state_forecast(nass_crop, "planted", _CACHE_VERSION)
                states_avail_nass = sorted(_fc_st_avail["State"].unique()) if not _fc_st_avail.empty else []
            else:
                states_avail_nass = sorted(nass_df["State"].unique())
            with nc3:
                state_opts_nass = ["— US Overview —"] + [
                    f"{a}  —  {ABBR_TO_NAME.get(a, a)}" for a in states_avail_nass
                ]
                default_nass = 0
                if st.session_state.nass_sel_state:
                    try:
                        default_nass = states_avail_nass.index(st.session_state.nass_sel_state) + 1
                    except ValueError:
                        default_nass = 0
                nass_sel = st.selectbox(
                    "State Drill-Down", state_opts_nass,
                    index=default_nass, key="nass_state_dd"
                )
                st.session_state.nass_sel_state = (
                    None if nass_sel.startswith("—") else nass_sel[:2]
                )

            # ── Summary metrics ───────────────────────────────────────────────
            sel_st = st.session_state.nass_sel_state

            # Official state-level totals — used for KPI (state map already uses state_vdf)
            _kpi_state = _st_cur if sel_st is None else _st_cur[_st_cur["State"] == sel_st]

            # County coverage % — current year vs 2023 benchmark
            _bench_df  = load_nass_county(nass_crop, _NASS_BENCHMARK_YEAR, _CACHE_VERSION)
            if sel_st is None:
                _bench_n = len(_bench_df)
                _curr_n  = len(nass_df)
            else:
                _bench_n = len(_bench_df[_bench_df["State"] == sel_st])
                _curr_n  = len(nass_df[nass_df["State"] == sel_st])
            _pct_rep = _curr_n / _bench_n * 100 if _bench_n > 0 else 0.0

            scope_v = county_vdf if sel_st is None else county_vdf[county_vdf["State"] == sel_st]

            def _official_kpi_str(df_state):
                """Format the official KPI value from state-level NASS data."""
                if nass_metric in ("Yield (bu/ac)", "% Harvested"):
                    v = df_state["Value"].mean()
                    if pd.isna(v):
                        return "—"
                    return f"{v:.1f} bu/ac" if nass_metric == "Yield (bu/ac)" else f"{v:.1f}%"
                if nass_metric in ("Planted Acres", "Harvested Acres", "Prevent Plant Acres"):
                    v = df_state["Value"].sum()
                    return f"{v/1e6:.1f}M ac"
                # Production
                v = df_state["Value"].sum()
                return f"{v/1e9:.2f}B bu" if v >= 1e9 else f"{v/1e6:.1f}M bu"

            if nass_change == "Current Year":
                nm1, nm2, nm3, nm4 = st.columns(4)
                nm1.metric(f"{nass_year} {nass_metric}", _official_kpi_str(_kpi_state))
                if nass_year == FORECAST_YEAR and nass_df.empty:
                    nm2.metric("County Coverage", "Forecast", help="County data not yet published for 2026")
                    nm3.metric("Counties Reporting", "—")
                    nm4.metric("States in Data", f"{len(states_avail_nass):,}")
                else:
                    nm2.metric("County Coverage",
                               f"{_pct_rep:.0f}%",
                               help=f"{_curr_n:,} counties reporting vs {_bench_n:,} in {_NASS_BENCHMARK_YEAR} benchmark")
                    nm3.metric("Counties Reporting", f"{_curr_n:,}")
                    nm4.metric("States in Data", f"{_st_cur['State'].nunique():,}")
            else:
                nm1, nm2, nm3, nm4 = st.columns(4)
                nm1.metric(f"{nass_year} {nass_metric}", _official_kpi_str(_kpi_state))
                nm2.metric("County Coverage", f"{_pct_rep:.0f}%",
                           help=f"{_curr_n:,} of {_bench_n:,} counties reporting")
                valid_v  = scope_v["Value"].dropna()
                avg_chg  = valid_v.mean() if not valid_v.empty else float("nan")
                improved = int((valid_v > 0).sum())
                declined = int((valid_v < 0).sum())
                nm3.metric("Counties Above Prior", f"{improved:,} ▲")
                nm4.metric("Counties Below Prior", f"{declined:,} ▼")

            # ── Map ───────────────────────────────────────────────────────────
            if sel_st is None:
                if state_vdf.empty and nass_year == FORECAST_YEAR:
                    st.info(
                        f"**{FORECAST_YEAR} Forecast** — National overview not available for "
                        f"{nass_metric} (NASS has not yet published {nass_year} yield or "
                        "production). Use the **State Drill-Down** above to view ASD district "
                        "forecasts for a specific state."
                    )
                elif state_vdf.empty:
                    st.info(
                        "No comparison data available for the selected view and year range. "
                        "Try selecting a different year or view."
                    )
                else:
                    nass_fig = build_nass_state_fig(
                        state_vdf, nass_crop, nass_year, nass_metric, nass_change, logo_50yr
                    )
                    nass_fig.update_layout(
                        dragmode=False,
                        geo=dict(
                            projection_type="albers usa",
                            showframe=False,
                            bgcolor=DARK,
                            landcolor=LAND,
                        ),
                    )
                    _chart(nass_fig, use_container_width=True,
                                    key="nass_state_map",
                                    config={
                                        "scrollZoom":    False,
                                        "displayModeBar": False,
                                        "doubleClick":   False,
                                    })
                    st.caption("Use the State Drill-Down dropdown above to view county detail.")

            else:
                nass_state = sel_st
                state_df   = nass_df[nass_df["State"] == nass_state].copy()

                # ── Forecast year setup ────────────────────────────────────
                _is_forecast_year = (nass_year == FORECAST_YEAR)
                _fc_stats: dict         = {}
                _fc_state_totals: dict  = {}
                if _is_forecast_year:
                    _yield_key = f"fc_yield_{nass_crop}_{nass_state}"
                    if _yield_key not in st.session_state:
                        _def_yield = _get_default_yield_est(
                            nass_crop, nass_state, _CACHE_VERSION
                        )
                        st.session_state[_yield_key] = _def_yield if _def_yield > 0 else 0.0
                    _nass_st_yield_avail = _nass_has_official(
                        nass_crop, nass_state, FORECAST_YEAR, "yield", _CACHE_VERSION
                    )
                    _banner_extra = (
                        " NASS yield is now available — your estimate overrides it."
                        if _nass_st_yield_avail else ""
                    )
                    st.info(
                        f"**{FORECAST_YEAR} Forecast** — NASS has not yet published "
                        f"{nass_crop} yield or production for "
                        f"{ABBR_TO_NAME.get(nass_state, nass_state)}. "
                        "Enter a yield estimate below to project ASD district production."
                        + _banner_extra
                    )
                    _fc_col1, _, _ = st.columns([1.2, 1, 2.8])
                    with _fc_col1:
                        # Use key-only pattern — session_state holds the value
                        _yield_input = st.number_input(
                            f"{nass_crop} Yield Est. (bu/ac)",
                            min_value=0.0, max_value=500.0,
                            step=0.5, format="%.1f",
                            key=_yield_key,
                        )
                    if _yield_input > 0:
                        _fc_stats, _fc_state_totals = get_asd_forecast_data(
                            nass_crop, nass_state, _yield_input, _CACHE_VERSION
                        )

                # Back button + map-view toggle on the same row
                _back_col, _view_col, _ = st.columns([0.7, 2.2, 2])
                with _back_col:
                    if st.button("← Back", key="nass_back_btn"):
                        st.session_state.nass_sel_state = None
                        st.rerun()
                with _view_col:
                    nass_map_view = st.radio(
                        "Map View",
                        ["ASD District", "County"],
                        horizontal=True,
                        key="nass_map_view",
                        index=0,
                    )

                if (state_df.empty or state_df["Production"].sum() == 0) and not _is_forecast_year:
                    st.warning(
                        f"No NASS {nass_year} county data available for "
                        f"{ABBR_TO_NAME.get(nass_state, nass_state)}. "
                        "This crop may not be produced in this state or data has not been published."
                    )
                elif _is_forecast_year and not _fc_stats:
                    st.info("Enter a yield estimate above to generate the ASD district forecast.")
                else:
                    # Load fips_map always — used by both the ASD map and the
                    # ASD-grouped ranking chart regardless of current map view.
                    _sfips    = STATE_FIPS_ALL.get(nass_state, "")
                    _fips_map = load_boundary_fips_map(
                        nass_crop, _sfips, _CACHE_VERSION, geo
                    )

                    # ── Map ───────────────────────────────────────────────────
                    if nass_map_view == "ASD District":
                        # Current-year absolute district values for the side table
                        # Pass geo so production uses Tier-2 estimated counties
                        _dist_abs  = get_nass_district_view_data(
                            nass_crop, nass_year, nass_metric, "Current Year",
                            _fips_map, nass_state, None, _geo=geo,
                        )
                        # For forecast year, build side-table from _fc_stats
                        if _dist_abs.empty and _is_forecast_year and _fc_stats:
                            _fc_ms_abs = _METRIC_TO_STAT.get(nass_metric, "production")
                            _dist_abs = pd.DataFrame(
                                [(d, v) for d, v in _fc_stats.get(_fc_ms_abs, {}).items()
                                 if v is not None],
                                columns=["District", "Value"],
                            )
                        # Estimated county count badge — all estimable metrics
                        _est_stat = _METRIC_TO_STAT.get(nass_metric, "")
                        if _est_stat in ("production", "planted", "harvested", "yield"):
                            _comp_data = get_completed_county_data(
                                nass_crop, nass_state, nass_year, _est_stat, _CACHE_VERSION
                            )
                            _n_est = int(_comp_data["is_estimated"].sum()) if not _comp_data.empty else 0
                        else:
                            _comp_data = pd.DataFrame()
                            _n_est = 0
                        _abs_cfg_d = _nass_view_cfg(nass_metric, "Current Year")
                        _is_yield  = nass_metric == "Yield (bu/ac)"

                        # Build name → ASD code lookup from fips_map
                        _name_to_code = {
                            v[0]: v[1] for v in _fips_map.values() if v[0] and v[1]
                        }

                        if not _dist_abs.empty:
                            _dt = _dist_abs.copy()
                            _dt["ASD"] = _dt["District"].map(_name_to_code).fillna("")
                            _dt = _dt.sort_values("ASD").reset_index(drop=True)
                            _dt[nass_metric] = _dt["Value"].apply(_abs_cfg_d["label_fn"])
                            _st_total = (
                                _dt["Value"].mean() if _is_yield
                                else _dt["Value"].sum()
                            )
                            _dt["% of State"] = (
                                (_dt["Value"] / _st_total * 100
                                 ).round(1).astype(str)
                                if not _is_yield and _st_total > 0
                                else "—"
                            )

                            # Per-district estimated county count
                            if not _comp_data.empty and _est_stat in ("production","planted","harvested","yield"):
                                _est_by_dist = (
                                    _comp_data[_comp_data["is_estimated"]]
                                    .groupby("District")["is_estimated"]
                                    .count()
                                    .reset_index()
                                    .rename(columns={"is_estimated": "Est. Counties"})
                                )
                                _dt = _dt.merge(_est_by_dist, on="District", how="left")
                                _dt["Est. Counties"] = _dt["Est. Counties"].fillna(0).astype(int)
                                _dt["Est. Counties"] = _dt["Est. Counties"].apply(
                                    lambda n: str(n) if n > 0 else "—"
                                )
                                _tbl_cols = ["District", "ASD", nass_metric,
                                             "% of State", "Est. Counties"]
                            else:
                                _tbl_cols = ["District", "ASD", nass_metric, "% of State"]

                            _tbl_col, _map_col = st.columns([1, 2.5])
                            with _tbl_col:
                                st.markdown(
                                    f"<p style='color:{MUTED};font-size:0.78rem;"
                                    f"margin:0 0 4px 0;'>"
                                    f"{ABBR_TO_NAME.get(nass_state,nass_state)} "
                                    f"{nass_year} {nass_crop}</p>",
                                    unsafe_allow_html=True,
                                )
                                st.dataframe(
                                    _dt[_tbl_cols],
                                    use_container_width=True, hide_index=True,
                                )
                                if _n_est > 0:
                                    st.caption(
                                        "Est = Some counties not yet final. Production is "
                                        "estimated using each county's historical share of "
                                        "state output, adjusted for current district "
                                        "performance, and scaled to reconcile with the "
                                        "NASS state total."
                                    )
                                _km1, _km2 = st.columns(2)
                                _km1.metric(
                                    "State Avg" if _is_yield else "State Total",
                                    _abs_cfg_d["label_fn"](_st_total),
                                )
                                _km2.metric("Districts", f"{len(_dt)}")
                        else:
                            _map_col = st.container()

                        with _map_col:
                            with st.spinner(
                                f"Building {ABBR_TO_NAME.get(nass_state, nass_state)}"
                                " ASD district map…"
                            ):
                                # ── Compute district values directly from
                                # get_completed_county_data so map and table
                                # always use identical numbers. ───────────
                                _ms = _METRIC_TO_STAT.get(nass_metric, "production")
                                _mr = nass_metric in ("Yield (bu/ac)", "% Harvested")

                                # Years we need for the selected comparison
                                _need = {nass_year}
                                if nass_change == "vs Prior Year":
                                    _need.add(nass_year - 1)
                                elif nass_change == "vs Selected Year" and nass_comp_yr:
                                    _need.add(nass_comp_yr)
                                elif nass_change == "vs 3-Yr Avg":
                                    for _y in [nass_year-1, nass_year-2, nass_year-3]:
                                        if _y >= 2015: _need.add(_y)

                                # Load and aggregate each year
                                _mdv: dict = {}   # {year: {district: value}}
                                _mde: dict = {}   # {year: {district: is_estimated}}
                                for _yr in sorted(_need):
                                    # Forecast year: use estimated ASD data
                                    if _is_forecast_year and _yr == FORECAST_YEAR and _fc_stats:
                                        _fc_mv = _fc_stats.get(_ms, {})
                                        _mdv[_yr] = {d: v for d, v in _fc_mv.items() if v is not None}
                                        _mde[_yr] = {d: True for d in _mdv[_yr]}
                                        continue
                                    _cdf = get_completed_county_data(
                                        nass_crop, nass_state, _yr, _ms, _CACHE_VERSION
                                    )
                                    _mdv[_yr] = {}; _mde[_yr] = {}
                                    if not _cdf.empty and "District" in _cdf.columns:
                                        for dist, grp in _cdf.groupby("District"):
                                            _mdv[_yr][dist] = float(
                                                grp["Value"].mean() if _mr
                                                else grp["Value"].sum()
                                            )
                                            _mde[_yr][dist] = bool(
                                                grp["is_estimated"].any()
                                            )

                                # Compute view values
                                _cur = _mdv.get(nass_year, {})
                                if nass_change == "Current Year":
                                    _view = dict(_cur)
                                else:
                                    if nass_change == "vs Prior Year":
                                        _base = _mdv.get(nass_year - 1, {})
                                    elif nass_change == "vs Selected Year" and nass_comp_yr:
                                        _base = _mdv.get(nass_comp_yr, {})
                                    else:  # vs 3-Yr Avg
                                        _avg_maps = [
                                            _mdv.get(y, {})
                                            for y in [nass_year-1, nass_year-2, nass_year-3]
                                            if y >= 2015 and y in _mdv
                                        ]
                                        _base = {
                                            d: sum(m[d] for m in _avg_maps if d in m)
                                               / sum(1 for m in _avg_maps if d in m)
                                            for d in set(d for m in _avg_maps for d in m)
                                        }
                                    _view = {}
                                    for dist, cur_v in _cur.items():
                                        base_v = _base.get(dist)
                                        if cur_v and base_v and base_v != 0:
                                            _view[dist] = (cur_v - base_v) / abs(base_v) * 100

                                # Build DataFrames for the figure builder
                                _dvdf = pd.DataFrame(
                                    [(d, v) for d, v in _view.items()],
                                    columns=["District", "Value"]
                                ).dropna(subset=["Value"])
                                _drdf = pd.DataFrame(
                                    [(d, v) for d, v in _cur.items()],
                                    columns=["District", "Value"]
                                ).dropna(subset=["Value"])
                                _est_dists = {
                                    d for d, ie in _mde.get(nass_year, {}).items() if ie
                                }
                                _dgdf = build_nass_district_gdf(
                                    _sfips, _CACHE_VERSION, _fips_map, geo
                                )
                                nass_dist_fig = build_nass_district_fig(
                                    _dvdf, _drdf, _dgdf,
                                    nass_state, nass_crop, nass_year,
                                    nass_metric, nass_change, logo_50yr, geo,
                                    estimated_districts=_est_dists,
                                )

                            if nass_dist_fig is None:
                                st.info(f"ASD district map not available for "
                                        f"{ABBR_TO_NAME.get(nass_state, nass_state)}.")
                            else:
                                nass_dist_fig.update_layout(dragmode=False)
                                _chart(nass_dist_fig, use_container_width=True,
                                                key="nass_district_map",
                                                config={"scrollZoom": False,
                                                        "displayModeBar": False,
                                                        "doubleClick": False})
                    else:
                        _cty_est_stat = _METRIC_TO_STAT.get(nass_metric, "")
                        _use_est_county = (
                            _cty_est_stat in ("production","planted","harvested","yield")
                            and nass_change == "Current Year"
                        )
                        with st.spinner(
                            f"Building {ABBR_TO_NAME.get(nass_state, nass_state)} county map…"
                        ):
                            if _use_est_county:
                                _comp_cty = get_completed_county_data(
                                    nass_crop, nass_state, nass_year,
                                    _cty_est_stat, _CACHE_VERSION
                                )
                                nass_county_fig = build_nass_county_fig_with_est(
                                    _comp_cty, geo, nass_state, nass_crop,
                                    nass_year, nass_metric, logo_50yr, centroids,
                                )
                                _cty_n_est = int(_comp_cty["is_estimated"].sum()) \
                                    if not _comp_cty.empty else 0
                            else:
                                nass_county_fig = cached_nass_county_fig(
                                    nass_state, nass_crop, nass_year,
                                    nass_metric, nass_change,
                                    nass_comp_yr if nass_comp_yr else 0,
                                    _CACHE_VERSION, geo, centroids, logo_50yr, fips_lk
                                )
                                _cty_n_est = 0
                        if nass_county_fig is None:
                            st.info(
                                f"County map not available for "
                                f"{ABBR_TO_NAME.get(nass_state, nass_state)}."
                            )
                        else:
                            nass_county_fig.update_layout(dragmode=False)
                            _chart(nass_county_fig, use_container_width=True,
                                            key="nass_county_map",
                                            config={"scrollZoom": False,
                                                    "displayModeBar": False,
                                                    "doubleClick": False})
                            if _cty_n_est > 0:
                                st.caption(
                                    f"Est = {_cty_n_est} counties not yet final. "
                                    "Production is estimated using each county's "
                                    "historical share of state output, adjusted for "
                                    "current district performance, and scaled to "
                                    "reconcile with the NASS state total."
                                )

                    st.markdown(f"<hr style='border-color:{BORDER};margin:8px 0'>",
                                unsafe_allow_html=True)

                    # ── Scope selector — controls BOTH chart and table ─────────
                    _state_full = ABBR_TO_NAME.get(nass_state, nass_state)
                    _hsc1, _hsc2 = st.columns([1.4, 2.6])
                    with _hsc1:
                        _htbl_scope = st.radio(
                            "View",
                            ["State", "ASD District", "County"],
                            horizontal=True, key="htbl_scope",
                        )
                    with _hsc2:
                        if _htbl_scope == "ASD District":
                            _dist_opts = sorted(
                                set(v[0] for v in _fips_map.values() if v[0])
                            )
                            _htbl_dist = st.selectbox(
                                "District", _dist_opts, key="htbl_district"
                            )
                            _htbl_county = None
                            _scope_label = f"{_state_full} — {_htbl_dist}"
                        elif _htbl_scope == "County":
                            _county_opts = sorted(
                                state_df["County"].dropna().unique()
                            )
                            _htbl_county = st.selectbox(
                                "County", _county_opts, key="htbl_county"
                            )
                            _htbl_dist = None
                            _scope_label = f"{_state_full} — {_htbl_county} County"
                        else:
                            _htbl_dist = _htbl_county = None
                            _scope_label = _state_full

                    # ── Historical Bar Chart (scope-aware) ────────────────────
                    _nc_n = st.radio("Chart history (years)", [5, 10],
                                     horizontal=True, key="nass_chart_yrs", index=1)
                    _nc_yrs  = sorted([y for y in NASS_YEARS if y <= nass_year][:_nc_n])
                    _nc_stat = _METRIC_TO_STAT.get(nass_metric, "production")
                    _nc_ratio= nass_metric in ("Yield (bu/ac)", "% Harvested")

                    with st.spinner("Building chart…"):
                        _nc_vals = []
                        for _ny in _nc_yrs:
                            _v = None
                            # Forecast year: use ASD projection instead of NASS
                            if _is_forecast_year and _ny == FORECAST_YEAR and _fc_stats:
                                if _htbl_scope == "State":
                                    _v = _fc_state_totals.get(_nc_stat)
                                elif _htbl_scope == "ASD District" and _htbl_dist:
                                    _v = _fc_stats.get(_nc_stat, {}).get(_htbl_dist)
                                # County scope: no forecast at county level — leave None
                                _nc_vals.append(_v)
                                continue
                            if _htbl_scope == "State":
                                _ndf = _load_state_for_stat(nass_crop, _ny, _nc_stat, _CACHE_VERSION)
                                if not _ndf.empty and "State" in _ndf.columns:
                                    _s = _ndf[_ndf["State"] == nass_state]
                                    if not _s.empty:
                                        _v = float(_s["Value"].mean() if _nc_ratio else _s["Value"].sum())
                            elif _htbl_scope == "ASD District" and _htbl_dist:
                                _cdf = get_completed_county_data(nass_crop, nass_state, _ny, _nc_stat, _CACHE_VERSION)
                                if not _cdf.empty and "District" in _cdf.columns:
                                    _d = _cdf[_cdf["District"] == _htbl_dist]
                                    if not _d.empty:
                                        _v = float(_d["Value"].mean() if _nc_ratio else _d["Value"].sum())
                            elif _htbl_scope == "County" and _htbl_county:
                                _cdf = _load_for_metric(nass_crop, _ny, _nc_stat)
                                if not _cdf.empty and "State" in _cdf.columns:
                                    _c = _cdf[(_cdf["State"] == nass_state) & (_cdf["County"] == _htbl_county)]
                                    if not _c.empty:
                                        _v = float(_c["Value"].mean() if _nc_ratio else _c["Value"].sum())
                            _nc_vals.append(_v)

                    # Auto-scale unit from actual data so small states/districts
                    # don't show as 0.3 B bu when M bu is more readable
                    _nc_max = max((v for v in _nc_vals if v), default=0)

                    if nass_metric == "Production (bu)":
                        _nc_unit, _nc_y_lbl = _auto_bu(_nc_max)
                    elif nass_metric in ("Planted Acres", "Harvested Acres",
                                         "Prevent Plant Acres"):
                        _nc_unit, _nc_y_lbl = _auto_ac(_nc_max)
                    elif nass_metric == "Yield (bu/ac)":
                        _nc_unit, _nc_y_lbl = 1, "bu/ac"
                    elif nass_metric == "% Harvested":
                        _nc_unit, _nc_y_lbl = 1, "%"
                    else:
                        _nc_unit, _nc_y_lbl = 1e9, "B bu"

                    _nc_fig = build_history_bar(
                        {nass_metric: [v / _nc_unit if v else 0 for v in _nc_vals]},
                        _nc_yrs,
                        title=f"{_scope_label} — {nass_crop} {nass_metric}",
                        y_label=_nc_y_lbl,
                    )
                    _chart(_nc_fig, use_container_width=True,
                                    key="nass_hist_chart",
                                    config={"displayModeBar": False})

                    st.markdown(f"<hr style='border-color:{BORDER};margin:8px 0'>",
                                unsafe_allow_html=True)

                    # ── National / District / County Heatmap Table ────────────

                    st.markdown(
                        f"<p style='color:{MUTED};font-size:0.82rem;font-weight:600;"
                        f"margin:0 0 6px 0;letter-spacing:0.04em;'>"
                        f"📅 HISTORICAL SUMMARY — {_scope_label} | "
                        f"{nass_year - 5}–{nass_year}</p>",
                        unsafe_allow_html=True,
                    )

                    # Rolling window — user-selectable 5 or 10 years (default 5)
                    _tbl_n_yrs  = st.radio("Table history (years)", [5, 10],
                                           horizontal=True, key="nass_tbl_yrs", index=0)
                    _HIST_YEARS = list(range(nass_year - (_tbl_n_yrs - 1), nass_year + 1))
                    _HIST_STATTYPES = ["planted", "harvested", "pct_harvested",
                                       "yield", "production"]
                    _HIST_ROW_LBL   = {
                        "planted":        "Planted Acres (000 ac)",
                        "harvested":      "Harvested Acres (000 ac)",
                        "pct_harvested":  "% Harvested",
                        "yield":          "Yield (bu/ac)",
                        "production":     "Production (M bu)",
                    }

                    with st.spinner("Loading historical data…"):
                        _hist: dict = {}
                        _tbl_est_years: set = set()   # years where production is estimated
                        for _hyr in _HIST_YEARS:
                            _hist[_hyr] = {}
                            # Forecast year: use ASD/state projection
                            if _is_forecast_year and _hyr == FORECAST_YEAR and _fc_stats:
                                for _hst in _HIST_STATTYPES:
                                    if _htbl_scope == "State":
                                        _hist[_hyr][_hst] = _fc_state_totals.get(_hst)
                                    elif _htbl_scope == "ASD District" and _htbl_dist:
                                        _hist[_hyr][_hst] = _fc_stats.get(_hst, {}).get(_htbl_dist)
                                    else:
                                        _hist[_hyr][_hst] = None
                                _tbl_est_years.add(_hyr)
                                continue
                            for _hst in _HIST_STATTYPES:
                                try:
                                    if _htbl_scope == "State":
                                        # Official state totals from state-level API
                                        _hdf = _load_state_for_stat(
                                            nass_crop, _hyr, _hst, _CACHE_VERSION
                                        )
                                        _hdf_s = (
                                            _hdf[_hdf["State"] == nass_state]
                                            if not _hdf.empty and "State" in _hdf.columns
                                            else pd.DataFrame()
                                        )
                                        _hist[_hyr][_hst] = (
                                            float(_hdf_s["Value"].iloc[0])
                                            if not _hdf_s.empty else None
                                        )
                                    elif (_htbl_scope == "ASD District"
                                          and _htbl_dist
                                          and _hst in ("production","planted","harvested",
                                                        "pct_harvested","yield")):
                                        # Use Tier-1 estimated county data for all metrics
                                        if _hst == "pct_harvested":
                                            # Derive from completed planted + harvested
                                            _plt_yr = get_completed_county_data(
                                                nass_crop, nass_state, _hyr, "planted", _CACHE_VERSION
                                            )
                                            _hv_yr  = get_completed_county_data(
                                                nass_crop, nass_state, _hyr, "harvested", _CACHE_VERSION
                                            )
                                            _plt_d  = _plt_yr[_plt_yr["District"] == _htbl_dist] if not _plt_yr.empty else pd.DataFrame()
                                            _hv_d   = _hv_yr[_hv_yr["District"] == _htbl_dist]  if not _hv_yr.empty  else pd.DataFrame()
                                            if _plt_d.empty or _hv_d.empty or _plt_d["Value"].sum() == 0:
                                                _hist[_hyr][_hst] = None
                                            else:
                                                _hist[_hyr][_hst] = float(
                                                    _hv_d["Value"].sum() / _plt_d["Value"].sum() * 100
                                                )
                                                if _plt_d["is_estimated"].any() or _hv_d["is_estimated"].any():
                                                    _tbl_est_years.add(_hyr)
                                        else:
                                            _comp_yr = get_completed_county_data(
                                                nass_crop, nass_state, _hyr, _hst, _CACHE_VERSION
                                            )
                                            if _comp_yr.empty:
                                                _hist[_hyr][_hst] = None
                                                continue
                                            _comp_dist = _comp_yr[
                                                _comp_yr["District"] == _htbl_dist
                                            ]
                                            if _comp_dist.empty:
                                                _hist[_hyr][_hst] = None
                                            else:
                                                _hist[_hyr][_hst] = float(
                                                    _comp_dist["Value"].mean()
                                                    if _hst == "yield"
                                                    else _comp_dist["Value"].sum()
                                                )
                                                if _comp_dist["is_estimated"].any():
                                                    _tbl_est_years.add(_hyr)
                                    else:
                                        # County-level data filtered to district or county
                                        _hdf = _load_for_metric(
                                            nass_crop, _hyr, _hst
                                        )
                                        if _hdf.empty or "State" not in _hdf.columns:
                                            _hist[_hyr][_hst] = None
                                            continue
                                        _hdf_s = _hdf[_hdf["State"] == nass_state].copy()
                                        if _htbl_scope == "ASD District" and _htbl_dist:
                                            _hdf_s["_Dist"] = _hdf_s["fips"].map(
                                                lambda f: (_fips_map.get(f) or (None,))[0]
                                            )
                                            _hdf_s = _hdf_s[_hdf_s["_Dist"] == _htbl_dist]
                                        elif _htbl_scope == "County" and _htbl_county:
                                            _hdf_s = _hdf_s[_hdf_s["County"] == _htbl_county]
                                        if _hdf_s.empty:
                                            _hist[_hyr][_hst] = None
                                        elif _hst == "yield":
                                            _hist[_hyr][_hst] = float(_hdf_s["Value"].mean())
                                        else:
                                            _hist[_hyr][_hst] = float(_hdf_s["Value"].sum())
                                except Exception:
                                    _hist[_hyr][_hst] = None

                    def _hfmt(stype, v):
                        if v is None or (isinstance(v, float) and np.isnan(v)):
                            return "—"
                        if stype == "production":      return f"{v/1e6:,.0f}"
                        if stype in ("planted","harvested"): return f"{v/1e3:,.0f}"
                        if stype == "pct_harvested":   return f"{v:.1f}%"
                        if stype == "yield":           return f"{v:.1f}"
                        return f"{v:,.0f}"

                    def _hdelta_str(v):
                        if v is None or (isinstance(v, float) and np.isnan(v)):
                            return ""
                        return f" ({'+'if v>=0 else ''}{v:.1f}%)"

                    # Compute deltas using same nass_change view as map
                    _hdelta: dict = {yr: {} for yr in _HIST_YEARS}
                    if nass_change == "vs Prior Year":
                        for _hi, _hyr in enumerate(_HIST_YEARS):
                            if _hi == 0:
                                continue
                            _hprev = _HIST_YEARS[_hi - 1]
                            for _hst in _HIST_STATTYPES:
                                _hc, _hp = _hist[_hyr].get(_hst), _hist[_hprev].get(_hst)
                                _hdelta[_hyr][_hst] = (
                                    (_hc - _hp) / abs(_hp) * 100
                                    if _hc and _hp else None
                                )
                    elif nass_change == "vs 3-Yr Avg":
                        for _hst in _HIST_STATTYPES:
                            # 3-yr avg base = 3 years prior to the selected year
                            _avg_base = [nass_year - 3, nass_year - 2, nass_year - 1]
                            _hvals = [_hist[y].get(_hst) for y in _avg_base
                                      if _hist.get(y, {}).get(_hst)]
                            _havg  = sum(_hvals) / len(_hvals) if _hvals else None
                            for _hyr in _HIST_YEARS:
                                _hc = _hist[_hyr].get(_hst)
                                _hdelta[_hyr][_hst] = (
                                    (_hc - _havg) / abs(_havg) * 100
                                    if _hc and _havg else None
                                )
                    elif nass_change == "vs Selected Year" and nass_comp_yr:
                        for _hyr in _HIST_YEARS:
                            for _hst in _HIST_STATTYPES:
                                _hc   = _hist[_hyr].get(_hst)
                                _hbase= _hist[nass_comp_yr].get(_hst)
                                _hdelta[_hyr][_hst] = (
                                    (_hc - _hbase) / abs(_hbase) * 100
                                    if _hc and _hbase and _hyr != nass_comp_yr else None
                                )

                    # Build national all-states heatmap table
                    _nt_stat  = _METRIC_TO_STAT.get(nass_metric, "production")
                    _nt_ratio = nass_metric in ("Yield (bu/ac)", "% Harvested")
                    _nt_units = {
                        "Production (bu)":"M bu",   # M bu matches map labels exactly
                        "Planted Acres":"M ac","Harvested Acres":"M ac",
                        "% Harvested":"%","Yield (bu/ac)":"bu/ac",
                        "Prevent Plant Acres":"M ac",
                    }
                    _nt_divs  = {
                        "Production (bu)":1e6,      # M bu; .1f = nearest 100K bu
                        "Planted Acres":1e6,"Harvested Acres":1e6,"% Harvested":1,
                        "Yield (bu/ac)":1,"Prevent Plant Acres":1e6,
                    }
                    _nt_unit  = _nt_units.get(nass_metric, "M bu")
                    _nt_div   = _nt_divs.get(nass_metric, 1e6)
                    _nt_fmt   = ".1f"   # 1 decimal for all metrics

                    if _htbl_scope == "State":
                        # National all-states heatmap
                        with st.spinner("Building national table…"):
                            _nt_state_yr: dict = {}
                            _nt_us_tot:   dict = {}
                            for _ny in _HIST_YEARS:
                                _ntdf = _load_state_for_stat(nass_crop, _ny, _nt_stat, _CACHE_VERSION)
                                if not _ntdf.empty and "State" in _ntdf.columns:
                                    for _, _nr in _ntdf.iterrows():
                                        _ns = _nr["State"]
                                        if _ns not in _nt_state_yr: _nt_state_yr[_ns] = {}
                                        _nv = _nr.get("Value")
                                        _nt_state_yr[_ns][_ny] = float(_nv) if pd.notna(_nv) and _nv else None
                                    _nt_us_tot[_ny] = float(
                                        _ntdf["Value"].mean() if _nt_ratio else _ntdf["Value"].sum()
                                    )
                        _nt_tbl_title = (f"{nass_crop} — {nass_metric} ({_nt_unit}) | "
                                         f"{min(_HIST_YEARS)}–{max(_HIST_YEARS)}")
                        _nt_fig = build_heatmap_table(
                            _nt_state_yr, _HIST_YEARS, title=_nt_tbl_title,
                            unit=_nt_unit, divisor=_nt_div, is_ratio=_nt_ratio,
                            us_totals=_nt_us_tot if not _nt_ratio else None, fmt=_nt_fmt,
                        )

                    elif _htbl_scope == "ASD District":
                        # District × year heatmap + per-district county expanders
                        with st.spinner("Building district table…"):
                            _nd_yr:   dict = {}   # {district: {year: value}}
                            _nd_us:   dict = {}   # {year: state total}
                            _nd_ctys: dict = {}   # {district: {county: {year: value}}}
                            _nd_est:  dict = {}   # {district: {county: {year: bool}}}
                            for _ny in _HIST_YEARS:
                                _cddf = get_completed_county_data(
                                    nass_crop, nass_state, _ny, _nt_stat, _CACHE_VERSION
                                )
                                if not _cddf.empty and "District" in _cddf.columns:
                                    for dist, grp in _cddf.groupby("District"):
                                        if dist not in _nd_yr:   _nd_yr[dist]   = {}
                                        if dist not in _nd_ctys:  _nd_ctys[dist] = {}
                                        if dist not in _nd_est:   _nd_est[dist]  = {}
                                        _nd_yr[dist][_ny] = float(
                                            grp["Value"].mean() if _nt_ratio else grp["Value"].sum()
                                        )
                                        for _, _crow in grp.iterrows():
                                            _cn = _crow.get("County", "")
                                            if _cn:
                                                if _cn not in _nd_ctys[dist]: _nd_ctys[dist][_cn] = {}
                                                if _cn not in _nd_est[dist]:  _nd_est[dist][_cn]  = {}
                                                _cv = _crow.get("Value")
                                                _nd_ctys[dist][_cn][_ny] = float(_cv) if pd.notna(_cv) and _cv else None
                                                _nd_est[dist][_cn][_ny]  = bool(_crow.get("is_estimated", False))
                                    _nd_us[_ny] = float(
                                        _cddf["Value"].mean() if _nt_ratio else _cddf["Value"].sum()
                                    )
                        # Apply (Est)/(USDA) labels based on latest-year status
                        _latest_yr = max(_HIST_YEARS)
                        def _dist_any_est(dist):
                            return any(
                                _nd_est.get(dist, {}).get(c, {}).get(_latest_yr, False)
                                for c in _nd_ctys.get(dist, {})
                            )
                        # District cell_status: for each district/year,
                        # True if ANY county in that district was estimated
                        _nd_cell_status = {
                            dist: {
                                yr: any(
                                    _nd_est.get(dist, {}).get(cn, {}).get(yr, False)
                                    for cn in _nd_ctys.get(dist, {})
                                )
                                for yr in _HIST_YEARS
                            }
                            for dist in _nd_yr
                        }
                        _nt_tbl_title = (f"{nass_state} {nass_crop} — {nass_metric} "
                                         f"by ASD District ({_nt_unit}) | "
                                         f"{min(_HIST_YEARS)}–{max(_HIST_YEARS)}")
                        _nt_fig = build_heatmap_table(
                            _nd_yr, _HIST_YEARS, title=_nt_tbl_title,
                            unit=_nt_unit, divisor=_nt_div, is_ratio=_nt_ratio,
                            us_totals=_nd_us if not _nt_ratio else None,
                            regions=None, fmt=_nt_fmt,
                            cell_status=_nd_cell_status,
                            total_label=f"{nass_state} Total",
                        )

                    else:
                        # County scope — show all counties for selected state
                        with st.spinner("Building county table…"):
                            _nco_yr: dict = {}
                            for _ny in _HIST_YEARS:
                                _cdf = _load_for_metric(nass_crop, _ny, _nt_stat)
                                if not _cdf.empty and "State" in _cdf.columns:
                                    _cdf_s = _cdf[_cdf["State"] == nass_state]
                                    for _, _cr in _cdf_s.iterrows():
                                        _cn = _cr.get("County", "")
                                        if _cn:
                                            if _cn not in _nco_yr: _nco_yr[_cn] = {}
                                            _nco_yr[_cn][_ny] = float(_cr["Value"]) if pd.notna(_cr["Value"]) else None
                        _nt_tbl_title = (f"{nass_state} {nass_crop} — {nass_metric} "
                                         f"by County ({_nt_unit}) | "
                                         f"{min(_HIST_YEARS)}–{max(_HIST_YEARS)}")
                        _nt_fig = build_heatmap_table(
                            _nco_yr, _HIST_YEARS, title=_nt_tbl_title,
                            unit=_nt_unit, divisor=_nt_div, is_ratio=_nt_ratio,
                            regions=None, fmt=_nt_fmt,
                            total_label=f"{nass_state} Total",
                        )

                    _chart(_nt_fig, use_container_width=True,
                                    key="nass_heatmap_tbl",
                                    config={"displayModeBar": False})

                    # ── ASD county drill-down — tight expanders below table ───
                    if _htbl_scope == "ASD District" and "_nd_ctys" in dir() and _nd_ctys:
                        # CSS: remove gap between table and expanders, style to match
                        st.markdown(
                            f"""<style>
                            /* tighten the first expander right under the table */
                            div[data-testid="stExpander"] {{
                                background-color: {PANEL};
                                border: 1px solid {BORDER};
                                border-radius: 0 !important;
                                margin-top: 0 !important;
                                margin-bottom: 1px !important;
                            }}
                            div[data-testid="stExpander"] summary {{
                                background-color: {PANEL};
                                color: {TEXT};
                                font-size: 0.85rem;
                                font-family: Arial, sans-serif;
                                padding: 6px 12px;
                            }}
                            div[data-testid="stExpander"] summary:hover {{
                                background-color: {BORDER};
                            }}
                            </style>""",
                            unsafe_allow_html=True,
                        )
                        st.caption(
                            "▼ Expand any district below to view county detail  ·  "
                            "Top 2 = green  ·  Bottom 2 = red per row  ·  "
                            "Olympic Avg drops single highest and lowest year."
                        )
                        for _dist_name in sorted(_nd_ctys.keys()):
                            _cty_data = _nd_ctys.get(_dist_name, {})
                            _n_cty    = len(_cty_data)
                            with st.expander(
                                f"▼  {_dist_name}  —  {_n_cty} counties",
                                expanded=False,
                            ):
                                if _cty_data:
                                    _cty_est_map = _nd_est.get(_dist_name, {})
                                    # Per-county, per-year estimated flag
                                    _cty_cell_status = {
                                        cn: {yr: _cty_est_map.get(cn, {}).get(yr, False)
                                             for yr in _HIST_YEARS}
                                        for cn in _cty_data
                                    }
                                    # District total row: estimated if any county was estimated that year
                                    _dist_top_cs = {
                                        yr: any(_cty_est_map.get(cn, {}).get(yr, False)
                                                for cn in _cty_data)
                                        for yr in _HIST_YEARS
                                    }
                                    _cty_fig = build_heatmap_table(
                                        _cty_data, _HIST_YEARS,
                                        title=(f"{_dist_name} — County Detail "
                                               f"({_nt_unit})"),
                                        unit=_nt_unit, divisor=_nt_div,
                                        is_ratio=_nt_ratio, regions=None,
                                        fmt=_nt_fmt,
                                        top_row=_nd_yr.get(_dist_name, {}),
                                        top_row_label=f"{_dist_name} District Total",
                                        cell_status=_cty_cell_status,
                                        top_row_cell_status=_dist_top_cs,
                                        total_label=f"{nass_state} Total",
                                    )
                                    _chart(
                                        _cty_fig, use_container_width=True,
                                        key=f"cty_tbl_{_dist_name}",
                                        config={"displayModeBar": False},
                                    )
                                else:
                                    st.info("No county data for this district.")
                    else:
                        st.caption(
                            "Top 2 = green  ·  Bottom 2 = red per row  ·  "
                            "Olympic Avg drops single highest and lowest year."
                        )

                    # Per-state detail (ASD District / County) in expander
                    with st.expander(
                        f"📋 {_state_full} Detail — {nass_metric} "
                        f"({min(_HIST_YEARS)}–{max(_HIST_YEARS)})",
                        expanded=False,
                    ):
                        _htbl_rows = []
                        for _hst in _HIST_STATTYPES:
                            _hrow = {"": _HIST_ROW_LBL[_hst]}
                            for _hyr in _HIST_YEARS:
                                _raw_s = _hfmt(_hst, _hist[_hyr].get(_hst))
                                _dlt_s = (
                                    _hdelta_str(_hdelta[_hyr].get(_hst))
                                    if nass_change != "Current Year" else ""
                                )
                                _hrow[str(_hyr)] = f"{_raw_s}{_dlt_s}"
                            _htbl_rows.append(_hrow)

                        _htbl_df = pd.DataFrame(_htbl_rows).set_index("")
                        if _tbl_est_years:
                            _htbl_df = _htbl_df.rename(
                                columns={str(y): f"{y} (Est)" for y in _tbl_est_years}
                            )

                        def _htbl_style(val):
                            if "(+" in str(val): return f"color:{ACCENT};font-weight:600"
                            if "(-" in str(val): return "color:#ef4444;font-weight:600"
                            return ""

                        if nass_change != "Current Year":
                            st.dataframe(_htbl_df.style.map(_htbl_style),
                                         use_container_width=True)
                        else:
                            st.dataframe(_htbl_df, use_container_width=True)

                        if _tbl_est_years:
                            st.caption(
                                "Est = Some counties not yet final — estimated via "
                                "historical share × district performance adjustment."
                            )

                    st.markdown(f"<hr style='border-color:{BORDER};margin:8px 0'>",
                                unsafe_allow_html=True)

                    # ── Ranking Chart ─────────────────────────────────────────
                    state_county_v = county_vdf[county_vdf["State"] == nass_state].copy()
                    if not state_county_v.empty:
                        if nass_map_view == "ASD District":
                            ranking_nass = build_nass_asd_ranking_chart(
                                state_county_v, _fips_map, fips_lk,
                                nass_state, nass_crop, nass_year,
                                nass_metric, nass_change,
                            )
                        else:
                            ranking_nass = build_nass_ranking_chart(
                                state_county_v, nass_state, nass_crop, nass_year,
                                nass_metric, nass_change,
                            )
                        _chart(ranking_nass, use_container_width=True,
                                        key="nass_ranking")

                    # ── County Data Table ─────────────────────────────────────
                    with st.expander(
                        f"County Data Table — {ABBR_TO_NAME.get(nass_state, nass_state)}",
                        expanded=False,
                    ):
                        tbl = state_county_v[["County", "Value"]].dropna(
                            subset=["Value"]
                        ).sort_values("Value", ascending=False).copy()

                        if nass_change != "Current Year":
                            col_label = f"% Change ({nass_metric})"
                            tbl[col_label] = tbl["Value"].apply(
                                lambda v: f"{v:+.1f}%" if pd.notna(v) else "—"
                            )
                        elif nass_metric == "Yield (bu/ac)":
                            col_label = "Yield (bu/ac)"
                            tbl[col_label] = tbl["Value"].apply(
                                lambda v: f"{v:.1f}" if pd.notna(v) else "—"
                            )
                        elif nass_metric in ("Planted Acres", "Harvested Acres",
                                             "Prevent Plant Acres"):
                            col_label = nass_metric
                            tbl[col_label] = tbl["Value"].apply(
                                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
                            )
                        else:
                            col_label = "Production (bu)"
                            tbl[col_label] = tbl["Value"].apply(
                                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
                            )

                        show_tbl = tbl[["County", col_label]].copy()
                        if nass_metric != "Production (bu)":
                            prod_ctx = state_df[["County", "Production"]].copy()
                            prod_ctx["Production (bu)"] = prod_ctx["Production"].apply(
                                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
                            )
                            show_tbl = show_tbl.merge(
                                prod_ctx[["County", "Production (bu)"]],
                                on="County", how="left",
                            )
                        st.dataframe(show_tbl, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════════════
    # RMA TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_rma:
        if "rma_sel_state" not in st.session_state:
            st.session_state.rma_sel_state = None

        crops_available = [c for c in ["Corn", "Soybeans", "Wheat"] if c in rma_data]
        c1, c2, c3, c4, c5, c6 = st.columns([1, 1.2, 1.2, 1.2, 1.5, 0.6])

        with c1:
            crop = st.selectbox("Crop", crops_available, key="rma_crop")
        with c2:
            metric = st.selectbox("Metric", list(METRIC_COL.keys()), key="rma_metric")
        with c3:
            practice = st.selectbox("Practice", ["All", "Irrigated", "Non-Irrigated"],
                                    key="rma_practice")
        with c4:
            if crop == "Wheat":
                wheat_types = sorted(
                    t for t in rma_data["Wheat"]["Type"].dropna().unique()
                    if "khor" not in t.lower()
                )
                default_wt = next(
                    (i for i, t in enumerate(wheat_types) if "winter" in t.lower()), 0
                )
                wheat_type = st.selectbox("Wheat Type ✱", wheat_types,
                                          index=default_wt, key="rma_wheat_type")
            else:
                wheat_type = None
        with c6:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Refresh Data", use_container_width=True, key="rma_refresh"):
                st.cache_data.clear()
                st.rerun()

        df = rma_data[crop].copy()
        if crop == "Wheat" and wheat_type:
            df = df[df["Type"] == wheat_type]
        crop_label = f"Wheat — {wheat_type}" if crop == "Wheat" else crop

        with c5:
            states_avail = sorted(df["State"].unique())
            state_opts   = ["— US Overview —"] + [
                f"{a}  —  {ABBR_TO_NAME.get(a, a)}" for a in states_avail
            ]
            default_idx = 0
            if st.session_state.rma_sel_state:
                try:
                    default_idx = states_avail.index(st.session_state.rma_sel_state) + 1
                except ValueError:
                    default_idx = 0
            sel = st.selectbox("State Drill-Down", state_opts,
                               index=default_idx, key="rma_state_dd")
            st.session_state.rma_sel_state = (
                None if sel.startswith("—") else sel[:2]
            )

        col  = METRIC_COL[metric]
        unit = METRIC_UNIT[metric]
        fmt  = METRIC_FMT[metric]

        scope_df = filter_practice(df, practice)
        if st.session_state.rma_sel_state:
            scope_df = scope_df[scope_df["State"] == st.session_state.rma_sel_state]

        if metric == "Yield":
            p = scope_df["Reported Production"].sum()
            a = scope_df["Reported Production Acres"].sum()
            summary_val = p / a if a > 0 else 0.0
        else:
            summary_val = scope_df[col].sum()

        m1, m2, m3 = st.columns(3)
        lbl = "Avg Yield" if metric == "Yield" else f"Total {metric}"
        m1.metric(lbl, f"{summary_val:{fmt}} {unit}")
        m2.metric("Counties", f"{scope_df[['State','County']].drop_duplicates().shape[0]:,}")
        m3.metric("States",   f"{scope_df['State'].nunique():,}")

        if st.session_state.rma_sel_state is None:
            agg = agg_data(df, practice, metric, ["State"])
            fig = build_state_fig(agg, metric, crop_label, practice, logo_50yr)
            fig.update_layout(dragmode=False)
            _chart(fig, use_container_width=True, key="rma_state_map",
                            config={"scrollZoom": False, "displayModeBar": False,
                                    "doubleClick": False})
            st.caption("Use the State Drill-Down dropdown above to view county detail.")

        else:
            state = st.session_state.rma_sel_state
            agg   = agg_data(df[df["State"] == state], practice, metric, ["County"])

            if st.button("← Back to US Map", key="rma_back_btn"):
                st.session_state.rma_sel_state = None
                st.rerun()

            if agg.empty or agg[col].sum() == 0:
                st.warning(f"No data for {ABBR_TO_NAME.get(state, state)} with selected filters.")
            else:
                with st.spinner(f"Building {ABBR_TO_NAME.get(state, state)} county map…"):
                    fig = cached_rma_county_fig(
                        state, crop, metric, practice, wheat_type, _CACHE_VERSION,
                        geo, fips_lk, centroids, logo_50yr
                    )
                if fig is None:
                    st.info(f"County map not available for {ABBR_TO_NAME.get(state, state)}.")
                else:
                    fig.update_layout(dragmode=False)
                    _chart(fig, use_container_width=True, key="rma_county_map",
                                    config={"scrollZoom": False, "displayModeBar": False,
                                            "doubleClick": False})
                    st.caption("Use ← Back to US Map to return to the national overview.")

            state_name = ABBR_TO_NAME.get(state, state)
            st.markdown(f"<hr style='border-color:{BORDER};margin:8px 0'>",
                        unsafe_allow_html=True)
            ranking_fig = build_ranking_chart(agg, metric, state)
            _chart(ranking_fig, use_container_width=True, key="rma_ranking_chart")

            with st.expander(f"County Data Table — {state_name}", expanded=False):
                disp = agg.sort_values(col, ascending=False).copy()
                disp.columns = ["County", f"{metric} ({unit})"]
                disp[f"{metric} ({unit})"] = disp[f"{metric} ({unit})"].apply(
                    lambda v: f"{v:,.1f}" if pd.notna(v) else "—"
                )
                st.dataframe(disp, use_container_width=True, hide_index=True)


    # ══════════════════════════════════════════════════════════════════════════
    # GRAIN STOCKS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_stocks:
        # ── Row 1: crop / year / period / metric / refresh ────────────────────
        sc1, sc2, sc3, sc4, sc5 = st.columns([1, 0.9, 1.1, 1.8, 0.55])
        with sc1:
            stk_crop = st.selectbox("Crop", STOCKS_CROPS, key="stk_crop")
        with sc2:
            stk_year = st.selectbox("Year", NASS_YEARS, index=0, key="stk_year")
        with sc3:
            stk_period_lbl = st.selectbox("Period", list(STOCKS_PERIODS.keys()),
                                          index=1, key="stk_period")  # Dec 1 default
            stk_period = STOCKS_PERIODS[stk_period_lbl]
        with sc4:
            stk_view = st.radio(
                "Metric", STOCKS_VIEWS, horizontal=True, key="stk_view",
                label_visibility="collapsed",
            )
        with sc5:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🔄 Refresh", use_container_width=True, key="stk_refresh"):
                st.cache_data.clear(); st.rerun()

        # ── Row 2: comparison view + optional base year ────────────────────────
        cv1, cv2 = st.columns([3.8, 1.0])
        with cv1:
            stk_change = st.radio(
                "Compare",
                ["Current Year", "vs Prior Year", "vs 3-Yr Avg", "vs Selected Year"],
                horizontal=True, key="stk_change", label_visibility="collapsed",
            )
        with cv2:
            if stk_change == "vs Selected Year":
                _avail_comp = [y for y in NASS_YEARS if y < stk_year]
                stk_comp_yr = st.selectbox("Base Year", _avail_comp,
                                           key="stk_comp_yr")
            else:
                stk_comp_yr = None

        # Nominal/% toggle lives BELOW the map — read from session state here
        # so the computation uses the correct value on every re-run.
        # Default: Nominal (bu change) for all comparison views.
        stk_change_type = (
            st.session_state.get("stk_nominal_pct", "Nominal")
            if stk_change != "Current Year" else None
        )

        # ── Load data ─────────────────────────────────────────────────────────
        with st.spinner(f"Loading {stk_year} {stk_crop} data…"):
            stk_df   = load_grain_stocks(stk_crop, stk_year, stk_period, _CACHE_VERSION)
            # Production (state level — always loaded)
            _prod_st = _load_state_for_stat(stk_crop, stk_year, "production", _CACHE_VERSION)
            _prod_map = dict(zip(_prod_st["State"], _prod_st["Value"])) \
                        if not _prod_st.empty else {}
            # Sep 1 stocks — used for Total Supply regardless of selected period
            _sep_df  = load_grain_stocks(stk_crop, stk_year, "FIRST OF SEP", _CACHE_VERSION)
            _sep_map = dict(zip(_sep_df["State"], _sep_df["Total"])) \
                       if not _sep_df.empty else {}

        if stk_df.empty and not _prod_map:
            st.info(
                f"No {stk_crop} data available for {stk_year} {stk_period_lbl}. "
                "NASS publishes quarterly stocks — data may not yet be released for this period."
            )
        else:
            # ── Helper: enrich a stocks DataFrame with Production + TotalSupply ─
            def _enrich(df, prod_m, sep_m):
                """Add Production and TotalSupply columns to a stocks df."""
                if df.empty:
                    df = pd.DataFrame({"State": sorted(prod_m.keys())})
                    for c in ("Total","OnFarm","OffFarm","PctOnFarm","PctOffFarm"):
                        df[c] = None
                df = df.copy()
                df["Production"]  = df["State"].map(prod_m)
                df["TotalSupply"] = df["State"].map(
                    lambda s: ((sep_m.get(s) or 0) + (prod_m.get(s) or 0)) or None
                )
                df["StateName"]   = df["State"].map(ABBR_TO_NAME)
                return df

            stk_df = _enrich(stk_df, _prod_map, _sep_map)

            # ── View config ───────────────────────────────────────────────────
            def _bu_fmt(v):
                if v is None or (isinstance(v, float) and np.isnan(v)): return "—"
                return f"{v/1e9:.2f}B bu" if v >= 1e9 else f"{v/1e6:.0f}M bu"

            def _delta_fmt(v, is_ratio_metric):
                """Format a nominal delta — pp for ratio metrics, bu for rest."""
                if v is None or (isinstance(v, float) and np.isnan(v)): return "—"
                sign = "+" if v >= 0 else ""
                if is_ratio_metric:
                    return f"{sign}{v:.1f} pp"
                return f"{sign}{v/1e6:.0f}M bu" if abs(v) >= 1e6 \
                       else f"{sign}{v:,.0f} bu"

            _stk_col_map = {
                "Total Stocks": ("Total",      "YlOrBr", "bu"),
                "On-Farm":      ("OnFarm",      "YlGn",   "bu"),
                "Off-Farm":     ("OffFarm",     "YlOrRd", "bu"),
                "% On-Farm":    ("PctOnFarm",   "RdYlGn", "%"),
                "% Off-Farm":   ("PctOffFarm",  "RdYlBu", "%"),
                "Production":   ("Production",  "YlOrBr", "bu"),
                "Total Supply": ("TotalSupply", "YlOrBr", "bu"),
            }
            _col, _abs_cscale, _unit = _stk_col_map[stk_view]
            _is_ratio = _unit == "%"   # % On-Farm / % Off-Farm

            # ── Load comparison base data ─────────────────────────────────────
            def _load_yr(yr):
                """Load and enrich a full year of stocks + production."""
                _s = load_grain_stocks(stk_crop, yr, stk_period, _CACHE_VERSION)
                _p = _load_state_for_stat(stk_crop, yr, "production", _CACHE_VERSION)
                _e = load_grain_stocks(stk_crop, yr, "FIRST OF SEP", _CACHE_VERSION)
                _pm = dict(zip(_p["State"], _p["Value"])) if not _p.empty else {}
                _sm = dict(zip(_e["State"], _e["Total"])) if not _e.empty else {}
                return _enrich(_s, _pm, _sm)

            if stk_change == "Current Year":
                _display_df   = stk_df.copy()
                _display_col  = _col
                _map_cscale   = _abs_cscale
                _is_change    = False
                _change_unit  = _unit
            else:
                # Build base DataFrame
                with st.spinner("Loading comparison data…"):
                    if stk_change == "vs Prior Year":
                        _base_df = _load_yr(stk_year - 1)
                    elif stk_change == "vs Selected Year" and stk_comp_yr:
                        _base_df = _load_yr(stk_comp_yr)
                    else:  # vs 3-Yr Avg
                        _frames  = [_load_yr(y)
                                    for y in [stk_year-1, stk_year-2, stk_year-3]
                                    if y >= 2015]
                        _frames  = [f for f in _frames if not f.empty]
                        if _frames:
                            _base_df = (
                                pd.concat(_frames)
                                .groupby("State")[list(_stk_col_map[v][0]
                                                       for v in STOCKS_VIEWS
                                                       if _stk_col_map[v][0]
                                                       in _frames[0].columns)]
                                .mean().reset_index()
                            )
                            _base_df["StateName"] = _base_df["State"].map(ABBR_TO_NAME)
                        else:
                            _base_df = pd.DataFrame()

                _base_map = dict(zip(_base_df["State"], _base_df[_col])) \
                            if not _base_df.empty and _col in _base_df.columns else {}

                # Compute delta per state
                _display_df = stk_df.copy()
                def _delta(state, cur_col):
                    cur  = stk_df[stk_df["State"]==state][cur_col].values
                    base = _base_map.get(state)
                    if len(cur) == 0 or cur[0] is None or base is None or base == 0:
                        return None
                    c = float(cur[0])
                    if stk_change_type == "% Change":
                        return (c - base) / abs(base) * 100
                    return c - base   # Nominal

                _display_df["_delta"] = _display_df["State"].apply(
                    lambda s: _delta(s, _col)
                )
                _display_col = "_delta"
                _map_cscale  = "RdYlGn"
                _is_change   = True
                _change_unit = "%" if stk_change_type == "% Change" else \
                               ("pp" if _is_ratio else "bu")

            # ── KPI cards (always show absolute values) ───────────────────────
            _us_stk  = stk_df["Total"].sum()
            _us_on   = stk_df["OnFarm"].sum()
            _us_off  = stk_df["OffFarm"].sum()
            _us_prod = stk_df["Production"].sum()
            _us_sup  = stk_df["TotalSupply"].sum()
            _pct_on  = _us_on  / _us_stk * 100 if _us_stk > 0 else 0
            _pct_off = _us_off / _us_stk * 100 if _us_stk > 0 else 0

            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric(f"Total Stocks ({stk_period_lbl})", _bu_fmt(_us_stk))
            k2.metric("On-Farm",    _bu_fmt(_us_on),  f"{_pct_on:.1f}% of total")
            k3.metric("Off-Farm",   _bu_fmt(_us_off), f"{_pct_off:.1f}% of total")
            k4.metric(f"{stk_year} Production",  _bu_fmt(_us_prod))
            k5.metric("Total Supply (Sep + Prod)", _bu_fmt(_us_sup),
                      help="Sep 1 beginning stocks + crop year production")

            st.markdown(f"<hr style='border-color:{BORDER};margin:6px 0'>",
                        unsafe_allow_html=True)

            # ── State choropleth map ──────────────────────────────────────────
            _period_note = "Sep 1 + Prod" if stk_view == "Total Supply" else stk_period_lbl
            _cmp_note    = "" if stk_change == "Current Year" \
                           else f" ({stk_change} — {stk_change_type})"
            _map_title   = f"{stk_year} {stk_crop} — {stk_view}{_cmp_note} | {_period_note}"

            _plot_df = _display_df.dropna(subset=[_display_col]).copy()
            _z_vals  = [v for v in _plot_df[_display_col].tolist() if pd.notna(v)]
            if _is_change:
                _abs_max = max((abs(v) for v in _z_vals), default=1.0)
                _z_min, _z_max = -_abs_max, _abs_max
            else:
                _z_min = min(_z_vals) if _z_vals else 0
                _z_max = max(_z_vals) if _z_vals else 1

            stk_fig = px.choropleth(
                _plot_df,
                locations="State", locationmode="USA-states",
                color=_display_col, scope="usa",
                color_continuous_scale=_map_cscale,
                hover_name="StateName",
                hover_data={_display_col: ":.1f", "State": False},
                labels={_display_col: f"{stk_view} ({_change_unit})"},
                range_color=[_z_min, _z_max],
            )
            stk_fig.update_layout(
                **_base_layout(_map_title),
                height=520, dragmode=False,
                geo=dict(showlakes=False, bgcolor=DARK, landcolor=LAND,
                         showframe=False, projection_type="albers usa"),
                coloraxis_colorbar=dict(
                    title=dict(text=f"{stk_view}<br>({_change_unit})",
                               font=dict(color=TEXT)),
                    tickfont=dict(color=TEXT),
                ),
            )
            _lbl_lons, _lbl_lats, _lbl_texts = [], [], []
            for _, row in _plot_df.iterrows():
                v = row[_display_col]
                if row["State"] in STATE_CENTROIDS and pd.notna(v):
                    if _is_change:
                        _lbl = _delta_fmt(v, _is_ratio) if stk_change_type == "Nominal" \
                               else f"{'+'if v>=0 else ''}{v:.1f}%"
                    elif _is_ratio:
                        _lbl = f"{v:.1f}%"
                    else:
                        _lbl = _bu_fmt(v)
                    _lbl_lons.append(STATE_CENTROIDS[row["State"]][0])
                    _lbl_lats.append(STATE_CENTROIDS[row["State"]][1])
                    _lbl_texts.append(_lbl)
            if _lbl_lons:
                stk_fig.add_trace(go.Scattergeo(
                    lon=_lbl_lons, lat=_lbl_lats, text=_lbl_texts, mode="text",
                    textfont=dict(color="#374151", size=10, family="Arial Black"),
                    showlegend=False, hoverinfo="skip", geo="geo",
                ))
            _add_logo(stk_fig, logo_50yr)
            _chart(stk_fig, use_container_width=True, key="stk_map",
                            config={"scrollZoom": False, "displayModeBar": False,
                                    "doubleClick": False})

            # ── Nominal / % toggle (only shown for comparison views) ──────────
            if stk_change != "Current Year":
                st.radio(
                    "Map values",
                    ["Nominal", "% Change"],
                    horizontal=True,
                    key="stk_nominal_pct",
                    index=0,   # default to Nominal
                    label_visibility="collapsed",
                    help="Nominal = absolute change in bushels  |  % Change = relative change",
                )

            st.markdown(f"<hr style='border-color:{BORDER};margin:6px 0'>",
                        unsafe_allow_html=True)

            # ── Historical bar chart ──────────────────────────────────────────
            _chart_n = st.radio("Chart history (years)", [5, 10],
                                horizontal=True, key="stk_chart_yrs", index=1)
            _chart_yrs = sorted([y for y in NASS_YEARS if y <= stk_year][:_chart_n])

            with st.spinner("Building chart…"):
                _cyr_dfs = {y: _load_yr(y) for y in _chart_yrs}

            def _us_col(df, col):
                if df.empty or col not in df.columns: return 0
                return df[col].mean() if _is_ratio else df[col].sum()

            # Auto-scale helper for stocks charts
            def _stk_raw_max(*series):
                return max((v for s in series for v in s if v), default=0)

            if stk_view == "Total Stocks":
                _on_raw  = [_us_col(_cyr_dfs[y], "OnFarm")  for y in _chart_yrs]
                _off_raw = [_us_col(_cyr_dfs[y], "OffFarm") for y in _chart_yrs]
                _su, _sl = _auto_bu(_stk_raw_max(_on_raw, _off_raw))
                _chart_fig = build_history_bar(
                    {
                        "On-Farm":  [v / _su for v in _on_raw],
                        "Off-Farm": [v / _su for v in _off_raw],
                    },
                    _chart_yrs,
                    title=f"US {stk_crop} Total Stocks — {stk_period_lbl}",
                    y_label=_sl, stacked=True,
                )
            elif stk_view == "Total Supply":
                _sep_chart = {
                    y: load_grain_stocks(stk_crop, y, "FIRST OF SEP", _CACHE_VERSION)
                    for y in _chart_yrs
                }
                _sep_raw  = [(_sep_chart[y]["Total"].sum() if not _sep_chart[y].empty else 0) for y in _chart_yrs]
                _prod_raw = [_us_col(_cyr_dfs[y], "Production") for y in _chart_yrs]
                _su, _sl  = _auto_bu(_stk_raw_max(_sep_raw, _prod_raw))
                _chart_fig = build_history_bar(
                    {
                        "Sep 1 Stocks": [v / _su for v in _sep_raw],
                        "Production":   [v / _su for v in _prod_raw],
                    },
                    _chart_yrs,
                    title=f"US {stk_crop} Total Supply (Sep 1 Stocks + Production)",
                    y_label=_sl, stacked=True,
                )
            else:
                _y_raw  = [_us_col(_cyr_dfs[y], _col) for y in _chart_yrs]
                if _is_ratio:
                    _su, _sl = 1, "%"
                else:
                    _su, _sl = _auto_bu(_stk_raw_max(_y_raw))
                _chart_fig = build_history_bar(
                    {stk_view: [v / _su for v in _y_raw]},
                    _chart_yrs,
                    title=f"US {stk_crop} {stk_view} — {stk_period_lbl}",
                    y_label=_sl,
                )
            _add_logo(_chart_fig, logo_50yr)
            _chart(_chart_fig, use_container_width=True, key="stk_chart",
                            config={"displayModeBar": False})

            st.markdown(f"<hr style='border-color:{BORDER};margin:6px 0'>",
                        unsafe_allow_html=True)

            # ── Historical table ──────────────────────────────────────────────
            _hist_n_yrs = 6
            _hist_years = sorted([y for y in NASS_YEARS if y <= stk_year][:_hist_n_yrs])

            # Unit label for the table title
            _tbl_unit = "%" if _is_ratio else "M bu"

            st.markdown(
                f"<p style='color:{MUTED};font-size:0.82rem;font-weight:600;"
                f"margin:0 0 6px 0;letter-spacing:0.04em;'>"
                f"📅 HISTORICAL — {stk_crop} {stk_view} ({_tbl_unit}) | {_period_note} "
                f"({min(_hist_years)}–{max(_hist_years)})</p>",
                unsafe_allow_html=True,
            )

            with st.spinner("Loading historical data…"):
                _yr_dfs = {_hy: _load_yr(_hy) for _hy in _hist_years}

            # Build state × year dict for heatmap table
            _stk_state_yr: dict = {}
            _stk_us_tot:   dict = {}
            for _hy, _df_h in _yr_dfs.items():
                if _df_h.empty or _col not in _df_h.columns:
                    continue
                for _, _r in _df_h.iterrows():
                    _s = _r["State"]
                    if _s not in _stk_state_yr:
                        _stk_state_yr[_s] = {}
                    v = _r.get(_col)
                    _stk_state_yr[_s][_hy] = float(v) if pd.notna(v) and v else None
                _stk_us_tot[_hy] = float(
                    _df_h[_col].mean() if _is_ratio else _df_h[_col].sum()
                )

            _stk_htbl = build_heatmap_table(
                _stk_state_yr,
                _hist_years,
                title=f"{stk_crop} — {stk_view} ({_tbl_unit}) | {_period_note} "
                      f"({min(_hist_years)}–{max(_hist_years)})",
                unit=_tbl_unit,
                divisor=1 if _is_ratio else 1e6,   # raw bu → M bu; ratios stay as-is
                is_ratio=_is_ratio,
                us_totals=_stk_us_tot if not _is_ratio else None,
                regions=None,       # no crop-specific regions for stocks
                fmt=".1f",          # 1 decimal = nearest 100K bu in M bu units
            )
            _chart(_stk_htbl, use_container_width=True,
                            key="stk_heatmap_tbl",
                            config={"displayModeBar": False})
            st.caption(
                "Top 2 values per row = green  ·  Bottom 2 = red  ·  "
                "Total Supply = Sep 1 beginning stocks + crop year production. "
                "Source: USDA NASS Grain Stocks Survey and Crop Production (state level only)."
            )

            # ── Disappearance (Sep 1 beg stocks + production − Sep 1 end stocks) ──
            st.markdown(f"<hr style='border-color:{BORDER};margin:6px 0'>",
                        unsafe_allow_html=True)
            st.markdown(
                f"<p style='color:{MUTED};font-size:0.82rem;font-weight:600;"
                f"margin:0 0 4px 0;letter-spacing:0.04em;'>"
                f"📉 DISAPPEARANCE — {stk_crop} | Marketing Year (Sep 1 → Sep 1)</p>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<p style='color:{MUTED};font-size:0.79rem;margin:0 0 10px;'>"
                "Disappearance = Sep 1 Beginning Stocks + Production − Sep 1 Ending Stocks. "
                "Represents total domestic use + exports consumed from the marketing year's supply.</p>",
                unsafe_allow_html=True,
            )

            _is_wheat = (stk_crop == "Wheat")

            # Wheat uses June 1 marketing year with winter/spring production split.
            # Other crops use Sep 1 marketing year with total production.
            # 2026 is included: Sep 1 2026 not yet published, so it is estimated
            # from Jun 1 2026 stocks using avg historical seasonal draw-down.
            _disapp_years = sorted([y for y in NASS_YEARS if y >= 2015])

            _disapp_rows = []
            for _dy in _disapp_years:
                if _is_wheat:
                    _jun_dy  = load_grain_stocks("Wheat", _dy,     "FIRST OF JUN", _CACHE_VERSION)
                    _sep_dy  = load_grain_stocks("Wheat", _dy,     "FIRST OF SEP", _CACHE_VERSION)
                    _jun_dy1 = load_grain_stocks("Wheat", _dy + 1, "FIRST OF JUN", _CACHE_VERSION)
                    _beg_bu = float(_jun_dy["Total"].sum())  if not _jun_dy.empty  else None
                    _sep_bu = float(_sep_dy["Total"].sum())  if not _sep_dy.empty  else None
                    _end_bu = float(_jun_dy1["Total"].sum()) if not _jun_dy1.empty else None
                    _winter_bu = _load_wheat_class_prod(_dy, "WINTER",               _CACHE_VERSION)
                    _spring_bu = (_load_wheat_class_prod(_dy, "SPRING, (EXCL DURUM)", _CACHE_VERSION) +
                                  _load_wheat_class_prod(_dy, "SPRING, DURUM",        _CACHE_VERSION))
                    _total_prod = _winter_bu + _spring_bu
                    if not _beg_bu:
                        continue
                    _junaug_supply = _beg_bu + _winter_bu
                    _sep_is_est = False
                    if not _sep_bu:
                        # Sep 1 not yet published — estimate using avg historical Jun-Aug draw-down
                        _hist_jd = [r["junaug_disapp"] for r in _disapp_rows
                                    if r.get("junaug_disapp") and not r.get("is_est")]
                        if _hist_jd:
                            _est_jd  = sum(_hist_jd) / len(_hist_jd)
                            _sep_bu  = max(0, _junaug_supply - _est_jd)
                            _sep_is_est = True
                    _junaug_disapp = (_junaug_supply - _sep_bu) if _sep_bu else None
                    # For ending stocks (Jun 1 next year), try WASDE if NASS not yet published
                    _end_is_est = False
                    if not _end_bu:
                        _wasde_end = load_wasde_ending_stocks("Wheat", _dy, _CACHE_VERSION)
                        if _wasde_end:
                            _end_bu     = _wasde_end
                            _end_is_est = "W"
                    _annual_disapp = (_beg_bu + _total_prod - _end_bu) if _end_bu else None
                    _disapp_rows.append({
                        "year":          _dy,
                        "begin_bu":      _beg_bu,
                        "winter_bu":     _winter_bu,
                        "spring_bu":     _spring_bu,
                        "prod_bu":       _total_prod,
                        "junaug_supply": _junaug_supply,
                        "sep_bu":        _sep_bu,
                        "end_bu":        _end_bu,
                        "junaug_disapp": _junaug_disapp,
                        "disapp_bu":     _annual_disapp,
                        "is_est":        _sep_is_est or _end_is_est,
                        "sep_is_est":    _sep_is_est,
                        "end_is_est":    _end_is_est,
                    })
                else:
                    _sep_dy  = load_grain_stocks(stk_crop, _dy,     "FIRST OF SEP", _CACHE_VERSION)
                    _sep_dy1 = load_grain_stocks(stk_crop, _dy + 1, "FIRST OF SEP", _CACHE_VERSION)
                    _prod_dy = _load_state_for_stat(stk_crop, _dy, "production", _CACHE_VERSION)
                    _begin_bu = float(_sep_dy["Total"].sum())  if not _sep_dy.empty  else None
                    _end_bu   = float(_sep_dy1["Total"].sum()) if not _sep_dy1.empty else None
                    _prod_bu  = float(_prod_dy["Value"].sum()) if not _prod_dy.empty else None
                    if not _begin_bu or not _prod_bu:
                        continue
                    if _end_bu:
                        _disapp_rows.append({
                            "year":     _dy,
                            "begin_bu": _begin_bu,
                            "prod_bu":  _prod_bu,
                            "end_bu":   _end_bu,
                            "disapp_bu": _begin_bu + _prod_bu - _end_bu,
                            "is_est":   False,
                        })
                    else:
                        # Sep 1 end stocks not yet published.
                        # Try WASDE (FAS PSD) ending stocks for this marketing year first.
                        _wasde_end = load_wasde_ending_stocks(stk_crop, _dy, _CACHE_VERSION)
                        if _wasde_end:
                            _disapp_rows.append({
                                "year":     _dy,
                                "begin_bu": _begin_bu,
                                "prod_bu":  _prod_bu,
                                "end_bu":   _wasde_end,
                                "disapp_bu": _begin_bu + _prod_bu - _wasde_end,
                                "is_est":   "W",  # W = WASDE-sourced
                            })
                        else:
                            # Fall back: Jun 1 quarterly stocks × avg historical Sep/Jun ratio
                            _jun_cur = load_grain_stocks(stk_crop, _dy + 1, "FIRST OF JUN", _CACHE_VERSION)
                            _jun_bu  = float(_jun_cur["Total"].sum()) if not _jun_cur.empty else None
                            if not _jun_bu:
                                continue
                            _hist_ratios = [
                                r["end_bu"] / _load_yr_jun(stk_crop, r["year"] + 1, _CACHE_VERSION)
                                for r in _disapp_rows
                                if r.get("end_bu") and not r.get("is_est")
                                and _load_yr_jun(stk_crop, r["year"] + 1, _CACHE_VERSION)
                            ]
                            _sep_ratio = (sum(_hist_ratios) / len(_hist_ratios)) if _hist_ratios else 1.0
                            _est_end   = _jun_bu * _sep_ratio
                            _disapp_rows.append({
                                "year":     _dy,
                                "begin_bu": _begin_bu,
                                "prod_bu":  _prod_bu,
                                "end_bu":   _est_end,
                                "disapp_bu": _begin_bu + _prod_bu - _est_end,
                                "is_est":   True,
                            })

            _plot_rows = ([r for r in _disapp_rows if r.get("disapp_bu") is not None] if not _is_wheat
                          else [r for r in _disapp_rows if r["begin_bu"]])

            if _plot_rows:
                _dd = pd.DataFrame(_plot_rows)

                # For wheat: always use junaug_disapp for bars (always computed, even for
                # the current year) so 2026 never drops out. Show full-year disapp as a
                # separate line overlay when available.
                # For other crops: use disapp_bu; drop rows where it's None.
                if _is_wheat:
                    _dd_chart = _dd.dropna(subset=["junaug_disapp"])
                    _ref_col  = "junaug_disapp"
                else:
                    _dd_chart = _dd.dropna(subset=["disapp_bu"])
                    _ref_col  = "disapp_bu"

                _su_d, _sl_d = (_auto_bu(_dd_chart[_ref_col].abs().max())
                                if not _dd_chart.empty else (1e6, "M bu"))

                fig_disapp = go.Figure()
                if not _dd_chart.empty:
                    _bar_colors = [ACCENT if y == _dd_chart["year"].max() else "#64748b"
                                   for y in _dd_chart["year"]]
                    # Hatched (lighter) fill for estimated bars
                    _bar_opacity = [0.55 if r else 1.0
                                    for r in _dd_chart["is_est"].tolist()]
                    fig_disapp.add_trace(go.Bar(
                        x=_dd_chart["year"].astype(str),
                        y=_dd_chart[_ref_col] / _su_d,
                        marker_color=_bar_colors,
                        opacity=1.0,
                        marker_opacity=_bar_opacity,
                        name="Jun–Aug Disappearance" if _is_wheat else "Disappearance",
                        hovertemplate=("%{y:.2f} " + _sl_d +
                                       " (est.)<extra></extra>" if _is_wheat else
                                       "%{y:.2f} " + _sl_d + "<extra></extra>"),
                    ))

                # Full-year disappearance overlay for wheat (only years where it's available)
                if _is_wheat:
                    _dd_fy = _dd.dropna(subset=["disapp_bu"])
                    if not _dd_fy.empty:
                        fig_disapp.add_trace(go.Scatter(
                            x=_dd_fy["year"].astype(str),
                            y=_dd_fy["disapp_bu"] / _su_d,
                            mode="lines+markers",
                            line=dict(color="#e879f9", width=2),
                            marker=dict(color="#e879f9", size=7, symbol="diamond"),
                            name="Full-Year Disappearance",
                            hovertemplate="Full-Yr Disapp: %{y:.2f} " + _sl_d + "<extra></extra>",
                        ))

                fig_disapp.add_trace(go.Scatter(
                    x=_dd["year"].astype(str),
                    y=_dd["prod_bu"] / _su_d,
                    mode="lines+markers",
                    line=dict(color="#f59e0b", width=2, dash="dot"),
                    marker=dict(color="#f59e0b", size=6),
                    name="Total Production",
                    hovertemplate="Production: %{y:.2f} " + _sl_d + "<extra></extra>",
                ))
                if _is_wheat:
                    fig_disapp.add_trace(go.Scatter(
                        x=_dd["year"].astype(str),
                        y=_dd["winter_bu"] / _su_d,
                        mode="lines+markers",
                        line=dict(color="#e0b800", width=1.5, dash="dot"),
                        marker=dict(color="#e0b800", size=5),
                        name="Winter Wheat (Jun–Aug)",
                        hovertemplate="Winter Wheat: %{y:.2f} " + _sl_d + "<extra></extra>",
                    ))
                    fig_disapp.add_trace(go.Scatter(
                        x=_dd["year"].astype(str),
                        y=_dd["spring_bu"] / _su_d,
                        mode="lines+markers",
                        line=dict(color="#34d399", width=1.5, dash="dot"),
                        marker=dict(color="#34d399", size=5),
                        name="Spring+Durum (Aug–Sep)",
                        hovertemplate="Spring/Durum: %{y:.2f} " + _sl_d + "<extra></extra>",
                    ))
                    fig_disapp.add_trace(go.Scatter(
                        x=_dd["year"].astype(str),
                        y=_dd["junaug_supply"] / _su_d,
                        mode="lines+markers",
                        line=dict(color="#60a5fa", width=2, dash="dash"),
                        marker=dict(color="#60a5fa", size=6),
                        name="Jun–Aug Supply (Jun 1 + Winter)",
                        hovertemplate="Jun–Aug Supply: %{y:.2f} " + _sl_d + "<extra></extra>",
                    ))
                else:
                    fig_disapp.add_trace(go.Scatter(
                        x=_dd["year"].astype(str),
                        y=(_dd["begin_bu"] + _dd["prod_bu"]) / _su_d,
                        mode="lines+markers",
                        line=dict(color="#60a5fa", width=1.5, dash="dash"),
                        marker=dict(color="#60a5fa", size=5),
                        name="Total Supply",
                        hovertemplate="Total Supply: %{y:.2f} " + _sl_d + "<extra></extra>",
                    ))
                fig_disapp.update_layout(
                    paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                    font=dict(color=TEXT, family="Arial"),
                    margin=dict(l=60, r=20, t=30, b=50),
                    height=340,
                    showlegend=True,
                    legend=dict(font=dict(color=TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                                orientation="h", yanchor="bottom", y=1.02, x=0),
                    xaxis=dict(title="Marketing Year Start", tickfont=dict(color=TEXT),
                               gridcolor=BORDER),
                    yaxis=dict(title=_sl_d, gridcolor=BORDER,
                               tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
                    hovermode="x unified",
                )
                _add_logo(fig_disapp, logo_50yr)
                _chart(fig_disapp, use_container_width=True, key="stk_disapp_chart",
                       config={"displayModeBar": False})

                def _efmt(v, is_est, fmt="{:,.0f}"):
                    """Format a bushel value; append source tag if estimated."""
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        return "—"
                    s = fmt.format(v / 1e6)
                    if is_est == "W":
                        return s + " (W)"   # WASDE-sourced estimate
                    return s + " (E)" if is_est else s

                def _est_tag(flag):
                    if flag == "W": return " (W)"
                    if flag:        return " (E)"
                    return ""

                if _is_wheat:
                    _dd_disp = pd.DataFrame({
                        "Mkt Year":              _dd.apply(lambda r: f"Jun {r['year']}–{r['year']+1}" + _est_tag(r["is_est"]), axis=1),
                        "Jun 1 Beg (M bu)":      (_dd["begin_bu"]      / 1e6).map(lambda v: f"{v:,.0f}"),
                        "Winter Wheat (M bu)":   (_dd["winter_bu"]     / 1e6).map(lambda v: f"{v:,.0f}" if v else "—"),
                        "Jun–Aug Supply (M bu)": (_dd["junaug_supply"] / 1e6).map(lambda v: f"{v:,.0f}"),
                        "Sep 1 Stocks (M bu)":   _dd.apply(lambda r: _efmt(r["sep_bu"],        r.get("sep_is_est") or r["is_est"]), axis=1),
                        "Jun–Aug Disapp (M bu)": _dd.apply(lambda r: _efmt(r["junaug_disapp"], r.get("sep_is_est") or r["is_est"]), axis=1),
                        "Spring+Durum (M bu)":   (_dd["spring_bu"]     / 1e6).map(lambda v: f"{v:,.0f}" if v else "—"),
                        "Total Prod (M bu)":     (_dd["prod_bu"]       / 1e6).map(lambda v: f"{v:,.0f}"),
                        "Jun 1 End (M bu)":      _dd.apply(lambda r: _efmt(r["end_bu"],    r.get("end_is_est", False)), axis=1),
                        "Full-Yr Disapp (M bu)": _dd.apply(lambda r: _efmt(r["disapp_bu"], r["is_est"]), axis=1),
                    })
                    st.dataframe(_dd_disp, use_container_width=True, hide_index=True)
                    st.caption(
                        "Wheat marketing year: Jun 1 → May 31.  "
                        "Jun–Aug Supply = Jun 1 Old Crop Stocks + Winter Wheat Production (harvested Jun–Jul).  "
                        "Sep 1 (E) = avg historical Jun–Aug draw-down applied when formal report not yet published.  "
                        "Jun 1 End (W) = USDA WASDE/FAS PSD ending stocks forecast when NASS not yet published.  "
                        "(E) = model estimate · (W) = WASDE forecast — replaced automatically once NASS publishes.  "
                        "Source: USDA NASS Grain Stocks, Crop Production, and USDA FAS PSD (WASDE)."
                    )
                else:
                    _dd_disp = pd.DataFrame({
                        "Mkt Year":            _dd.apply(lambda r: f"Sep {r['year']}–{r['year']+1}" + _est_tag(r["is_est"]), axis=1),
                        "Beg Stocks (M bu)":   (_dd["begin_bu"] / 1e6).map(lambda v: f"{v:,.0f}"),
                        "Production (M bu)":   (_dd["prod_bu"]  / 1e6).map(lambda v: f"{v:,.0f}"),
                        "Total Supply (M bu)": ((_dd["begin_bu"]+_dd["prod_bu"])/1e6).map(lambda v: f"{v:,.0f}"),
                        "End Stocks (M bu)":   _dd.apply(lambda r: _efmt(r["end_bu"],    r["is_est"]), axis=1),
                        "Disappearance (M bu)":_dd.apply(lambda r: _efmt(r["disapp_bu"], r["is_est"]), axis=1),
                        "Disapp % of Supply":  _dd.apply(
                            lambda r: (f"{r['disapp_bu']/(r['begin_bu']+r['prod_bu'])*100:.1f}%" + _est_tag(r["is_est"]))
                            if r.get("disapp_bu") else "—", axis=1),
                    })
                    st.dataframe(_dd_disp, use_container_width=True, hide_index=True)
                    st.caption(
                        "Marketing year Sep 1–Aug 31.  "
                        "End Stocks (W) = USDA WASDE/FAS PSD ending stocks forecast for the 25/26 marketing year.  "
                        "End Stocks (E) = Jun 1 quarterly stocks × avg historical Sep/Jun ratio (fallback when WASDE unavailable).  "
                        "(W) = WASDE forecast · (E) = model estimate — replaced automatically once NASS Sep 1 publishes.  "
                        "Source: USDA NASS Grain Stocks, Crop Production, and USDA FAS PSD (WASDE)."
                    )
            else:
                st.info("Insufficient stocks data to compute disappearance for this crop/year range.")

    # ══════════════════════════════════════════════════════════════════════════
    # ACREAGE SUMMARY TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_acreage:
        # ── Controls ──────────────────────────────────────────────────────────
        _ac1, _ac2, _ac3 = st.columns([1.5, 1, 2.5])
        with _ac1:
            _acr_states = ["National"] + sorted(ABBR_TO_NAME.keys())
            _acr_state  = st.selectbox(
                "Geography", _acr_states, key="acr_state",
                format_func=lambda x: x if x == "National"
                             else f"{x}  —  {ABBR_TO_NAME.get(x, x)}",
            )
        with _ac2:
            _acr_n_yrs = st.radio(
                "Years shown", [10, 15, 20],
                horizontal=True, key="acr_n_yrs", index=1,
            )

        _acr_state_abbr = "" if _acr_state == "National" else _acr_state
        _acr_end_yr   = max(NASS_YEARS[-1], FORECAST_YEAR)
        _acr_start_yr = _acr_end_yr - _acr_n_yrs + 1
        _acr_cal_yrs  = list(range(_acr_start_yr, _acr_end_yr + 1))

        def _mkt_yr(y: int) -> str:
            return f"{str(y)[2:]}-{str(y+1)[2:]}"

        # ── Fetch all crops ───────────────────────────────────────────────────
        with st.spinner("Loading acreage data…"):
            _acr: dict = {}   # {crop_key: {yr: mil_ac}}
            _pp:  dict = {}   # {crop_key: {yr: mil_ac}}
            for _ck, _cp in _ACR_PARAMS.items():
                _acr[_ck] = load_acreage_crop_hist(
                    _ck, _cp, _acr_start_yr, _acr_end_yr,
                    _acr_state_abbr, False, _CACHE_VERSION
                )
                if _ck in _ACR_PP_CROPS:
                    _pp[_ck] = load_acreage_crop_hist(
                        _ck, _cp, _acr_start_yr, _acr_end_yr,
                        _acr_state_abbr, True, _CACHE_VERSION
                    )

        # ── RMA prevent-plant lookup (NASS QuickStats lacks PP data) ────────────
        _rma_pp_by_yr: dict = {}   # {yr: {crop_key: mil_ac}}
        for _rma_crop, _pp_ck in [("Corn","Corn"),("Soybeans","Soybeans"),("Wheat","Wheat")]:
            if _rma_crop not in rma_data:
                continue
            _rdf = rma_data[_rma_crop].copy()
            if "Prev Plant Acres" not in _rdf.columns or "Yield Year" not in _rdf.columns:
                continue
            if _acr_state_abbr:
                _rdf = _rdf[_rdf["State"] == _acr_state_abbr]
            _rdf["_yr"] = pd.to_numeric(_rdf["Yield Year"], errors="coerce")
            _rdf["_ppv"] = pd.to_numeric(_rdf["Prev Plant Acres"], errors="coerce").fillna(0)
            for _ryr, _rgrp in _rdf.groupby("_yr"):
                _rma_pp_by_yr.setdefault(int(_ryr), {})[_pp_ck] = float(_rgrp["_ppv"].sum()) / 1e6

        # ── Build rows ────────────────────────────────────────────────────────
        def _g(crop, yr):
            """Safe get — mil ac or None."""
            return _acr.get(crop, {}).get(yr)

        def _gp(crop, yr):
            return _pp.get(crop, {}).get(yr)

        def _s(*vals):
            """Sum non-None values, return None if all None."""
            vs = [v for v in vals if v is not None]
            return sum(vs) if vs else None

        _crp_key = "US" if not _acr_state_abbr else _acr_state_abbr
        _crp_src = _CRP_DATA.get(_crp_key, {})

        _acr_rows = []
        for _yr in _acr_cal_yrs:
            _wh  = _g("Wheat",     _yr)
            _co  = _g("Corn",      _yr)
            _sg  = _g("Sorghum",   _yr)
            _ba  = _g("Barley",    _yr)
            _oa  = _g("Oats",      _yr)
            _sy  = _g("Soybeans",  _yr)
            _su  = _g("Sunflowers",_yr)
            _ca  = _g("Canola",    _yr)
            _ct  = _g("Cotton",    _yr)
            _ri  = _g("Rice",      _yr)
            _pe  = _g("Peanuts",   _yr)
            _sb  = _g("SugarBeets",_yr)
            _db  = _g("DryBeans",  _yr)
            _ha  = _g("Hay",       _yr)
            _fg  = _s(_co, _sg, _ba, _oa)
            _os  = _s(_sy, _su, _ca)
            _csoy= _s(_co, _sy)
            _tmaj= _s(_wh, _fg, _os, _ct)
            _oth = _s(_ri, _pe, _sb, _db)
            _hayo= _s(_ha, _oth)
            _prin= _s(_tmaj, _hayo)
            # PP — sourced from RMA Excel (Corn/Soy/Wht only; NASS lacks PP)
            _pp_yr_d = _rma_pp_by_yr.get(_yr, {})
            _pp_co  = _gp("Corn",     _yr) or _pp_yr_d.get("Corn")
            _pp_sy  = _gp("Soybeans", _yr) or _pp_yr_d.get("Soybeans")
            _pp_wh  = _gp("Wheat",    _yr) or _pp_yr_d.get("Wheat")
            _pp_maj = _s(_pp_co, _pp_sy, _pp_wh)
            _pp_oth_vals = [_gp(k, _yr) for k in _ACR_PP_CROPS
                            if k not in ("Corn","Soybeans","Wheat")]
            _pp_oth = _s(*_pp_oth_vals)
            _pp_tot = _s(_pp_maj, _pp_oth)
            _crp = _crp_src.get(_yr)
            _acr_rows.append({
                "mkt_yr":     _mkt_yr(_yr),
                "Wheat":      _wh,
                "Corn":       _co,
                "Sorghum":    _sg,
                "Barley":     _ba,
                "Oats":       _oa,
                "FG_Total":   _fg,
                "Soybeans":   _sy,
                "Sunflowers": _su,
                "Canola":     _ca,
                "OS_Total":   _os,
                "CornSoy":    _csoy,
                "Cotton":     _ct,
                "Total_Major":_tmaj,
                "HayOther":   _hayo,
                "Principal":  _prin,
                "CRP":        _crp,
                "TotalCRP":   _s(_prin, _crp),
                "PP_Major":   _pp_maj,
                "PP_Other":   _pp_oth,
                "PP_Total":   _pp_tot,
            })

        # ── Render ────────────────────────────────────────────────────────────
        _scope_lbl = "National" if not _acr_state_abbr else f"{_acr_state_abbr}  —  {ABBR_TO_NAME.get(_acr_state_abbr,'')}"
        _tbl_title = f"{_mkt_yr(_acr_start_yr)} to {_mkt_yr(_acr_end_yr)}"

        # Auto-scale: pick unit based on the largest individual crop value
        _INDV_KEYS = ("Corn","Soybeans","Wheat","Cotton","Sorghum","Barley","Oats","Sunflowers","Canola")
        _max_indv = max((row.get(k) or 0.0 for row in _acr_rows for k in _INDV_KEYS), default=0.0)
        if _max_indv >= 2.0:
            _tbl_mul, _tbl_unit = 1.0, "mil ac"
        elif _max_indv >= 0.002:
            _tbl_mul, _tbl_unit = 1000.0, "K ac"
        else:
            _tbl_mul, _tbl_unit = 1_000_000.0, "ac"

        _html = _render_acreage_html(
            _acr_rows, _acr_cal_yrs, _tbl_title, _scope_lbl,
            unit_mul=_tbl_mul, unit_lbl=_tbl_unit,
        )
        st.markdown(_html, unsafe_allow_html=True)

        st.markdown(
            f"<p style='font-size:0.72rem;color:{MUTED};margin-top:8px;'>"
            "Planted acres: USDA NASS QuickStats. "
            "Prevent Plant (Cn/Soy/Wht only): USDA RMA Summary of Business — years available in RMA Excel. "
            "CRP: USDA FSA CRPHistoryState86-25 (fiscal year = planting calendar year), million acres."
            "</p>",
            unsafe_allow_html=True,
        )

        # ── Acreage Trend Chart ───────────────────────────────────────────────
        st.markdown(
            f"<div style='margin-top:28px;'>"
            f"<h4 style='color:{ACCENT};margin:0 0 2px 0;font-size:1rem;font-weight:600;'>"
            "Planted Acres Trend</h4>"
            f"<p style='color:{MUTED};font-size:0.78rem;margin:0 0 12px 0;'>"
            "Select one or more crops / aggregates to compare over time.</p></div>",
            unsafe_allow_html=True,
        )

        _CHART_COLS: dict = {
            "Corn":               "Corn",
            "Soybeans":           "Soybeans",
            "All Wheat":          "Wheat",
            "Sorghum":            "Sorghum",
            "Barley":             "Barley",
            "Oats":               "Oats",
            "Sunflowers":         "Sunflowers",
            "Canola":             "Canola",
            "Cotton":             "Cotton",
            "Feedgrains Total":   "FG_Total",
            "Oilseeds Total":     "OS_Total",
            "Corn + Soy":         "CornSoy",
            "Hay & Other":        "HayOther",
            "Total Major Crops":  "Total_Major",
            "Principal Crops":    "Principal",
            "CRP":                "CRP",
            "Total w/CRP":        "TotalCRP",
            "Prevent Plant Total":"PP_Total",
        }
        # Categorical palette — JPSI blue anchors position 1, then colorblind-safe spread
        _CHART_COLORS = [
            "#0693e3","#f97316","#22c55e","#a855f7",
            "#ef4444","#ca8a04","#06b6d4","#ec4899",
            "#84cc16","#8b5cf6",
        ]

        _acr_chart_sel = st.multiselect(
            "Commodities",
            options=list(_CHART_COLS.keys()),
            default=["Corn", "Soybeans", "All Wheat", "CRP"],
            key="acr_chart_sel",
            label_visibility="collapsed",
        )

        if _acr_chart_sel and _acr_rows:
            _cfig = go.Figure()
            _x_labs = [row["mkt_yr"] for row in _acr_rows]
            for _ci, _clbl in enumerate(_acr_chart_sel):
                _ckey = _CHART_COLS[_clbl]
                _ys   = [row.get(_ckey) for row in _acr_rows]
                _x_pt = [_x_labs[i] for i, y in enumerate(_ys) if y is not None]
                _y_pt = [y for y in _ys if y is not None]
                _cc   = _CHART_COLORS[_ci % len(_CHART_COLORS)]
                _cfig.add_trace(go.Scatter(
                    x=_x_pt, y=_y_pt,
                    mode="lines+markers",
                    name=_clbl,
                    line=dict(color=_cc, width=2),
                    marker=dict(color=_cc, size=5,
                                line=dict(color="#ffffff", width=1)),
                    hovertemplate=(
                        f"<b>{_clbl}</b><br>"
                        "%{x}: %{y:.2f}M ac<extra></extra>"
                    ),
                ))
            _cfig.update_layout(
                yaxis_title="Million Acres",
                xaxis_title="Marketing Year",
                hovermode="x unified",
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1, font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)",
                ),
                height=440,
                margin=dict(l=60, r=20, t=56, b=80),
                plot_bgcolor=PANEL,
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color=TEXT, family="sans-serif", size=12),
                xaxis=dict(
                    tickangle=-45, showgrid=False,
                    linecolor=BORDER, tickfont=dict(size=10),
                    zeroline=False,
                ),
                yaxis=dict(
                    gridcolor=BORDER, gridwidth=1,
                    zeroline=False, tickfont=dict(size=10),
                ),
            )
            _chart(_cfig, use_container_width=True)

    # ══════════════════════════════════════════════════════════════════════════
    # LIVESTOCK INVENTORY TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_livestock:
        st.markdown(
            f"<p style='font-size:0.8rem;color:{MUTED};margin:0 0 10px 0;'>"
            "USDA NASS QuickStats — annual livestock inventory surveys. "
            "County and ASD coverage varies by species and year; NASS withholds "
            "data when disclosure thresholds apply.</p>",
            unsafe_allow_html=True,
        )
        # ── Controls ──────────────────────────────────────────────────────────
        _lv_c1, _lv_c2, _lv_c3, _lv_c4 = st.columns([2.2, 1.2, 1.5, 2.1])
        with _lv_c1:
            _lv_species = st.selectbox(
                "Species", list(_LIVESTOCK_SPECIES.keys()), key="lv_species"
            )
        with _lv_c2:
            _lv_year = st.selectbox("Year", _LIVESTOCK_YEARS, key="lv_year")
        with _lv_c3:
            _lv_drill = st.selectbox(
                "Drill-down level", ["County", "ASD District"], key="lv_drill"
            )
        with _lv_c4:
            _lv_state_sel = st.selectbox(
                "State",
                ["— National —"] + sorted(ABBR_TO_NAME.keys()),
                key="lv_state",
                format_func=lambda x: x if x.startswith("—")
                            else f"{x} — {ABBR_TO_NAME.get(x, '')}",
            )
        _lv_state_abbr = None if _lv_state_sel.startswith("—") else _lv_state_sel
        _lv_base_unit  = _LIVESTOCK_UNIT.get(_lv_species, "head")

        # ── National / state-level choropleth ─────────────────────────────────
        with st.spinner(f"Loading {_lv_species} state inventory..."):
            _lv_st_df = load_livestock(
                "STATE", _lv_species, _lv_year, cache_ver=_CACHE_VERSION
            )

        if _lv_st_df.empty:
            _lv_note = livestock_coverage_note(_lv_species, "STATE", _lv_year)
            st.warning(_lv_note or (
                f"No {_lv_species} inventory returned for {_lv_year}. "
                "Try an earlier year."
            ))
        else:
            _lv_st_agg = (
                _lv_st_df
                .groupby("state_alpha", as_index=False)["Value"].sum()
                .rename(columns={"state_alpha": "State"})
            )
            _lv_st_agg["StateName"] = _lv_st_agg["State"].map(ABBR_TO_NAME)
            _lv_st_agg = _lv_st_agg.dropna(subset=["StateName"])
            _lv_smx = _lv_st_agg["Value"].max()
            _lv_sdiv, _lv_sunit = _lv_auto_scale(_lv_smx, _lv_base_unit)
            _lv_st_agg["Display"] = _lv_st_agg["Value"] / _lv_sdiv

            # state centroid lookup for data labels
            _LV_STATE_CTR = {
                'AL':(32.79,-86.83),'AK':(64.20,-153.37),'AZ':(34.30,-111.09),
                'AR':(34.75,-92.13),'CA':(36.78,-119.42),'CO':(39.55,-105.78),
                'CT':(41.60,-72.70),'DE':(39.15,-75.40),'FL':(27.77,-81.69),
                'GA':(32.68,-83.44),'HI':(20.90,-157.40),'ID':(44.07,-114.74),
                'IL':(40.35,-88.99),'IN':(39.85,-86.26),'IA':(42.01,-93.21),
                'KS':(38.53,-96.73),'KY':(37.67,-84.67),'LA':(31.17,-91.87),
                'ME':(44.69,-69.38),'MD':(38.97,-76.69),'MA':(42.23,-71.53),
                'MI':(43.33,-84.54),'MN':(46.28,-94.31),'MS':(32.74,-89.67),
                'MO':(38.46,-92.29),'MT':(46.88,-110.36),'NE':(41.49,-99.90),
                'NV':(39.32,-116.63),'NH':(43.45,-71.58),'NJ':(40.03,-74.52),
                'NM':(34.84,-106.25),'NY':(42.77,-75.49),'NC':(35.54,-79.39),
                'ND':(47.45,-100.47),'OH':(40.42,-82.79),'OK':(35.59,-96.93),
                'OR':(44.57,-122.07),'PA':(40.60,-77.21),'RI':(41.68,-71.51),
                'SC':(33.84,-80.90),'SD':(44.37,-100.35),'TN':(35.85,-86.35),
                'TX':(31.05,-97.56),'UT':(39.32,-111.09),'VT':(44.07,-72.67),
                'VA':(37.43,-78.66),'WA':(47.38,-120.44),'WV':(38.65,-80.62),
                'WI':(44.27,-89.62),'WY':(42.96,-107.55),
            }

            _lv_us_fig = px.choropleth(
                _lv_st_agg,
                locations="State", locationmode="USA-states",
                color="Display", scope="usa",
                color_continuous_scale="YlOrRd",
                hover_name="StateName",
                hover_data={"Display": ":.2f", "State": False},
                labels={"Display": f"{_lv_species} ({_lv_sunit})"},
            )

            # build label text: "ST\nXXX K" (abbr + formatted value)
            def _lv_fmt(v):
                if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
                if v >= 1_000: return f"{v/1_000:.0f}K"
                return f"{v:.0f}"

            _lbl_rows = _lv_st_agg[_lv_st_agg["State"].isin(_LV_STATE_CTR)].copy()
            _lv_us_fig.add_trace(go.Scattergeo(
                lat=[_LV_STATE_CTR[s][0] for s in _lbl_rows["State"]],
                lon=[_LV_STATE_CTR[s][1] for s in _lbl_rows["State"]],
                text=[
                    f"<b>{s}</b><br>{_lv_fmt(v)}"
                    for s, v in zip(_lbl_rows["State"], _lbl_rows["Value"])
                ],
                mode="text",
                textfont=dict(size=9, color="#1e2e22", family="Arial Black"),
                hoverinfo="skip",
                showlegend=False,
            ))

            _lv_us_fig.update_layout(
                height=420,
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor=PANEL,
                geo=dict(
                    bgcolor=DARK, landcolor=LAND,
                    showlakes=True, lakecolor="#aacdd4",
                    showframe=False, scope="usa",
                ),
                coloraxis_colorbar=dict(
                    title=dict(text=_lv_sunit, font=dict(size=11, color=TEXT)),
                    thickness=12, len=0.6,
                    tickfont=dict(size=10, color=TEXT),
                ),
            )
            _lv_us_fig.update_traces(
                marker_line_color=BORDER, marker_line_width=0.5,
                selector=dict(type="choropleth"),
            )
            _chart(_lv_us_fig, use_container_width=True, key="lv_us_map")

        # ── State drill-down ──────────────────────────────────────────────────
        if _lv_state_abbr:
            _lv_state_name = ABBR_TO_NAME.get(_lv_state_abbr, _lv_state_abbr)
            _lv_detail_lbl = "County" if _lv_drill == "County" else "ASD District"
            st.markdown(
                f"<h4 style='color:{ACCENT};margin:16px 0 4px 0;"
                f"font-size:1rem;font-weight:600;'>"
                f"{_lv_species} — {_lv_state_name} {_lv_detail_lbl} Detail</h4>",
                unsafe_allow_html=True,
            )

            if _lv_drill == "County":
                # ── County choropleth ────────────────────────────────────────
                if _lv_species in _LIVESTOCK_POULTRY:
                    st.info(
                        "County-level data for this series is limited or unavailable. "
                        "NASS discontinued annual county poultry estimates starting 2024; "
                        "milk production is published at state level only. "
                        "Historical county data may appear below where NASS has it; "
                        "the Census of Agriculture (2017, 2022) is the most complete "
                        "county-level source."
                    )
                with st.spinner("Loading county data..."):
                    _lv_co_df = load_livestock(
                        "COUNTY", _lv_species, _lv_year,
                        state_alpha=_lv_state_abbr, cache_ver=_CACHE_VERSION
                    )
                if _lv_co_df.empty:
                    _lv_co_note = livestock_coverage_note(
                        _lv_species, "COUNTY", _lv_year)
                    st.info(_lv_co_note or (
                        f"No county-level data for {_lv_species} in "
                        f"{_lv_state_abbr} ({_lv_year}). NASS may have withheld "
                        "values due to disclosure rules, or this species/year "
                        "combination was not surveyed at the county level."
                    ))
                else:
                    _lv_co_df["fips"] = (
                        _lv_co_df["state_fips_code"].astype(str).str.zfill(2)
                        + _lv_co_df["county_ansi"].astype(str).str.zfill(3)
                    )
                    _lv_co_agg = (
                        _lv_co_df.dropna(subset=["county_name"])
                        .groupby(["fips", "county_name"], as_index=False)["Value"].sum()
                        .rename(columns={"county_name": "County"})
                    )
                    # Drop state-level aggregate rows (county_ansi 998/999)
                    _lv_co_agg = _lv_co_agg[
                        ~_lv_co_agg["fips"].str[-3:].isin(["998", "999"])
                    ]
                    if _lv_co_agg.empty:
                        st.info("Returned rows are state-level aggregates only. "
                                "No county detail available for this selection.")
                    else:
                        _lv_comx = _lv_co_agg["Value"].max()
                        _lv_cdiv, _lv_cunit = _lv_auto_scale(_lv_comx, _lv_base_unit)
                        _lv_co_agg["Display"] = _lv_co_agg["Value"] / _lv_cdiv

                        _lv_sfips = STATE_FIPS_ALL.get(_lv_state_abbr, "")
                        _lv_st_feats = [
                            f for f in geo["features"]
                            if f["properties"]["STATE"] == _lv_sfips
                        ]
                        _lv_all_fips = [
                            f["properties"]["STATE"] + f["properties"]["COUNTY"]
                            for f in _lv_st_feats
                        ]
                        _lv_st_geo = {
                            "type": "FeatureCollection", "features": _lv_st_feats
                        }
                        _lv_co_line = dict(color=BORDER, width=0.4)
                        _lv_cz = _lv_co_agg["Display"].tolist()

                        _lv_co_fig = go.Figure()
                        _lv_co_fig.add_trace(go.Choropleth(
                            geojson=_lv_st_geo, featureidkey="id",
                            locations=_lv_all_fips,
                            z=[0] * len(_lv_all_fips),
                            colorscale=[[0, PANEL], [1, PANEL]],
                            showscale=False,
                            marker=dict(line=_lv_co_line),
                            hoverinfo="skip",
                        ))
                        _lv_co_fig.add_trace(go.Choropleth(
                            geojson=_lv_st_geo, featureidkey="id",
                            locations=_lv_co_agg["fips"].tolist(),
                            z=_lv_cz,
                            colorscale="YlOrRd",
                            zmin=0, zmax=max(_lv_cz) if _lv_cz else 1,
                            colorbar=dict(
                                title=dict(text=_lv_cunit,
                                           font=dict(size=11, color=TEXT)),
                                thickness=12, len=0.6,
                                tickfont=dict(size=10, color=TEXT),
                            ),
                            marker=dict(line=_lv_co_line),
                            text=_lv_co_agg["County"].str.title().tolist(),
                            hovertemplate=(
                                "<b>%{text} County</b><br>"
                                "%{z:,.1f} " + _lv_cunit + "<extra></extra>"
                            ),
                        ))
                        _lv_co_fig.update_layout(
                            height=460,
                            margin=dict(l=0, r=0, t=10, b=0),
                            paper_bgcolor=PANEL,
                            geo=dict(
                                bgcolor=PANEL, showframe=False,
                                fitbounds="locations", resolution=50,
                            ),
                        )
                        _chart(_lv_co_fig, use_container_width=True,
                                        key="lv_co_map")

                        # Ranked table
                        _lv_co_tbl = (
                            _lv_co_agg.sort_values("Value", ascending=False)
                            [["County", "Display"]].copy()
                            .rename(columns={"Display": f"Inventory ({_lv_cunit})"})
                        )
                        _lv_co_tbl["County"] = _lv_co_tbl["County"].str.title()
                        _lv_co_tbl[f"Inventory ({_lv_cunit})"] = (
                            _lv_co_tbl[f"Inventory ({_lv_cunit})"]
                            .map(lambda x: f"{x:,.1f}")
                        )
                        st.dataframe(_lv_co_tbl, hide_index=True,
                                     use_container_width=True)

                # ── County historical trend ───────────────────────────────
                st.markdown(
                    f"<h5 style='color:{ACCENT};margin:20px 0 4px 0;"
                    "font-size:0.9rem;font-weight:600;'>"
                    "County Historical Trend</h5>",
                    unsafe_allow_html=True,
                )
                with st.spinner("Loading county history..."):
                    _lv_co_hist = load_livestock_county_hist(
                        _lv_species, _lv_state_abbr, cache_ver=_CACHE_VERSION
                    )
                if _lv_co_hist.empty or "county_name" not in _lv_co_hist.columns:
                    st.info("No multi-year county history available for this selection.")
                else:
                    _lv_ch = _lv_co_hist.copy()
                    _lv_ch["fips"] = (
                        _lv_ch["state_fips_code"].astype(str).str.zfill(2)
                        + _lv_ch["county_ansi"].astype(str).str.zfill(3)
                    )
                    _lv_ch = _lv_ch[~_lv_ch["fips"].str[-3:].isin(["998", "999"])]
                    _lv_ch = _lv_ch.dropna(subset=["county_name"])
                    _lv_ch_agg = (
                        _lv_ch.groupby(["year", "county_name"], as_index=False)["Value"].sum()
                    )
                    _lv_ch_agg["county_name"] = _lv_ch_agg["county_name"].str.title()
                    _lv_ch_agg["year"] = _lv_ch_agg["year"].astype(int)

                    _lv_counties = sorted(_lv_ch_agg["county_name"].unique().tolist())
                    # Default to the top county from the current-year snapshot
                    _lv_def_co = _lv_counties[0] if _lv_counties else None
                    if not _lv_co_agg.empty:
                        _top_co = (
                            _lv_co_agg.sort_values("Value", ascending=False)
                            ["County"].str.title().iloc[0]
                        )
                        if _top_co in _lv_counties:
                            _lv_def_co = _top_co

                    _lv_sel_co = st.selectbox(
                        "Select county",
                        _lv_counties,
                        index=_lv_counties.index(_lv_def_co) if _lv_def_co in _lv_counties else 0,
                        key="lv_county_hist_sel",
                    )

                    _lv_co_ts = (
                        _lv_ch_agg[_lv_ch_agg["county_name"] == _lv_sel_co]
                        .sort_values("year")
                    )
                    if not _lv_co_ts.empty:
                        _lv_ch_div, _lv_ch_unit = _lv_auto_scale(
                            _lv_co_ts["Value"].max(), _lv_base_unit
                        )
                        _lv_co_ts = _lv_co_ts.copy()
                        _lv_co_ts["Display"] = _lv_co_ts["Value"] / _lv_ch_div

                        _lv_ch_fig = go.Figure()
                        _lv_ch_fig.add_trace(go.Scatter(
                            x=_lv_co_ts["year"],
                            y=_lv_co_ts["Display"],
                            mode="lines+markers",
                            name=_lv_sel_co,
                            line=dict(color=ACCENT, width=2),
                            marker=dict(size=6),
                            hovertemplate=(
                                f"<b>{_lv_sel_co}</b><br>"
                                "%{x}: %{y:,.1f} " + _lv_ch_unit + "<extra></extra>"
                            ),
                        ))
                        _lv_ch_fig.update_layout(
                            height=280,
                            margin=dict(l=0, r=10, t=10, b=0),
                            paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                            showlegend=False,
                            xaxis=dict(
                                title="Year",
                                title_font=dict(size=11, color=MUTED),
                                tickfont=dict(size=10, color=TEXT),
                                gridcolor=BORDER, dtick=2,
                            ),
                            yaxis=dict(
                                title=f"Inventory ({_lv_ch_unit})",
                                title_font=dict(size=11, color=MUTED),
                                tickfont=dict(size=10, color=TEXT),
                                gridcolor=BORDER, zeroline=False,
                            ),
                        )
                        _chart(_lv_ch_fig, use_container_width=True,
                                        key="lv_county_hist_chart")

                        _lv_ch_tbl = _lv_co_ts[["year", "Display"]].copy()
                        _lv_ch_tbl.columns = ["Year", f"Inventory ({_lv_ch_unit})"]
                        _lv_ch_tbl["Year"] = _lv_ch_tbl["Year"].astype(str)
                        _lv_ch_tbl[f"Inventory ({_lv_ch_unit})"] = (
                            _lv_ch_tbl[f"Inventory ({_lv_ch_unit})"]
                            .map(lambda x: f"{x:,.1f}")
                        )
                        st.dataframe(
                            _lv_ch_tbl.sort_values("Year", ascending=False),
                            hide_index=True, use_container_width=True,
                        )

            else:  # ASD District
                # ── ASD choropleth + bar chart ────────────────────────────────
                with st.spinner("Loading ASD data..."):
                    _lv_asd_df = load_livestock(
                        "AG DISTRICT", _lv_species, _lv_year,
                        state_alpha=_lv_state_abbr, cache_ver=_CACHE_VERSION
                    )
                if _lv_asd_df.empty or "asd_desc" not in _lv_asd_df.columns:
                    st.info(
                        f"No ASD-level data for {_lv_species} in "
                        f"{_lv_state_abbr} ({_lv_year})."
                    )
                else:
                    _lv_asd_agg = (
                        _lv_asd_df.dropna(subset=["asd_desc"])
                        .groupby("asd_desc", as_index=False)["Value"].sum()
                        .rename(columns={"asd_desc": "District"})
                        .sort_values("Value", ascending=False)
                    )
                    _lv_asdmx = _lv_asd_agg["Value"].max()
                    _lv_adiv, _lv_aunit = _lv_auto_scale(_lv_asdmx, _lv_base_unit)
                    _lv_asd_agg["Display"] = _lv_asd_agg["Value"] / _lv_adiv

                    # ASD choropleth — reuse the district GDF builder
                    _lv_sfips = STATE_FIPS_ALL.get(_lv_state_abbr, "")
                    try:
                        _lv_fmap = load_boundary_fips_map(
                            "Corn", _lv_sfips, _CACHE_VERSION, geo
                        )
                        _lv_dgdf = build_nass_district_gdf(
                            _lv_sfips, _CACHE_VERSION, _lv_fmap, geo
                        )
                    except Exception:
                        _lv_dgdf = gpd.GeoDataFrame()

                    if not _lv_dgdf.empty:
                        _lv_djson = json.loads(_lv_dgdf.to_json())
                        # Match on uppercase — NASS returns uppercase, GDF is Title
                        _lv_dval = {
                            row["District"].upper(): row["Display"]
                            for _, row in _lv_asd_agg.iterrows()
                        }
                        _lv_ddists = _lv_dgdf["District"].tolist()
                        _lv_dz = [_lv_dval.get(d.upper(), 0) for d in _lv_ddists]
                        _lv_dhov = [
                            (f"<b>{d}</b><br>"
                             f"{_lv_dval[d.upper()]:,.1f} {_lv_aunit}")
                            if d.upper() in _lv_dval
                            else f"<b>{d}</b><br>—"
                            for d in _lv_ddists
                        ]
                        _lv_asd_fig = go.Figure()
                        _lv_asd_fig.add_trace(go.Choropleth(
                            geojson=_lv_djson,
                            featureidkey="properties.District",
                            locations=_lv_ddists, z=_lv_dz,
                            colorscale="YlOrRd",
                            zmin=0, zmax=max(_lv_dz) if _lv_dz else 1,
                            colorbar=dict(
                                title=dict(text=_lv_aunit,
                                           font=dict(size=11, color=TEXT)),
                                thickness=12, len=0.6,
                                tickfont=dict(size=10, color=TEXT),
                            ),
                            marker=dict(line=dict(color=BORDER, width=0.6)),
                            text=_lv_dhov,
                            hovertemplate="%{text}<extra></extra>",
                        ))
                        _lv_asd_fig.update_layout(
                            height=420,
                            margin=dict(l=0, r=0, t=10, b=0),
                            paper_bgcolor=PANEL,
                            geo=dict(
                                bgcolor=PANEL, showframe=False,
                                fitbounds="locations", resolution=50,
                            ),
                        )
                        _chart(_lv_asd_fig, use_container_width=True,
                                        key="lv_asd_map")

                    # ASD bar chart (always shown)
                    _lv_bar_df = _lv_asd_agg.sort_values("Value", ascending=True)
                    _lv_bar_fig = px.bar(
                        _lv_bar_df, x="Display", y="District",
                        orientation="h",
                        color="Display",
                        color_continuous_scale="YlOrRd",
                        labels={"Display": f"Inventory ({_lv_aunit})",
                                "District": ""},
                    )
                    _lv_bar_fig.update_layout(
                        height=max(220, len(_lv_asd_agg) * 38),
                        margin=dict(l=0, r=0, t=0, b=0),
                        paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                        coloraxis_showscale=False,
                        showlegend=False,
                        yaxis=dict(tickfont=dict(size=11, color=TEXT)),
                        xaxis=dict(
                            tickfont=dict(size=10, color=TEXT),
                            title_font=dict(size=11, color=TEXT),
                        ),
                    )
                    _lv_bar_fig.update_traces(
                        hovertemplate="%{y}: %{x:,.1f} " + _lv_aunit
                                      + "<extra></extra>"
                    )
                    _chart(_lv_bar_fig, use_container_width=True,
                                    key="lv_asd_bar")

        # ── Historical trend chart ────────────────────────────────────────────
        st.markdown(
            f"<h4 style='color:{ACCENT};margin:24px 0 4px 0;"
            f"font-size:1rem;font-weight:600;'>"
            f"Historical Inventory Trend — {_lv_species}</h4>",
            unsafe_allow_html=True,
        )
        with st.spinner("Loading historical data..."):
            _lv_hist_df = load_livestock_hist(_lv_species, cache_ver=_CACHE_VERSION)

        if _lv_hist_df.empty:
            st.info("Historical trend data not available for this species.")
        else:
            # National total per year (sum of all state rows returned)
            _lv_nat = (
                _lv_hist_df
                .groupby("year", as_index=False)["Value"].sum()
                .assign(Label="US National")
            )
            _lv_trend_parts = [_lv_nat]

            # State line if a state is selected
            if _lv_state_abbr and "state_alpha" in _lv_hist_df.columns:
                _lv_st_hist = (
                    _lv_hist_df[_lv_hist_df["state_alpha"] == _lv_state_abbr]
                    .groupby("year", as_index=False)["Value"].sum()
                    .assign(Label=ABBR_TO_NAME.get(_lv_state_abbr, _lv_state_abbr))
                )
                if not _lv_st_hist.empty:
                    _lv_trend_parts.append(_lv_st_hist)

            _lv_trend_df = pd.concat(_lv_trend_parts, ignore_index=True)
            _lv_trend_df["year"] = _lv_trend_df["year"].astype(int)

            # Auto-scale
            _lv_tmx = _lv_trend_df["Value"].max()
            _lv_tdiv, _lv_tunit = _lv_auto_scale(_lv_tmx, _lv_base_unit)
            _lv_trend_df["Display"] = _lv_trend_df["Value"] / _lv_tdiv

            _lv_labels = _lv_trend_df["Label"].unique().tolist()
            _lv_colors = {
                "US National": MUTED,
                **{
                    lbl: ACCENT
                    for lbl in _lv_labels if lbl != "US National"
                },
            }
            _lv_dashes = {
                "US National": "dot",
                **{lbl: "solid" for lbl in _lv_labels if lbl != "US National"},
            }

            _lv_tfig = go.Figure()
            for _lbl in _lv_labels:
                _seg = _lv_trend_df[_lv_trend_df["Label"] == _lbl].sort_values("year")
                _lv_tfig.add_trace(go.Scatter(
                    x=_seg["year"], y=_seg["Display"],
                    mode="lines+markers",
                    name=_lbl,
                    line=dict(
                        color=_lv_colors.get(_lbl, ACCENT),
                        dash=_lv_dashes.get(_lbl, "solid"),
                        width=2,
                    ),
                    marker=dict(size=5),
                    hovertemplate=(
                        f"<b>{_lbl}</b><br>"
                        "%{x}: %{y:,.2f} " + _lv_tunit + "<extra></extra>"
                    ),
                ))

            _lv_period_lbl = _LIVESTOCK_PERIOD.get(_lv_species, "")
            _lv_tfig.update_layout(
                height=340,
                margin=dict(l=0, r=10, t=10, b=0),
                paper_bgcolor=PANEL,
                plot_bgcolor=PANEL,
                hovermode="x unified",
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0,
                    font=dict(size=11, color=TEXT),
                    bgcolor="rgba(0,0,0,0)",
                ),
                xaxis=dict(
                    title=f"Year ({_lv_period_lbl} inventory)",
                    title_font=dict(size=11, color=MUTED),
                    tickfont=dict(size=10, color=TEXT),
                    gridcolor=BORDER, showgrid=True,
                    dtick=2,
                ),
                yaxis=dict(
                    title=f"Inventory ({_lv_tunit})",
                    title_font=dict(size=11, color=MUTED),
                    tickfont=dict(size=10, color=TEXT),
                    gridcolor=BORDER, showgrid=True,
                    zeroline=False,
                ),
            )
            _chart(_lv_tfig, use_container_width=True, key="lv_trend")

        st.markdown(
            f"<p style='font-size:0.7rem;color:{MUTED};margin-top:12px;'>"
            "Source: USDA NASS QuickStats — Animals &amp; Products, Inventory, HEAD. "
            "January 1 survey reference date for cattle/sheep; quarterly for hogs. "
            "County data withheld by NASS where fewer than 3 operations would be identified.</p>",
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # AQUACULTURE TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_aqua:
        st.markdown(
            f"<h3 style='color:{ACCENT};margin-top:0;margin-bottom:4px;'>"
            "Aquaculture — NASS Census &amp; EPA Facilities</h3>",
            unsafe_allow_html=True,
        )
        st.caption(
            "USDA NASS Census of Aquaculture sales by state (Census years) · "
            "EPA ECHO NPDES-permitted facility locations."
        )

        # ── Controls ──────────────────────────────────────────────────────────
        _aq_c1, _aq_c2, _aq_c3 = st.columns([2, 2, 2])
        with _aq_c1:
            _aq_species = st.selectbox(
                "Species", list(_AQUA_SPECIES.keys()), key="aq_species"
            )
        with _aq_c2:
            _aq_year = st.selectbox("Census Year", _AQUA_YEARS, key="aq_year")
        with _aq_c3:
            _aq_state_opts = ["All States"] + sorted(ABBR_TO_NAME.keys())
            _aq_state_sel  = st.selectbox("State Filter", _aq_state_opts, key="aq_state")
            _aq_state = "" if _aq_state_sel == "All States" else _aq_state_sel

        st.markdown("---")

        # ── NASS State Choropleth ─────────────────────────────────────────────
        st.markdown(
            f"<h4 style='color:{ACCENT};margin-bottom:4px;'>"
            "NASS Census of Aquaculture — Sales by State</h4>",
            unsafe_allow_html=True,
        )
        with st.spinner("Loading NASS aquaculture data…"):
            _aq_df = load_aquaculture_nass(
                _aq_species, _aq_year, cache_ver=_CACHE_VERSION
            )

        if _aq_df.empty:
            st.info(
                f"No NASS data returned for **{_aq_species}** in {_aq_year}. "
                "The Census of Aquaculture is most complete for 2017 and 2022; "
                "some species have limited state coverage."
            )
        else:
            _aq_st = (
                _aq_df
                .groupby("state_alpha", as_index=False)["Value"].sum()
                .rename(columns={"state_alpha": "State"})
            )
            _aq_st["StateName"] = _aq_st["State"].map(ABBR_TO_NAME)
            _aq_st = _aq_st.dropna(subset=["StateName"])

            _aq_mx = float(_aq_st["Value"].max()) if not _aq_st.empty else 0.0
            if _aq_mx >= 500e6:
                _aq_div, _aq_unit = 1e9, "B $"
            elif _aq_mx >= 500e3:
                _aq_div, _aq_unit = 1e6, "M $"
            elif _aq_mx >= 500:
                _aq_div, _aq_unit = 1e3, "K $"
            else:
                _aq_div, _aq_unit = 1.0, "$"
            _aq_st["Display"] = _aq_st["Value"] / _aq_div

            _aq_choro = go.Figure(go.Choropleth(
                locations=_aq_st["State"],
                z=_aq_st["Display"],
                locationmode="USA-states",
                colorscale=[[0,"#1a2d1e"],[0.4,"#425248"],[0.7,"#5ba5af"],[1,"#88b131"]],
                text=_aq_st["StateName"],
                hovertemplate=(
                    "<b>%{text}</b><br>Sales: %{z:,.2f} "
                    + _aq_unit + "<extra></extra>"
                ),
                colorbar=dict(
                    title=dict(text=_aq_unit, side="right"),
                    thickness=14,
                    len=0.7,
                ),
                marker_line_color="white",
                marker_line_width=0.5,
            ))
            if _aq_state:
                _aq_sel = _aq_st[_aq_st["State"] == _aq_state]
                if not _aq_sel.empty:
                    _aq_choro.add_trace(go.Choropleth(
                        locations=[_aq_state],
                        z=[_aq_sel["Display"].iloc[0]],
                        locationmode="USA-states",
                        colorscale=[[0, ACCENT], [1, ACCENT]],
                        showscale=False,
                        marker_line_width=3,
                        marker_line_color="white",
                    ))
            _aq_choro.update_layout(
                height=380,
                margin=dict(l=0, r=0, t=20, b=0),
                geo=dict(
                    scope="usa",
                    projection_type="albers usa",
                    showlakes=True,
                    lakecolor="rgba(200,220,240,0.4)",
                    showland=True,
                    landcolor=LAND,
                    bgcolor="rgba(0,0,0,0)",
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=TEXT),
            )
            _chart(_aq_choro, use_container_width=True, key="aq_choro")

            # Top-states bar chart
            _n_bars = min(15, len(_aq_st))
            _aq_top = _aq_st.nlargest(_n_bars, "Value").sort_values("Display", ascending=True)
            _aq_bar = go.Figure(go.Bar(
                x=_aq_top["Display"],
                y=_aq_top["StateName"],
                orientation="h",
                marker_color=ACCENT,
                text=[f"{v:,.1f}" for v in _aq_top["Display"]],
                textposition="outside",
                hovertemplate=(
                    "<b>%{y}</b><br>Sales: %{x:,.2f} " + _aq_unit + "<extra></extra>"
                ),
            ))
            _aq_bar.update_layout(
                title=dict(
                    text=f"Top States — {_aq_species} Sales ({_aq_unit}), {_aq_year}",
                    font=dict(size=13, color=TEXT),
                    x=0,
                ),
                height=max(260, 34 * _n_bars + 60),
                margin=dict(l=0, r=60, t=40, b=0),
                xaxis=dict(
                    title=f"Sales ({_aq_unit})",
                    gridcolor=SURFACE,
                    zeroline=False,
                ),
                yaxis=dict(tickfont=dict(size=11)),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=TEXT),
            )
            _chart(_aq_bar, use_container_width=True, key="aq_bar")

        st.markdown(
            f"<p style='font-size:0.7rem;color:{MUTED};margin-top:4px;'>"
            "Source: USDA NASS QuickStats — Census of Agriculture, Census of Aquaculture. "
            "Sales measured in dollars; withheld values (D) excluded. "
            "Census years with coverage: 2007, 2012, 2017, 2022.</p>",
            unsafe_allow_html=True,
        )

        st.markdown("---")

        # ── EPA ECHO Facility Map ─────────────────────────────────────────────
        st.markdown(
            f"<h4 style='color:{ACCENT};margin-bottom:2px;'>"
            "EPA ECHO — Permitted Aquaculture Facilities</h4>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Facilities holding NPDES discharge permits under SIC 0921 "
            "(Fish Hatcheries &amp; Preserves) or SIC 0273 (Animal Aquaculture) — active permits only."
        )
        with st.spinner("Loading EPA ECHO facility data…"):
            _echo_df = load_echo_aquaculture(cache_ver=_CACHE_VERSION)

        if _echo_df.empty:
            st.info(
                "EPA ECHO facility data is unavailable or returned no results. "
                "The ECHO API may be temporarily offline — try refreshing later. "
                "Note: closed-loop / recirculating systems without NPDES permits are not listed."
            )
        else:
            _echo_plot = (
                _echo_df[_echo_df["state"] == _aq_state] if _aq_state else _echo_df
            )
            _echo_fig = go.Figure(go.Scattergeo(
                lat=_echo_plot["lat"],
                lon=_echo_plot["lon"],
                text=_echo_plot["name"],
                customdata=(
                    _echo_plot["state"].astype(str)
                    + " · " + _echo_plot["county"].astype(str)
                ),
                mode="markers",
                marker=dict(
                    size=5,
                    color=ACCENT,
                    opacity=0.7,
                    line=dict(width=0),
                ),
                hovertemplate="<b>%{text}</b><br>%{customdata}<extra></extra>",
            ))
            _echo_fig.update_layout(
                height=420,
                margin=dict(l=0, r=0, t=10, b=0),
                geo=dict(
                    scope="usa",
                    projection_type="albers usa",
                    showlakes=True,
                    lakecolor="rgba(200,220,240,0.4)",
                    showland=True,
                    landcolor=LAND,
                    bgcolor="rgba(0,0,0,0)",
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=TEXT),
            )
            _chart(_echo_fig, use_container_width=True, key="aq_echo")
            _echo_loc = (
                f" in {ABBR_TO_NAME.get(_aq_state, _aq_state)}" if _aq_state else " nationwide"
            )
            st.caption(
                f"{len(_echo_plot):,} facilities shown{_echo_loc} "
                f"(of {len(_echo_df):,} total)"
            )

        st.markdown(
            f"<p style='font-size:0.7rem;color:{MUTED};margin-top:4px;'>"
            "Source: EPA Enforcement and Compliance History Online (ECHO), "
            "CWA NPDES permits. "
            "Only facilities with active discharge permits are shown; "
            "recirculating / closed-loop systems without NPDES permits are not included. "
            "SIC 0921: Fish Hatcheries &amp; Preserves · SIC 0273: Animal Aquaculture.</p>",
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PROCESSING PLANTS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_proc:
        ACCENT_local = ACCENT
        st.markdown(
            f"<h3 style='color:{ACCENT_local};margin-top:0;margin-bottom:4px;'>"
            "🏭 Corn &amp; Soybean Processing Plants</h3>",
            unsafe_allow_html=True,
        )
        ptab_corn, ptab_crush = st.tabs(["🌽  Corn Processing", "🫘  Soybean Crush"])

        # ── CORN PROCESSING ─────────────────────────────────────────────────
        with ptab_corn:
            st.markdown(
                f"<p style='color:{TEXT};font-size:0.85rem;margin-top:0;'>"
                "Corn processing plants in the United States — ethanol, food/starch, "
                "and specialty facilities. Marker size reflects ethanol capacity "
                "(gallons/year); food/starch-only plants shown at minimum size. "
                "Source: RFA / Corn Refiners Association plant registry.</p>",
                unsafe_allow_html=True,
            )
            # ── Filters ─────────────────────────────────────────────────────
            cf1, cf2, cf3 = st.columns(3)
            all_statuses = sorted({p["status"] for p in _CORN_PLANTS})
            all_classes  = sorted({p["cls"]    for p in _CORN_PLANTS})
            all_corn_states = sorted({p["st"] for p in _CORN_PLANTS})

            sel_statuses = cf1.multiselect(
                "Status", all_statuses,
                default=["Run"],
                key="cp_status",
            )
            sel_classes = cf2.multiselect(
                "Class", all_classes, default=all_classes, key="cp_class",
            )
            sel_corn_states = cf3.multiselect(
                "State", all_corn_states, default=all_corn_states, key="cp_state",
            )

            corn_filtered = [
                p for p in _CORN_PLANTS
                if p["status"] in sel_statuses
                and p["cls"]    in sel_classes
                and p["st"]     in sel_corn_states
            ]

            # ── Color mapping ────────────────────────────────────────────────
            STATUS_COLORS = {
                "Run":       "#2ecc71",
                "Expand":    "#00d4ff",
                "Build":     "#3498db",
                "Proposed":  "#9b59b6",
                "Hold":      "#f39c12",
                "Idled":     "#e67e22",
                "Repurposed":"#95a5a6",
                "Closed":    "#e74c3c",
            }

            import plotly.graph_objects as go_

            def _corn_size(p):
                eth = p.get("eth_gal")
                if eth and eth > 0:
                    return max(6, min(28, eth / 10_000_000))
                return 5

            if corn_filtered:
                # group by status so we get one legend entry per status
                corn_traces = []
                for status in all_statuses:
                    pts = [p for p in corn_filtered if p["status"] == status]
                    if not pts:
                        continue
                    corn_traces.append(go_.Scattergeo(
                        lat=[p["lat"] for p in pts],
                        lon=[p["lon"] for p in pts],
                        mode="markers",
                        name=status,
                        marker=dict(
                            size=[_corn_size(p) for p in pts],
                            color=STATUS_COLORS.get(status, "#888"),
                            opacity=0.85,
                            line=dict(width=0.5, color="#4a6a54"),
                        ),
                        text=[
                            f"<b>{p['co']}</b><br>"
                            f"{p['city']}, {p['st']}<br>"
                            f"Status: {p['status']}  |  Class: {p['cls']}  |  Type: {p['typ']}<br>"
                            + (f"Ethanol cap: {p['eth_gal']:,.0f} gal/yr<br>" if p.get('eth_gal') else "")
                            + (f"Corn received: {p['corn_bu']:,.0f} bu/yr" if p.get('corn_bu') else "")
                            for p in pts
                        ],
                        hoverinfo="text",
                        showlegend=True,
                    ))

                fig_corn = go_.Figure(data=corn_traces)
                fig_corn.update_layout(
                    geo=dict(
                        scope="usa",
                        projection_type="albers usa",
                        showland=True, landcolor="#c8dccb",
                        showlakes=True, lakecolor="#aacdd4",
                        showcoastlines=True, coastlinecolor="#a0b8a4",
                        showframe=False,
                        bgcolor="#f4f8f5",
                    ),
                    paper_bgcolor="#f4f8f5",
                    plot_bgcolor="#f4f8f5",
                    margin=dict(l=0, r=0, t=10, b=0),
                    height=520,
                    legend=dict(
                        bgcolor="rgba(232,240,235,0.92)",
                        bordercolor="#b8d0be",
                        borderwidth=1,
                        font=dict(color="#1e2e22", size=11),
                        orientation="v",
                        x=0.01, y=0.99,
                        xanchor="left", yanchor="top",
                    ),
                )
                _chart(fig_corn, use_container_width=True)
                st.caption(
                    f"{len(corn_filtered)} plants shown "
                    f"({sum(1 for p in corn_filtered if p.get('eth_gal'))} with ethanol capacity)."
                )
            else:
                st.info("No plants match the selected filters.")

            # ── Summary table ─────────────────────────────────────────────────
            with st.expander("📋 Plant detail table", expanded=False):
                import pandas as _pd
                df_corn = _pd.DataFrame([
                    {
                        "Company": p["co"],
                        "State":   p["st"],
                        "City":    p["city"],
                        "County":  p.get("county") or "—",
                        "Status":  p["status"],
                        "Class":   p["cls"],
                        "Type":    p["typ"],
                        "Ethanol cap (M gal/yr)": f"{p['eth_gal']/1e6:.1f}" if p.get("eth_gal") else "—",
                        "Corn received (M bu/yr)": f"{p['corn_bu']/1e6:.2f}" if p.get("corn_bu") else "—",
                        "Start yr": str(p["start_yr"]) if p.get("start_yr") else "—",
                    }
                    for p in corn_filtered
                ])
                st.dataframe(df_corn, use_container_width=True, hide_index=True)

        # ── SOYBEAN CRUSH ────────────────────────────────────────────────────
        with ptab_crush:
            st.markdown(
                f"<p style='color:{TEXT};font-size:0.85rem;margin-top:0;'>"
                "US soybean crush facilities. Marker size reflects daily crush rate "
                "(bu/day). NOPA region and rail access shown on hover. "
                "Source: NOPA / company public data.</p>",
                unsafe_allow_html=True,
            )
            # ── Filters ─────────────────────────────────────────────────────
            sf1, sf2 = st.columns(2)
            all_crush_co     = sorted({p["co"]   for p in _CRUSH_PLANTS})
            all_crush_states = sorted({p["st"]   for p in _CRUSH_PLANTS})

            sel_crush_co = sf1.multiselect(
                "Company", all_crush_co, default=all_crush_co, key="cr_co",
            )
            sel_crush_states = sf2.multiselect(
                "State", all_crush_states, default=all_crush_states, key="cr_st",
            )

            crush_filtered = [
                p for p in _CRUSH_PLANTS
                if p["co"] in sel_crush_co and p["st"] in sel_crush_states
            ]

            # ── Color mapping by company ─────────────────────────────────────
            CRUSH_COLORS = {
                "ADM":                "#e74c3c",
                "ADM (Swing Plant)":  "#e74c3c",
                "ADM/Marathon":       "#e74c3c",
                "AGP":                "#3498db",
                "Bartlett Grain":     "#f39c12",
                "Bunge":              "#2ecc71",
                "Cargill":            "#9b59b6",
                "CGB":                "#1abc9c",
                "NDSP / CGB":         "#1abc9c",
                "CHS":                "#e67e22",
                "Dreyfus":            "#00d4ff",
                "Incobrasa":          "#ff6b9d",
                "MnSP":               "#a8e6cf",
                "Norfolk Crush":      "#ffd700",
                "Perdue":             "#ff8c42",
                "Platinum":           "#c0c0c0",
                "Riceland":           "#8bc34a",
                "Scoular (Swing Plant)": "#795548",
                "Shell Rock/P66":     "#607d8b",
                "SDSP":               "#ff5722",
                "White River Soy Proc": "#4caf50",
                "Zeeland Farms":      "#9c27b0",
                "High Plains Part":   "#ff9800",
            }

            def _crush_size(p):
                d = p.get("daily_bu") or 0
                return max(7, min(32, d / 6_000))

            if crush_filtered:
                all_crush_cos_filtered = sorted({p["co"] for p in crush_filtered})
                crush_traces = []
                for co in all_crush_cos_filtered:
                    pts = [p for p in crush_filtered if p["co"] == co]
                    crush_traces.append(go_.Scattergeo(
                        lat=[p["lat"] for p in pts],
                        lon=[p["lon"] for p in pts],
                        mode="markers",
                        name=co,
                        marker=dict(
                            size=[_crush_size(p) for p in pts],
                            color=CRUSH_COLORS.get(co, "#aaa"),
                            opacity=0.88,
                            line=dict(width=0.5, color="#4a6a54"),
                        ),
                        text=[
                            f"<b>{p['co']}</b><br>"
                            f"{p['city']}, {p['st']}<br>"
                            f"NOPA Region: {p['nopa']}<br>"
                            f"Daily crush: {p['daily_bu']:,} bu/day<br>"
                            f"Rail: {p['rr']}"
                            for p in pts
                        ],
                        hoverinfo="text",
                        showlegend=True,
                    ))

                total_daily = sum(p["daily_bu"] for p in crush_filtered if p.get("daily_bu"))
                annual_est  = total_daily * 330 / 1_000_000

                fig_crush = go_.Figure(data=crush_traces)
                fig_crush.update_layout(
                    geo=dict(
                        scope="usa",
                        projection_type="albers usa",
                        showland=True, landcolor="#c8dccb",
                        showlakes=True, lakecolor="#aacdd4",
                        showcoastlines=True, coastlinecolor="#a0b8a4",
                        showframe=False,
                        bgcolor="#f4f8f5",
                    ),
                    paper_bgcolor="#f4f8f5",
                    plot_bgcolor="#f4f8f5",
                    margin=dict(l=0, r=0, t=10, b=0),
                    height=520,
                    legend=dict(
                        bgcolor="rgba(232,240,235,0.92)",
                        bordercolor="#b8d0be",
                        borderwidth=1,
                        font=dict(color="#1e2e22", size=10),
                        orientation="v",
                        x=0.01, y=0.99,
                        xanchor="left", yanchor="top",
                    ),
                )
                _chart(fig_crush, use_container_width=True)
                st.caption(
                    f"{len(crush_filtered)} crush facilities shown — "
                    f"combined daily capacity: {total_daily:,} bu/day "
                    f"(~{annual_est:.0f}M bu/yr at 330 operating days)."
                )
            else:
                st.info("No crush facilities match the selected filters.")

            # ── Summary table ─────────────────────────────────────────────────
            with st.expander("📋 Crush facility detail table", expanded=False):
                import pandas as _pd2
                df_crush = _pd2.DataFrame([
                    {
                        "Company":       p["co"],
                        "State":         p["st"],
                        "City":          p["city"],
                        "NOPA Region":   p["nopa"],
                        "Census Region": p.get("census") or "—",
                        "Rail":          p["rr"],
                        "Daily (bu/day)": f"{p['daily_bu']:,}" if p.get("daily_bu") else "—",
                    }
                    for p in crush_filtered
                ])
                st.dataframe(df_crush, use_container_width=True, hide_index=True)

    # ABOUT THE DATA TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_about:
        st.markdown(
            f"<h3 style='color:{ACCENT};margin-top:0;margin-bottom:4px;'>"
            "Understanding NASS vs RMA Production Data</h3>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<p style='color:{MUTED};font-size:0.86rem;margin-top:0;margin-bottom:20px;'>"
            "Two federal agencies measure crop production using fundamentally different methodologies — "
            "knowing which lens you're looking through matters when interpreting the numbers.</p>",
            unsafe_allow_html=True,
        )

        # ── Side-by-side methodology cards ────────────────────────────────────
        ab_l, ab_r = st.columns(2, gap="large")
        with ab_l:
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:20px 24px;height:100%;'>
                <h4 style='color:{ACCENT};margin-top:0;margin-bottom:10px;font-size:1.05rem;'>
                🌾&nbsp; NASS — Survey-Based Total Production</h4>
                <p style='color:{TEXT};font-size:0.91rem;line-height:1.75;margin:0 0 10px 0;'>
                The <b>USDA National Agricultural Statistics Service (NASS)</b> produces
                official crop estimates by surveying thousands of farm operators,
                grain elevators, and agribusinesses nationwide.
                </p>
                <ul style='color:{MUTED};font-size:0.88rem;line-height:1.85;
                margin:0;padding-left:18px;'>
                <li>Covers <b style='color:{TEXT};'>all planted acres</b> — insured and uninsured alike</li>
                <li>Final estimates released post-harvest (typically November)</li>
                <li>Represents <b style='color:{TEXT};'>actual harvested production</b> for the crop year</li>
                <li>The definitive benchmark for U.S. crop supply &amp; demand</li>
                <li>Feeds directly into the USDA WASDE monthly balance sheets</li>
                </ul>
                </div>""",
                unsafe_allow_html=True,
            )
        with ab_r:
            st.markdown(
                f"""<div style='background:{PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:20px 24px;height:100%;'>
                <h4 style='color:{ACCENT};margin-top:0;margin-bottom:10px;font-size:1.05rem;'>
                📋&nbsp; RMA — Insurance Policy-Based Production</h4>
                <p style='color:{TEXT};font-size:0.91rem;line-height:1.75;margin:0 0 10px 0;'>
                The <b>USDA Risk Management Agency (RMA)</b> collects data through the
                federal crop insurance program, drawing from individual policy records
                filed by insured farmers and processed by approved insurance providers.
                </p>
                <ul style='color:{MUTED};font-size:0.88rem;line-height:1.85;
                margin:0;padding-left:18px;'>
                <li>Covers only <b style='color:{TEXT};'>federally insured acres</b></li>
                <li>Based on Actual Production History (APH) and policy-reported yields</li>
                <li>Breakdowns available by practice (Irrigated / Non-Irrigated)</li>
                <li>Published annually via the RMA Summary of Business</li>
                <li>Reflects <b style='color:{TEXT};'>insured-sector production</b>, not the full market</li>
                </ul>
                </div>""",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # ── Why numbers differ callout ─────────────────────────────────────────
        st.markdown(
            f"""<div style='background:{SURFACE};border-left:3px solid {ACCENT};
            border-radius:0 6px 6px 0;padding:14px 20px;margin-bottom:24px;'>
            <h4 style='color:{ACCENT};margin:0 0 8px 0;font-size:1rem;'>
            ⚡&nbsp; Why RMA Figures Trend Higher Than NASS</h4>
            <p style='color:{TEXT};font-size:0.90rem;line-height:1.80;margin:0;'>
            Even for the same county and crop year, RMA reported production often runs
            <b>above the NASS estimate</b>. Several structural factors drive this gap:
            </p>
            <ul style='color:{MUTED};font-size:0.88rem;line-height:1.85;
            margin:8px 0 0 0;padding-left:18px;'>
            <li><b style='color:{TEXT};'>Larger, higher-yielding operations</b> — farms that purchase
            crop insurance tend to be bigger and more productive than the average uninsured acre.</li>
            <li><b style='color:{TEXT};'>APH yield smoothing</b> — RMA uses Actual Production History
            (a multi-year rolling average) rather than any single harvest year, which dampens
            downside and can overstate expected production in weak years.</li>
            <li><b style='color:{TEXT};'>Irrigated acre weighting</b> — insured acres skew toward
            irrigated, higher-yield ground, lifting the portfolio average above the county mean.</li>
            <li><b style='color:{TEXT};'>Convergence over time</b> — as participation has climbed to
            ~92–93 % of planted acres, the RMA dataset increasingly mirrors the full universe,
            and the gap with NASS has narrowed materially.</li>
            </ul>
            </div>""",
            unsafe_allow_html=True,
        )

        # ── Participation rate chart ───────────────────────────────────────────
        st.markdown(
            f"<h4 style='color:{ACCENT};margin-bottom:2px;'>"
            "From Optional to Universal: U.S. Crop Insurance Participation</h4>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<p style='color:{MUTED};font-size:0.82rem;margin-top:0;margin-bottom:10px;'>"
            "Share of planted acres covered under federally reinsured crop insurance, 2000 – 2025.</p>",
            unsafe_allow_html=True,
        )

        # Approximate annual participation rates derived from
        # USDA RMA Summary of Business ÷ USDA NASS planted acres
        _PART_YEARS = list(range(2000, 2026))
        _CORN_PCT = [
            66, 67, 70, 72, 73, 74, 75, 77,   # 2000-2007
            80, 82, 83, 84, 85, 87, 89, 90,   # 2008-2015
            90, 91, 91, 91, 91, 91, 92, 92, 92, 92,  # 2016-2025
        ]
        _SOY_PCT = [
            64, 65, 67, 69, 71, 72, 73, 75,   # 2000-2007
            77, 79, 80, 82, 83, 85, 87, 89,   # 2008-2015
            90, 91, 91, 92, 92, 92, 93, 93, 93, 93,  # 2016-2025
        ]

        part_fig = go.Figure()

        # Shaded area beneath lines for visual weight
        part_fig.add_trace(go.Scatter(
            x=_PART_YEARS + _PART_YEARS[::-1],
            y=_CORN_PCT + [55] * len(_CORN_PCT),
            fill="toself",
            fillcolor=f"rgba(74,222,128,0.06)",
            line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ))
        part_fig.add_trace(go.Scatter(
            x=_PART_YEARS + _PART_YEARS[::-1],
            y=_SOY_PCT + [55] * len(_SOY_PCT),
            fill="toself",
            fillcolor=f"rgba(96,165,250,0.06)",
            line=dict(width=0),
            showlegend=False, hoverinfo="skip",
        ))

        # Corn line
        part_fig.add_trace(go.Scatter(
            x=_PART_YEARS, y=_CORN_PCT,
            mode="lines+markers",
            name="Corn",
            line=dict(color=ACCENT, width=2.5),
            marker=dict(size=5, color=ACCENT),
            hovertemplate="%{x}: <b>%{y}%</b><extra>Corn</extra>",
        ))

        # Soybeans line
        part_fig.add_trace(go.Scatter(
            x=_PART_YEARS, y=_SOY_PCT,
            mode="lines+markers",
            name="Soybeans",
            line=dict(color="#60a5fa", width=2.5),
            marker=dict(size=5, color="#60a5fa"),
            hovertemplate="%{x}: <b>%{y}%</b><extra>Soybeans</extra>",
        ))

        # 2008 Farm Bill reference
        part_fig.add_vline(
            x=2008, line_color=BORDER, line_width=1.5, line_dash="dot",
            annotation_text="2008 Farm Bill",
            annotation_position="top right",
            annotation_font=dict(color=MUTED, size=10),
        )
        # 2014 Farm Bill reference
        part_fig.add_vline(
            x=2014, line_color=BORDER, line_width=1.5, line_dash="dot",
            annotation_text="2014 Farm Bill",
            annotation_position="top right",
            annotation_font=dict(color=MUTED, size=10),
        )

        # Endpoint annotations — 2000
        part_fig.add_annotation(
            x=2000, y=66, text="66%", showarrow=False,
            xanchor="right", xshift=-6, yshift=8,
            font=dict(color=ACCENT, size=11, family="Arial Bold"),
        )
        part_fig.add_annotation(
            x=2000, y=64, text="64%", showarrow=False,
            xanchor="right", xshift=-6, yshift=-10,
            font=dict(color="#60a5fa", size=11, family="Arial Bold"),
        )
        # Endpoint annotations — 2025
        part_fig.add_annotation(
            x=2025, y=92, text="92%", showarrow=False,
            xanchor="left", xshift=6, yshift=-10,
            font=dict(color=ACCENT, size=11, family="Arial Bold"),
        )
        part_fig.add_annotation(
            x=2025, y=93, text="93%", showarrow=False,
            xanchor="left", xshift=6, yshift=8,
            font=dict(color="#60a5fa", size=11, family="Arial Bold"),
        )

        part_fig.update_layout(
            paper_bgcolor=DARK, plot_bgcolor=SURFACE,
            font=dict(color=TEXT, family="Arial"),
            margin=dict(l=60, r=70, t=30, b=50),
            height=380,
            xaxis=dict(
                title="Year",
                gridcolor=BORDER,
                tickfont=dict(color=MUTED),
                title_font=dict(color=MUTED),
                dtick=2,
                range=[1998.5, 2026.5],
                zeroline=False,
            ),
            yaxis=dict(
                title="% of Planted Acres Insured",
                gridcolor=BORDER,
                tickfont=dict(color=MUTED),
                title_font=dict(color=MUTED),
                range=[55, 100],
                ticksuffix="%",
                zeroline=False,
            ),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="left", x=0,
                font=dict(color=TEXT, size=11),
                bgcolor="rgba(0,0,0,0)",
            ),
            hovermode="x unified",
        )

        _chart(
            part_fig, use_container_width=True,
            config={"displayModeBar": False},
        )

        st.markdown(
            f"<p style='color:{MUTED};font-size:0.78rem;margin-top:2px;'>"
            "Source: USDA RMA Summary of Business &nbsp;+&nbsp; USDA NASS Quick Stats · Annual, 2000–2025. "
            "Participation rates represent federally insured planted acres as a share of total NASS planted acres. "
            "Historical series are approximate; endpoint values (2000 &amp; 2025) are per RMA/NASS published data.</p>",
            unsafe_allow_html=True,
        )

        # ── Quick-reference comparison table ─────────────────────────────────
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        st.markdown(
            f"<h4 style='color:{ACCENT};margin-bottom:8px;'>At a Glance: Key Differences</h4>",
            unsafe_allow_html=True,
        )
        cmp_df = pd.DataFrame({
            "": ["Data collection method", "Acre coverage", "Yield basis",
                 "Practice detail", "Primary use", "Publication timing"],
            "NASS": [
                "Farm & elevator surveys",
                "All planted acres (insured + uninsured)",
                "Actual harvested yield",
                "No (all-practice aggregate)",
                "Supply/demand balance sheets (WASDE)",
                "Monthly estimates; final in November",
            ],
            "RMA": [
                "Individual insurance policy records",
                "Federally insured acres only (~92–93%)",
                "APH / policy-reported yield (multi-year avg)",
                "Yes — Irrigated vs Non-Irrigated",
                "Crop insurance pricing & indemnity analysis",
                "Annual Summary of Business (spring/summer)",
            ],
        })
        st.dataframe(cmp_df, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════════════
    # WCMD GRAIN WAREHOUSES TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_wcmd:
        st.markdown(
            f"<h3 style='color:{ACCENT};margin-bottom:4px;'>USDA FSA Licensed Grain Warehouses</h3>"
            f"<p style='color:{MUTED};font-size:0.85rem;margin-top:0;'>Licensed grain storage capacity by state and county. "
            "Source: USDA FSA Warehouse Commodity Management Division (WCMD). Data as of current license file.</p>",
            unsafe_allow_html=True,
        )

        @st.cache_data(ttl=86400, show_spinner=False)
        def load_wcmd_data():
            data_dir = Path(__file__).parent / "data"
            state_path = data_dir / "wcmd_warehouses.csv"
            county_path = data_dir / "wcmd_county.csv"
            if not state_path.exists() or not county_path.exists():
                return None, None

            # State level
            raw = pd.read_csv(state_path, encoding="utf-16", sep="\t")
            grain = raw[raw["Commodity*"].str.strip() == "Grain"].copy()
            state_df = grain.pivot_table(
                index="State", columns="Unnamed: 6", values="Grain", aggfunc="first"
            ).reset_index()
            state_df.columns.name = None
            if "Capacity*" in state_df.columns:
                state_df["capacity_bu"] = (
                    state_df["Capacity*"].astype(str)
                    .str.replace(",", "").apply(pd.to_numeric, errors="coerce")
                )
            if "CCC Approved Warehouse Locations" in state_df.columns:
                state_df["locations"] = pd.to_numeric(
                    state_df["CCC Approved Warehouse Locations"], errors="coerce"
                )
            state_df = state_df[["State", "capacity_bu", "locations"]].rename(columns={"State": "state"})

            # County level
            county_df = pd.read_csv(county_path)
            return state_df, county_df

        wcmd_state, wcmd_county = load_wcmd_data()

        if wcmd_state is None:
            st.warning("WCMD data not found. Run `wcmd_scraper.py` to download.")
        else:
            # ── KPI row ──────────────────────────────────────────────────────
            total_cap = wcmd_state["capacity_bu"].sum()
            total_locs = wcmd_state["locations"].sum()
            top_state = wcmd_state.nlargest(1, "capacity_bu").iloc[0]
            k1, k2, k3 = st.columns(3)
            k1.metric("Total Licensed Grain Capacity", f"{total_cap/1e9:.2f}B bu")
            k2.metric("Licensed Locations", f"{int(total_locs):,}")
            k3.metric("Largest State", f"{top_state['state']} — {top_state['capacity_bu']/1e9:.2f}B bu")

            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

            # ── State choropleth ──────────────────────────────────────────────
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:6px;'>Licensed Grain Storage Capacity by State (Bushels)</h4>",
                unsafe_allow_html=True,
            )
            fig_wmap = px.choropleth(
                wcmd_state,
                locations="state",
                locationmode="USA-states",
                color="capacity_bu",
                scope="usa",
                color_continuous_scale=[[0, "#1a2535"], [0.3, "#1e4d6b"], [0.65, "#0693e3"], [1, "#60d0f0"]],
                labels={"capacity_bu": "Capacity (bu)", "state": "State"},
                hover_data={"state": True, "capacity_bu": ":,.0f", "locations": ":,"},
            )
            fig_wmap.update_layout(
                paper_bgcolor=DARK, geo_bgcolor=DARK,
                font=dict(color=TEXT, family="Arial"),
                margin=dict(l=0, r=0, t=10, b=0),
                height=420,
                coloraxis_colorbar=dict(
                    title="Bushels",
                    tickformat=".2s",
                    tickfont=dict(color=MUTED, size=10),
                    title_font=dict(color=MUTED),
                    len=0.6,
                ),
                geo=dict(
                    lakecolor=DARK, landcolor=SURFACE,
                    subunitcolor=BORDER, showlakes=True,
                ),
            )
            _add_logo(fig_wmap, logo_50yr)
            _chart(fig_wmap, use_container_width=True, config={"displayModeBar": False})

            # ── State bar chart ───────────────────────────────────────────────
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:6px;margin-top:20px;'>Top States by Licensed Capacity</h4>",
                unsafe_allow_html=True,
            )
            top_states = wcmd_state.nlargest(20, "capacity_bu").sort_values("capacity_bu")
            fig_wbar = go.Figure(go.Bar(
                x=top_states["capacity_bu"] / 1e9,
                y=top_states["state"],
                orientation="h",
                marker_color=ACCENT,
                text=(top_states["capacity_bu"] / 1e9).map("{:.2f}B".format),
                textposition="outside",
                textfont=dict(color=TEXT, size=10),
            ))
            fig_wbar.update_layout(
                paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                font=dict(color=TEXT, family="Arial"),
                margin=dict(l=20, r=60, t=10, b=40),
                height=480,
                xaxis=dict(
                    title="Capacity (billion bu)",
                    gridcolor=BORDER, tickfont=dict(color=MUTED),
                    title_font=dict(color=MUTED),
                    tickformat=".1f",
                ),
                yaxis=dict(tickfont=dict(color=TEXT), gridcolor=BORDER),
            )
            _add_logo(fig_wbar, logo_50yr)
            _chart(fig_wbar, use_container_width=True, config={"displayModeBar": False})

            # ── County / ASD drilldown ────────────────────────────────────────
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:6px;'>County / ASD Drilldown</h4>",
                unsafe_allow_html=True,
            )

            @st.cache_data(ttl=86400, show_spinner=False)
            def load_county_asd_map(cache_ver: str) -> dict:
                """Build {(state_alpha, county_name_lower): (asd_desc, asd_code)} from NASS corn data."""
                asd_map = {}
                for yr in [2024, 2023, 2022, 2021, 2020]:
                    try:
                        df = load_nass_county("Corn", yr, cache_ver)
                    except Exception:
                        continue
                    if df.empty or "asd_desc" not in df.columns:
                        continue
                    for _, row in df.iterrows():
                        key = (str(row.get("State", "")).strip().upper(),
                               str(row.get("County", "")).strip().lower())
                        if key not in asd_map:
                            desc = str(row.get("asd_desc", "")).strip().title()
                            code = str(row.get("asd_code", "")).strip()
                            if desc and desc.lower() not in ("", "nan"):
                                asd_map[key] = (desc, code)
                    if len(asd_map) > 500:
                        break
                return asd_map

            county_asd_map = load_county_asd_map(_CACHE_VERSION)

            dc1, dc2, dc3 = st.columns([1.2, 1, 2])
            with dc1:
                states_available = sorted(wcmd_county["state"].unique().tolist())
                sel_wcmd_state = st.selectbox("Select State", states_available, key="wcmd_state_sel")
            with dc2:
                wcmd_drill_view = st.radio("View by", ["County", "ASD District"],
                                           horizontal=True, key="wcmd_drill_view")

            county_filtered = wcmd_county[wcmd_county["state"] == sel_wcmd_state].copy()

            if wcmd_drill_view == "ASD District":
                # Map county → ASD then aggregate
                county_filtered["_asd_key"] = list(zip(
                    county_filtered["state"].str.upper(),
                    county_filtered["county"].str.strip().str.lower(),
                ))
                county_filtered["asd_desc"] = county_filtered["_asd_key"].map(
                    lambda k: county_asd_map.get(k, (None, None))[0]
                )
                county_filtered["asd_code"] = county_filtered["_asd_key"].map(
                    lambda k: county_asd_map.get(k, (None, None))[1]
                )
                asd_agg = county_filtered.groupby("asd_desc").agg(
                    est_capacity_bu=("est_capacity_bu", "sum"),
                    counties=("county", "count"),
                ).reset_index().dropna(subset=["asd_desc"])
                asd_agg = asd_agg.sort_values("est_capacity_bu", ascending=False)
                unmatched = county_filtered["asd_desc"].isna().sum()
                if unmatched > 0:
                    st.caption(f"⚠️ {unmatched} counties could not be mapped to an ASD district — shown in state total but excluded from chart.")

                fig_cbar = go.Figure(go.Bar(
                    x=asd_agg["est_capacity_bu"] / 1e6,
                    y=asd_agg["asd_desc"],
                    orientation="h",
                    marker_color=ACCENT,
                    text=(asd_agg["est_capacity_bu"] / 1e6).map("{:.1f}M".format),
                    textposition="outside",
                    textfont=dict(color=TEXT, size=9),
                    customdata=asd_agg["counties"],
                    hovertemplate="%{y}<br>Capacity: %{x:.1f}M bu<br>Counties: %{customdata}<extra></extra>",
                ))
                fig_cbar.update_layout(
                    paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                    font=dict(color=TEXT, family="Arial"),
                    margin=dict(l=20, r=60, t=10, b=40),
                    height=max(300, len(asd_agg) * 36),
                    xaxis=dict(
                        title="Est. Capacity (million bu)", gridcolor=BORDER,
                        tickfont=dict(color=MUTED), title_font=dict(color=MUTED),
                    ),
                    yaxis=dict(tickfont=dict(color=TEXT), gridcolor=BORDER, autorange="reversed"),
                )
            else:
                county_filtered = county_filtered.sort_values("est_capacity_bu", ascending=False)
                fig_cbar = go.Figure(go.Bar(
                    x=county_filtered["est_capacity_bu"] / 1e6,
                    y=county_filtered["county"],
                    orientation="h",
                    marker_color=ACCENT,
                    text=(county_filtered["est_capacity_bu"] / 1e6).map("{:.1f}M".format),
                    textposition="outside",
                    textfont=dict(color=TEXT, size=9),
                ))
                fig_cbar.update_layout(
                    paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                    font=dict(color=TEXT, family="Arial"),
                    margin=dict(l=20, r=60, t=10, b=40),
                    height=max(300, len(county_filtered) * 22),
                    xaxis=dict(
                        title="Est. Capacity (million bu)", gridcolor=BORDER,
                        tickfont=dict(color=MUTED), title_font=dict(color=MUTED),
                    ),
                    yaxis=dict(tickfont=dict(color=TEXT), gridcolor=BORDER, autorange="reversed"),
                )
            _add_logo(fig_cbar, logo_50yr)
            _chart(fig_cbar, use_container_width=True, config={"displayModeBar": False})

            # ── State table ───────────────────────────────────────────────────
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:6px;margin-top:20px;'>Full State Summary</h4>",
                unsafe_allow_html=True,
            )
            tbl_state = wcmd_state.copy().sort_values("capacity_bu", ascending=False)
            tbl_state["capacity_bu"] = tbl_state["capacity_bu"].map("{:,.0f}".format)
            tbl_state["locations"] = tbl_state["locations"].map("{:.0f}".format)
            tbl_state.columns = ["State", "Capacity (bu)", "Licensed Locations"]
            st.dataframe(tbl_state, use_container_width=True, hide_index=True)
            st.markdown(
                f"<p style='color:{MUTED};font-size:0.78rem;margin-top:4px;'>County capacity is estimated by distributing state totals "
                "proportionally to functional storage units per county. Source: USDA FSA WCMD.</p>",
                unsafe_allow_html=True,
            )

            # ── NASS On-Farm vs Off-Farm Storage by State ─────────────────────
            @st.cache_data(ttl=86400, show_spinner=False)
            def load_nass_storage_capacity():
                p = Path(__file__).parent / "data" / "nass_grain_storage_capacity_state.csv"
                if not p.exists():
                    return None
                nc = pd.read_csv(p, dtype=str)
                nc["value_bu"] = pd.to_numeric(nc["Value"].str.replace(",", ""), errors="coerce")
                return nc[nc["value_bu"].notna()].copy()

            nass_sc = load_nass_storage_capacity()
            if nass_sc is not None:
                st.markdown("<hr style='border-color:#3a3f47;margin:28px 0 16px 0;'>", unsafe_allow_html=True)
                st.markdown(
                    f"<h4 style='color:{ACCENT};margin-bottom:4px;'>NASS Grain Storage Capacity — On-Farm & Off-Farm by State</h4>"
                    f"<p style='color:{MUTED};font-size:0.82rem;margin-top:0;'>Annual survey covering farmer-owned bins/bags (on-farm) and "
                    "commercial elevators & warehouses (off-farm). Source: USDA NASS.</p>",
                    unsafe_allow_html=True,
                )
                avail_nc_years = sorted(nass_sc["year"].unique(), reverse=True)
                sel_nc_yr = str(st.selectbox("Year", avail_nc_years, key="wcmd_nass_yr"))
                nc_yr = nass_sc[nass_sc["year"].astype(str) == sel_nc_yr]

                OFF_DESC = "GRAIN STORAGE CAPACITY, OFF FARM - CAPACITY, MEASURED IN BU"
                ON_DESC  = "GRAIN STORAGE CAPACITY, ON FARM - CAPACITY, MEASURED IN BU"
                nc_off = nc_yr[nc_yr["short_desc"] == OFF_DESC][["state_alpha","value_bu"]].rename(columns={"value_bu":"off_farm"})
                nc_on  = nc_yr[nc_yr["short_desc"] == ON_DESC ][["state_alpha","value_bu"]].rename(columns={"value_bu":"on_farm"})
                nc_state = nc_off.merge(nc_on, on="state_alpha", how="outer").dropna()
                nc_state["total"] = nc_state["off_farm"] + nc_state["on_farm"]
                nc_state = nc_state.sort_values("total", ascending=True)

                fig_nsc = go.Figure()
                fig_nsc.add_trace(go.Bar(
                    name="Off-Farm (Commercial)", x=nc_state["off_farm"] / 1e9, y=nc_state["state_alpha"],
                    orientation="h", marker_color="#f97316",
                    hovertemplate="Off-Farm: %{x:.2f}B bu<extra></extra>",
                ))
                fig_nsc.add_trace(go.Bar(
                    name="On-Farm / Temporary", x=nc_state["on_farm"] / 1e9, y=nc_state["state_alpha"],
                    orientation="h", marker_color="#a3e635",
                    hovertemplate="On-Farm: %{x:.2f}B bu<extra></extra>",
                ))
                fig_nsc.update_layout(
                    paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                    font=dict(color=TEXT, family="Arial"),
                    margin=dict(l=20, r=20, t=10, b=50),
                    height=max(400, len(nc_state) * 22),
                    barmode="stack",
                    legend=dict(
                        font=dict(color=TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                        orientation="h", yanchor="bottom", y=1.02, x=0,
                    ),
                    xaxis=dict(
                        title="Billion Bushels", gridcolor=BORDER,
                        tickfont=dict(color=MUTED), title_font=dict(color=MUTED),
                    ),
                    yaxis=dict(tickfont=dict(color=TEXT), gridcolor=BORDER),
                    hovermode="y unified",
                )
                _add_logo(fig_nsc, logo_50yr)
                _chart(fig_nsc, use_container_width=True, config={"displayModeBar": False})

                # Trend: national on-farm vs off-farm
                st.markdown(
                    f"<h4 style='color:{ACCENT};margin-bottom:6px;margin-top:20px;'>National Trend: On-Farm vs Off-Farm Capacity</h4>",
                    unsafe_allow_html=True,
                )
                nat_sc = nass_sc.groupby(["year","short_desc"])["value_bu"].sum().reset_index()
                TREND_MAP = {
                    OFF_DESC: ("Off-Farm (Commercial)", "#f97316"),
                    ON_DESC:  ("On-Farm / Temporary",   "#a3e635"),
                }
                fig_sc_trend = go.Figure()
                for desc, (label, color) in TREND_MAP.items():
                    sub = nat_sc[nat_sc["short_desc"] == desc].sort_values("year")
                    fig_sc_trend.add_trace(go.Scatter(
                        x=sub["year"].astype(int), y=sub["value_bu"] / 1e9,
                        mode="lines+markers", name=label,
                        line=dict(color=color, width=2),
                        marker=dict(color=color, size=5),
                        hovertemplate=f"{label}: %{{y:.2f}}B bu<extra></extra>",
                    ))
                fig_sc_trend.update_layout(
                    paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                    font=dict(color=TEXT, family="Arial"),
                    margin=dict(l=60, r=20, t=10, b=50),
                    height=300,
                    legend=dict(font=dict(color=TEXT), bgcolor="rgba(0,0,0,0)",
                                orientation="h", yanchor="bottom", y=1.02, x=0),
                    xaxis=dict(title="Year", gridcolor=BORDER, tickfont=dict(color=MUTED),
                               title_font=dict(color=MUTED), dtick=2),
                    yaxis=dict(title="Billion Bushels", gridcolor=BORDER,
                               tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
                    hovermode="x unified",
                )
                _chart(fig_sc_trend, use_container_width=True, config={"displayModeBar": False})
                st.markdown(
                    f"<p style='color:{MUTED};font-size:0.78rem;margin-top:4px;'>"
                    "On-farm includes farmer-owned bins, grain bags, and temporary structures. "
                    "Off-farm includes commercial grain elevators and licensed warehouses. "
                    "USDA NASS Grain Storage Capacity report, published annually each January.</p>",
                    unsafe_allow_html=True,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # STORAGE VS. PRODUCTION TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_storage_cmp:
        st.markdown(
            f"<h3 style='color:{ACCENT};margin-bottom:4px;'>Grain Supply to Storage</h3>"
            f"<p style='color:{MUTED};font-size:0.85rem;margin-top:0;'>Annual grain supply (production) stacked by commodity vs. "
            "total licensed storage capacity (USDA FSA WCMD). Ratio &lt; 1.00 indicates a space deficit. "
            "Historical trend uses NASS total off-farm storage capacity.</p>",
            unsafe_allow_html=True,
        )

        @st.cache_data(ttl=3600, show_spinner=False)
        def load_storage_cmp_data():
            data_dir = Path(__file__).parent / "data"
            stocks_path    = data_dir / "nass_sep1_stocks_state.csv"
            prod_path      = data_dir / "nass_production_state.csv"
            wcmd_path      = data_dir / "wcmd_warehouses.csv"
            nass_cap_path  = data_dir / "nass_grain_storage_capacity_state.csv"
            silage_path    = data_dir / "nass_corn_silage_state.csv"
            for p in [stocks_path, prod_path, wcmd_path]:
                if not p.exists():
                    return None, None, None, None, None

            GRAINS = ["CORN", "SOYBEANS", "WHEAT", "OATS", "SORGHUM"]

            stocks = pd.read_csv(stocks_path, dtype=str)
            stocks = stocks[stocks["commodity_desc"].isin(GRAINS)].copy()
            stocks["value_bu"] = pd.to_numeric(stocks["Value"].str.replace(",", ""), errors="coerce")
            stocks = stocks[["year", "state_alpha", "commodity_desc", "value_bu"]].dropna()

            prod = pd.read_csv(prod_path, dtype=str)
            prod = prod[
                (prod["commodity_desc"].isin(GRAINS)) &
                (prod["reference_period_desc"] == "YEAR") &
                (prod["prodn_practice_desc"] == "ALL PRODUCTION PRACTICES") &
                (prod["class_desc"] == "ALL CLASSES")
            ].copy()
            prod["value_bu"] = pd.to_numeric(prod["Value"].str.replace(",", ""), errors="coerce")
            prod = prod[["year", "state_alpha", "commodity_desc", "value_bu"]].dropna()

            # WCMD licensed capacity (static — from scraper)
            raw = pd.read_csv(wcmd_path, encoding="utf-16", sep="\t")
            grain_cap = raw[(raw["Commodity*"].str.strip() == "Grain") &
                            (raw["Unnamed: 6"].str.strip() == "Capacity*")].copy()
            grain_cap["wcmd_licensed_bu"] = (
                grain_cap["Grain"].astype(str).str.replace(",", "")
                .apply(pd.to_numeric, errors="coerce")
            )
            cap_df = grain_cap[["State", "wcmd_licensed_bu"]].rename(columns={"State": "state"})

            # NASS on-farm / off-farm capacity (annual by state)
            nass_cap = None
            if nass_cap_path.exists():
                nc = pd.read_csv(nass_cap_path, dtype=str)
                nc["value_bu"] = pd.to_numeric(nc["Value"].str.replace(",", ""), errors="coerce")
                nass_cap = nc[["year", "state_alpha", "short_desc", "value_bu"]].copy()
                nass_cap = nass_cap[nass_cap["value_bu"].notna()]

            # Corn silage (tons)
            silage = None
            if silage_path.exists():
                sl = pd.read_csv(silage_path, dtype=str)
                sl["value_tons"] = pd.to_numeric(sl["Value"].str.replace(",", ""), errors="coerce")
                silage = sl[sl["value_tons"] > 0][["year", "state_alpha", "value_tons"]].dropna()
                silage["year"] = silage["year"].astype(str)

            return stocks, prod, cap_df, nass_cap, silage

        stocks_df, prod_df, cap_df, nass_cap_df, silage_df = load_storage_cmp_data()

        if stocks_df is None:
            st.warning("Data files missing. Run NASS data fetcher and WCMD scraper first.")
        else:
            # ── Commodity colors (match reference chart style) ─────────────────
            COMM_COLORS_SVC = {
                "CORN+SORGHUM": "#1f5c2e",
                "SOYBEANS":     "#a8d5e8",
                "WHEAT":        "#e0b800",
                "OATS":         "#c0392b",
            }

            # ── Region definitions ─────────────────────────────────────────────
            REGIONS_SVC = {
                "All States":   None,
                "ECB (IL/IN/OH/MI)":             ["IL","IN","OH","MI"],
                "High Plains (ND/SD/NE/KS)":     ["ND","SD","NE","KS"],
                "Corn Belt":                     ["IA","IL","IN","MN","MO","OH","SD","NE","KS","ND"],
                "Midsouth (AR/MS/TN/MO/LA)":     ["AR","MS","TN","MO","LA"],
            }

            # ── Controls ───────────────────────────────────────────────────────
            sc_c1, sc_c2, sc_c3 = st.columns([1, 1.6, 1])
            with sc_c1:
                avail_yrs_svc = sorted(prod_df["year"].unique(), reverse=True)
                sel_yr_svc = st.selectbox("Year", avail_yrs_svc, key="svc_year")
            with sc_c2:
                sel_region_svc = st.selectbox("Region", list(REGIONS_SVC.keys()), key="svc_region")
            with sc_c3:
                storage_layer = st.radio(
                    "Storage Layer",
                    ["NASS Total (On+Off)", "NASS Off-Farm", "NASS On-Farm", "WCMD Licensed"],
                    horizontal=True, key="svc_layer",
                )

            yr_svc  = str(sel_yr_svc)
            yr_prev = str(int(yr_svc) - 1)
            region_states = REGIONS_SVC[sel_region_svc]

            # ── Helper: build per-state supply+storage df for a given year ─────
            def _build_svc_df(yr_str):
                pg = prod_df[prod_df["year"] == yr_str]
                corn_sorg = pg[pg["commodity_desc"].isin(["CORN","SORGHUM"])].groupby("state_alpha")["value_bu"].sum().reset_index()
                corn_sorg.columns = ["state","corn_sorg_bu"]
                soybeans  = pg[pg["commodity_desc"] == "SOYBEANS"].groupby("state_alpha")["value_bu"].sum().reset_index()
                soybeans.columns = ["state","soybeans_bu"]
                wheat     = pg[pg["commodity_desc"] == "WHEAT"].groupby("state_alpha")["value_bu"].sum().reset_index()
                wheat.columns = ["state","wheat_bu"]
                oats      = pg[pg["commodity_desc"] == "OATS"].groupby("state_alpha")["value_bu"].sum().reset_index()
                oats.columns = ["state","oats_bu"]

                df = corn_sorg.merge(soybeans, on="state", how="outer") \
                              .merge(wheat,    on="state", how="outer") \
                              .merge(oats,     on="state", how="outer") \
                              .merge(cap_df,   on="state", how="inner")
                for c in ["corn_sorg_bu","soybeans_bu","wheat_bu","oats_bu"]:
                    df[c] = df[c].fillna(0)
                df["total_supply_bu"] = df[["corn_sorg_bu","soybeans_bu","wheat_bu","oats_bu"]].sum(axis=1)

                # NASS capacity for that year
                nass_off = pd.DataFrame(columns=["state","nass_offfarm_bu"])
                nass_on  = pd.DataFrame(columns=["state","nass_onfarm_bu"])
                if nass_cap_df is not None:
                    avail_nc = sorted(nass_cap_df["year"].unique())
                    nc_yr = yr_str if yr_str in avail_nc else max((y for y in avail_nc if y <= yr_str), default=avail_nc[-1])
                    nc_sub = nass_cap_df[nass_cap_df["year"] == nc_yr]
                    off = nc_sub[nc_sub["short_desc"].str.contains("OFF FARM", na=False)][["state_alpha","value_bu"]].rename(columns={"state_alpha":"state","value_bu":"nass_offfarm_bu"})
                    on  = nc_sub[nc_sub["short_desc"].str.contains("ON FARM",  na=False)][["state_alpha","value_bu"]].rename(columns={"state_alpha":"state","value_bu":"nass_onfarm_bu"})
                    nass_off = off; nass_on = on
                df = df.merge(nass_off, on="state", how="left").merge(nass_on, on="state", how="left")
                df["nass_offfarm_bu"] = df.get("nass_offfarm_bu", pd.Series(0, index=df.index)).fillna(0)
                df["nass_onfarm_bu"]  = df.get("nass_onfarm_bu",  pd.Series(0, index=df.index)).fillna(0)

                # Active storage column based on layer selector
                if storage_layer == "NASS Off-Farm":
                    df["storage_bu"] = df["nass_offfarm_bu"]
                elif storage_layer == "NASS On-Farm":
                    df["storage_bu"] = df["nass_onfarm_bu"]
                elif storage_layer == "NASS Total (On+Off)":
                    df["storage_bu"] = df["nass_offfarm_bu"] + df["nass_onfarm_bu"]
                else:  # WCMD Licensed
                    df["storage_bu"] = df["wcmd_licensed_bu"].fillna(0)

                df["ratio"] = df.apply(
                    lambda r: r["storage_bu"] / r["total_supply_bu"] if r["total_supply_bu"] > 0 else None, axis=1
                )
                return df

            df_cur  = _build_svc_df(yr_svc)
            df_prev = _build_svc_df(yr_prev)

            ratio_prev_map = dict(zip(df_prev["state"], df_prev["ratio"]))

            if region_states:
                df_cur = df_cur[df_cur["state"].isin(region_states)]

            df_chart = df_cur.dropna(subset=["total_supply_bu"]) \
                             .sort_values("total_supply_bu", ascending=False)

            # ── KPI row ────────────────────────────────────────────────────────
            nat_supply  = df_chart["total_supply_bu"].sum()
            nat_storage = df_chart["storage_bu"].sum()
            nat_ratio   = nat_storage / nat_supply if nat_supply > 0 else 0
            nat_deficit = nat_storage - nat_supply

            nat_supply_p  = df_prev[df_prev["state"].isin(df_chart["state"])]["total_supply_bu"].sum()
            nat_storage_p = df_prev[df_prev["state"].isin(df_chart["state"])]["storage_bu"].sum()
            nat_ratio_p   = nat_storage_p / nat_supply_p if nat_supply_p > 0 else 0

            k1, k2, k3, k4 = st.columns(4)
            k1.metric(
                f"Total Supply ({yr_svc})",
                f"{nat_supply/1e9:.2f}B bu",
                delta=f"{(nat_supply-nat_supply_p)/1e6:+.0f}M bu vs {yr_prev}",
            )
            k2.metric(
                f"{storage_layer} Capacity",
                f"{nat_storage/1e9:.2f}B bu",
            )
            k3.metric(
                "Space Ratio (Storage/Supply)",
                f"{nat_ratio:.2f}",
                delta=f"{nat_ratio - nat_ratio_p:+.2f} YoY",
                delta_color="normal",
            )
            k4.metric(
                "Space Surplus / (Deficit)",
                f"{nat_deficit/1e6:+.0f}M bu",
                delta=f"{'Surplus' if nat_deficit >= 0 else 'Deficit'}",
                delta_color="normal" if nat_deficit >= 0 else "inverse",
            )

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # ── State Supply vs Storage chart ──────────────────────────────────
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:4px;'>Grain Supply to Storage by State — {yr_svc} (Million Bushels)</h4>",
                unsafe_allow_html=True,
            )

            fig_svc = go.Figure()

            # Stacked supply bars
            for comm_key, col_name, color in [
                ("CORN+SORGHUM", "corn_sorg_bu",  COMM_COLORS_SVC["CORN+SORGHUM"]),
                ("SOYBEANS",     "soybeans_bu",   COMM_COLORS_SVC["SOYBEANS"]),
                ("WHEAT",        "wheat_bu",      COMM_COLORS_SVC["WHEAT"]),
                ("OATS",         "oats_bu",       COMM_COLORS_SVC["OATS"]),
            ]:
                fig_svc.add_trace(go.Bar(
                    name=comm_key.replace("+", " & ").title(),
                    x=df_chart["state"],
                    y=df_chart[col_name] / 1e6,
                    marker_color=color,
                    hovertemplate=f"{comm_key.replace('+', ' & ').title()}: %{{y:,.0f}}M bu<extra></extra>",
                ))

            # Total Storage line overlay
            _svc_line_color = {
                "WCMD Licensed":       "#c07000",
                "NASS Off-Farm":       "#444444",
                "NASS On-Farm":        "#1055aa",
                "NASS Total (On+Off)": "#444444",
            }.get(storage_layer, "#444444")
            fig_svc.add_trace(go.Scatter(
                name=storage_layer,
                x=df_chart["state"],
                y=df_chart["storage_bu"] / 1e6,
                mode="lines+markers",
                line=dict(color=_svc_line_color, width=2.5),
                marker=dict(color=_svc_line_color, size=6, symbol="line-ew-open"),
                hovertemplate="Storage: %{y:,.0f}M bu<extra></extra>",
            ))

            # Ratio annotations above each bar
            annotations_svc = []
            for _, row in df_chart.iterrows():
                if row["total_supply_bu"] > 0 and pd.notna(row["ratio"]):
                    annotations_svc.append(dict(
                        x=row["state"],
                        y=row["total_supply_bu"] / 1e6 + row["total_supply_bu"] / 1e6 * 0.03,
                        text=f"<b>{row['ratio']:.2f}</b>",
                        showarrow=False,
                        font=dict(size=10, color="#000000"),
                        yanchor="bottom",
                    ))

            # YoY delta text (below x-axis via annotation trick)
            yoy_anns = []
            min_y_svc = -(df_chart["total_supply_bu"].max() / 1e6 * 0.12)
            for _, row in df_chart.iterrows():
                prev_r = ratio_prev_map.get(row["state"])
                delta_r = row["ratio"] - prev_r if (pd.notna(row["ratio"]) and prev_r is not None) else None
                if delta_r is not None:
                    color_d = "#0a7a50" if delta_r >= 0 else "#c0392b"
                    yoy_anns.append(dict(
                        x=row["state"], y=min_y_svc,
                        text=f"<b style='color:{color_d}'>{delta_r:+.2f}</b>",
                        showarrow=False,
                        font=dict(size=9),
                        yanchor="top",
                    ))

            fig_svc.update_layout(
                paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                font=dict(color=TEXT, family="Arial"),
                margin=dict(l=60, r=20, t=20, b=80),
                height=500,
                barmode="stack",
                showlegend=True,
                legend=dict(
                    font=dict(color=TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, x=0,
                ),
                xaxis=dict(tickfont=dict(color=TEXT, size=11), gridcolor=BORDER, tickangle=0),
                yaxis=dict(
                    title="Million Bushels",
                    gridcolor=BORDER, tickfont=dict(color=MUTED),
                    title_font=dict(color=MUTED),
                    range=[min_y_svc, df_chart["total_supply_bu"].max() / 1e6 * 1.15],
                ),
                annotations=annotations_svc + yoy_anns,
                hovermode="x unified",
            )
            _add_logo(fig_svc, logo_50yr)
            _chart(fig_svc, use_container_width=True, config={"displayModeBar": False})

            # YoY label below chart
            st.markdown(
                f"<p style='color:{MUTED};font-size:0.77rem;margin-top:-8px;margin-bottom:16px;'>"
                f"Numbers above bars = Storage/Supply ratio. Row below x-axis = YoY ratio change vs {yr_prev}.</p>",
                unsafe_allow_html=True,
            )

            # ── Regional summary table ─────────────────────────────────────────
            ECB_STATES       = ["IL","IN","OH","MI"]
            HIPL_STATES      = ["ND","SD","NE","KS"]
            CORNBELT_STATES  = ["IA","IL","IN","MN","MO","OH","SD","NE","KS","ND"]

            def _region_summary(df_c, df_p, states, label):
                sub_c = df_c[df_c["state"].isin(states)]
                sub_p = df_p[df_p["state"].isin(states)]
                sup_c  = sub_c["total_supply_bu"].sum()
                sto_c  = sub_c["storage_bu"].sum()
                sup_p  = sub_p["total_supply_bu"].sum()
                sto_p  = sub_p["storage_bu"].sum()
                def_c  = sto_c - sup_c
                def_p  = sto_p - sup_p
                chg    = def_c - def_p
                ratio_c = sto_c / sup_c if sup_c > 0 else None
                ratio_p = sto_p / sup_p if sup_p > 0 else None
                return {
                    "Region": label,
                    "Supply (M bu)": f"{sup_c/1e6:,.0f}",
                    "Storage (M bu)": f"{sto_c/1e6:,.0f}",
                    f"Space {yr_svc}": f"{def_c/1e6:+,.0f}",
                    f"Space {yr_prev}": f"{def_p/1e6:+,.0f}",
                    "YoY Change": f"{chg/1e6:+,.0f}",
                    f"Ratio {yr_svc}": f"{ratio_c:.2f}" if ratio_c else "—",
                    f"Ratio {yr_prev}": f"{ratio_p:.2f}" if ratio_p else "—",
                }

            df_full_cur  = _build_svc_df(yr_svc)
            df_full_prev = _build_svc_df(yr_prev)
            region_rows = [
                _region_summary(df_full_cur, df_full_prev, ECB_STATES,      "ECB (IL/IN/OH/MI)"),
                _region_summary(df_full_cur, df_full_prev, HIPL_STATES,     "High Plains (ND/SD/NE/KS)"),
                _region_summary(df_full_cur, df_full_prev, CORNBELT_STATES, "Corn Belt (10-state)"),
            ]
            reg_df = pd.DataFrame(region_rows)

            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:4px;margin-top:20px;'>Regional Space Summary</h4>",
                unsafe_allow_html=True,
            )
            st.dataframe(reg_df, use_container_width=True, hide_index=True)

            # ── Historical trend: selected region ─────────────────────────────
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:4px;margin-top:24px;'>"
                f"{'Corn Belt' if sel_region_svc == 'All States' else sel_region_svc} Grain Supply to Storage — Historical</h4>",
                unsafe_allow_html=True,
            )

            hist_states = region_states if region_states else CORNBELT_STATES
            hist_yrs    = [y for y in sorted(prod_df["year"].unique()) if int(y) >= 2015]

            # Build historical data using NASS off-farm as storage line (available annually)
            hist_rows = []
            for hy in hist_yrs:
                hpg = prod_df[prod_df["year"] == hy]
                corn_s  = hpg[hpg["commodity_desc"].isin(["CORN","SORGHUM"]) & hpg["state_alpha"].isin(hist_states)]["value_bu"].sum()
                soy_s   = hpg[(hpg["commodity_desc"] == "SOYBEANS") & hpg["state_alpha"].isin(hist_states)]["value_bu"].sum()
                wht_s   = hpg[(hpg["commodity_desc"] == "WHEAT")    & hpg["state_alpha"].isin(hist_states)]["value_bu"].sum()
                oat_s   = hpg[(hpg["commodity_desc"] == "OATS")     & hpg["state_alpha"].isin(hist_states)]["value_bu"].sum()
                total_s = corn_s + soy_s + wht_s + oat_s

                # Storage: respect selected storage_layer
                hist_sto = 0
                if storage_layer == "WCMD Licensed":
                    hist_sto = df_full_cur[df_full_cur["state"].isin(hist_states)]["wcmd_licensed_bu"].sum()
                elif nass_cap_df is not None:
                    avail_nc = sorted(nass_cap_df["year"].unique())
                    nc_yr_h = hy if hy in avail_nc else max((y for y in avail_nc if y <= hy), default=avail_nc[-1])
                    nc_h_st = nass_cap_df[(nass_cap_df["year"] == nc_yr_h) & nass_cap_df["state_alpha"].isin(hist_states)]
                    if storage_layer == "NASS On-Farm":
                        hist_sto = nc_h_st[nc_h_st["short_desc"].str.contains("ON FARM", na=False)]["value_bu"].sum()
                    elif storage_layer == "NASS Off-Farm":
                        hist_sto = nc_h_st[nc_h_st["short_desc"].str.contains("OFF FARM", na=False)]["value_bu"].sum()
                    else:  # NASS Total (On+Off)
                        hist_sto = nc_h_st["value_bu"].sum()

                hist_rows.append({
                    "year": int(hy),
                    "corn_sorg": corn_s / 1e6,
                    "soybeans":  soy_s  / 1e6,
                    "wheat":     wht_s  / 1e6,
                    "oats":      oat_s  / 1e6,
                    "total_supply": total_s / 1e6,
                    "storage": hist_sto / 1e6,
                    "ratio": hist_sto / total_s if total_s > 0 else None,
                })

            hist_df = pd.DataFrame(hist_rows).sort_values("year", ascending=False)

            fig_hist = go.Figure()
            for comm_key, col_h, color in [
                ("Corn & Sorg Supply", "corn_sorg", COMM_COLORS_SVC["CORN+SORGHUM"]),
                ("Soybean Supply",     "soybeans",  COMM_COLORS_SVC["SOYBEANS"]),
                ("Wheat Supply",       "wheat",     COMM_COLORS_SVC["WHEAT"]),
                ("Oat Supply",         "oats",      COMM_COLORS_SVC["OATS"]),
            ]:
                fig_hist.add_trace(go.Bar(
                    name=comm_key,
                    x=hist_df["year"].astype(str),
                    y=hist_df[col_h],
                    marker_color=color,
                    hovertemplate=f"{comm_key}: %{{y:,.0f}}M bu<extra></extra>",
                ))

            fig_hist.add_trace(go.Scatter(
                name=storage_layer,
                x=hist_df["year"].astype(str),
                y=hist_df["storage"],
                mode="lines+markers",
                line=dict(color=_svc_line_color, width=2.5),
                marker=dict(color=_svc_line_color, size=6),
                hovertemplate=f"{storage_layer}: %{{y:,.0f}}M bu<extra></extra>",
            ))

            # Ratio annotations
            hist_anns = []
            for _, row in hist_df.iterrows():
                if pd.notna(row["ratio"]):
                    hist_anns.append(dict(
                        x=str(int(row["year"])),
                        y=row["total_supply"] * 1.04,
                        text=f"<b>{row['ratio']:.2f}</b>",
                        showarrow=False,
                        font=dict(size=10, color="#000000"),
                        yanchor="bottom",
                    ))

            fig_hist.update_layout(
                paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                font=dict(color=TEXT, family="Arial"),
                margin=dict(l=60, r=20, t=30, b=50),
                height=460,
                barmode="stack",
                showlegend=True,
                legend=dict(
                    font=dict(color=TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                    orientation="h", yanchor="bottom", y=1.02, x=0,
                ),
                xaxis=dict(tickfont=dict(color=TEXT), gridcolor=BORDER, categoryorder="array",
                           categoryarray=hist_df["year"].astype(str).tolist()),
                yaxis=dict(
                    title="Million Bushels",
                    gridcolor=BORDER, tickfont=dict(color=MUTED),
                    title_font=dict(color=MUTED),
                ),
                annotations=hist_anns,
                hovermode="x unified",
            )
            _add_logo(fig_hist, logo_50yr)
            _chart(fig_hist, use_container_width=True, config={"displayModeBar": False})

            # ── State detail table ─────────────────────────────────────────────
            st.markdown(
                f"<h4 style='color:{ACCENT};margin-bottom:4px;margin-top:20px;'>State Detail — {yr_svc}</h4>",
                unsafe_allow_html=True,
            )
            tbl_svc = df_chart.copy().sort_values("total_supply_bu", ascending=False)
            ratio_prev_col = tbl_svc["state"].map(ratio_prev_map)
            tbl_disp_svc = pd.DataFrame({
                "State":                tbl_svc["state"],
                "Corn & Sorg (M bu)":   (tbl_svc["corn_sorg_bu"] / 1e6).map(lambda v: f"{v:,.0f}"),
                "Soybeans (M bu)":      (tbl_svc["soybeans_bu"]  / 1e6).map(lambda v: f"{v:,.0f}"),
                "Wheat (M bu)":         (tbl_svc["wheat_bu"]     / 1e6).map(lambda v: f"{v:,.0f}"),
                "Oats (M bu)":          (tbl_svc["oats_bu"]      / 1e6).map(lambda v: f"{v:,.0f}"),
                "Total Supply (M bu)":  (tbl_svc["total_supply_bu"] / 1e6).map(lambda v: f"{v:,.0f}"),
                "Storage (M bu)":       (tbl_svc["storage_bu"]   / 1e6).map(lambda v: f"{v:,.0f}" if v > 0 else "—"),
                "Surplus/(Deficit)":    ((tbl_svc["storage_bu"] - tbl_svc["total_supply_bu"]) / 1e6).map(lambda v: f"{v:+,.0f}"),
                f"Ratio {yr_svc}":      tbl_svc["ratio"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—"),
                f"Ratio {yr_prev}":     ratio_prev_col.map(lambda v: f"{v:.2f}" if pd.notna(v) else "—"),
                "YoY Δ Ratio":          (tbl_svc["ratio"] - ratio_prev_col).map(lambda v: f"{v:+.2f}" if pd.notna(v) else "—"),
            })
            st.dataframe(tbl_disp_svc, use_container_width=True, hide_index=True)
            st.markdown(
                f"<p style='color:{MUTED};font-size:0.77rem;margin-top:4px;'>"
                f"Supply = annual production (corn+sorghum, soybeans, wheat, oats). "
                f"Storage layer: {storage_layer} (USDA FSA WCMD / USDA NASS Grain Storage Capacity). "
                "Ratio = Storage ÷ Supply; values &lt; 1.00 indicate a space deficit.</p>",
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # EIA BIOFUELS TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_eia:
        st.markdown(
            f"<h3 style='color:{ACCENT};margin-bottom:4px;'>US Biofuels — EIA Data</h3>"
            f"<p style='color:{MUTED};font-size:0.85rem;margin-top:0;'>"
            "Ethanol and biodiesel production by PADD region (weekly/monthly) and "
            "national feedstock consumption (corn → ethanol, soybean oil → biodiesel). "
            "Source: US Energy Information Administration (EIA) Open Data API.</p>",
            unsafe_allow_html=True,
        )

        _PADD_LABELS = {
            "NUS": "US Total",
            "R10": "PADD 1 — East Coast",
            "R20": "PADD 2 — Midwest",
            "R30": "PADD 3 — Gulf Coast",
            "R40": "PADD 4 — Rocky Mountain",
            "R50": "PADD 5 — West Coast",
        }
        _PADD_COLORS = {
            "NUS": "#94a3b8",
            "R10": "#60a5fa",
            "R20": "#34d399",
            "R30": "#f59e0b",
            "R40": "#c084fc",
            "R50": "#f87171",
        }
        _FEED_LABELS = {
            "EPOOBDAFC": "Corn (→ Ethanol)",
            "EPOOBDAFS": "Grain Sorghum (→ Ethanol)",
            "EPOOBDSO":  "Soybean Oil (→ Biodiesel)",
            "EPOOBDCNOD":"Corn Oil (→ Biodiesel)",
        }
        _FEED_COLORS = {
            "EPOOBDAFC": "#f59e0b",
            "EPOOBDAFS": "#94a3b8",
            "EPOOBDSO":  "#34d399",
            "EPOOBDCNOD":"#60a5fa",
        }

        if not EIA_API_KEY:
            st.warning(
                "⚠️ EIA API key not configured. "
                "Register for a free key at https://www.eia.gov/opendata/register.php "
                "then set `EIA_API_KEY` at the top of app.py."
            )
        else:
            @st.cache_data(ttl=3600, show_spinner=False)
            def load_eia_ethanol_weekly(weeks: int = 260) -> pd.DataFrame:
                """Weekly ethanol plant production by PADD (kbbl/day).
                Aggregated to monthly averages for trend charts."""
                rows = []
                for area in ["NUS", "R10", "R20", "R30", "R40", "R50"]:
                    params = urllib.parse.urlencode({
                        "api_key": EIA_API_KEY,
                        "frequency": "weekly",
                        "data[]": "value",
                        "facets[product][]": "EPOOXE",
                        "facets[process][]": "YOP",
                        f"facets[duoarea][]": area,
                        "sort[0][column]": "period",
                        "sort[0][direction]": "desc",
                        "length": str(weeks),
                    }, doseq=False)
                    url = f"{EIA_BASE_URL}petroleum/pnp/wprode/data?{params}"
                    try:
                        with urllib.request.urlopen(url, timeout=30) as r:
                            data = json.load(r).get("response", {}).get("data", [])
                        for rec in data:
                            try:
                                rows.append({
                                    "period": rec["period"],
                                    "padd": area,
                                    "kbblday": float(rec["value"]) if rec["value"] not in (None, "") else None,
                                })
                            except Exception:
                                pass
                    except Exception:
                        pass
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows)
                df["period_dt"] = pd.to_datetime(df["period"])
                df["mgal_day"] = df["kbblday"] * 42 / 1000   # kbbl/day → million gallons/day
                # Monthly averages for trend charting
                df["month"] = df["period_dt"].dt.to_period("M").dt.to_timestamp()
                monthly = df.groupby(["month", "padd"]).agg(
                    kbblday=("kbblday", "mean"),
                    mgal_day=("mgal_day", "mean"),
                ).reset_index()
                monthly = monthly.rename(columns={"month": "period"})
                return monthly.sort_values("period")

            @st.cache_data(ttl=3600, show_spinner=False)
            def load_eia_feedstocks(months: int = 72) -> pd.DataFrame:
                """Monthly biofuel feedstock consumption — national (million lbs)."""
                rows = []
                for prod in ["EPOOBDAFC", "EPOOBDAFS", "EPOOBDSO", "EPOOBDCNOD"]:
                    url = (
                        f"{EIA_BASE_URL}petroleum/pnp/feedbiofuel/data"
                        f"?api_key={EIA_API_KEY}"
                        f"&frequency=monthly"
                        f"&facets[product][]={prod}"
                        f"&facets[duoarea][]=NUS"
                        f"&data[]=value"
                        f"&sort[0][column]=period&sort[0][direction]=desc"
                        f"&length={months}"
                    )
                    try:
                        with urllib.request.urlopen(url, timeout=30) as r:
                            data = json.load(r).get("response", {}).get("data", [])
                        for rec in data:
                            try:
                                rows.append({
                                    "period": rec["period"],
                                    "product": prod,
                                    "mmlb": float(rec["value"]) if rec["value"] not in (None, "") else None,
                                })
                            except Exception:
                                pass
                    except Exception:
                        pass
                if not rows:
                    return pd.DataFrame()
                df = pd.DataFrame(rows)
                df["period"] = pd.to_datetime(df["period"])
                df["label"] = df["product"].map(_FEED_LABELS)
                df["mbu_corn"] = df.apply(
                    lambda r: r["mmlb"] * 1e6 / 56 / 1e6
                    if r["product"] == "EPOOBDAFC" else None, axis=1
                )
                df["mbu_sorg"] = df.apply(
                    lambda r: r["mmlb"] * 1e6 / 56 / 1e6
                    if r["product"] == "EPOOBDAFS" else None, axis=1
                )
                return df.sort_values("period")

            with st.spinner("Loading EIA data…"):
                eth_df  = load_eia_ethanol_weekly(weeks=365)
                feed_df = load_eia_feedstocks(months=72)

            if eth_df.empty and feed_df.empty:
                st.error("Could not fetch EIA data. Check API key or network connection.")
            else:
                # ── KPI row ───────────────────────────────────────────────────
                if not eth_df.empty:
                    latest_m = eth_df["period"].max()
                    prev_yr_m = latest_m - pd.DateOffset(years=1)
                    nat_latest = eth_df[(eth_df["padd"] == "NUS") & (eth_df["period"] == latest_m)]["kbblday"].sum()
                    nat_prev   = eth_df[(eth_df["padd"] == "NUS") &
                                        (eth_df["period"].dt.year == (latest_m - pd.DateOffset(years=1)).year) &
                                        (eth_df["period"].dt.month == latest_m.month)]["kbblday"].sum()
                    nat_mgal_yr = nat_latest * 42 / 1000 * 365
                    delta_pct   = (nat_latest - nat_prev) / nat_prev * 100 if nat_prev > 0 else 0
                else:
                    latest_m = None
                    nat_latest = nat_mgal_yr = delta_pct = 0

                corn_latest = feed_df[
                    (feed_df["product"] == "EPOOBDAFC") &
                    (feed_df["period"] == feed_df[feed_df["product"] == "EPOOBDAFC"]["period"].max())
                ]["mbu_corn"].sum() if not feed_df.empty else 0

                soy_latest = feed_df[
                    (feed_df["product"] == "EPOOBDSO") &
                    (feed_df["period"] == feed_df[feed_df["product"] == "EPOOBDSO"]["period"].max())
                ]["mmlb"].sum() if not feed_df.empty else 0

                k1, k2, k3, k4 = st.columns(4)
                k1.metric(
                    "US Ethanol Production",
                    f"{nat_latest:,.0f} kbbl/day" if nat_latest > 0 else "—",
                    delta=f"{delta_pct:+.1f}% YoY" if delta_pct != 0 else None,
                    help=f"Latest month: {latest_m.strftime('%b %Y') if latest_m else '—'}",
                )
                k2.metric(
                    "Annualized Rate",
                    f"{nat_mgal_yr:,.0f} M gal/yr" if nat_mgal_yr > 0 else "—",
                    help="kbbl/day × 42 gal × 365 days",
                )
                k3.metric(
                    "Corn Feedstock (latest mo.)",
                    f"{corn_latest:,.0f} M bu" if corn_latest > 0 else "—",
                    help="Corn consumed for ethanol production (million bushels)",
                )
                k4.metric(
                    "Soybean Oil Feedstock",
                    f"{soy_latest:,.0f} M lbs" if soy_latest > 0 else "—",
                    help="Soybean oil consumed for biodiesel production (million lbs)",
                )

                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

                # ── Controls ──────────────────────────────────────────────────
                ec1, ec2 = st.columns([2, 1])
                with ec1:
                    eia_yr_range = st.slider(
                        "Year range",
                        min_value=2010, max_value=datetime.datetime.now().year,
                        value=(2015, datetime.datetime.now().year),
                        key="eia_yr_range",
                    )
                with ec2:
                    eia_padds = st.multiselect(
                        "PADD Regions",
                        options=["R10","R20","R30","R40","R50"],
                        default=["R10","R20","R30","R40","R50"],
                        format_func=lambda x: _PADD_LABELS[x],
                        key="eia_padds",
                    )

                yr_start, yr_end = eia_yr_range

                # ── PADD Ethanol Production — stacked area / bar ──────────────
                st.markdown(
                    f"<h4 style='color:{ACCENT};margin-bottom:6px;margin-top:12px;'>"
                    "Ethanol Plant Production by PADD Region (Monthly, Million Gallons/Day)</h4>",
                    unsafe_allow_html=True,
                )

                if not eth_df.empty:
                    eth_plot = eth_df[
                        (eth_df["padd"].isin(eia_padds)) &
                        (eth_df["period"].dt.year >= yr_start) &
                        (eth_df["period"].dt.year <= yr_end)
                    ].copy()

                    fig_eth = go.Figure()
                    for area in ["R10","R20","R30","R40","R50"]:
                        if area not in eia_padds:
                            continue
                        sub = eth_plot[eth_plot["padd"] == area].sort_values("period")
                        fig_eth.add_trace(go.Scatter(
                            x=sub["period"], y=sub["mgal_day"],
                            mode="lines",
                            name=_PADD_LABELS[area],
                            line=dict(color=_PADD_COLORS[area], width=1.5),
                            stackgroup="one",
                            fillcolor=_PADD_COLORS[area],
                            hovertemplate=f"{_PADD_LABELS[area]}: %{{y:.1f}} Mgal/day<extra></extra>",
                        ))
                    # US Total overlay line (monthly avg)
                    nat_plot = eth_df[
                        (eth_df["padd"] == "NUS") &
                        (eth_df["period"].dt.year >= yr_start) &
                        (eth_df["period"].dt.year <= yr_end)
                    ].sort_values("period")
                    fig_eth.add_trace(go.Scatter(
                        x=nat_plot["period"], y=nat_plot["mgal_day"],
                        mode="lines",
                        name="US Total",
                        line=dict(color="#ffffff", width=2, dash="dot"),
                        hovertemplate="US Total: %{y:.1f} Mgal/day<extra></extra>",
                    ))
                    fig_eth.update_layout(
                        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                        font=dict(color=TEXT, family="Arial"),
                        margin=dict(l=60, r=20, t=20, b=50),
                        height=380,
                        showlegend=True,
                        legend=dict(font=dict(color=TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                                    orientation="h", yanchor="bottom", y=1.02, x=0),
                        xaxis=dict(title="Month", gridcolor=BORDER, tickfont=dict(color=MUTED),
                                   title_font=dict(color=MUTED)),
                        yaxis=dict(title="Million Gallons/Day", gridcolor=BORDER,
                                   tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
                        hovermode="x unified",
                    )
                    _add_logo(fig_eth, logo_50yr)
                    _chart(fig_eth, use_container_width=True, config={"displayModeBar": False})

                    # ── PADD snapshot bar (latest 12-month average) ───────────
                    st.markdown(
                        f"<h4 style='color:{ACCENT};margin-bottom:6px;margin-top:20px;'>"
                        "PADD Region Share — Latest 12-Month Average</h4>",
                        unsafe_allow_html=True,
                    )
                    cutoff_12m = eth_df["period"].max() - pd.DateOffset(months=11)
                    padd_avg = eth_df[
                        (eth_df["padd"] != "NUS") &
                        (eth_df["period"] >= cutoff_12m)
                    ].groupby("padd")["mgal_day"].mean().reset_index()
                    padd_avg["label"] = padd_avg["padd"].map(_PADD_LABELS)
                    padd_avg["color"] = padd_avg["padd"].map(_PADD_COLORS)
                    padd_avg = padd_avg.sort_values("mgal_day", ascending=True)
                    padd_avg["share_pct"] = padd_avg["mgal_day"] / padd_avg["mgal_day"].sum() * 100

                    fig_padd = go.Figure(go.Bar(
                        x=padd_avg["mgal_day"],
                        y=padd_avg["label"],
                        orientation="h",
                        marker_color=padd_avg["color"],
                        text=padd_avg.apply(lambda r: f"{r['mgal_day']:.1f} ({r['share_pct']:.0f}%)", axis=1),
                        textposition="outside",
                        textfont=dict(color=TEXT, size=10),
                        hovertemplate="%{y}: %{x:.1f} Mgal/day<extra></extra>",
                    ))
                    fig_padd.update_layout(
                        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                        font=dict(color=TEXT, family="Arial"),
                        margin=dict(l=20, r=100, t=10, b=50),
                        height=300,
                        xaxis=dict(title="Avg. Million Gallons/Day", gridcolor=BORDER,
                                   tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
                        yaxis=dict(tickfont=dict(color=TEXT), gridcolor=BORDER),
                    )
                    _add_logo(fig_padd, logo_50yr)
                    _chart(fig_padd, use_container_width=True, config={"displayModeBar": False})

                # ── Feedstock Consumption ─────────────────────────────────────
                if not feed_df.empty:
                    st.markdown("<hr style='border-color:#3a3f47;margin:24px 0 16px 0;'>",
                                unsafe_allow_html=True)
                    st.markdown(
                        f"<h4 style='color:{ACCENT};margin-bottom:4px;'>Biofuel Feedstock Consumption — National (Monthly)</h4>"
                        f"<p style='color:{MUTED};font-size:0.82rem;margin-top:0;'>"
                        "Feedstocks consumed at US biofuel plants. Corn converted to million bushels (÷ 56 lbs/bu). "
                        "Data available from Jan 2019. Source: EIA Monthly Biofuels Capacity and Feedstocks Update.</p>",
                        unsafe_allow_html=True,
                    )

                    feed_plot = feed_df[feed_df["period"].dt.year.between(yr_start, yr_end)].copy()

                    fig_feed = go.Figure()

                    # Corn on primary axis (million bu)
                    corn_sub = feed_plot[feed_plot["product"] == "EPOOBDAFC"].sort_values("period")
                    if not corn_sub.empty:
                        fig_feed.add_trace(go.Bar(
                            x=corn_sub["period"],
                            y=corn_sub["mbu_corn"],
                            name="Corn (M bu)",
                            marker_color=_FEED_COLORS["EPOOBDAFC"],
                            hovertemplate="Corn: %{y:.1f}M bu<extra></extra>",
                        ))

                    # Grain sorghum on primary axis (million bu), stacked with corn
                    sorg_sub = feed_plot[feed_plot["product"] == "EPOOBDAFS"].sort_values("period")
                    if not sorg_sub.empty:
                        fig_feed.add_trace(go.Bar(
                            x=sorg_sub["period"],
                            y=sorg_sub["mbu_sorg"],
                            name="Grain Sorghum (M bu)",
                            marker_color=_FEED_COLORS["EPOOBDAFS"],
                            hovertemplate="Grain Sorghum: %{y:,.1f}M bu<extra></extra>",
                        ))

                    # Soy oil on secondary axis (million lbs)
                    soy_sub = feed_plot[feed_plot["product"] == "EPOOBDSO"].sort_values("period")
                    if not soy_sub.empty:
                        fig_feed.add_trace(go.Scatter(
                            x=soy_sub["period"],
                            y=soy_sub["mmlb"],
                            name="Soy Oil (M lbs)",
                            mode="lines+markers",
                            line=dict(color=_FEED_COLORS["EPOOBDSO"], width=2),
                            marker=dict(size=4),
                            yaxis="y2",
                            hovertemplate="Soy Oil: %{y:,.0f}M lbs<extra></extra>",
                        ))

                    # Corn oil for biodiesel (secondary axis, M lbs)
                    cno_sub = feed_plot[feed_plot["product"] == "EPOOBDCNOD"].sort_values("period")
                    if not cno_sub.empty:
                        fig_feed.add_trace(go.Scatter(
                            x=cno_sub["period"],
                            y=cno_sub["mmlb"],
                            name="Corn Oil (M lbs)",
                            mode="lines+markers",
                            line=dict(color=_FEED_COLORS["EPOOBDCNOD"], width=1.5, dash="dot"),
                            marker=dict(size=4),
                            yaxis="y2",
                            hovertemplate="Corn Oil: %{y:,.0f}M lbs<extra></extra>",
                        ))

                    fig_feed.update_layout(
                        paper_bgcolor=DARK, plot_bgcolor=SURFACE,
                        font=dict(color=TEXT, family="Arial"),
                        margin=dict(l=60, r=70, t=20, b=50),
                        height=380,
                        barmode="stack",
                        showlegend=True,
                        legend=dict(font=dict(color=TEXT, size=10), bgcolor="rgba(0,0,0,0)",
                                    orientation="h", yanchor="bottom", y=1.02, x=0),
                        xaxis=dict(title="Month", gridcolor=BORDER,
                                   tickfont=dict(color=MUTED), title_font=dict(color=MUTED)),
                        yaxis=dict(title="Grain Feedstocks (Million Bushels)", gridcolor=BORDER,
                                   tickfont=dict(color=_FEED_COLORS["EPOOBDAFC"]),
                                   title_font=dict(color=_FEED_COLORS["EPOOBDAFC"])),
                        yaxis2=dict(title="Million Lbs (Oils)", overlaying="y", side="right",
                                    tickfont=dict(color=_FEED_COLORS["EPOOBDSO"], size=10),
                                    title_font=dict(color=_FEED_COLORS["EPOOBDSO"]),
                                    showgrid=False),
                        hovermode="x unified",
                    )
                    _add_logo(fig_feed, logo_50yr)
                    _chart(fig_feed, use_container_width=True, config={"displayModeBar": False})

                    # ── Annual feedstock table ────────────────────────────────
                    st.markdown(
                        f"<h4 style='color:{ACCENT};margin-bottom:6px;margin-top:20px;'>Annual Feedstock Summary</h4>",
                        unsafe_allow_html=True,
                    )
                    feed_ann = feed_df.copy()
                    feed_ann["year"] = feed_ann["period"].dt.year
                    corn_ann = feed_ann[feed_ann["product"] == "EPOOBDAFC"].groupby("year").agg(
                        corn_mbu=("mbu_corn", "sum"),
                        corn_mmlb=("mmlb", "sum"),
                    ).reset_index()
                    sorg_ann = feed_ann[feed_ann["product"] == "EPOOBDAFS"].groupby("year").agg(
                        sorg_mbu=("mbu_sorg", "sum"),
                        sorg_mmlb=("mmlb", "sum"),
                    ).reset_index()
                    soy_ann  = feed_ann[feed_ann["product"] == "EPOOBDSO"].groupby("year").agg(
                        soy_mmlb=("mmlb", "sum"),
                    ).reset_index()
                    cno_ann  = feed_ann[feed_ann["product"] == "EPOOBDCNOD"].groupby("year").agg(
                        cno_mmlb=("mmlb", "sum"),
                    ).reset_index()
                    ann_tbl = corn_ann.merge(sorg_ann, on="year", how="outer") \
                                      .merge(soy_ann, on="year", how="outer") \
                                      .merge(cno_ann, on="year", how="outer") \
                                      .sort_values("year", ascending=False)

                    ann_disp = pd.DataFrame({
                        "Year":                  ann_tbl["year"].astype(str),
                        "Corn for Ethanol (M bu)": ann_tbl["corn_mbu"].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "—"),
                        "Grain Sorghum for Ethanol (M bu)": ann_tbl["sorg_mbu"].map(lambda v: f"{v:,.1f}" if pd.notna(v) else "—"),
                        "Soy Oil for Biodiesel (M lbs)": ann_tbl["soy_mmlb"].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "—"),
                        "Corn Oil for Biodiesel (M lbs)": ann_tbl["cno_mmlb"].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "—"),
                    })
                    st.dataframe(ann_disp, use_container_width=True, hide_index=True)

                    st.caption(
                        "EIA Monthly Biofuels Capacity and Feedstocks Update (petroleum/pnp/feedbiofuel). "
                        "Feedstock data available from Jan 2019 – present. "
                        "Ethanol production data: petroleum/pnp/wprode, PADD 1–5 + US Total, Jun 2010 – present."
                    )

    # ── Disclaimer footer ────────────────────────────────────────────────────
    st.markdown("<hr style='border-color:#3a3f47;margin-top:40px;margin-bottom:12px;'>", unsafe_allow_html=True)
    st.markdown(
        f"""<p style='color:#6b7280;font-size:0.72rem;line-height:1.55;text-align:center;
            max-width:960px;margin:0 auto 24px auto;'>
        Trading commodity futures, options on futures, cash commodities, and over-the-counter
        derivative products involves substantial risk of loss and may not be suitable for all
        investors. This communication is provided for informational purposes only and does not
        constitute investment advice, a recommendation, or an offer or solicitation to buy or
        sell any futures, options, cash commodities, or derivative products. John Stewart &amp;
        Associates, Inc. does not accept orders to buy or sell any financial instruments via
        email. The information contained herein has been obtained from sources believed to be
        reliable; however, its accuracy and completeness are not guaranteed. Any opinions
        expressed are solely those of the author, are subject to change without notice, and
        should not be relied upon as a basis for investment decisions. Past performance is not
        indicative of future results. This message may contain confidential or proprietary
        information intended solely for the use of the designated recipient.
        &copy; John Stewart &amp; Associates, Inc. {datetime.datetime.now().year}
        </p>""",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()

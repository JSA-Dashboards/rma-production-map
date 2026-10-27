"""
heat_map.py - the Heat Map tab: where a crop is concentrated.

Kolten 2026-10-27: "I want to start to work on a heat map but first can we take the NASS & RMA production map that breaks down into county and ASD levels ... then build heat map around
that", then "Lets start with production concentration map but we will trend towards the basis on the map concept."

How it is built. The functions here are pure (values in, frames / GeoJSON out) so test_heat_map.py can run them on a toy map; the Streamlit tab is render_tab() at the bottom.

  county values  NASS: each county NASS publishes, as published, plus the counties it withheld, filled so every state adds up to the official NASS state total (fill_withheld: a withheld
                 county takes its usual share of what the published counties leave, from earlier years' NASS or from RMA); the glow adds up to the number on the NASS tab;
                 RMA:  the insured production / acres of the 2025 workbook, as reported.
  points         a county's value is spread evenly over a regular lattice of points inside its own polygon (lattice_cells / spread_values), so the heat follows county shapes and conserves
                 every bushel / acre. (Random dots were tried first: at state zoom they leave a speckled surface that looks like a pattern and is only noise.)
  heat           a deck.gl HeatmapLayer draws the points as a smooth surface on the Carto light basemap the Basis Tracker's Map tab already uses.
  outlines       state or NASS-district (ASD) outlines on top carry the hover numbers; districts are the counties dissolved by NASS district code, as in the NASS tab.

build_deck takes extra pydeck layers, so basis pins / a basis surface can be added later as one more layer on the same canvas.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

CARTO_LIGHT = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"      # the basemap of the Basis Tracker's Map tab
US_VIEW = (38.8, -96.5, 3.55)                                                         # lat, lon, zoom

# Low density is transparent so the basemap shows through; six stops is all deck.gl takes.
PALETTES = {
    "Heat":  [[255, 255, 178, 0], [254, 217, 118, 95], [254, 178, 76, 145], [253, 141, 60, 195], [240, 59, 32, 228], [189, 0, 38, 255]],
    "Green": [[225, 240, 230, 0], [190, 225, 200, 85], [130, 200, 160, 145], [70, 165, 120, 195], [30, 120, 85, 228], [10, 75, 55, 255]],
}


# -- the county shapes --------------------------------------------------------------
class CountyIndex:
    """Shapely shapes of every county in the Plotly county GeoJSON, with what the tab needs from them. Built once per process."""

    def __init__(self, geo: dict):
        self.shapes: dict = {}
        self.state_of: dict = {}
        self.names: dict = {}
        self.state_bounds: dict = {}
        for feat in geo["features"]:
            p = feat["properties"]
            fips = p["STATE"] + p["COUNTY"]
            try:
                g = shape(feat["geometry"])
            except Exception:                                             # noqa: BLE001 - one bad polygon must not take the map down
                continue
            if g.is_empty:
                continue
            shapely.prepare(g)
            self.shapes[fips] = g
            self.state_of[fips] = p["STATE"]
            self.names[fips] = p.get("NAME", fips)
            b = g.bounds
            cur = self.state_bounds.get(p["STATE"])
            self.state_bounds[p["STATE"]] = b if cur is None else (min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3]))


# -- values -> points ---------------------------------------------------------------
LATTICE_STEP = 0.1          # degrees (about 9 x 11 km): fine enough that the surface is smooth at state zoom, coarse enough that the whole US is some 80,000 points


def lattice_cells(shapes: dict, step: float = LATTICE_STEP) -> dict:
    """{fips: (lons, lats)}: the centres of a regular `step`-degree lattice that fall inside each county. The lattice is anchored on multiples of `step`, so neighbouring counties share
    one lattice and meet without a seam. A county too small to hold a lattice point (an independent city, a sliver) gets its representative point, so it still counts."""
    out: dict = {}
    for fips, g in shapes.items():
        minx, miny, maxx, maxy = g.bounds
        xs = np.arange(math.floor(minx / step) * step + step / 2, maxx, step)
        ys = np.arange(math.floor(miny / step) * step + step / 2, maxy, step)
        keep = None
        if len(xs) and len(ys):
            gx, gy = np.meshgrid(xs, ys)
            gx, gy = gx.ravel(), gy.ravel()
            keep = shapely.contains_xy(g, gx, gy)
        if keep is not None and keep.any():
            out[fips] = (gx[keep], gy[keep])
        else:
            p = g.representative_point()
            out[fips] = (np.array([p.x]), np.array([p.y]))
    return out


def spread_values(cells: dict, values: dict) -> pd.DataFrame:
    """Spread each county's value evenly over its lattice points: [lon, lat, w, fips], where w is the point's share, so the weights add up to the county's value exactly and the heat
    layer sums them into a density. Deterministic: the same values always give the same surface."""
    lon, lat, w, f = [], [], [], []
    for fips, v in values.items():
        c = cells.get(fips)
        if c is None or not (v and v > 0):
            continue
        n = len(c[0])
        lon.append(c[0])
        lat.append(c[1])
        w.append(np.full(n, float(v) / n))
        f.extend([fips] * n)
    if not lon:
        return pd.DataFrame({"lon": [], "lat": [], "w": [], "fips": []})
    return pd.DataFrame({"lon": np.round(np.concatenate(lon), 4), "lat": np.round(np.concatenate(lat), 4), "w": np.round(np.concatenate(w), 1), "fips": f})


MIN_GAP = 0.005             # a state whose published counties come within 0.5% of its official total counts as complete
LOOKBACK = 3                # earlier years consulted for a withheld county's share of its state


def gap_states(county_df: pd.DataFrame, state_totals: dict) -> dict:
    """{State: amount the published counties fall short of the official state total}, for the states short by more than MIN_GAP. NASS publishes fewer county figures each year: the published
    counties held about 90% of the US corn crop in 2022 and 73% in 2024 and 2025 (Illinois: 100% in 2022, 63% in 2025). county_df [State, fips, Value]; state_totals {State: total}."""
    sums = county_df.groupby("State")["Value"].sum() if len(county_df) else pd.Series(dtype=float)
    out = {}
    for st_, tot in state_totals.items():
        if tot and tot > 0:
            short = float(tot) - float(sums.get(st_, 0.0))
            if short > MIN_GAP * float(tot):
                out[st_] = short
    return out


def _shares(county_df: pd.DataFrame, state_totals: dict) -> dict:
    """{State: {fips: the county's share of its state's official total}} for one year; a county without a positive value, or in a state without a total, is left out."""
    out: dict = {}
    for st_, fips, v in zip(county_df["State"], county_df["fips"], county_df["Value"]):
        t = state_totals.get(st_)
        if t and t > 0 and v and v > 0:
            d = out.setdefault(st_, {})
            d[fips] = d.get(fips, 0.0) + float(v) / float(t)
    return out


def fill_withheld(county_df: pd.DataFrame, state_totals: dict, history=(), fallback: pd.DataFrame | None = None, apply: bool = True) -> tuple:
    """Complete a year's counties to NASS's official state totals. The counties NASS published keep their published numbers; what a state's published counties leave of its official total is
    spread over its withheld counties in proportion to the share each held of its state in the latest earlier year NASS published it (the county's usual weight within the state), or, for a
    county NASS never published in those years, its share of the state's `fallback` values (RMA's insured production). Every state then adds up to NASS's total, and a published county is
    never scaled. A county with no earlier figure and no fallback gets nothing: if a state has no such county at all, its remainder is left off the map and listed in info['short'].
      county_df     [State, fips, Value]   the counties published this year
      state_totals  {State: official total}
      history       [(county_df, state_totals), ...] earlier years, most recent first, same shapes
      fallback      [State, fips, Value] any value whose county proportions follow the crop (RMA production)
    Returns (frame [State, fips, Value, Estimated], info); with apply=False (or when no state is short) the frame holds the published counties only. info holds
      coverage         published share of the official totals (states over 100% count as 100%)
      estimated        {State: (withheld counties filled, amount filled)}      estimated_n / estimated_total   the same, summed
      short            [(State, amount)] remainder that nothing could be placed on, largest first"""
    df = county_df[["State", "fips", "Value"]]
    df = df[df["Value"] > 0].copy()                                           # a (D) comes through as 0: that county is withheld, not empty
    df["Estimated"] = False
    gaps = gap_states(df, state_totals)
    sums = df.groupby("State")["Value"].sum() if len(df) else pd.Series(dtype=float)
    den = sum(float(t) for t in state_totals.values() if t and t > 0)
    num = sum(min(float(sums.get(s, 0.0)), float(t)) for s, t in state_totals.items() if t and t > 0)
    info = {"coverage": (num / den) if den else None, "estimated": {}, "estimated_n": 0, "estimated_total": 0.0, "short": []}
    if not apply or not gaps:
        return df, info
    past = [_shares(c, t) for c, t in history]
    rma: dict = {}
    if fallback is not None and len(fallback):
        fsum = fallback.groupby("State")["Value"].sum()
        for st_, fips, v in zip(fallback["State"], fallback["fips"], fallback["Value"]):
            if v and v > 0 and fsum.get(st_, 0) > 0:
                rma.setdefault(st_, {})[fips] = float(v) / float(fsum[st_])
    have = set(df["fips"])
    rows = []
    for st_, short in gaps.items():
        w: dict = {}
        for src in [h.get(st_, {}) for h in past] + [rma.get(st_, {})]:        # latest earlier year first, RMA last
            for fips, s in src.items():
                if fips not in have and fips not in w:
                    w[fips] = s
        total_w = sum(w.values())
        if total_w <= 0:
            info["short"].append((st_, short))
            continue
        rows.extend((st_, fips, short * s / total_w, True) for fips, s in w.items())
        info["estimated"][st_] = (len(w), short)
    if rows:
        df = pd.concat([df, pd.DataFrame(rows, columns=["State", "fips", "Value", "Estimated"])], ignore_index=True)
    info["estimated_n"] = len(rows)
    info["estimated_total"] = sum(a for _, a in info["estimated"].values())
    info["short"].sort(key=lambda t: -t[1])
    return df, info


_SUFFIXES = (" county", " parish", " city and borough", " borough", " census area", " municipality")          # 'city and borough' before 'borough', or Juneau keeps its 'city and'


def norm_county(name: str) -> str:
    """A county name reduced to what survives the spelling differences between RMA and the Census file: 'St. Clair' / 'St Clair' / 'Saint Clair', "O'Brien" / 'O Brien', 'De Kalb' / 'DeKalb',
    'Prince George's' ..., case, punctuation and spaces gone."""
    s = str(name or "").strip().lower()
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
    s = s.replace(".", " ").replace("'", "").replace("’", "")
    s = " ".join(s.split())
    s = " ".join({"saint": "st", "sainte": "st", "ste": "st"}.get(w, w) for w in s.split())        # Ste. Genevieve is 'St Genevieve' in RMA
    return s.replace(" ", "").replace("-", "")


_ALIASES = {("SD", "oglalalakota"): "46113",          # Shannon County, renamed Oglala Lakota in 2015; the county file still has the old FIPS
            ("AK", "kusilvak"): "02270"}               # Wade Hampton Census Area, renamed Kusilvak in 2015


def make_resolver(lookup: dict):
    """(state abbr, county name) -> FIPS or None, from build_name_lookup: the normalized name, then the renamed counties, then an independent city written 'Suffolk City'."""
    def resolve(state: str, county: str):
        k = norm_county(county)
        f = lookup.get((state, k)) or _ALIASES.get((state, k))
        if f is None and k.endswith("city"):
            f = lookup.get((state, k[:-4]))
        return f
    return resolve


def build_name_lookup(geo: dict, state_abbr: dict) -> dict:
    """{(state abbr, normalized county name): fips} from the county GeoJSON. state_abbr is {state FIPS: abbreviation}. Where a county and an independent city share a name (St. Louis,
    Baltimore, Fairfax ...) the county wins, because both RMA and NASS mean the county."""
    out: dict = {}
    feats = sorted(geo["features"], key=lambda f: 0 if str(f["properties"].get("LSAD", "County")).lower() in ("county", "parish", "borough", "census area", "cty") else 1)
    for feat in feats:
        p = feat["properties"]
        ab = state_abbr.get(p["STATE"])
        if not ab:
            continue
        out.setdefault((ab, norm_county(p["NAME"])), p["STATE"] + p["COUNTY"])
    return out


def rma_county_values(frame: pd.DataFrame, column: str, practice: str, resolver) -> tuple:
    """County values from one RMA crop sheet: [State, County, PG, <column>] -> ({fips: value}, unresolved [(State, County)]). `resolver(state, county)` gives the FIPS or None. `practice` is
    All / Irrigated / Non-Irrigated."""
    df = frame if practice == "All" else frame[frame["PG"] == practice]
    g = df.groupby(["State", "County"], as_index=False)[column].sum()
    out, miss = {}, []
    for st_, county, v in zip(g["State"], g["County"], g[column]):
        f = resolver(st_, county)
        if f is None:
            miss.append((st_, county))
        elif v > 0:
            out[f] = out.get(f, 0.0) + float(v)
    return out, miss


# -- outlines ------------------------------------------------------------------------
def _round_coords(obj, nd: int = 3):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(c), nd) for c in obj]
        return [_round_coords(o, nd) for o in obj]
    return obj


def outline_collection(index: CountyIndex, group_of: dict, props_of: dict, simplify: float = 0.02) -> dict:
    """County shapes dissolved by `group_of` {fips: group key} into one outline per group: a GeoJSON FeatureCollection whose feature properties are props_of[group key]. Counties with
    no group are left out."""
    parts: dict = {}
    for fips, key in group_of.items():
        g = index.shapes.get(fips)
        if g is not None and key is not None:
            parts.setdefault(key, []).append(g)
    feats = []
    for key, gs in parts.items():
        u = unary_union(gs).simplify(simplify, preserve_topology=True)
        m = mapping(u)
        feats.append({"type": "Feature", "geometry": {"type": m["type"], "coordinates": _round_coords(m["coordinates"])}, "properties": {**props_of.get(key, {}), "key": key}})
    return {"type": "FeatureCollection", "features": feats}


def district_groups(frame: pd.DataFrame) -> tuple:
    """From a NASS county frame (fips, State, asd_code, asd_desc) -> ({fips: district key}, {district key: name, state}). The key is state FIPS + district code, because district codes repeat
    across states."""
    if frame is None or frame.empty or "asd_code" not in frame.columns:
        return {}, {}
    f = frame.dropna(subset=["fips", "asd_code"]).drop_duplicates("fips")
    group, props = {}, {}
    for fips, code, desc, st_ in zip(f["fips"], f["asd_code"], f.get("asd_desc", pd.Series([""] * len(f))), f["State"]):
        code = str(code).strip()
        if not code:
            continue
        key = f"{fips[:2]}-{code}"
        group[fips] = key
        title = str(desc).strip().title() if pd.notna(desc) and str(desc).strip() else f"District {code}"
        props[key] = {"name": f"{title}, {st_}", "state": st_}
    return group, props


def attach_values(collection: dict, totals: dict, fmt) -> dict:
    """A copy of the collection with each outline's summed value on its properties for the tooltip: value (number) and label (formatted). `totals` is {group key: value}, keyed like the
    features' 'key' property. The input is never changed (it may be a cached object shared by every session)."""
    feats = []
    for feat in collection["features"]:
        p = dict(feat["properties"])
        v = float(totals.get(p.get("key"), 0.0))
        p["value"] = round(v, 1)
        p["label"] = fmt(v)
        feats.append({**feat, "properties": p})
    return {**collection, "features": feats}


# -- view and deck --------------------------------------------------------------------
def view_for(bounds, width_px: int = 900, height_px: int = 560) -> tuple:
    """(lat, lon, zoom) that fits a lon/lat bounding box (minx, miny, maxx, maxy) in a map of the given size, with a little margin."""
    if not bounds:
        return US_VIEW
    minx, miny, maxx, maxy = bounds
    lat = (miny + maxy) / 2
    dx = max(maxx - minx, 0.05)
    dy = max(maxy - miny, 0.05)
    zx = math.log2(width_px * 360 / (512 * dx))
    zy = math.log2(height_px * 360 * math.cos(math.radians(lat)) / (512 * dy))
    return lat, (minx + maxx) / 2, round(max(2.5, min(7.5, min(zx, zy) - 0.35)), 2)


def radius_px(km: float, lat: float, zoom: float) -> int:
    """The HeatmapLayer radius, in screen pixels, that smooths over about `km` kilometres at the view's latitude and zoom. The layer's visible kernel is only about a third of its radius,
    so a fixed pixel radius is far too tight at state zoom (the lattice rows show as stripes): the smoothing is asked for in kilometres and converted here."""
    m_per_px = 78271.517 * math.cos(math.radians(lat)) / (2 ** zoom)         # Web-Mercator metres per pixel on a 512-px-tile map (deck.gl / mapbox-gl)
    return int(max(12, min(220, round(km * 1000 / m_per_px))))


def fmt_amount(v: float, unit: str) -> str:
    """12,345,678 -> '12.3M bu' style label for a hover or KPI."""
    a = abs(v)
    if a >= 1e9:
        s = f"{v / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"{v / 1e6:.1f}M"
    elif a >= 1e3:
        s = f"{v / 1e3:.0f}K"
    else:
        s = f"{v:,.0f}"
    return f"{s} {unit}".strip()


def build_deck(points: pd.DataFrame, *, radius: int = 38, intensity: float = 1.0, palette: str = "Heat", outlines: dict | None = None, view: tuple = US_VIEW,
               extra_layers: list | None = None):
    """The pydeck Deck: the heat surface, the outlines (hover for numbers) and any extra layers (basis pins later)."""
    import pydeck as pdk
    layers = []
    if points is not None and len(points):
        layers.append(pdk.Layer(
            "HeatmapLayer", data=points[["lon", "lat", "w"]].to_dict("records"), get_position="[lon, lat]", get_weight="w", radius_pixels=int(radius), intensity=float(intensity),
            threshold=0.04, aggregation="SUM", color_range=PALETTES.get(palette, PALETTES["Heat"]), opacity=0.92, pickable=False))
    if outlines and outlines.get("features"):
        layers.append(pdk.Layer(
            "GeoJsonLayer", data=outlines, stroked=True, filled=True, get_fill_color=[0, 0, 0, 1], get_line_color=[63, 107, 79, 190], line_width_min_pixels=1,
            pickable=True, auto_highlight=True, highlight_color=[94, 113, 100, 60]))
    layers.extend(extra_layers or [])
    lat, lon, zoom = view
    tooltip = {"html": "<b>{name}</b><br/>{label}", "style": {"backgroundColor": "#ffffff", "color": "#32373c", "border": "1px solid #d7e2dc", "fontFamily": "Source Sans 3, sans-serif"}}
    return pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=zoom, pitch=0), map_style=CARTO_LIGHT, tooltip=tooltip)


# -- the Streamlit tab ------------------------------------------------------------------
try:
    import streamlit as st
except ImportError:                                           # the pure functions above work without it (test_heat_map.py)
    st = None

_MUTED, _TEXT = "#5f7267", "#32373c"
NASS_MEASURES = {"Production (bu)": ("production", "bu"), "Harvested Acres": ("harvested", "ac"), "Planted Acres": ("planted", "ac")}
RMA_MEASURES = {"Production (bu)": ("Reported Production", "bu"), "Production Acres": ("Reported Production Acres", "ac")}
RMA_CROPS = ("Corn", "Soybeans", "Wheat")
RMA_YEAR = 2025
_VER = "v1"                                                   # bump to rebuild the cached shapes / lattice / outlines

if st is not None:
    @st.cache_resource(show_spinner=False)
    def _county_index(_geo, ver: str) -> CountyIndex:
        return CountyIndex(_geo)

    @st.cache_resource(show_spinner=False)
    def _lattice(_index: CountyIndex, ver: str, step: float) -> dict:
        return lattice_cells(_index.shapes, step)

    @st.cache_resource(show_spinner=False)
    def _state_outlines(_index: CountyIndex, ver: str, _names: dict) -> dict:
        group = dict(_index.state_of)                                                  # every county, so a state is outlined whole whatever has data
        props = {s: {"name": _names.get(s, s)} for s in set(group.values())}
        return outline_collection(_index, group, props, simplify=0.02)

    @st.cache_resource(show_spinner=False)
    def _district_outlines(_index: CountyIndex, sig: str, _group: dict, _props: dict) -> dict:
        return outline_collection(_index, _group, _props, simplify=0.015)


def _field_label(text: str) -> None:
    st.markdown(f"<p style='color:{_MUTED};font-size:0.78rem;margin:4px 0 2px 0;'>{text}</p>", unsafe_allow_html=True)


def _show_deck(deck) -> None:
    try:
        st.pydeck_chart(deck, height=640, width="stretch")
    except TypeError:                                          # an older Streamlit without width=
        st.pydeck_chart(deck, height=640, use_container_width=True)


def render_tab(*, rma_data: dict, geo: dict, state_fips: dict, abbr_to_name: dict, load_county, load_state, load_districts, nass_years: list, nass_crops: list, current_year: int) -> None:
    """The Heat Map tab. The app hands in its own loaders so this module never talks to NASS itself:
       load_county(crop, year, stat) -> [State, County, fips, Value]     load_state(crop, year, stat) -> [State, Value]     load_districts(crop, year) -> NASS county frame with asd_code / asd_desc."""
    inv = {v: k for k, v in state_fips.items()}                                       # state FIPS -> abbreviation
    index = _county_index(geo, _VER)
    cells = _lattice(index, _VER, LATTICE_STEP)

    st.markdown(f"<p style='color:{_MUTED};font-size:0.86rem;margin:2px 0 8px 0;'>Where a crop is concentrated: each county's value is spread over its own area and drawn as a heat "
                "surface, so the glow adds up to the totals below. Hover a state or district for its number.</p>", unsafe_allow_html=True)
    _field_label("Source")
    source = st.radio("Source", ["NASS (all acres, survey)", "RMA (insured acres, 2025)"], horizontal=True, key="heat_source", label_visibility="collapsed")
    is_nass = source.startswith("NASS")

    c1, c2, c3, c4 = st.columns([1, 0.8, 1.2, 1.2])
    with c1:
        crop = st.selectbox("Crop", nass_crops if is_nass else [c for c in RMA_CROPS if c in rma_data], key="heat_crop_n" if is_nass else "heat_crop_r")
    with c2:
        if is_nass:
            years = [y for y in nass_years if y < current_year]                       # completed years: the current year has no county survey until January
            year = st.selectbox("Year", years, key="heat_year")
        else:
            year = RMA_YEAR
            st.selectbox("Year", [RMA_YEAR], key="heat_year_rma", disabled=True)
    with c3:
        measure = st.selectbox("Measure", list(NASS_MEASURES) if is_nass else list(RMA_MEASURES), key="heat_measure_n" if is_nass else "heat_measure_r")
    with c4:
        if is_nass:
            _field_label("Counties")
            fill = st.checkbox("Fill withheld counties", value=True, key="heat_fill",
                               help="NASS publishes fewer county figures each year, so the published counties can add up to well under the state (US corn 2025: 73%). On: what a state's published "
                                    "counties leave of NASS's state total is spread over its withheld counties by their share in earlier years (RMA where a county was never published), so every "
                                    "state adds up to NASS's total and no published figure is changed. Off: the published counties only.")
            practice = "All"
        else:
            practice = st.selectbox("Practice", ["All", "Irrigated", "Non-Irrigated"], key="heat_practice")
            fill = False

    d1, d2, d3, d4 = st.columns([1.3, 1.2, 1, 1.5])
    with d1:
        names_sorted = sorted(state_fips, key=lambda a: abbr_to_name.get(a, a))
        focus = st.selectbox("Zoom to", ["— US —"] + [f"{a} — {abbr_to_name.get(a, a)}" for a in names_sorted], key="heat_focus")
        focus_abbr = None if focus.startswith("—") else focus[:2]
    with d2:
        _field_label("Outlines")
        outline = st.radio("Outlines", ["States", "Districts", "None"], horizontal=True, key="heat_outline", label_visibility="collapsed")
    with d3:
        _field_label("Colors")
        palette = st.radio("Colors", list(PALETTES), horizontal=True, key="heat_palette", label_visibility="collapsed")
    view = view_for(index.state_bounds[state_fips[focus_abbr]]) if focus_abbr else US_VIEW
    with d4:
        smooth_km = st.slider("Smoothing (km)", 20, 300, 70 if focus_abbr else 200, 10, key=f"heat_smooth_{'state' if focus_abbr else 'us'}",
                              help="How far each county's value is blurred. Wide shows regions; narrow shows counties. Re-set whenever you change the zoom.")

    # -- county values --------------------------------------------------------------
    def rma_values(rma_crop: str, column: str, which: str) -> tuple:
        frame = rma_data[rma_crop]
        if rma_crop == "Wheat" and "Type" in frame.columns:
            frame = frame[~frame["Type"].astype(str).str.lower().str.contains("khor")]          # the RMA tab leaves Khorasan out too
        return rma_county_values(frame, column, which, make_resolver(build_name_lookup(geo, inv)))

    info, est_fips = None, set()
    if is_nass:
        stat, unit = NASS_MEASURES[measure]
        with st.spinner(f"Loading NASS {year} {crop} county {measure.lower()}…"):
            cdf = load_county(crop, year, stat)
            sdf = load_state(crop, year, stat)
        if cdf is None or cdf.empty:
            st.info(f"No county {measure.lower()} for {crop} {year} could be loaded (NASS may not publish it, or the request failed). Try another year, or press Refresh on the NASS tab.")
            return
        cdf = cdf.groupby(["State", "fips"], as_index=False)["Value"].sum()
        totals = dict(zip(sdf["State"], sdf["Value"])) if sdf is not None and not sdf.empty else {}
        past, fallback = [], None
        if fill and gap_states(cdf, totals):                                                    # earlier years are only fetched when a state is short
            with st.spinner(f"Filling withheld counties from earlier years ({year - LOOKBACK}-{year - 1})…"):
                for yr in range(year - 1, year - 1 - LOOKBACK, -1):
                    hc, hs = load_county(crop, yr, stat), load_state(crop, yr, stat)
                    if hc is not None and not hc.empty and hs is not None and not hs.empty:
                        past.append((hc.groupby(["State", "fips"], as_index=False)["Value"].sum(), dict(zip(hs["State"], hs["Value"]))))
            if crop in rma_data:                                                                 # a county NASS never published in those years: its share of the state's insured crop
                rvals, _ = rma_values(crop, RMA_MEASURES["Production (bu)" if stat == "production" else "Production Acres"][0], "All")
                fallback = pd.DataFrame([(inv[f[:2]], f, v) for f, v in rvals.items() if f[:2] in inv], columns=["State", "fips", "Value"])
        filled, info = fill_withheld(cdf, totals, past, fallback, apply=fill)
        values = {f: float(v) for f, v in zip(filled["fips"], filled["Value"]) if v and v > 0}
        est_fips = set(filled.loc[filled["Estimated"], "fips"])
        district_crop, district_year = crop, year
    else:
        col, unit = RMA_MEASURES[measure]
        values, unresolved = rma_values(crop, col, practice)
        district_crop, district_year = crop, RMA_YEAR
    outside = sum(v for f, v in values.items() if f not in cells)                           # values on a county the shape file lacks (a renamed or new county)
    values = {f: v for f, v in values.items() if f in cells}
    total = sum(values.values())
    if total <= 0:
        st.info("Nothing to draw for this selection.")
        return

    # -- outlines ---------------------------------------------------------------------
    by_state: dict = {}
    for f, v in values.items():
        by_state[index.state_of[f]] = by_state.get(index.state_of[f], 0.0) + v
    label = lambda v: fmt_amount(v, unit)                                                    # noqa: E731
    outlines, top_name, top_val = None, None, 0.0
    if outline == "States":
        outlines = attach_values(_state_outlines(index, _VER, {s: abbr_to_name.get(a, a) for a, s in state_fips.items()}), by_state, label)
        if by_state:
            s_top = max(by_state, key=by_state.get)
            top_name, top_val = abbr_to_name.get(inv.get(s_top, s_top), s_top), by_state[s_top]
    elif outline == "Districts":
        groups, props = {}, {}
        for yr in (district_year, district_year - 1, district_year - 2):                  # a county that did not report this year still has its district from an earlier one
            g, p = district_groups(load_districts(district_crop, yr))
            for k, v in g.items():
                groups.setdefault(k, v)
            for k, v in p.items():
                props.setdefault(k, v)
        if groups:
            by_district: dict = {}
            for f, v in values.items():
                k = groups.get(f)
                if k:
                    by_district[k] = by_district.get(k, 0.0) + v
            outlines = attach_values(_district_outlines(index, f"{district_crop}|{district_year}|{len(groups)}", groups, props), by_district, label)
            if by_district:
                k_top = max(by_district, key=by_district.get)
                top_name, top_val = f"{props[k_top]['name']}", by_district[k_top]
        else:
            st.caption("District outlines need the NASS county list, which could not be loaded; showing none.")

    # -- the map ----------------------------------------------------------------------
    points = spread_values(cells, values)
    deck = build_deck(points, radius=radius_px(smooth_km, view[0], view[2]), palette=palette, outlines=outlines, view=view)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(measure, label(total), help=f"{crop}, {year}: the total the glow adds up to.")
    n_est = sum(1 for f in values if f in est_fips)
    k2.metric("Counties drawn", f"{len(values):,}", help=f"{len(values) - n_est:,} published by NASS and {n_est:,} withheld ones filled in." if n_est else None)
    if is_nass and info and info["coverage"] is not None:
        k3.metric("Published", f"{info['coverage']:.0%}", help="Share of NASS's official state totals that sits in counties NASS published." + (" The rest is filled in over its withheld counties." if n_est else ""))
    elif not is_nass:
        k3.metric("Basis", "Insured acres", help="RMA counts federally insured acres only, so it differs from the NASS survey of all acres.")
    else:
        k3.metric("Published", "—")
    k4.metric("Largest " + ("district" if outline == "Districts" else "state"), top_name or "—",
              help=(f"{label(top_val)}, {top_val / total:.0%} of the total." if top_name else "Switch Outlines to States or Districts to see the biggest one."))
    _show_deck(deck)
    gradient = ", ".join(f"rgba({r},{g},{b},{max(a, 25) / 255:.2f})" for r, g, b, a in PALETTES[palette])
    st.markdown(f"<div style='display:flex;align-items:center;gap:10px;margin:2px 0 6px 0;font-size:0.78rem;color:{_MUTED}'><span>Less concentrated</span>"
                f"<div style='flex:0 0 220px;height:10px;border-radius:5px;background:linear-gradient(90deg,{gradient});border:1px solid #d7e2dc'></div><span>More concentrated</span></div>",
                unsafe_allow_html=True)
    notes = [f"Each county's {measure.lower()} is spread evenly over its area (lattice of {LATTICE_STEP}° points), then blurred about {smooth_km} km; the legend is relative to the busiest part of the view, "
             "so zooming in re-scales it."]
    if is_nass and info:
        if not fill:
            notes.append("NASS counties are shown as published, so the counties NASS withheld are missing from the glow.")
        elif info["estimated_n"]:
            big = ", ".join(f"{s} {fmt_amount(a, unit)}" for s, (n, a) in sorted(info["estimated"].items(), key=lambda t: -t[1][1])[:5])
            notes.append(f"NASS withheld {info['estimated_n']:,} counties' figures ({sum(v for f, v in values.items() if f in est_fips) / total:.0%} of the total). Each is filled with its usual share of what "
                         f"its state's published counties leave of NASS's state total, from the latest of the {LOOKBACK} earlier years NASS published it (RMA if it never did); published figures are not "
                         f"changed. Largest fills: {big}.")
        if fill and info["short"]:
            miss = ", ".join(f"{s} ({fmt_amount(t, unit)})" for s, t in info["short"][:6])
            notes.append(f"No county to put these on (none published in the {LOOKBACK} earlier years or in RMA), so they are left off the map: {miss}.")
    if not is_nass and unresolved:
        notes.append(f"{len(unresolved)} RMA county name(s) could not be matched to a county shape and are left out.")
    if outside > 0:
        notes.append(f"{fmt_amount(outside, unit)} sat on counties missing from the shape file and is left out.")
    st.caption("  \n".join(notes))

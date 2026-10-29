"""
Interactive rail map for the Map tab: Class I + CN, TPW, Iowa Interstate and Watco-operated lines (BTS NTAD rail network),
the navigable grain rivers highlighted, and markers for crush plants, ethanol / corn processors and the railroads' grain shippers.
Data files are built by build_rail_lines.py, build_waterways.py and build_map_points.py.
"""
import gzip
import json
import os
import re

import plotly.graph_objects as go
import streamlit as st

_DATA = os.path.join(os.path.dirname(__file__), "data", "rail_map")
if not os.path.isdir(_DATA):                       # the Rail FOB portal keeps the files in data/ itself
    _DATA = os.path.join(os.path.dirname(__file__), "data")

# NTAD owner code -> (label, colour, width)
RAILROADS = {
    "CN": ("CN", "#d4272e", 1.6), "UP": ("UP", "#e8a317", 1.1), "BNSF": ("BNSF", "#f26b21", 1.1),
    "CPKC": ("CPKC", "#7a1f2b", 1.0), "CSXT": ("CSX", "#1f4e9c", 1.1), "NS": ("NS", "#2d2d2d", 1.1),
    "TPW": ("Toledo, Peoria & Western", "#0a9396", 2.4), "IAIS": ("Iowa Interstate", "#7b2cbf", 2.4),
    "WSOR": ("Watco: Wisconsin & Southern", "#2a9d8f", 2.2), "KO": ("Watco: Kansas & Oklahoma", "#8ab17d", 2.2),
    # short lines serving plants and shippers in the belt (codes are the NTAD owner marks; names are our reading of them)
    "RRVW": ("Red River Valley & Western", "#c2185b", 2.0), "NKCR": ("Nebraska Kansas & Colorado RailNet", "#6d4c41", 2.0),
    "IORY": ("Indiana & Ohio Railway", "#00897b", 2.0), "RCPE": ("Rapid City, Pierre & Eastern", "#5e35b1", 2.0),
    "DMVW": ("Dakota, Missouri Valley & Western", "#039be5", 2.0), "CVR": ("Cimarron Valley", "#7cb342", 2.0),
    "CFE": ("Chicago, Fort Wayne & Eastern", "#ef6c00", 2.0), "INRD": ("Indiana Rail Road", "#d81b60", 2.0),
    "TRRA": ("Terminal Railroad Assn of St. Louis", "#455a64", 2.0), "KCTL": ("Kansas City Terminal", "#8d6e63", 2.0),
    "ALS": ("Alton & Southern", "#546e7a", 2.0), "DN": ("Dakota Northern", "#3949ab", 2.0), "KYLE": ("Kyle Railroad", "#827717", 2.0),
    "RJCW": ("R.J. Corman (RJCW)", "#00acc1", 2.0), "RJCC": ("R.J. Corman (RJCC)", "#26a69a", 2.0), "RJCM": ("R.J. Corman (RJCM)", "#43a047", 2.0),
}
DEFAULT_ON = list(RAILROADS)
SHORT_GRAIN_LINES = ["RRVW", "NKCR", "IORY", "RCPE", "DMVW", "CVR", "CFE", "INRD", "TRRA", "KCTL", "ALS", "DN", "KYLE", "RJCW", "RJCC", "RJCM"]
# Grain rivers drawn as the highlighted system (NTAD river names)
GRAIN_RIVERS = ["MISSISSIPPI RIVER", "ILLINOIS RIVER", "OHIO RIVER", "MISSOURI RIVER", "ARKANSAS RIVER", "TENNESSEE RIVER",
                "CUMBERLAND RIVER", "KENTUCKY RIVER", "MONONGAHELA RIVER", "ALLEGHENY RIVER", "KANAWHA RIVER", "COLUMBIA RIVER",
                "SNAKE RIVER", "WHITE RIVER", "ATCHAFALAYA RIVER", "MINNESOTA RIVER", "GULF INTRACOASTAL WATERWAY"]
GAL_PER_BU = 3.0                                  # ethanol gallons per bushel of corn, to put ethanol capacity in bushels
PROPOSED_COLOR, IDLE_COLOR, CLOSED_COLOR = "#f2b705", "#9aa0a6", "#d3d6da"
STATUS_GROUP = {"Run": "live", "Expand": "live", "Proposed": "proposed", "Build": "proposed", "Idle": "idle", "Idled": "idle", "Hold": "idle",
                "Closed": "closed", "Repurposed": "closed"}
MIN_DOT, MAX_DOT = 5, 26                          # plant marker diameters (px)
SHIPPER_COLORS = {"UP": "#e8a317", "BNSF": "#f26b21", "CN": "#d4272e", "CSX": "#1f4e9c", "NS": "#2d2d2d"}


@st.cache_data(show_spinner=False)
def _load(name):
    with open(os.path.join(_DATA, name), encoding="utf8") as fh:
        return json.load(fh)


KIND_NAME = {"z": "ZIP", "c": "county", "p": "town"}
KIND_ZOOM = {"z": 10, "c": 8, "p": 10}


@st.cache_data(show_spinner=False)
def _geo_index():
    """US towns, counties and ZIP areas (Census gazetteer; build_geo_search.py): the rows and their lower-cased search keys."""
    with gzip.open(os.path.join(_DATA, "geo_search.json.gz"), "rt", encoding="utf8") as fh:
        rows = json.load(fh)
    return rows, [re.sub(r"\s+", " ", r[0].lower().replace(",", " ")).strip() for r in rows]


def search_places(query, limit=12):
    """Rows for a town ("Peoria IL", "peoria, il"), a county ("Story County IA") or a ZIP (or its first digits), best matches first."""
    q = re.sub(r"\s+", " ", query.lower().replace(",", " ")).strip()
    if not q:
        return []
    rows, keys = _geo_index()
    digits = q.isdigit()
    hits = []
    for r, k in zip(rows, keys):
        if digits:
            if r[3] == "z" and k.startswith(q):
                hits.append((0, k, r))
        elif r[3] != "z":
            if k.startswith(q):
                hits.append((0, k, r))
            elif q in k:
                hits.append((1, k, r))
    hits.sort(key=lambda h: (h[0], h[2][3] != "c", len(h[1]), h[1]))
    return [h[2] for h in hits[:limit]]


def _line_xy(paths, step=0.1):
    """Paths as one None-separated line. With `step`, long straight stretches get extra points so a hover (which only
    fires on a vertex) finds the line wherever the mouse is on it."""
    lon, lat = [], []
    for p in paths:
        for i, (x, y) in enumerate(p):
            if step and i:
                px, py = p[i - 1]
                n = int(max(abs(x - px), abs(y - py)) / step)
                for k in range(1, n + 1):
                    lon.append(round(px + (x - px) * k / (n + 1), 3))
                    lat.append(round(py + (y - py) * k / (n + 1), 3))
            lon.append(x)
            lat.append(y)
        lon.append(None)
        lat.append(None)
    return lon, lat


CROP_SCALES = {"Corn": "YlOrBr", "Soybeans": "Greens", "Wheat": "Purples"}


def _production_layer(crop):
    """County choropleth of the 5-year average NASS production for a crop (bushels; withheld counties already filled)."""
    prod = _load("county_prod_avg.json")
    avg = prod["avg"][crop]
    fips = list(avg)
    vals = [avg[f] for f in fips]
    top = float(sorted(vals)[int(len(vals) * 0.97)])          # cap the scale at the 97th percentile so a few giants don't wash the rest out
    yrs = prod["years"][crop]
    return go.Choroplethmap(
        geojson=_load("county_geo.json"), locations=fips, z=vals, zmin=0, zmax=top, colorscale=CROP_SCALES[crop],
        marker=dict(opacity=0.65, line=dict(width=0.2, color="rgba(90,90,90,0.5)")),
        colorbar=dict(title=dict(text=f"{crop}<br>5-yr avg bu<br>{yrs[0]}-{yrs[-1]}"), thickness=12, len=0.45, y=0.2, x=0.995),
        customdata=[[prod["names"].get(f, f), f"{v / 1e6:,.2f}"] for f, v in zip(fips, vals)],
        hovertemplate="%{customdata[0]}<br>" + crop + " 5-yr avg: %{customdata[1]} M bu<extra></extra>", name=f"{crop} 5-yr avg")


def build_figure(rails, rivers, show_crush, show_ethanol, shipper_roads, height=720, crop=None, states=True, show_closed=False, focus=None):
    fig = go.Figure()
    if crop:
        fig.add_trace(_production_layer(crop))
    if states:
        lon, lat = _line_xy(_load("state_lines.json"), step=0)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=1, color="#555"), name="State lines",
                                    hoverinfo="skip"))
    lines = _load("rail_lines.json")
    if rivers:
        water = _load("waterways.json")
        paths = [p for r in GRAIN_RIVERS for p in water.get(r, [])]
        lon, lat = _line_xy(paths)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=4, color="#4aa3ff"), name="Grain rivers",
                                    hovertemplate="Grain rivers<extra></extra>", opacity=0.75))
    for code in rails:
        label, color, width = RAILROADS[code]
        lon, lat = _line_xy(lines.get(code, []))
        short = code in SHORT_GRAIN_LINES                  # one legend entry for the 16 short lines (hover still names each; the entry toggles them all)
        first = short and code == next(c for c in SHORT_GRAIN_LINES if c in rails)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=width, color=color), name=label,
                                    hovertemplate="%{fullData.name}<extra></extra>",
                                    legendgroup="shortlines" if short else None, showlegend=(not short) or first))
        if first:
            fig.data[-1].name = "Other short lines (hover for names)"
            fig.data[-1].hovertemplate = label + "<extra></extra>"
        elif short:
            fig.data[-1].hovertemplate = label + "<extra></extra>"
    pts = _load("map_points.json")
    for road in shipper_roads:
        rows = [r for r in pts["shipper"] if r["road"] == road]
        fig.add_trace(go.Scattermap(
            lon=[r["lon"] for r in rows], lat=[r["lat"] for r in rows], mode="markers",
            marker=dict(size=6, color=SHIPPER_COLORS[road]), name=f"{road} grain shippers ({len(rows)})",
            text=[f'{r["name"]}, {r["state"]}' + (f' ({r["cars"]}-car shuttle)' if r.get("cars") else " (shuttle)" if r.get("shuttle") else "") for r in rows], hoverinfo="text"))
    # marker area follows capacity in bushels a year: crush as listed, ethanol converted from gallons at GAL_PER_BU (or the corn use quoted
    # for a Canadian plant). Colour follows status: live (the type's colour), proposed / under construction (gold), idled / on hold (grey).
    for flag, key, name, color, cap, div, ref in ((show_ethanol, "ethanol", "Ethanol / corn processing", "#2e9e4f", "mgy", GAL_PER_BU, 50.0),
                                                  (show_crush, "crush", "Crush plants", "#7b3fbf", "mbu", 1.0, 70.0)):
        if not flag:
            continue
        for group, label, gcolor in (("live", "live", color), ("proposed", "proposed / building", PROPOSED_COLOR),
                                     ("idle", "idled / on hold", IDLE_COLOR), ("closed", "closed", CLOSED_COLOR)):
            if group == "closed" and not show_closed:
                continue
            rows = [r for r in pts[key] if STATUS_GROUP.get(r.get("status"), "live") == group]
            if not rows:
                continue
            bu = [r["mbu"] if r.get("mbu") else ((r[cap] / div) if r.get(cap) else None) for r in rows]
            sizes = [MIN_DOT + (MAX_DOT - MIN_DOT) * min(1.0, (b or 0) / ref) ** 0.5 for b in bu]
            text = [f'{r["name"]}, {r["city"]} {r["state"]}' + (" (Canada)" if r.get("country") == "CA" else "") + "<br>"
                    + (f'{b:,.1f} M bu/yr' + (f' of corn ({r[cap]:,.0f} M gal ÷ {GAL_PER_BU:g})' if key == "ethanol" and r.get(cap) else "")
                    if b else "capacity not listed") + f' · {r.get("status") or label}' for r, b in zip(rows, bu)]
            fig.add_trace(go.Scattermap(
                lon=[r["lon"] for r in rows], lat=[r["lat"] for r in rows], mode="markers",
                marker=dict(size=sizes, color=gcolor, opacity=0.85), name=f"{name}: {label} ({len(rows)})", text=text, hoverinfo="text"))
    view = dict(center=dict(lat=40.5, lon=-91), zoom=4.6)
    if focus:                                                    # a searched place: pin it and zoom to it
        label, lat, lon, kind = focus
        fig.add_trace(go.Scattermap(lon=[lon], lat=[lat], mode="markers+text", text=[label], textposition="top center",
                                    marker=dict(size=16, color="#e4002b"), name=f"Search: {label}", hovertext=label, hoverinfo="text"))
        view = dict(center=dict(lat=lat, lon=lon), zoom=KIND_ZOOM[kind])
    fig.update_layout(map=dict(style="carto-positron", **view), height=height,
                      margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h", y=1.0, font=dict(size=11), bgcolor="rgba(255,255,255,0.85)"))
    return fig


def render():
    st.markdown("### Rail Network Map")
    q_col, pick_col = st.columns([2, 3])
    query = q_col.text_input("Search a town, county or ZIP", placeholder="e.g. Peoria IL, Story County IA, 61602", key="railmap_search")
    focus = None
    if query.strip():
        found = search_places(query)
        if found:
            i = pick_col.selectbox("Matches", range(len(found)), key="railmap_pick",
                                   format_func=lambda j: f"{found[j][0]} ({KIND_NAME[found[j][3]]})")
            focus = tuple(found[i])
        else:
            pick_col.caption("No match. Try a town and state (Peoria IL), a county (Story County IA) or a 5-digit ZIP.")
    c1, c2, c3 = st.columns([3, 2, 2])
    rails = c1.multiselect("Railroads", list(RAILROADS), default=DEFAULT_ON, format_func=lambda k: RAILROADS[k][0], key="railmap_rails")
    with c2:
        rivers = st.checkbox("Highlight grain rivers", value=True, key="railmap_rivers")
        crush = st.checkbox("Crush plants", value=True, key="railmap_crush")
        ethanol = st.checkbox("Ethanol / corn processing", value=True, key="railmap_eth")
        closed = st.checkbox("Include closed plants", value=False, key="railmap_closed")
    crop = c1.selectbox("County production overlay (5-yr avg)", [None, "Corn", "Soybeans", "Wheat"], key="railmap_crop",
                        format_func=lambda c: "None" if c is None else c)
    states = c2.checkbox("State boundaries", value=True, key="railmap_states")
    shippers = c3.multiselect("Grain shippers by railroad", list(SHIPPER_COLORS), default=list(SHIPPER_COLORS), key="railmap_ship")
    st.plotly_chart(build_figure(rails, rivers, crush, ethanol, shippers, crop=crop, states=states, show_closed=closed, focus=focus), use_container_width=True, key="railmap_fig")
    st.caption("Rail lines: US DOT BTS North American Rail Network (NTAD); rivers: USACE navigable waterway network via BTS NTAD (2018). "
               "Shippers: BNSF and CN directories with coordinates; CSX and NS only where the station's city is geocoded. "
               "County overlay: NASS county production, 5-year average of the last five crop years (counties NASS withheld are filled by the basis tracker's heat builder). Crush and ethanol markers are JSA's plant lists, placed by city (live plants in colour, proposed or under construction in gold, idled in grey; includes Canadian plants near the border), sized by annual capacity in bushels (ethanol gallons ÷ 3.0 gal/bu, crush as listed). "
               "[CN GeoMapGuide](https://cnebusiness.geomapguide.ca/) · [TPW](https://www.gwrr.com/railroads/toledo-peoria-and-western-railway) · "
               "[Iowa Interstate](https://iaisrr.com/ship-with-iais/what-where-how/) · [Watco system map](https://www.watco.com/system-map/)")

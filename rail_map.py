"""
Interactive rail map for the Map tab: Class I + CN, TPW, Iowa Interstate and Watco-operated lines (BTS NTAD rail network),
the navigable grain rivers highlighted, and markers for crush plants, ethanol / corn processors and the railroads' grain shippers.
Data files are built by build_rail_lines.py, build_waterways.py and build_map_points.py.
"""
import json
import os

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
}
DEFAULT_ON = list(RAILROADS)
# Grain rivers drawn as the highlighted system (NTAD river names)
GRAIN_RIVERS = ["MISSISSIPPI RIVER", "ILLINOIS RIVER", "OHIO RIVER", "MISSOURI RIVER", "ARKANSAS RIVER", "TENNESSEE RIVER",
                "CUMBERLAND RIVER", "KENTUCKY RIVER", "MONONGAHELA RIVER", "ALLEGHENY RIVER", "KANAWHA RIVER", "COLUMBIA RIVER",
                "SNAKE RIVER", "WHITE RIVER", "ATCHAFALAYA RIVER", "MINNESOTA RIVER", "GULF INTRACOASTAL WATERWAY"]
MIN_DOT, MAX_DOT = 5, 26                          # plant marker diameters (px)
SHIPPER_COLORS = {"BNSF": "#f26b21", "CN": "#d4272e", "CSX": "#1f4e9c", "NS": "#2d2d2d"}


@st.cache_data(show_spinner=False)
def _load(name):
    with open(os.path.join(_DATA, name), encoding="utf8") as fh:
        return json.load(fh)


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


def build_figure(rails, rivers, show_crush, show_ethanol, shipper_roads, height=720, crop=None, states=True):
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
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=width, color=color), name=label,
                                    hovertemplate="%{fullData.name}<extra></extra>"))
    pts = _load("map_points.json")
    for road in shipper_roads:
        rows = [r for r in pts["shipper"] if r["road"] == road]
        fig.add_trace(go.Scattermap(
            lon=[r["lon"] for r in rows], lat=[r["lat"] for r in rows], mode="markers",
            marker=dict(size=6, color=SHIPPER_COLORS[road]), name=f"{road} grain shippers ({len(rows)})",
            text=[f'{r["name"]}, {r["state"]}' + (" (shuttle)" if r.get("shuttle") else "") for r in rows], hoverinfo="text"))
    # marker area follows capacity: ethanol in million gal/yr, crush in million bu/yr (a plant with no capacity listed gets the smallest dot)
    for flag, key, name, color, cap, unit, ref in ((show_ethanol, "ethanol", "Ethanol / corn processing", "#2e9e4f", "mgy", "M gal/yr", 150.0),
                                                   (show_crush, "crush", "Crush plants", "#7b3fbf", "mbu", "M bu/yr", 70.0)):
        if flag:
            rows = pts[key]
            sizes = [MIN_DOT + (MAX_DOT - MIN_DOT) * min(1.0, (r.get(cap) or 0) / ref) ** 0.5 for r in rows]
            text = [f'{r["name"]}, {r["city"]} {r["state"]}<br>' + (f'{r[cap]:,.0f} {unit}' if r.get(cap) else "capacity not listed")
                    + (f' · {r["status"]}' if r.get("status") else "") for r in rows]
            fig.add_trace(go.Scattermap(
                lon=[r["lon"] for r in rows], lat=[r["lat"] for r in rows], mode="markers",
                marker=dict(size=sizes, color=color, opacity=0.85), name=f"{name} ({len(rows)})", text=text, hoverinfo="text"))
    fig.update_layout(map=dict(style="carto-positron", center=dict(lat=40.5, lon=-91), zoom=4.6), height=height,
                      margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h", y=1.0, bgcolor="rgba(255,255,255,0.85)"))
    return fig


def render():
    st.markdown("### Rail Network Map")
    c1, c2, c3 = st.columns([3, 2, 2])
    rails = c1.multiselect("Railroads", list(RAILROADS), default=DEFAULT_ON, format_func=lambda k: RAILROADS[k][0], key="railmap_rails")
    with c2:
        rivers = st.checkbox("Highlight grain rivers", value=True, key="railmap_rivers")
        crush = st.checkbox("Crush plants", value=True, key="railmap_crush")
        ethanol = st.checkbox("Ethanol / corn processing", value=True, key="railmap_eth")
    crop = c1.selectbox("County production overlay (5-yr avg)", [None, "Corn", "Soybeans", "Wheat"], key="railmap_crop",
                        format_func=lambda c: "None" if c is None else c)
    states = c2.checkbox("State boundaries", value=True, key="railmap_states")
    shippers = c3.multiselect("Grain shippers by railroad", list(SHIPPER_COLORS), default=list(SHIPPER_COLORS), key="railmap_ship")
    st.plotly_chart(build_figure(rails, rivers, crush, ethanol, shippers, crop=crop, states=states), use_container_width=True, key="railmap_fig")
    st.caption("Rail lines: US DOT BTS North American Rail Network (NTAD); rivers: USACE navigable waterway network via BTS NTAD (2018). "
               "Shippers: BNSF and CN directories with coordinates; CSX and NS only where the station's city is geocoded. "
               "County overlay: NASS county production, 5-year average of the last five crop years (counties NASS withheld are filled by the basis tracker's heat builder). Crush and ethanol markers are JSA's plant lists, placed by city, sized by capacity (ethanol: M gal/yr, crush: M bu/yr). "
               "[CN GeoMapGuide](https://cnebusiness.geomapguide.ca/) · [TPW](https://www.gwrr.com/railroads/toledo-peoria-and-western-railway) · "
               "[Iowa Interstate](https://iaisrr.com/ship-with-iais/what-where-how/) · [Watco system map](https://www.watco.com/system-map/)")

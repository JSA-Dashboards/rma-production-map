"""
Draw Territory tab (soybeans): where each county's beans go, crush plants against the river terminals.
The logic is in draw_model.py; this file only places the controls and draws the result.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import draw_model as dm
import rail_map as rm

FOCUS_STATES = ["IL", "IN", "IA", "MO", "WI", "KY", "OH"]
RIVER_COLOR = "#9ecae1"
OTHER_PLANT_COLOR = "#bdbdbd"
PALETTE = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf", "#17becf", "#bcbd22", "#7f7f7f", "#1b9e77", "#d95f02"]


def _in_box(paths, box):
    """The paths with a vertex inside (west, south, east, north), so the map carries only the region's lines."""
    w, so, e, n = box
    return [p for p in paths if any(w <= x <= e and so <= y <= n for x, y in p[::3] + p[-1:])]


@st.cache_data(show_spinner="Solving the draw…")
def _solve(rate, tau, river_premium, util, river_share, plant_premium_items):
    inp = dm.load()
    res = dm.solve(inp, rate=rate, tau=tau, river_premium=river_premium, util=util, river_share=river_share,
                   plant_premium=dict(plant_premium_items))
    return res


def render():
    inp = dm.load()
    st.markdown("### Draw Territory: soybeans")
    st.caption("Where each county's beans go. Crush plants fill to their annual need (Daily Max × 88% × 350 days, from the Crush Downtime "
               "Calculator); the river terminals take a share of what is left. Beans go to the nearest buyers with limited crossover. "
               "First pass: a model to set the structure, not a forecast.")
    c1, c2, c3, c4 = st.columns(4)
    state = c1.selectbox("Region to show", FOCUS_STATES, index=0, key="draw_state")
    tau = c2.slider("Crossover (cents/bu)", 0.25, 4.0, 1.0, 0.25, key="draw_tau",
                    help="How much a county's beans spread across the nearer plants. Small = sharp territories, large = a lot of overlap.")
    util = c3.slider("Plant run rate vs workbook", 0.7, 1.1, 1.0, 0.05, key="draw_util",
                     help="1.0 = the workbook's Daily Max × 0.88 × 350 days.")
    river_share = c4.slider("River share of non-crush beans", 0.0, 1.0, 0.6, 0.05, key="draw_river_share",
                            help="Of the beans left after crush, the share that moves through the river terminals. The rest (feed, rail export to the "
                                 "coast, Mexico, stocks) is taken out of every county evenly. A placeholder until you set it.")
    c5, c6 = st.columns(2)
    rate = c5.number_input("Truck freight (cents / bu / mile)", 0.005, 0.10, dm.CENTS_PER_BU_MILE, 0.005, format="%.3f", key="draw_rate",
                           help="Your note: 3 cents a bushel for 152 miles = 0.02.")
    river_prem = c6.number_input("River bid over crush bids (cents/bu)", -30.0, 30.0, 0.0, 1.0, key="draw_river_prem",
                                 help="How much more (or less) the river terminals pay for a bean than the crush plants, after their basis. "
                                      "Positive pulls beans toward the river.")
    o1, o2 = st.columns([1, 3])
    show_rivers = o1.checkbox("Rivers", value=True, key="draw_rivers")
    rails = o2.multiselect("Railroads", list(rm.RAILROADS), default=["UP", "BNSF", "CSXT", "NS", "CN", "TPW", "IAIS"],
                           format_func=lambda k: rm.RAILROADS[k][0], key="draw_rails")
    with st.expander("Plant bid adjustments (cents/bu over the base, per plant)"):
        keys = [p["key"] for p in inp["plants"] if p["state"] == state] or [p["key"] for p in inp["plants"][:6]]
        prem = {}
        cols = st.columns(3)
        for i, k in enumerate(keys):
            prem[k] = cols[i % 3].number_input(k, -20.0, 20.0, 0.0, 0.5, key=f"draw_prem_{k}")
    res = _solve(rate, tau, river_prem, util, river_share, tuple(sorted((k, v) for k, v in prem.items() if v)))
    plants_t, river_t, cty_t = dm.summarize(inp, res, state)

    in_state = [c for c in inp["counties"] if c["state"] == state]
    supply = sum(c["supply_bu"] for c in in_state) / 1e6
    st_plants = [r for r in plants_t if r["state"] == state]
    need_local = sum(r["need_mbu"] for r in st_plants)
    to_pl = sum(r["from_state_mbu"] for r in plants_t)
    to_local = sum(r["from_state_mbu"] for r in st_plants)
    to_rv = sum(r["from_state_mbu"] for r in river_t)
    other = supply - to_pl - to_rv
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(f"{state} soybean supply", f"{supply:,.0f} M bu", help="5-year average NASS county production")
    m2.metric(f"{state} crush need", f"{need_local:,.0f} M bu", help="Plants in the state, at the run rate above")
    m3.metric(f"{state} beans to crush", f"{to_pl:,.0f} M bu", f"{to_local:,.0f} to {state} plants", delta_color="off")
    m4.metric(f"{state} beans to river", f"{to_rv:,.0f} M bu")
    m5.metric("Other (feed, rail export, stocks)", f"{max(other, 0):,.0f} M bu")

    # map: each county coloured by where most of its beans go
    top_load = {}
    for r in plants_t:
        top_load[r["plant"]] = r["from_state_mbu"]
    ranked = [k for k, _ in sorted(top_load.items(), key=lambda kv: -kv[1]) if top_load[k] > 0][:len(PALETTE)]
    color_of = {k: PALETTE[i] for i, k in enumerate(ranked)}
    fips, colors, hover = [], [], []
    for c in cty_t:
        if c["state"] not in FOCUS_STATES:
            continue
        fips.append(c["fips"])
        colors.append(RIVER_COLOR if c["top_is_river"] else color_of.get(c["top_buyer"], OTHER_PLANT_COLOR))
        hover.append(f'{c["county"]}<br>{c["supply_mbu"]:.1f} M bu<br>mostly to: {c["top_buyer"]} ({c["top_share_pct"]:.0f}%)<br>{c["to_plants_pct"]:.0f}% to crush plants')
    geo = rm._load("county_geo.json")
    order = {f: i for i, f in enumerate(fips)}
    fig = go.Figure(go.Choroplethmap(
        geojson={"type": "FeatureCollection", "features": [f for f in geo["features"] if f["id"] in order]}, locations=fips,
        z=[order[f] for f in fips], colorscale=[[0, "#ffffff"], [1, "#ffffff"]], showscale=False, hovertext=hover, hoverinfo="text",
        marker=dict(opacity=0.0, line=dict(width=0.3, color="#888")), name="Counties"))
    # one fill trace per colour so the legend can name the buyers
    for label, col in [(k, v) for k, v in color_of.items()] + [("River terminals (top buyer)", RIVER_COLOR), ("Other plants", OTHER_PLANT_COLOR)]:
        sel = [f for f, c_ in zip(fips, colors) if c_ == col]
        if not sel:
            continue
        fig.add_trace(go.Choroplethmap(
            geojson={"type": "FeatureCollection", "features": [f for f in geo["features"] if f["id"] in set(sel)]}, locations=sel,
            z=[1] * len(sel), colorscale=[[0, col], [1, col]], showscale=False, marker=dict(opacity=0.62, line=dict(width=0.3, color="#888")),
            hovertext=[h for h, c_ in zip(hover, colors) if c_ == col], hoverinfo="text", name=label, showlegend=True))
    sc0 = [c for c in inp["counties"] if c["state"] == state]
    box = (min(c["lon"] for c in sc0) - 2.2, min(c["lat"] for c in sc0) - 2.2, max(c["lon"] for c in sc0) + 2.2, max(c["lat"] for c in sc0) + 2.2)
    if show_rivers:                                                      # the navigable grain rivers, as on the Rail Map tab
        water = rm._load("waterways.json")
        lon, lat = rm._line_xy(_in_box([p for r in rm.GRAIN_RIVERS for p in water.get(r, [])], box), step=0)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=3.5, color="#4aa3ff"), name="Rivers", hoverinfo="skip"))
    lines = rm._load("rail_lines.json")
    for code in rails:
        label, color, width = rm.RAILROADS[code]
        lon, lat = rm._line_xy(_in_box(lines.get(code, []), box), step=0.1)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=max(1.0, width * 0.8), color=color), name=label,
                                    hovertemplate="%{fullData.name}<extra></extra>"))
    lon, lat = rm._line_xy(_in_box(rm._load("state_lines.json"), box), step=0)
    fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=2.2, color="#2b2b2b"), name="State lines", hoverinfo="skip"))
    pts = [p for p in inp["plants"] if p["state"] in FOCUS_STATES]
    fig.add_trace(go.Scattermap(
        lon=[p["lon"] for p in pts], lat=[p["lat"] for p in pts], mode="markers+text", marker=dict(size=[8 + 14 * (p["need_bu"] / 1e8) ** 0.5 for p in pts], color="#222"),
        text=[p["city"] for p in pts], textposition="top right", textfont=dict(size=10), name="Crush plants",
        hovertext=[f'{p["key"]}<br>need {p["need_bu"] / 1e6:,.1f} M bu/yr' for p in pts], hoverinfo="text"))
    rv = [r for r in inp["river"] if 33.5 <= r["lat"] <= 46.5 and -95.5 <= r["lon"] <= -80 and r["key"] != "PNW export (rail)"]   # a few tracker pins sit far from any river
    fig.add_trace(go.Scattermap(lon=[r["lon"] for r in rv], lat=[r["lat"] for r in rv], mode="markers", marker=dict(size=6, color="#08519c"),
                                name="River terminals", hovertext=[r["key"] for r in rv], hoverinfo="text"))
    sc = [c for c in inp["counties"] if c["state"] == state]
    fig.update_layout(map=dict(style="carto-positron", center=dict(lat=float(np.mean([c["lat"] for c in sc])), lon=float(np.mean([c["lon"] for c in sc]))),
                               zoom=5.4 if state in ("IL", "IN", "OH", "KY") else 5.0),
                      height=640, margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h", y=1.0, bgcolor="rgba(255,255,255,0.85)", font=dict(size=11)))
    st.plotly_chart(fig, use_container_width=True, key="draw_map")
    st.caption("Fill = where most of a county's beans go (colour = that crush plant; light blue = a river terminal; grey = a plant outside the list). "
               "Counties spread beans across several buyers: hover for the top buyer's share.")

    st.markdown(f"**Plants that draw {state} beans** (annual, M bu)")
    df = pd.DataFrame(plants_t)
    df = df[df["from_state_mbu"] > 0.5].sort_values("from_state_mbu", ascending=False)
    df = df.rename(columns={"plant": "Plant", "state": "St", "need_mbu": "Need", "drawn_mbu": "Total drawn", "from_state_mbu": f"From {state}",
                            "avg_miles": "Avg haul (mi)", "river_access": "River/CIF"})
    st.dataframe(df.style.format({"Need": "{:,.1f}", "Total drawn": "{:,.1f}", f"From {state}": "{:,.1f}", "Avg haul (mi)": "{:,.0f}"}),
                 hide_index=True, use_container_width=True)
    st.markdown(f"**River terminals drawing {state} beans** (top 15, annual M bu)")
    rt = pd.DataFrame(river_t)
    rt = rt[rt["from_state_mbu"] > 0.05].sort_values("from_state_mbu", ascending=False).head(15)
    rt = rt.rename(columns={"terminal": "Terminal", "state": "St", "drawn_mbu": "Total drawn", "from_state_mbu": f"From {state}", "avg_miles": "Avg haul (mi)"})
    st.dataframe(rt.style.format({"Total drawn": "{:,.1f}", f"From {state}": "{:,.1f}", "Avg haul (mi)": "{:,.0f}"}), hide_index=True, use_container_width=True)
    st.caption(f"Supply: 5-year average NASS county soybean production. Plants: Crush Downtime Calculator (Gilman includes the 2026 expansion; "
               f"Marquis is left out until it runs). Road miles: {inp['source']}. Crush plants nationally compete for the same beans, so western plants "
               "draw western beans.")

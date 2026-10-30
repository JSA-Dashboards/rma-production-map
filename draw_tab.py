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
import export_scenario as es
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
def _solve(rate, tau, river_premium, util, lock_scale, pnw_mbu, area_items, cap_items, plant_premium_items, river_scale=1.0, pnw_scale=1.0):
    inp = dm.load()
    res = dm.solve(inp, rate=rate, tau=tau, river_premium=river_premium, util=util, nodes=dm.load_nodes(), lock_scale=lock_scale,
                   pnw_mbu=pnw_mbu, area_mbu=dict(area_items), pin_cap_mbu=dict(cap_items), plant_premium=dict(plant_premium_items),
                   river_scale=river_scale, pnw_scale=pnw_scale)
    return res


def _reset_exports(key, value):
    st.session_state[key] = value


def _export_panel():
    """US soybean export number -> East Gulf / West Coast bushels by the historical share -> scale factors for the river targets."""
    wz, h = es.wasde()["years"], es.history()
    years = sorted((int(k) for k in wz if int(k) >= 2015), reverse=True)
    with st.expander("Export scenario: US soybean exports and the Gulf's share", expanded=True):
        c1, c2, c3 = st.columns([1, 1.2, 2.2])
        my = c1.selectbox("Marketing year", years, index=0, format_func=es.label, key="draw_exp_my")
        usda = float(round(wz[str(my)]["exports_mbu"], 1))
        key = f"draw_exp_val_{my}"
        asof = wz[str(my)]["as_of"]
        exports = c2.number_input("US soybean exports (M bu)", 0.0, 3500.0, usda, 10.0, key=key,
                                  help=f"USDA's {es.label(my)} figure from the WASDE of {asof[4:]}/{asof[:4]} (a projection while the year is open, "
                                       "USDA's final number once it is over). Type any number to test it.")
        basis = c3.selectbox("Share basis", es.BASES, index=0, key="draw_exp_basis",
                             help="How the export number is split by where it leaves from. The averages apply a fixed share; the marginal rule moves each "
                                  "region by its historical response to a change in the total (the Gulf takes about 53 of every 100M bu, the West Coast 32, the interior 5).")
        year, manual = None, None
        if basis == es.BASES[4]:
            year = st.selectbox("Use the shares of", sorted(h.index, reverse=True), format_func=es.label, key="draw_exp_year")
        elif basis == es.BASES[5]:
            m1, m2 = st.columns(2)
            manual = (m1.number_input("East Gulf share (%)", 0.0, 100.0, 58.0, 0.5, key="draw_exp_eg"),
                      m2.number_input("West Coast share (%)", 0.0, 100.0, 21.0, 0.5, key="draw_exp_wc"))
        sc = es.scenario(exports, basis, year=year, manual=manual)
        b = sc["base"]
        if abs(exports - usda) > 0.05:
            st.button(f"Reset to USDA ({usda:,.0f})", key="draw_exp_reset", on_click=_reset_exports, args=(key, usda))
        m = st.columns(5)
        m[0].metric("Inspected for export", f"{sc['inspected']:,.0f} M bu",
                    help=f"Inspections run about {(1 - b['ratio']) * 100:.1f}% below USDA's export number (2021-25 average).")
        m[1].metric("East Gulf", f"{sc['east_gulf']:,.0f} M bu", f"{sc['east_gulf'] / b['east_gulf'] - 1:+.0%} vs 2021-25", delta_color="off",
                    help=f"{sc['east_gulf_share']:.1f}% of inspections. The 2021-25 average is {b['east_gulf']:,.0f}M bu.")
        m[2].metric("West Coast", f"{sc['west_coast']:,.0f} M bu", f"{sc['west_coast'] / b['west_coast'] - 1:+.0%} vs 2021-25", delta_color="off",
                    help=f"{sc['west_coast_share']:.1f}% of inspections. The 2021-25 average is {b['west_coast']:,.0f}M bu.")
        m[3].metric("Interior (rail, truck, container)", f"{sc['interior']:,.0f} M bu", f"{sc['interior'] / b['interior'] - 1:+.0%} vs 2021-25",
                    delta_color="off")
        m[4].metric("River targets ×", f"{sc['river_scale']:.2f}", f"Pacific NW ×{sc['pnw_scale']:.2f}", delta_color="off",
                    help="The scale applied to the barge-loading targets (East Gulf vs its 2021-25 average) and to the Pacific Northwest rail node "
                         "(West Coast vs its average).")
        apply_ = st.checkbox("Apply to the river targets below", value=True, key="draw_exp_apply",
                             help="Off = the river targets stay at their 2021-25 averages.")
        fig = go.Figure()
        for col, name, color in (("east_gulf_share_pct", "East Gulf", "#1f77b4"), ("west_coast_share_pct", "West Coast", "#d95f02"),
                                 ("interior_share_pct", "Interior", "#7f7f7f")):
            fig.add_trace(go.Scatter(x=[es.label(i) for i in h.index], y=h[col], mode="lines+markers", name=name, line=dict(color=color, width=2),
                                     marker=dict(size=5), hovertemplate=name + " %{y:.1f}%<extra>%{x}</extra>"))
        it_share = sc["interior"] / sc["inspected"] * 100 if sc["inspected"] else 0
        fig.add_trace(go.Scatter(x=[es.label(my)] * 3, y=[sc["east_gulf_share"], sc["west_coast_share"], it_share], mode="markers", name="This scenario",
                                 marker=dict(symbol="diamond", size=11, color="white", line=dict(width=2, color="#222")),
                                 hovertemplate="Scenario %{y:.1f}%<extra></extra>"))
        fig.update_layout(height=250, margin=dict(l=10, r=10, t=10, b=30), legend=dict(orientation="h", y=1.15, x=0), paper_bgcolor="white",
                          plot_bgcolor="white", yaxis=dict(title="% of soybeans inspected for export", ticksuffix="%", gridcolor="#e5e5e5"),
                          xaxis=dict(type="category", tickangle=-45))
        st.plotly_chart(fig, use_container_width=True, key="draw_exp_chart")
        sens = es.sensitivity(exports).rename(columns={"East Gulf share %": "East Gulf share (%)"})
        st.markdown(f"**The same {exports:,.0f}M bu under each share assumption** (the spread from the share alone)")
        st.dataframe(sens.style.format({"East Gulf share (%)": "{:.1f}", "East Gulf (M bu)": "{:,.0f}", "West Coast (M bu)": "{:,.0f}",
                                        "River scale": "{:.2f}"}, na_rep=""), hide_index=True, use_container_width=True)
        st.caption("Shares are of soybeans inspected for export (USDA FGIS), by marketing year from September: East Gulf = the Mississippi River, West Coast = "
                   "the Pacific Northwest, interior = rail, truck and container loaded inland. USDA's export number is the PSD / WASDE estimate. Refresh with "
                   "build_soy_export_shares.py after each WASDE.")
    return (round(sc["river_scale"], 4), round(sc["pnw_scale"], 4)) if apply_ else (1.0, 1.0)


def render():
    inp = dm.load()
    st.markdown("### Draw Territory: soybeans")
    nodes = dm.load_nodes()
    areas = [g for g in nodes["groups"] if g["kind"] == "area"]
    pnw_default = next(g["target_mbu"] for g in nodes["groups"] if g["kind"] == "pnw")
    st.caption("Where each county's beans go. Crush plants fill to their annual need (Daily Max × 88% × 350 days, from the Crush Downtime "
               "Calculator). The river is a set of regional nodes, each with the soybeans that actually load there: upriver pools are backed out of "
               "Corps and USDA lock tonnage (2021-25 average), below Lock 27 each area takes the Corps' port tonnage (St. Louis City, "
               "the Illinois side of the harbor, Cape Girardeau to Chester, the Lower Mississippi), and the Pacific Northwest takes the West Coast inspections. Beans go to the nearest "
               "buyers with limited crossover. First pass: a model to set the structure, not a forecast.")
    river_scale, pnw_scale = _export_panel()
    c1, c2, c3, c4 = st.columns(4)
    state = c1.selectbox("Region to show", FOCUS_STATES, index=0, key="draw_state")
    tau = c2.slider("Crossover (cents/bu)", 0.25, 4.0, 1.0, 0.25, key="draw_tau",
                    help="How much a county's beans spread across the nearer plants. Small = sharp territories, large = a lot of overlap.")
    util = c3.slider("Plant run rate vs workbook", 0.7, 1.1, 1.0, 0.05, key="draw_util",
                     help="1.0 = the workbook's Daily Max × 0.88 × 350 days.")
    lock_scale = c4.slider("Upriver lock calibration", 0.7, 1.3, 1.0, 0.05, key="draw_lock_scale",
                           help="Scales the soybeans loaded on the Illinois, upper Mississippi and Ohio, as measured by lock tonnage "
                                "(1.0 = the 2021-25 average). Those figures are scaled estimates, good to about ±10%.")
    c5, c6 = st.columns(2)
    rate = c5.number_input("Truck freight (cents / bu / mile)", 0.005, 0.10, dm.CENTS_PER_BU_MILE, 0.005, format="%.3f", key="draw_rate",
                           help="Your note: 3 cents a bushel for 152 miles = 0.02.")
    river_prem = c6.number_input("River bid over crush bids (cents/bu)", -30.0, 30.0, 0.0, 1.0, key="draw_river_prem",
                                 help="How much more (or less) the river terminals pay for a bean than the crush plants, after their basis. "
                                      "Positive pulls beans toward the river.")
    pnw_mbu = st.number_input("Beans railed to the Pacific Northwest (M bu/yr)", 0.0, 800.0, float(round(pnw_default)), 10.0, key="draw_pnw",
                              help="Baseline = West Coast soybean ship inspections (2021-25 average). The export scenario above scales it.")
    with st.expander("River area targets below Lock 27 (M bu/yr): baseline = the Corps' port tonnage, soybeans shipped, 2021-24 average; the export scenario scales them"):
        cols_a = st.columns(len(areas))
        area_over = {}
        for c_, g in zip(cols_a, areas):
            v = c_.number_input(g["name"], 0.0, 1000.0, float(round(g["target_mbu"], 1)), 5.0, key=f"draw_area_{g['name']}", help=g.get("note", ""))
            if abs(v - round(g["target_mbu"], 1)) > 1e-9:
                area_over[g["name"]] = v
        st.caption("Source: Corps Waterborne Commerce of the U.S., shipments (beans loaded out of the area). Nothing measures these below the last lock; "
                   "the Lower Mississippi figure is the whole stretch from Cairo to Baton Rouge, split among every tracker pin on it by distance.")
        capped = [r for r in inp["river"] if r.get("cap_mbu") is not None]
        cap_over = {}
        if capped:
            st.markdown("**Terminal ceilings** (M bu/yr of soybeans): a terminal never draws more than its ceiling; the rest of its area spreads over the others")
            for r, c_ in zip(capped, st.columns(len(capped))):
                v = c_.number_input(r["key"], 0.0, 1000.0, float(r["cap_mbu"]), 0.5, key=f"draw_cap_{r['key']}",
                                    help=(r.get("cap_note", "") + " Raise it to remove the cap.").strip())
                if abs(v - float(r["cap_mbu"])) > 1e-9:
                    cap_over[r["key"]] = v
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
    res = _solve(rate, tau, river_prem, util, lock_scale, pnw_mbu, tuple(sorted(area_over.items())), tuple(sorted(cap_over.items())),
                 tuple(sorted((k, v) for k, v in prem.items() if v)), river_scale, pnw_scale)
    plants_t, river_t, cty_t = dm.summarize(inp, res, state)
    if abs(river_scale - 1.0) > 1e-3 or abs(pnw_scale - 1.0) > 1e-3:
        st.caption(f"Export scenario applied: barge-loading targets ×{river_scale:.2f}, Pacific Northwest rail ×{pnw_scale:.2f}.")
    if res.get("target_scale", 1.0) < 0.999:
        st.warning(f"The river targets add up to more than the beans left after crush, so they were scaled to {res['target_scale']:.0%}. "
                   "Lower the area or PNW figures, or raise plant run rates.")

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
    st.markdown(f"**River nodes** (annual M bu): what loads in each stretch of river, and how much of it comes from {state}")
    gt = pd.DataFrame(dm.group_table(inp, res, state)).rename(columns={"node": "Node", "target_mbu": "Loads here", "drawn_mbu": "Modeled draw",
                                                                        "from_state_mbu": f"From {state}"}).drop(columns=["kind"])
    st.dataframe(gt.style.format({"Loads here": "{:,.1f}", "Modeled draw": "{:,.1f}", f"From {state}": "{:,.1f}"}), hide_index=True, use_container_width=True)
    st.caption("Upriver nodes: Corps lock tonnage (Food and Farm Products, both directions) scaled to USDA downbound grain at the seven locks USDA "
               "measures, soybeans = USDA's soy share; each pool = flow at its lower lock less flow at the upper one. Below Lock 27: Corps "
               "Waterborne Commerce port tonnage (soybeans shipped, 2021-24 average). Whatever the East Gulf inspects beyond these barge loadings "
               "(about 200M bu) reached the Gulf some other way and sits in 'other'. See river_segments.json and river_nodes.json.")
    st.caption(f"Supply: 5-year average NASS county soybean production. Plants: Crush Downtime Calculator (Gilman includes the 2026 expansion; "
               f"Marquis is left out until it runs). Road miles: {inp['source']}. Crush plants nationally compete for the same beans, so western plants "
               "draw western beans.")

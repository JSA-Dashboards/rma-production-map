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
HUB_COLORS = {"Illinois River": "#4292c6", "Upper Mississippi": "#08519c", "Ohio River": "#41b6c4", "Arkansas River": "#a6761d",
              "St. Louis": "#00897b", "Lower Mississippi": "#7fcdbb", "Pacific Northwest": "#d4a017", "Texas Gulf": "#c0392b", "Mexico Rail": "#8e44ad", "Louisiana Rail": "#e67e22", "Mobile": "#16a085"}
PALETTE = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf", "#17becf", "#bcbd22", "#7f7f7f", "#1b9e77", "#d95f02"]


def _in_box(paths, box):
    """The paths with a vertex inside (west, south, east, north), so the map carries only the region's lines."""
    w, so, e, n = box
    return [p for p in paths if any(w <= x <= e and so <= y <= n for x, y in p[::3] + p[-1:])]


@st.cache_data(show_spinner="Solving the draw…")
def _solve(rate, tau, river_premium, util, lock_scale, area_items, cap_items, plant_premium_items, river_scale=1.0, scale_items=(), rate_items=(), pnw_reach=30.0, barge_items=(), tau_items=()):
    inp = dm.load()
    res = dm.solve(inp, rate=rate, tau=tau, river_premium=river_premium, util=util, nodes=dm.load_nodes(), lock_scale=lock_scale,
                   area_mbu=dict(area_items), pin_cap_mbu=dict(cap_items), plant_premium=dict(plant_premium_items),
                   river_scale=river_scale, rail_scales=dict(scale_items), rail_rates=dict(rate_items), pnw_reach_miles=pnw_reach, barge_fed=dict(barge_items),
                   rail_tau=dict(tau_items))
    return res


def _terminal_sites(inp, res, river_t, state):
    """One entry per river site for the map: pins merged by site id (or by coordinates when none is given), the soybeans the solve gives it
    (so the export scenario shows up in the dot sizes), its area, and any ceiling. Sites that share a point are spread on a small ring."""
    n_p = len(inp["plants"])
    group_of, kind_of = {}, {}
    for g in res.get("groups", []):
        for c in g["cols"]:
            group_of[c - n_p], kind_of[c - n_p] = g["name"], g["kind"]
    vol = {r["terminal"]: r for r in river_t}
    sites = {}
    for j, r in enumerate(inp["river"]):
        if j not in group_of or r["key"] == "PNW export (rail)":
            continue
        if not (33.5 <= r["lat"] <= 46.5 and -95.5 <= r["lon"] <= -80):          # a few tracker pins sit far from any river
            continue
        k = r.get("site") or (round(r["lat"], 3), round(r["lon"], 3))
        t = sites.setdefault(k, {"lat": r["lat"], "lon": r["lon"], "pins": [], "vol": 0.0, "from_state": 0.0, "group": group_of[j],
                                 "kind": kind_of[j], "cap": None, "note": ""})
        row = vol.get(r["key"], {})
        t["pins"].append(r["key"])
        t["vol"] += row.get("drawn_mbu", 0.0)
        t["from_state"] += row.get("from_state_mbu", 0.0)
        if r.get("cap_mbu") is not None:
            t["cap"], t["note"] = r["cap_mbu"], r.get("cap_note", "")
    at = {}
    for t in sites.values():
        at.setdefault((round(t["lat"], 3), round(t["lon"], 3)), []).append(t)
    for ts in at.values():
        if len(ts) > 1:
            for i, t in enumerate(ts):                                            # distinct terminals the tracker geocodes to one point
                a = 2 * np.pi * i / len(ts)
                t["lat"] += 0.035 * np.sin(a)
                t["lon"] += 0.045 * np.cos(a)
    out = list(sites.values())
    for t in out:
        t["hover"] = (f"<b>{' / '.join(t['pins'])}</b><br>{t['group']}<br>Modeled: {t['vol']:.1f} M bu/yr ({t['from_state']:.1f} from {state})"
                      + (f"<br>Ceiling {t['cap']:g} M bu/yr: {t['note']}" if t["cap"] is not None else ""))
    return out


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
        m2_ = st.columns(5)
        m2_[0].metric("Texas Gulf", f"{sc['west_gulf']:,.0f} M bu", f"{sc['west_gulf'] / b['west_gulf'] - 1:+.0%} vs 2021-25" if b["west_gulf"] else "", delta_color="off",
                      help="FGIS soybean inspections at Houston/Galveston and Corpus Christi. Lumpy: 0.1 to 89M bu a year since 2018.")
        m2_[1].metric("Mexico by rail", f"{sc['mexico_rail']:,.0f} M bu", f"{sc['mexico_rail'] / b['mexico_rail'] - 1:+.0%} vs 2021-25" if b["mexico_rail"] else "",
                      delta_color="off", help="FGIS rail inspections for Mexico (part of the interior number). Trucks across the border are not inspected, "
                                              "and the East Gulf's own Mexico cargoes (about 50M bu by ship) stay in the East Gulf.")
        m2_[2].metric("Hub scales", f"Texas Gulf x{sc['txgulf_scale']:.2f}", f"Mexico rail x{sc['mexico_scale']:.2f}", delta_color="off",
                      help="The scale applied to the Texas Gulf and Mexico rail hub targets (each vs its 2021-25 average).")
        apply_ = st.checkbox("Apply to the hub targets below", value=True, key="draw_exp_apply",
                             help="Off = the hub targets stay at their 2021-25 averages.")
        fig = go.Figure()
        for col, name, color in (("east_gulf_share_pct", "East Gulf", "#1f77b4"), ("west_coast_share_pct", "West Coast", "#d95f02"),
                                 ("interior_share_pct", "Interior", "#7f7f7f"), ("mexico_rail_share_pct", "Mexico by rail (inside interior)", "#8e44ad"),
                                 ("west_gulf_share_pct", "Texas Gulf", "#c0392b")):
            if col not in h:
                continue
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
                   "the Pacific Northwest, Texas Gulf = Houston/Galveston and Corpus Christi, interior = rail, truck and container loaded inland (Mexico by rail is part of it). USDA's export number is the PSD / WASDE estimate. Refresh with "
                   "build_soy_export_shares.py after each WASDE.")
    if apply_:
        text = (f"Export scenario {es.label(my)}: US exports {exports:,.0f}M bu, East Gulf {sc['east_gulf']:,.0f}M bu "
                f"(x{sc['river_scale']:.2f} of the 2021-25 average)")
        return {"river": round(sc["river_scale"], 4), "pnw": round(sc["pnw_scale"], 4), "txg": round(sc["txgulf_scale"], 4),
                "mex": round(sc["mexico_scale"], 4)}, text
    return {"river": 1.0, "pnw": 1.0, "txg": 1.0, "mex": 1.0}, "Hub volumes at the 2021-25 average (no export scenario)"


def _hub_markers(inp, res, hubs):
    """One point per hub: the volume-weighted centre of its terminals' columns, and the soybeans the model sends it."""
    n_p = len(inp["plants"])
    out = []
    for h in hubs:
        lat = lon = w = 0.0
        for g in res["groups"]:
            if g["hub"] != h:
                continue
            for c in g["cols"]:
                r = inp["river"][c - n_p]
                if g.get("anchor") and r["key"] != g["anchor"]:
                    continue
                f = float(res["flow"][:, c].sum()) if not g.get("anchor") else float(res["flow"][:, g["cols"]].sum())
                lat, lon, w = lat + f * r["lat"], lon + f * r["lon"], w + f
        if w > 0:
            out.append({"hub": h, "lat": lat / w, "lon": lon / w, "vol": w / 1e6})
    return out


def render():
    inp = dm.load()
    st.markdown("### Draw Territory: soybeans")
    nodes = dm.load_nodes()
    area_groups = [g for g in nodes["groups"] if g["kind"] in ("area", "pnw", "txgulf", "mexico", "lagulf", "mobile")]
    hubs_in_order = []
    for g in nodes["groups"]:
        if g["hub"] not in hubs_in_order:
            hubs_in_order.append(g["hub"])
    st.caption("Where each county's beans go. Crush plants fill to their annual need (Daily Max × 88% × 350 days, from the Crush Downtime "
               "Calculator). The export hubs take the rest: each hub (an Illinois River, Upper Mississippi, Ohio River, St. Louis, Lower Mississippi or "
               "Pacific Northwest, Texas Gulf or Mexico-by-rail region) loads the soybeans the Corps, USDA and FGIS data say it loads, and counties draw to the nearest hubs "
               "and plants with limited crossover. The Pacific Northwest is fed by BNSF and UP rail, Texas Gulf and the Mexican border by BNSF, UP and CPKC. Individual terminals are only a check on a hub's volume. "
               "First pass: a model to set the structure, not a forecast.")
    scales, scenario_text = _export_panel()
    river_scale, pnw_scale, txg_scale, mex_scale = scales["river"], scales["pnw"], scales["txg"], scales["mex"]
    c1, c2, c3, c4 = st.columns(4)
    region = c1.selectbox("Region to show", ["All US"] + FOCUS_STATES, index=1, key="draw_state")
    state = None if region == "All US" else region
    label = "US" if state is None else state
    tau = c2.slider("Crossover (cents/bu)", 0.25, 4.0, 1.0, 0.25, key="draw_tau",
                    help="How much a county's beans spread across the nearer plants. Small = sharp territories, large = a lot of overlap.")
    util = c3.slider("Plant run rate vs workbook", 0.7, 1.1, 1.0, 0.05, key="draw_util",
                     help="1.0 = the workbook's Daily Max × 0.88 × 350 days.")
    lock_scale = c4.slider("Upriver lock calibration", 0.7, 1.3, 1.0, 0.05, key="draw_lock_scale",
                           help="Scales the soybeans loaded on the Illinois, upper Mississippi and Ohio, as measured by lock tonnage "
                                "(1.0 = the 2021-25 average). Those figures are scaled estimates, good to about ±10%.")
    c5, c6, c7, c8 = st.columns(4)
    rate = c5.number_input("Truck freight (cents / bu / mile)", 0.005, 0.10, dm.CENTS_PER_BU_MILE, 0.005, format="%.3f", key="draw_rate",
                           help="Your note: 3 cents a bushel for 152 miles = 0.02.")
    river_prem = c6.number_input("River bid over crush bids (cents/bu)", -30.0, 30.0, 0.0, 1.0, key="draw_river_prem",
                                 help="How much more (or less) the river terminals pay for a bean than the crush plants, after their basis. "
                                      "Positive pulls beans toward the river.")
    pnw_rate = c7.number_input("Rail freight to the Pacific NW (cents / bu / mile)", 0.001, 0.10, 0.015, 0.001, format="%.3f", key="draw_pnw_rate",
                               help="From USDA AMS rail tariff rates for soybeans to Seattle/Kalama (Oct 2026): BNSF shuttle runs about $6,200-6,350 a car (about 168-172 "
                                    "cents a bushel on AMS's 3,700 bu/car) from 1,700 to 2,180 lane miles, so the tariff alone barely rises with distance "
                                    "(0.002 cents per bushel-mile) and the BNSF fuel surcharge ($0.45/mile) adds about 0.012: total about 0.015. UP's surcharge is higher "
                                    "($0.68/mile, about 0.018) on a lower tariff (Cozad NE $5,365 a car). The Rail FOB portal's BN seed gives 0.020. "
                                    "A lower rate widens the area that feeds the coast; a higher one pulls it toward the Dakotas.")
    pnw_reach = c8.number_input("BNSF / UP reach (miles)", 5.0, 150.0, 30.0, 5.0, key="draw_pnw_reach",
                                help="A county can send beans to the Pacific Northwest only if BNSF or UP track is within this many miles of its centre "
                                     "(CPKC is left out: it feeds little of the coast).")
    c9, c10, c11, c12, c13 = st.columns(5)
    txg_rate = c9.number_input("Rail freight to Texas Gulf (cents / bu / mile)", 0.001, 0.20, 0.050, 0.005, format="%.3f", key="draw_txg_rate",
                               help="From USDA AMS rail tariffs for soybeans to Houston (Oct 2026): BNSF and UP shuttles run about $0.93 a bushel from Concordia KS (852 miles) "
                                    "to $1.40 from Argyle MN (1,662 miles). Fitted slope 0.05 cents per bushel-mile (0.057 BNSF, 0.049 all lanes), fuel surcharge included. "
                                    "Texas Gulf draws only from the plains rail belt (TX, OK, KS, NE, MO, IA, MN, SD, ND, CO, NM): nothing records where its soybeans come from, "
                                    "and without the limit the model would take the Delta's beans, which have the river.")
    mex_rate = c10.number_input("Rail freight to the Mexican border (cents / bu / mile)", 0.001, 0.20, 0.045, 0.005, format="%.3f", key="draw_mex_rate",
                                help="From USDA AMS rail tariffs to the border (rzdk-2e87, Oct 2026, soybeans, heavy cars, fuel surcharge included): UP 0.045 cents per "
                                     "bushel-mile (about $1.54 a bushel to Laredo or Eagle Pass from 1,200 miles, $1.64 to Brownsville), BNSF 0.053, CPKC/KCS Laredo about $1.43. "
                                     "Within each state the beans come from the nearest rail counties; the state totals are held to FGIS rail inspections for Mexico.")
    la_rate = c11.number_input("Rail freight to Louisiana (cents / bu / mile)", 0.001, 0.20, 0.065, 0.005, format="%.3f", key="draw_la_rate",
                               help="From USDA AMS rail tariffs to Reserve, Convent and Ama (2026): CN from Gibson City IL (870 miles, about $0.71-0.81 a bushel) and "
                                    "Ida Grove IA (1,376 miles, about $0.96-1.08), UP from Sloan IA (1,231 miles, $1.17): a fitted slope of about 0.068 cents per "
                                    "bushel-mile. Louisiana's rail and truck beans are what is left of the East Gulf's inspections after the barge arrivals, so "
                                    "this is the least certain hub: nothing records where they come from. They are drawn only from IL, IA, WI and MN: the "
                                    "Delta has little rail loading (one loader in Mississippi, not much volume).")
    mob_rate = c12.number_input("Rail freight to Mobile (cents / bu / mile)", 0.001, 0.20, 0.065, 0.005, format="%.3f", key="draw_mob_rate",
                                help="AMS publishes one soybean lane to Mobile (CSX from Casey IL, 779 miles, about $1.02-1.08 a bushel), too few to fit a slope: this "
                                     "borrows Louisiana's. Mobile draws from counties on CSX, NS or CN track, in IL, IN, OH, KY, TN and IA.")
    la_tau = c13.number_input("East Gulf rail crossover (cents / bu)", 1.0, 60.0, 20.0, 1.0, key="draw_la_tau",
                              help="How far Louisiana's and Mobile's rail draw spreads across origin states. Freight alone sends it all from the nearest "
                                   "states (Illinois); basis differences between states offset freight differences of 20-30 cents, so beans also come from Iowa, "
                                   "Wisconsin and Minnesota. Small = nearest first, large = spread widely.")
    st.caption("Rail hubs use the same reach (miles to track) as the Pacific Northwest. Pacific Northwest = BNSF + UP; Texas Gulf and the Mexican border = BNSF + UP + CPKC; "
               "Louisiana = UP + CPKC + CN; Mobile = CSX + NS + CN.")
    with st.expander("Hub targets (M bu/yr): baseline = Corps port tonnage below Lock 27 and FGIS inspections by port (Pacific Northwest, Texas Gulf) and FGIS rail "
                     "inspections for Mexico, 2021-25 average; the export scenario scales them"):
        area_over = {}
        for i in range(0, len(area_groups), 3):
            for c_, g in zip(st.columns(3), area_groups[i:i + 3]):
                v = c_.number_input(g["name"], 0.0, 1000.0, float(round(g["target_mbu"], 1)), 5.0, key=f"draw_area_{g['name']}", help=g.get("note", ""))
                if abs(v - round(g["target_mbu"], 1)) > 1e-9:
                    area_over[g["name"]] = v
        st.caption("Below Lock 27: Corps Waterborne Commerce of the U.S., shipments (beans loaded out of the area); nothing measures it directly and the Lower "
                   "Mississippi figure is the whole stretch from Cairo to Baton Rouge. Louisiana rail and truck = the East Gulf's ship inspections less the barge loadings (a residual); Mobile = FGIS at the other East Gulf ports. "
                   "Pacific Northwest and Texas Gulf: FGIS soybean inspections by port, "
                   "2021/22-2025/26 average. Mexico by rail: FGIS soybeans inspected on a railcar for Mexico, same years.")
        capped = [r for r in inp["river"] if r.get("cap_mbu") is not None]
        cap_over = {}
        if capped:
            st.markdown("**Terminal ceilings** (M bu/yr of soybeans): a terminal never draws more than its ceiling; the rest of its area spreads over the others")
            for r, c_ in zip(capped, st.columns(len(capped))):
                v = c_.number_input(r["key"], 0.0, 1000.0, float(r["cap_mbu"]), 0.5, key=f"draw_cap_{r['key']}",
                                    help=(r.get("cap_note", "") + " Raise it to remove the cap.").strip())
                if abs(v - float(r["cap_mbu"])) > 1e-9:
                    cap_over[r["key"]] = v
    o1, o2, o3 = st.columns([2.2, 1.6, 1])
    show_mode = o1.selectbox("Map shows", ["Top buyer (plant or hub)"] + [f"Draw area: {h}" for h in hubs_in_order], key="draw_show",
                             help="The top buyer of each county, or one hub's draw area: the share of each county's beans that goes to that hub.")
    show_terms = o2.checkbox("Show individual terminals (a check on volumes)", value=False, key="draw_terms")
    show_rivers = o3.checkbox("Rivers", value=True, key="draw_rivers")
    us_mode = state is None
    rails = st.multiselect("Railroads", list(rm.RAILROADS), default=["UP", "BNSF"] if us_mode else ["UP", "BNSF", "CSXT", "NS", "CN", "TPW", "IAIS"],
                           format_func=lambda k: rm.RAILROADS[k][0], key="draw_rails_us" if us_mode else "draw_rails")
    with st.expander("Barge-fed plants: the share of a plant's soybeans that arrive by barge from upriver"):
        bnames = {"Bunge North America Destrehan LA": "Bunge Destrehan LA", "Archer Daniels Midland Quincy IL": "ADM Quincy IL",
                  "Cargill Owensboro KY": "Cargill Owensboro KY"}
        barge_fed = {}
        for (k, lab), c_ in zip(bnames.items(), st.columns(3)):
            v = c_.number_input(lab + " (%)", 0.0, 100.0, float(dm.BARGE_FED_DEFAULT[k] * 100), 0.5, key=f"draw_barge_{k}",
                                help=("Takes the barge and stops it before export: all of its beans arrive by barge. No Corps receipts figure is on file for the "
                                      "lower river to check it against." if k.endswith("LA") else
                                      "Takes some barge from north of where it sits. The Corps records only 11.2M bu of barge soybean deliveries to ALL docks on the "
                                      "Mississippi from Minneapolis to the Missouri and 9.4M bu on the Ohio (receipts plus moves within the waterway, 2021-24 average), "
                                      "so half of that is assumed to reach this plant: 7.5% of Quincy's need, 10% of Owensboro's. Raise it if you know better."))
            barge_fed[k] = v / 100.0
        st.caption("A plant's barge beans are not trucked from nearby counties and never reach the export elevators, so the Louisiana rail and truck hub, "
                   "which is what is left of the East Gulf's inspections, grows by what Destrehan takes. Quincy and Owensboro sit between locks, so their "
                   "intake is also added back to the loadings of their river stretch (the lock-differenced loadings come out short by what is offloaded there).")
    with st.expander("Plant bid adjustments (cents/bu over the base, per plant)"):
        keys = [p["key"] for p in inp["plants"] if state is None or p["state"] == state][:12] or [p["key"] for p in inp["plants"][:6]]
        prem = {}
        cols = st.columns(3)
        for i, k in enumerate(keys):
            prem[k] = cols[i % 3].number_input(k, -20.0, 20.0, 0.0, 0.5, key=f"draw_prem_{k}")
    res = _solve(rate, tau, river_prem, util, lock_scale, tuple(sorted(area_over.items())), tuple(sorted(cap_over.items())),
                 tuple(sorted((k, v) for k, v in prem.items() if v)), river_scale,
                 (("pnw", pnw_scale), ("txgulf", txg_scale), ("mexico", mex_scale)),
                 (("pnw", pnw_rate), ("txgulf", txg_rate), ("mexico", mex_rate), ("lagulf", la_rate), ("mobile", mob_rate)), pnw_reach,
                 tuple(sorted(barge_fed.items())), (("lagulf", la_tau), ("mobile", la_tau)))
    plants_t, river_t, cty_t = dm.summarize(inp, res, state)
    if any(abs(x - 1.0) > 1e-3 for x in (river_scale, pnw_scale, txg_scale, mex_scale)):
        st.caption(f"Export scenario applied: barge-loading and East Gulf rail targets ×{river_scale:.2f}, Pacific Northwest rail ×{pnw_scale:.2f}, "
                   f"Texas Gulf ×{txg_scale:.2f}, Mexico by rail ×{mex_scale:.2f}.")
    if res.get("target_scale", 1.0) < 0.999:
        st.warning(f"The hub targets add up to more than the beans left after crush, so they were scaled to {res['target_scale']:.0%}. "
                   "Lower the hub targets or raise plant run rates.")

    in_state = [c for c in inp["counties"] if state is None or c["state"] == state]
    supply = sum(c["supply_bu"] for c in in_state) / 1e6
    st_plants = [r for r in plants_t if state is None or r["state"] == state]
    need_local = sum(r["need_mbu"] for r in st_plants)
    to_pl = sum(r["from_state_mbu"] for r in plants_t)
    to_local = sum(r["from_state_mbu"] for r in st_plants)
    to_rv = sum(r["from_state_mbu"] for r in river_t)
    other = supply - to_pl - to_rv
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(f"{label} soybean supply", f"{supply:,.0f} M bu", help="5-year average NASS county production")
    m2.metric(f"{label} crush need", f"{need_local:,.0f} M bu", help="Plants in the region, at the run rate above")
    m3.metric(f"{label} beans to crush", f"{to_pl:,.0f} M bu", f"{to_local:,.0f} to {label} plants", delta_color="off")
    m4.metric(f"{label} beans to export hubs", f"{to_rv:,.0f} M bu")
    m5.metric("Other (feed, rail export, stocks)", f"{max(other, 0):,.0f} M bu")

    # ---- the map: counties by the hub or plant they draw to, one marker per hub
    n_p = len(inp["plants"])
    cty = inp["counties"]
    hub_flow = dm.county_hub_flows(inp, res)
    hubs = [h for h in hubs_in_order if h in hub_flow]
    H = np.column_stack([hub_flow[h] for h in hubs])
    P = res["flow"][:, :n_p]
    sup = np.array([c["supply_bu"] for c in cty], float)
    top_load = {r["plant"]: r["from_state_mbu"] for r in plants_t}
    ranked = [k for k, _ in sorted(top_load.items(), key=lambda kv: -kv[1]) if top_load[k] > 0][:len(PALETTE)]
    color_of = {k: PALETTE[i] for i, k in enumerate(ranked)}
    keep = [i for i, c in enumerate(cty) if us_mode or c["state"] in FOCUS_STATES]
    geo = rm._load("county_geo.json")
    fig = go.Figure()
    focus = None if show_mode.startswith("Top buyer") else show_mode.replace("Draw area: ", "")
    if focus is None:
        fips, colors, hover = [], [], []
        for i in keep:
            tp, th = int(P[i].argmax()), int(H[i].argmax())
            tot = float(P[i].sum() + H[i].sum())
            hub_wins = H[i, th] > P[i, tp]
            top_name = hubs[th] + " hub" if hub_wins else inp["plants"][tp]["key"]
            top_flow = H[i, th] if hub_wins else P[i, tp]
            fips.append(cty[i]["fips"])
            colors.append(HUB_COLORS.get(hubs[th], RIVER_COLOR) if hub_wins else color_of.get(inp["plants"][tp]["key"], OTHER_PLANT_COLOR))
            hover.append(f'{cty[i]["name"]}, {cty[i]["state"]}<br>{cty[i]["supply_bu"] / 1e6:.1f} M bu<br>mostly to: {top_name} ({top_flow / max(tot, 1) * 100:.0f}%)'
                         f'<br>{P[i].sum() / max(tot, 1) * 100:.0f}% to crush plants, {H[i].sum() / max(tot, 1) * 100:.0f}% to export hubs')
        order = {f: i for i, f in enumerate(fips)}
        fig.add_trace(go.Choroplethmap(
            geojson={"type": "FeatureCollection", "features": [f for f in geo["features"] if f["id"] in order]}, locations=fips,
            z=[order[f] for f in fips], colorscale=[[0, "#ffffff"], [1, "#ffffff"]], showscale=False, hovertext=hover, hoverinfo="text",
            marker=dict(opacity=0.0, line=dict(width=0.3, color="#888")), name="Counties"))
        legend = [(k, v) for k, v in color_of.items()] + [(h + " hub", HUB_COLORS.get(h, RIVER_COLOR)) for h in hubs] + [("Other plants", OTHER_PLANT_COLOR)]
        for lab, col in legend:
            sel = [f for f, c_ in zip(fips, colors) if c_ == col]
            if not sel:
                continue
            fig.add_trace(go.Choroplethmap(
                geojson={"type": "FeatureCollection", "features": [f for f in geo["features"] if f["id"] in set(sel)]}, locations=sel,
                z=[1] * len(sel), colorscale=[[0, col], [1, col]], showscale=False, marker=dict(opacity=0.62, line=dict(width=0.3, color="#888")),
                hovertext=[h for h, c_ in zip(hover, colors) if c_ == col], hoverinfo="text", name=lab, showlegend=True))
    else:
        k = hubs.index(focus)
        z = [float(H[i, k] / max(sup[i], 1.0) * 100) for i in keep]
        fips = [cty[i]["fips"] for i in keep]
        hover = [f'{cty[i]["name"]}, {cty[i]["state"]}<br>{cty[i]["supply_bu"] / 1e6:.1f} M bu<br>to {focus}: {H[i, k] / 1e6:.2f} M bu ({zz:.0f}% of the county)'
                 for i, zz in zip(keep, z)]
        zmax = max(10.0, float(np.percentile([v for v in z if v > 0], 95)) if any(v > 0 for v in z) else 10.0)
        col = HUB_COLORS.get(focus, RIVER_COLOR)
        fig.add_trace(go.Choroplethmap(
            geojson={"type": "FeatureCollection", "features": [f for f in geo["features"] if f["id"] in set(fips)]}, locations=fips, z=z, zmin=0, zmax=zmax,
            colorscale=[[0, "#ffffff"], [1, col]], marker=dict(opacity=0.75, line=dict(width=0.3, color="#999")), hovertext=hover, hoverinfo="text",
            colorbar=dict(title=dict(text=f"% of county beans<br>to {focus}", font=dict(size=11)), thickness=12, len=0.5, y=0.45), name=f"Draw area: {focus}"))
    sc0 = [cty[i] for i in keep]
    box = (-128.0, 22.0, -65.0, 51.0) if us_mode else (min(c["lon"] for c in sc0) - 2.2, min(c["lat"] for c in sc0) - 2.2,
                                                       max(c["lon"] for c in sc0) + 2.2, max(c["lat"] for c in sc0) + 2.2)
    if show_rivers:                                                      # the navigable grain rivers, as on the Rail Map tab
        water = rm._load("waterways.json")
        lon, lat = rm._line_xy(_in_box([p for r in rm.GRAIN_RIVERS for p in water.get(r, [])], box), step=0)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=3.5, color="#4aa3ff"), name="Rivers", hoverinfo="skip"))
    lines = rm._load("rail_lines.json")
    for code in rails:
        lab, color, width = rm.RAILROADS[code]
        lon, lat = rm._line_xy(_in_box(lines.get(code, []), box), step=0.1)
        fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=max(1.0, width * 0.8), color=color), name=lab,
                                    hovertemplate="%{fullData.name}<extra></extra>"))
    lon, lat = rm._line_xy(_in_box(rm._load("state_lines.json"), box), step=0)
    fig.add_trace(go.Scattermap(lon=lon, lat=lat, mode="lines", line=dict(width=2.2, color="#2b2b2b"), name="State lines", hoverinfo="skip"))
    pts = [p for p in inp["plants"] if us_mode or p["state"] in FOCUS_STATES]
    fig.add_trace(go.Scattermap(
        lon=[p["lon"] for p in pts], lat=[p["lat"] for p in pts], mode="markers" if us_mode else "markers+text",
        marker=dict(size=[8 + 14 * (p["need_bu"] / 1e8) ** 0.5 for p in pts], color="#222"),
        text=[p["city"] for p in pts], textposition="top right", textfont=dict(size=10), name="Crush plants",
        hovertext=[f'{p["key"]}<br>need {p["need_bu"] / 1e6:,.1f} M bu/yr' for p in pts], hoverinfo="text"))
    hm = _hub_markers(inp, res, hubs)
    href = 2.0 * max([m["vol"] for m in hm] + [1.0]) / (46 ** 2)               # the biggest hub is about 46 px across
    hub_tbl = {t["hub"]: t for t in dm.hub_table(inp, res, state)}
    for m in hm:
        t = hub_tbl.get(m["hub"], {})
        fig.add_trace(go.Scattermap(
            lon=[m["lon"]], lat=[m["lat"]], mode="markers+text", name=f'{m["hub"]} hub (area = M bu/yr)', text=[f'{m["hub"]}  {m["vol"]:,.0f}'], textposition="top right",
            textfont=dict(size=12, color="#111"), marker=dict(size=[m["vol"]], sizemode="area", sizeref=href, sizemin=9, color=HUB_COLORS.get(m["hub"], "#2b6cb0"), opacity=0.9),
            hovertext=[f'<b>{m["hub"]} hub</b><br>Modeled: {m["vol"]:,.1f} M bu/yr<br>From {label}: {t.get("from_state_mbu", 0):,.1f} M bu'], hoverinfo="text"))
    if show_terms:
        sites = _terminal_sites(inp, res, river_t, state if state else "the region")
        top = max([t["vol"] for t in sites] + [1.0])
        ref = 2.0 * top / (34 ** 2)
        capped_sites = [t for t in sites if t["cap"] is not None]
        if capped_sites:
            fig.add_trace(go.Scattermap(lon=[t["lon"] for t in capped_sites], lat=[t["lat"] for t in capped_sites], mode="markers", name="Has a ceiling",
                                        marker=dict(size=[max(t["vol"], 0.5) * 1.7 for t in capped_sites], sizemode="area", sizeref=ref, sizemin=7, color="#d62728"),
                                        hoverinfo="skip"))
        for kind, lab, color in (("lock", "Terminals above Lock 27 (area = M bu/yr)", "#2b6cb0"), ("area", "Terminals below Lock 27 (area = M bu/yr)", "#00897b")):
            ts = [t for t in sites if t["kind"] == kind]
            if ts:
                fig.add_trace(go.Scattermap(lon=[t["lon"] for t in ts], lat=[t["lat"] for t in ts], mode="markers", name=lab,
                                            marker=dict(size=[max(t["vol"], 0.5) for t in ts], sizemode="area", sizeref=ref, sizemin=4, color=color, opacity=0.85),
                                            hovertext=[t["hover"] for t in ts], hoverinfo="text"))
    fig.add_annotation(text=scenario_text, xref="paper", yref="paper", x=0.01, y=0.01, xanchor="left", yanchor="bottom", showarrow=False,
                       font=dict(size=11, color="#222"), bgcolor="rgba(255,255,255,0.85)", borderpad=4)
    sc = [c for c in cty if state is None or c["state"] == state]
    center = dict(lat=39.0, lon=-98.0) if us_mode else dict(lat=float(np.mean([c["lat"] for c in sc])), lon=float(np.mean([c["lon"] for c in sc])))
    fig.update_layout(map=dict(style="carto-positron", center=center, zoom=3.1 if us_mode else (5.4 if state in ("IL", "IN", "OH", "KY") else 5.0)),
                      height=640, margin=dict(l=0, r=0, t=0, b=0), legend=dict(orientation="h", y=1.0, bgcolor="rgba(255,255,255,0.85)", font=dict(size=11)))
    st.plotly_chart(fig, use_container_width=True, key="draw_map")
    st.caption("Fill = where most of a county's beans go (colour = that crush plant or export hub; grey = a plant outside the list), or, in a hub's draw-area "
               "view, the share of each county's beans that goes to that hub. Hub circles are sized by the soybeans the hub loads a year under the export "
               "scenario above. The Pacific Northwest draws only from counties on BNSF or UP track; Texas Gulf and Mexico by rail from counties on BNSF, UP or "
               "CPKC track, Louisiana from UP, CPKC or CN track and Mobile from CSX, NS or CN track; Mexico's draw by state is held to FGIS rail inspections.")

    st.markdown(f"**Export hubs** (annual M bu): what each hub loads under the scenario, and how much of it comes from {label}")
    ht = pd.DataFrame(dm.hub_table(inp, res, state))
    ht["share"] = ht.from_state_mbu / ht.drawn_mbu.replace(0, np.nan) * 100
    ht = ht.rename(columns={"hub": "Hub", "target_mbu": "Target", "drawn_mbu": "Modeled draw", "from_state_mbu": f"From {label}", "share": f"% of hub from {label}"})
    st.dataframe(ht.style.format({"Target": "{:,.1f}", "Modeled draw": "{:,.1f}", f"From {label}": "{:,.1f}", f"% of hub from {label}": "{:.0f}"}),
                 hide_index=True, use_container_width=True)
    st.markdown(f"**Plants that draw {label} beans** (annual, M bu)")
    df = pd.DataFrame(plants_t)
    df = df[(df["from_state_mbu"] > 0.5) | (df["barge_mbu"] > 0)].sort_values("from_state_mbu", ascending=False)
    df = df.rename(columns={"plant": "Plant", "state": "St", "need_mbu": "Need (trucked)", "barge_mbu": "By barge", "drawn_mbu": "Total drawn", "from_state_mbu": f"From {label}",
                            "avg_miles": "Avg haul (mi)", "river_access": "River/CIF"})
    st.dataframe(df.style.format({"Need (trucked)": "{:,.1f}", "By barge": "{:,.1f}", "Total drawn": "{:,.1f}", f"From {label}": "{:,.1f}", "Avg haul (mi)": "{:,.0f}"}),
                 hide_index=True, use_container_width=True)
    with st.expander("Terminal and node detail: a check on the hub volumes"):
        st.markdown(f"**Terminals drawing {label} beans** (top 15, annual M bu)")
        rt = pd.DataFrame(river_t)
        rt = rt[rt["from_state_mbu"] > 0.05].sort_values("from_state_mbu", ascending=False).head(15)
        rt = rt.rename(columns={"terminal": "Terminal", "state": "St", "drawn_mbu": "Total drawn", "from_state_mbu": f"From {label}", "avg_miles": "Avg haul (mi)"})
        st.dataframe(rt.style.format({"Total drawn": "{:,.1f}", f"From {label}": "{:,.1f}", "Avg haul (mi)": "{:,.0f}"}), hide_index=True, use_container_width=True)
        st.markdown(f"**Nodes** (annual M bu): what loads in each stretch of river, and how much of it comes from {label}")
        gt = pd.DataFrame(dm.group_table(inp, res, state)).rename(columns={"node": "Node", "hub": "Hub", "target_mbu": "Loads here", "drawn_mbu": "Modeled draw",
                                                                            "from_state_mbu": f"From {label}"}).drop(columns=["kind"])
        st.dataframe(gt.style.format({"Loads here": "{:,.1f}", "Modeled draw": "{:,.1f}", f"From {label}": "{:,.1f}"}), hide_index=True, use_container_width=True)
        mex = next((g for g in res.get("groups", []) if g["kind"] == "mexico"), None)
        if mex is not None and mex.get("origin_states_mbu"):
            st.markdown("**Mexico by rail: where the beans come from** (annual M bu, 2021-25 average)")
            sts = np.array([c["state"] for c in cty])
            model = pd.Series(res["flow"][:, mex["cols"]].sum(axis=1) / 1e6).groupby(sts).sum()
            fg = pd.Series(mex["origin_states_mbu"]) * mex["target_bu"] / 1e6 / max(sum(mex["origin_states_mbu"].values()), 1e-9)
            mt = pd.DataFrame({"FGIS rail inspections (scaled to the target)": fg, "Modeled draw": model.reindex(fg.index).fillna(0.0)}).sort_values(
                "Modeled draw", ascending=False)
            mt["Share of the state's beans (%)"] = [model.get(k, 0.0) / max(sum(c["supply_bu"] for c in cty if c["state"] == k) / 1e6, 1e-9) * 100 for k in mt.index]
            mt.index.name = "State"
            st.dataframe(mt.style.format("{:,.1f}"), use_container_width=True)
            st.caption("FGIS records the state where the lot was inspected, which stands in for where it grew. The model's state totals are held to these, so this "
                       "table shows the calibration worked, not an independent test; within a state the beans come from the counties nearest the border.")
        st.caption("Upriver nodes: Corps lock tonnage (Food and Farm Products, both directions) scaled to USDA downbound grain at the seven locks USDA "
                   "measures, soybeans = USDA's soy share; each pool = flow at its lower lock less flow at the upper one. Below Lock 27: Corps "
                   "Waterborne Commerce port tonnage (soybeans shipped, 2021-24 average). Whatever the East Gulf inspects beyond these barge loadings "
                   "(about 230M bu) reached the Gulf some other way and sits in 'other'. See river_segments.json and river_nodes.json.")
    st.caption(f"Supply: 5-year average NASS county soybean production. Plants: Crush Downtime Calculator (Gilman includes the 2026 expansion; "
               f"Marquis is left out until it runs). Road miles: {inp['source']}. Crush plants nationally compete for the same beans, so western plants "
               "draw western beans.")

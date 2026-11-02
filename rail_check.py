"""
Rail validation panel for the Draw Territory tab: the model's rail hubs and plants against the STB Public Use Carload Waybill Sample.

The waybill sample (build_rail_waybill.py -> data/draw/waybill_<crop>_flows.csv) is an independent count of what railroads carried, by origin and termination
BEA economic area (build_bea_key.py -> data/draw/bea_key.json). Nothing here feeds the model: it only lays the two side by side.

What the sample cannot see (the panel says so too):
  - BEA 000 is an area STB withholds (it reports an area only when at least three station codes on one railroad ship that commodity there). Baton Rouge, Mobile, Norfolk,
    Charleston and Savannah never appear, so those hubs are only partly checked.
  - The export flag is inferred by the railroads, so some export grain at a port reads as domestic.
  - An origin area is split into states by the model's own county supply, so a state's share inside a mixed area (Kansas City: MO and KS) is an estimate.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "draw")
BU_PER_TON = {"corn": 2000.0 / 56.0, "soy": 2000.0 / 60.0, "wheat": 2000.0 / 60.0}
CROSS_BORDER = 173                           # STB's railroad-added codes: 173-183 Canadian provinces, 184 Puerto Rico, 185 Mexico

# model hub -> the termination areas that stand for it. 'any' counts every carload ending there, 'exp' only the ones flagged as export (San Antonio and El Paso are also
# feed and processing towns: only the export-flagged grain there is a Mexico border crossing). 'hidden' = STB withholds part of this hub's destinations.
HUBS = {
    "Pacific Northwest": {"codes": {"167": "any", "170": "any", "169": "any"}, "hidden": False,
                          "note": "Portland, Seattle and Richland-Kennewick. About half the carloads carry no export flag, but FGIS West Coast inspections match the all-rail count within about "
                                  "15% in every year (the year check below), so the unflagged carloads are export: count them all."},
    "Mexico Rail": {"codes": {"185": "any", "134": "any", "157": "any"}, "hidden": True,
                    "note": "Through 2021 the railroads coded border corn 'Mexico' (185); from 2022 they code the gateway area, San Antonio (Laredo, Eagle Pass) and El Paso. No corn there is "
                            "flagged 'not export' (it is export or unknown), so all of it counts. Brownsville (McAllen area) is withheld, so this is a floor."},
    "Louisiana Rail": {"codes": {"083": "any"}, "hidden": True,
                       "note": "New Orleans only: Baton Rouge, Lafayette and Lake Charles are withheld, so the sample sees part of this hub."},
    "Texas Gulf": {"codes": {"131": "any", "132": "any", "087": "any"}, "hidden": False,
                   "note": "Houston, Corpus Christi and Beaumont. About 70% of the corn tonnage has an unknown export flag, so some of it is Houston-area feed, not export."},
    "South Atlantic": {"codes": {"020": "any", "026": "any", "028": "any", "025": "any", "029": "any"}, "hidden": True,
                       "note": "Norfolk, Charleston, Savannah, Wilmington and Jacksonville: most are withheld, so this is a floor."},
    "Mobile": {"codes": {"080": "any", "082": "any", "081": "any"}, "hidden": True, "note": "Mobile, Biloxi and Pensacola: withheld, nothing to compare."},
}
# My reading of what a destination area is (not in the data): useful for the domestic table, to be corrected by someone who knows the trade.
LABELS = {
    "corn": {"138": "Texas Panhandle feedyards", "137": "Texas Panhandle feedyards", "162": "California dairy, feed and ethanol", "163": "California dairy and feed",
             "160": "California dairy and feed", "164": "California dairy and feed", "040": "Southeast poultry", "021": "Carolinas hogs and poultry", "018": "Carolinas poultry",
             "023": "Carolinas poultry", "024": "Carolinas poultry", "077": "Mississippi poultry", "078": "Alabama poultry", "149": "Idaho dairy", "064": "Chicago area processing",
             "068": "Decatur area processing", "122": "Kansas feedyards and mills", "158": "Arizona dairy and feed", "090": "Arkansas poultry", "127": "Dallas-Fort Worth mills and feed"},
    "soy": {"096": "St. Louis crush and river", "099": "Kansas City crush", "107": "Minneapolis crush", "067": "Indiana crush", "010": "New York area"},
    "wheat": {"064": "Chicago area flour mills", "096": "St. Louis flour mills", "099": "Kansas City flour mills", "107": "Minneapolis flour mills", "109": "Duluth-Superior: lake port",
              "010": "New York area flour and baking", "160": "Los Angeles area flour mills", "127": "Dallas-Fort Worth flour mills"},
}
PORT_AREAS = {"083", "131", "020", "026", "028"}
EXPORT_AREAS = {"167": "Pacific NW port: export (FGIS inspections match)", "170": "Pacific NW port: export (FGIS inspections match)",
                "169": "Pacific NW port: export (FGIS inspections match)", "134": "Mexico border crossing: export", "157": "Mexico border crossing: export"}


@st.cache_data(show_spinner=False)
def _load(crop: str):
    p = os.path.join(DATA, f"waybill_{crop}_flows.csv")
    if not os.path.exists(p):
        return None
    f = pd.read_csv(p, dtype={"originbea": str, "terminationbea": str, "typeofmoveimportexport": str, "typeofmoveviawater": str, "transitcode": str})
    f["o"], f["t"] = f.originbea.str.zfill(3), f.terminationbea.str.zfill(3)
    f["exp"] = f.typeofmoveimportexport.isin(["2", "3"])
    f["water"] = f.typeofmoveviawater.isin(["1", "2", "5"])
    return f


def waybill_years(crop: str) -> list:
    """Calendar years in the crop's waybill file (empty when it is not built)."""
    f = _load(crop)
    return sorted(int(y) for y in f.year.unique()) if f is not None else []


@st.cache_data(show_spinner=False)
def _load_monthly(crop: str):
    p = os.path.join(DATA, f"waybill_{crop}_monthly.csv")
    if not os.path.exists(p):
        return None
    m = pd.read_csv(p, dtype={"originbea": str, "typeofmoveimportexport": str})
    m["exp"] = m.typeofmoveimportexport.isin(["2", "3"])
    return m


@st.cache_data(show_spinner=False)
def _key():
    with open(os.path.join(DATA, "bea_key.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _name(code: str, key: dict) -> str:
    return key["areas"].get(code, "withheld by STB (000)" if code == "000" else code)


def _hub_rows(f: pd.DataFrame, spec: dict, flagged_only: bool) -> pd.DataFrame:
    """The waybill rows that stand for one hub."""
    keep = np.zeros(len(f), bool)
    for code, mode in spec["codes"].items():
        sel = f.t == code
        keep |= sel & (f.exp if (mode == "exp" or flagged_only) else True)
    return f[keep]


def _by_state(rows: pd.DataFrame, split: dict, years: int, bu_per_ton: float) -> pd.Series:
    """M bu a year by origin state, each origin area spread over its states by the model's county supply."""
    out: dict[str, float] = {}
    for o, tons in rows.groupby("o").tons.sum().items():
        mbu = tons / years * bu_per_ton / 1e6
        if o in split:
            for s, w in split[o].items():
                out[s] = out.get(s, 0.0) + mbu * w
        else:
            out["(origin withheld)"] = out.get("(origin withheld)", 0.0) + mbu
    return pd.Series(out, dtype=float)


def _bucket(t: str, exp: bool, crop: str) -> str:
    """Map a BEA termination code to a display bucket for the state × destination matrix."""
    for hub, spec in HUBS.items():
        if t in spec["codes"]:
            return hub
    if t == "000":
        return "Hidden export" if exp else "Hidden domestic"
    label = LABELS.get(crop, {}).get(t, "")
    for kw, grp in (("feedyard", "Feed/Livestock"), ("poultry", "Feed/Livestock"), ("hog", "Feed/Livestock"),
                    ("dairy", "Dairy"), ("ethanol", "Ethanol/Processing"), ("processing", "Ethanol/Processing"),
                    ("mill", "Ethanol/Processing"), ("crush", "Ethanol/Processing"), ("export", "Other export")):
        if kw in label.lower():
            return grp
    return "Other export" if exp else "Other domestic"


def render(crop: str, inp: dict, res: dict, hub_flow: dict, hub_rows: list, img_config, match_year: int | None = None) -> None:
    """hub_flow: {hub: bushels per county}; hub_rows: dm.hub_table(...) rows (hub, target_mbu, drawn_mbu). match_year: the scenario above was set to FGIS's actual volumes for
    this calendar year ("Match a waybill year"), so the waybill years default to it and the model and the waybill describe the same year."""
    f = _load(crop)
    if f is None or not os.path.exists(os.path.join(DATA, "bea_key.json")):
        st.info(f"The {crop} waybill file is not built yet: run build_bea_key.py and build_rail_waybill.py {crop}.")
        return
    key = _key()
    bpt = BU_PER_TON[crop]
    W = {"corn": "corn", "wheat": "wheat"}.get(crop, "soybeans")
    cty = inp["counties"]
    ca = key["county_area"]
    sup: dict[str, dict[str, float]] = {}
    for c in cty:
        a = ca.get(c["fips"])
        if a:
            sup.setdefault(a, {})
            sup[a][c["state"]] = sup[a].get(c["state"], 0.0) + c["supply_bu"]
    split = {a: {s: v / sum(d.values()) for s, v in d.items()} for a, d in sup.items() if sum(d.values()) > 0}

    y_all = sorted(int(y) for y in f.year.unique())
    default = (match_year, match_year) if match_year in y_all else (max(y_all[0], y_all[-1] - 2), y_all[-1])
    y0, y1 = st.select_slider("Waybill years averaged", options=y_all, value=default, key=f"rc_years_{crop}_{match_year or 'avg'}")
    if match_year in y_all and (y0, y1) == (match_year, match_year):
        st.success(f"The scenario above is set to FGIS's actual {match_year} volumes, so the model and the waybill describe the same calendar year. "
                   "Supply (production, carry-in, imports) follows the crop marketed through most of that year.")
    ny = int(y1 - y0 + 1)
    f_all = f
    f = f[(f.year >= y0) & (f.year <= y1)]
    total_mbu = f.tons.sum() / ny * bpt / 1e6
    hidden_mbu = f[f.t == "000"].tons.sum() / ny * bpt / 1e6
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(f"{W.capitalize()} by rail, M bu a year", f"{total_mbu:,.0f}")
    k2.metric("Export-flagged", f"{f[f.exp].tons.sum() / ny * bpt / 1e6:,.0f}", f"{f[f.exp].tons.sum() / f.tons.sum() * 100:.0f}% of rail")
    k3.metric("Ends in a withheld area", f"{hidden_mbu:,.0f}", f"{hidden_mbu / total_mbu * 100:.0f}% of rail", delta_color="off")
    k4.metric("Moved in unit trains (75+ cars)", f"{f[f.shipment_size_category == '75+'].tons.sum() / f.tons.sum() * 100:.0f}%")
    st.caption(f"STB's Carload Waybill Sample, {y0}-{y1} average, scaled up to all rail traffic. It is railroad data, independent of the model: nothing here changes the draw.")

    # ---- 1. hubs: model against the waybill
    st.markdown("**1. Rail hubs: the model against what railroads carried**")
    flagged_only = st.checkbox("Count only export-flagged carloads for the Pacific Northwest and the other port hubs", value=False, key=f"rc_flag_{crop}",
                               help="The railroads infer the export flag, so some export grain reads as domestic at a port. Off counts every carload ending at the port area.")
    ht = {r["hub"]: r for r in hub_rows}
    rows = []
    for hub, spec in HUBS.items():
        if hub not in ht:
            continue
        w = _hub_rows(f, spec, flagged_only)
        wm = w.tons.sum() / ny * bpt / 1e6
        rows.append({"Hub": hub, "Model target": ht[hub]["target_mbu"], "Model draw": ht[hub]["drawn_mbu"], "Waybill (rail)": wm,
                     "Waybill / model": wm / ht[hub]["drawn_mbu"] * 100 if ht[hub]["drawn_mbu"] > 0.5 else np.nan,
                     "Waybill sees": "nothing reported (withheld)" if w.n.sum() == 0 else "part (areas withheld)" if spec["hidden"] else "all of it", "Waybill sample": int(w.n.sum())})
    hid_exp = f[(f.t == "000") & f.exp].tons.sum() / ny * bpt / 1e6                 # export grain whose destination STB withholds: border crossings and ports may sit here
    rows.append({"Hub": "(export-flagged, area withheld)", "Model target": np.nan, "Model draw": np.nan, "Waybill (rail)": hid_exp, "Waybill / model": np.nan,
                 "Waybill sees": "hidden: could be any hub above", "Waybill sample": int(f[(f.t == "000") & f.exp].n.sum())})
    tbl = pd.DataFrame(rows)
    st.dataframe(tbl.style.format({"Model target": "{:,.1f}", "Model draw": "{:,.1f}", "Waybill (rail)": "{:,.1f}", "Waybill / model": "{:,.0f}%", "Waybill sample": "{:,}"}, na_rep="-"),
                 hide_index=True, use_container_width=True)
    st.caption("Model figures are M bu a year under the scenario above, which looks at the current marketing year; the waybill is the average of the years chosen, so a volume gap "
               "is partly a year gap. The model's hub targets also include truck and barge loadings; the waybill counts rail only, so a waybill figure well below the model is "
               "expected at Louisiana, South Atlantic and Mobile (part withheld, part not by rail). Where STB withholds a hub's areas the last row shows how much export-flagged "
               "grain ends in withheld areas: for soybeans that is where the Mexico crossings probably sit. The origin mix below is the firmer test than the volume.")

    # ---- volume check against FGIS, by calendar year
    fg_path = os.path.join(DATA, f"fgis_hub_calendar_{crop}.csv")
    if os.path.exists(fg_path):
        fg = pd.read_csv(fg_path, index_col="year")
        fg = fg[fg.months == 12]
        rows_y = []
        for yr in y_all:
            if yr not in fg.index:
                continue
            row = {"Year": int(yr)}
            for hub_, col, lab in (("Pacific Northwest", "west_coast_mbu", "Pacific NW"), ("Mexico Rail", "mexico_rail_mbu", "Mexico")):
                wv = _hub_rows(f_all[f_all.year == yr], HUBS[hub_], False).tons.sum() * bpt / 1e6
                row.update({f"FGIS {lab}": fg.loc[yr, col], f"Waybill {lab}": wv, f"{lab} waybill / FGIS": wv / fg.loc[yr, col] * 100 if fg.loc[yr, col] > 0 else np.nan})
            rows_y.append(row)
        if rows_y:
            st.markdown("**Volume check by year: FGIS inspections against the waybill** (calendar years, M bu)")
            yt = pd.DataFrame(rows_y)
            fmt = {c: "{:,.0f}" for c in yt.columns if c != "Year" and "/" not in c}
            fmt.update({c: "{:,.0f}%" for c in yt.columns if "/" in c})
            st.dataframe(yt.style.format(fmt, na_rep="-"), hide_index=True, use_container_width=True)
            st.caption("FGIS West Coast inspections against all rail corn or soybeans ending in Portland, Seattle and Richland; FGIS rail inspections for Mexico against carloads coded "
                       "Mexico or ending at San Antonio and El Paso. Two independent counts that agree within about 15% mean the hub's volume is not in doubt. Where they split, "
                       "the waybill is missing destinations STB withholds (Brownsville, and nearly all Mexico soybeans). The hub targets use FGIS marketing-year averages scaled to "
                       "the scenario's export total, so they follow FGIS, not the waybill years.")

    # ---- 2. origin mix for one hub
    avail = [h for h in HUBS if h in hub_flow and h in ht]
    hub = st.selectbox("Hub to compare, by origin state", avail, key=f"rc_hub_{crop}")
    spec = HUBS[hub]
    st.caption(spec["note"])
    sts = np.array([c["state"] for c in cty])
    model = pd.Series(hub_flow[hub] / 1e6).groupby(sts).sum()
    model = model[model > 0.05]
    way = _by_state(_hub_rows(f, spec, flagged_only), split, ny, bpt)
    both = pd.DataFrame({"Model": model, "Waybill": way}).fillna(0.0)
    both = both[(both.Model > 0.5) | (both.Waybill > 0.5)]
    if both.empty or both.Waybill.sum() < 0.5:
        st.info("The waybill sample shows nothing for this hub (its destination areas are withheld).")
    else:
        both["Model %"] = both.Model / both.Model.sum() * 100
        both["Waybill %"] = both.Waybill / both.Waybill.sum() * 100
        both["Gap (points)"] = both["Waybill %"] - both["Model %"]
        both = both.sort_values("Waybill", ascending=False)
        top = both.head(10)
        fig = go.Figure()
        fig.add_bar(x=top.index, y=top["Model %"], name="Model", marker_color="#32373c")
        fig.add_bar(x=top.index, y=top["Waybill %"], name="Waybill (rail)", marker_color="#0693e3")
        fig.update_layout(barmode="group", height=330, margin=dict(l=10, r=10, t=36, b=10), yaxis_title="% of the hub's grain", legend=dict(orientation="h", y=1.12),
                          title=dict(text=f"<b>{hub}</b>: where its {W} come from, by state (model {both.Model.sum():,.0f} M bu, waybill {both.Waybill.sum():,.0f} M bu a year)",
                                     font=dict(size=13)))
        st.plotly_chart(fig, use_container_width=True, key=f"rc_bar_{crop}", config=img_config(f"rail check {hub}"))
        both.index.name = "State"
        st.dataframe(both.style.format({"Model": "{:,.1f}", "Waybill": "{:,.1f}", "Model %": "{:.0f}", "Waybill %": "{:.0f}", "Gap (points)": "{:+.0f}"}), use_container_width=True)
        st.caption("A state's gap says where the model draws more (negative) or less (positive) of the hub's grain than the railroads carried. Mixed areas such as Kansas City "
                   "are split by the model's own county supply, so Missouri against Kansas is soft; the two together are firmer.")

    # ---- 3. everything that is not export: who else rail serves
    st.markdown(f"**2. Where else rail takes {W}: domestic destinations**")
    dom = f[(f.t.astype(int) < CROSS_BORDER) & ~f.exp & ~f.water & (f.t != "000")]
    nd = dom.groupby("t").agg(tons=("tons", "sum"), rev=("rev", "sum"), n=("n", "sum"))
    unit = dom[dom.shipment_size_category == "75+"].groupby("t").tons.sum()
    top_o = {}
    for t_, g in dom.groupby("t"):
        s_ = _by_state(g, split, ny, bpt)
        s_ = s_[s_.index != "(origin withheld)"].sort_values(ascending=False)
        top_o[t_] = ", ".join(f"{k} {v / max(s_.sum(), 1e-9) * 100:.0f}%" for k, v in s_.head(3).items())
    plants = inp["plants"]
    clat = np.array([c["lat"] for c in cty]); clon = np.array([c["lon"] for c in cty])
    plant_area = {}
    for j, p in enumerate(plants):
        d2 = (clat - p["lat"]) ** 2 + ((clon - p["lon"]) * np.cos(np.radians(p["lat"]))) ** 2
        a = ca.get(cty[int(np.argmin(d2))]["fips"])
        if a:
            plant_area[a] = plant_area.get(a, 0.0) + float(res["need"][j]) / 1e6
    out = pd.DataFrame({"Area": [f"{t} {_name(t, key)}" for t in nd.index], "M bu/yr by rail": nd.tons / ny * bpt / 1e6,
                        "Model plants there": [plant_area.get(t, 0.0) for t in nd.index],
                        "Unit trains %": [unit.get(t, 0.0) / v * 100 for t, v in zip(nd.index, nd.tons)], "Freight $/ton": nd.rev / nd.tons,
                        "Main origin states": [top_o.get(t, "") for t in nd.index],
                        "My reading": [EXPORT_AREAS.get(t) or LABELS[crop].get(t, "port area: some may be unflagged export" if t in PORT_AREAS else "") for t in nd.index],
                        "Waybills": nd.n.values}).sort_values("M bu/yr by rail", ascending=False)
    st.dataframe(out.head(25).style.format({"M bu/yr by rail": "{:,.1f}", "Model plants there": "{:,.1f}", "Unit trains %": "{:.0f}", "Freight $/ton": "{:,.0f}", "Waybills": "{:,}"}),
                 hide_index=True, use_container_width=True)
    noplant = out[out["Model plants there"] < 0.5]
    st.caption(f"Carloads that are not export-flagged, not a lake or river transfer and not cross-border, to the areas STB reports (the withheld {hidden_mbu:,.0f} M bu is left out). "
               f"{noplant['M bu/yr by rail'].sum():,.0f} M bu of it goes to areas where the model has no {'ethanol' if crop == 'corn' else 'flour mill' if crop == 'wheat' else 'crush'} plant: that is feed, flour and "
               f"food mills and other users the model does not draw by name, and a calibration target for a feed layer. 'Model plants there' puts each plant in the BEA area of its "
               f"nearest county. 'My reading' is my label for the area, not something in the data.")

    # ---- 4. when
    m = _load_monthly(crop)
    if m is not None:
        m = m[(m.year >= y0) & (m.year <= y1)]
        mm = m.groupby(["month", "exp"]).tons.sum().unstack(fill_value=0.0) / ny * bpt / 1e6
        mm = mm.reindex(range(1, 13), fill_value=0.0)
        fig = go.Figure()
        fig.add_bar(x=list(range(1, 13)), y=mm.get(False, pd.Series(0.0, index=mm.index)), name="Not export-flagged", marker_color="#32373c")
        fig.add_bar(x=list(range(1, 13)), y=mm.get(True, pd.Series(0.0, index=mm.index)), name="Export-flagged", marker_color="#0693e3")
        fig.update_layout(barmode="stack", height=300, margin=dict(l=10, r=10, t=36, b=10), yaxis_title="M bu a month", legend=dict(orientation="h", y=1.12),
                          xaxis=dict(tickmode="array", tickvals=list(range(1, 13)), ticktext=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]),
                          title=dict(text=f"<b>When railroads carry {W}</b>, {y0}-{y1} average by month of waybill", font=dict(size=13)))
        st.markdown("**3. When**")
        st.plotly_chart(fig, use_container_width=True, key=f"rc_month_{crop}", config=img_config(f"rail by month {crop}"))

    with st.expander("What this data can and cannot tell you"):
        st.markdown(
            "- **Withheld areas.** STB reports an origin or destination area only when at least three station codes on one railroad ship that commodity there. Everything else is "
            "coded 000: it is hidden, not zero. For corn about 16% of rail tons end there and for soybeans about 30%; Baton Rouge, Mobile, Norfolk, Charleston and Savannah never appear.\n"
            "- **Export flag.** The railroads infer it, and many carloads carry code 9, unknown (about 70% of the corn ending in Houston). The panel counts unknown as not export. "
            "Grain that goes to a port elevator without the flag reads as domestic, which is why the Pacific Northwest comparison offers both counts.\n"
            "- **Not seen.** Truck and barge loadings (the model's hub targets include them), grain that does not move by rail, and where a carload goes after the elevator.\n"
            "- **Origin states.** The waybill gives an origin area, not a state; each area is split by the model's county supply.\n"
            "- **Years.** The latest year in the public file lags (2024 now), and the model's hub targets may be built on other years.\n"
            "- **What it is good for.** A hub's rail volume and origin mix where the area is reported (Pacific Northwest, Mexico, New Orleans, Houston), and a map of the "
            "non-export users that rail reaches, which is where feed demand shows up.")

    # ── crop selector for destination + seasonal sections ────────────────────
    st.markdown("---")
    crop_wb = st.radio("Waybill crop view for sections 4-6",
                       ["corn", "soy", "wheat"],
                       index={"corn": 0, "soy": 1}.get(crop, 0),
                       horizontal=True, key=f"rc_crop_wb_{crop}")
    f_wb = _load(crop_wb)
    if f_wb is None:
        st.info(f"The {crop_wb} waybill file is not built yet — run build_rail_waybill.py {crop_wb}.")
        return
    bpt_wb = BU_PER_TON[crop_wb]
    W_wb = {"corn": "corn", "wheat": "wheat"}.get(crop_wb, "soybeans")
    f_wb_yr = f_wb[(f_wb.year >= y0) & (f_wb.year <= y1)]
    total_wb = f_wb_yr.tons.sum() / ny * bpt_wb / 1e6

    # ── 4. Domestic destination breakdown ────────────────────────────────────
    st.markdown(f"**4. Domestic destination breakdown — {W_wb}** (waybill {y0}-{y1} average)")
    all_states = sorted({s for a in split for s in split[a]})
    sel_states = st.multiselect("Filter to origin states (empty = all)", all_states,
                                default=[], key=f"rc_s4_states_{crop}")
    if sel_states:
        keep_o = {a for a, d in split.items() if any(s in d for s in sel_states)}
        f_dom2 = f_wb_yr[f_wb_yr.o.isin(keep_o) & ~f_wb_yr.exp & ~f_wb_yr.water &
                         (f_wb_yr.t.astype(int) < CROSS_BORDER) & (f_wb_yr.t != "000")]
    else:
        f_dom2 = f_wb_yr[~f_wb_yr.exp & ~f_wb_yr.water &
                         (f_wb_yr.t.astype(int) < CROSS_BORDER) & (f_wb_yr.t != "000")]
    dom_mbu2 = f_dom2.tons.sum() / ny * bpt_wb / 1e6
    hidden_dom2 = f_wb_yr[(f_wb_yr.t == "000") & ~f_wb_yr.exp].tons.sum() / ny * bpt_wb / 1e6
    k1, k2, k3 = st.columns(3)
    k1.metric(f"Domestic {W_wb} by rail", f"{dom_mbu2:,.0f} M bu/yr")
    k2.metric("% of total rail", f"{dom_mbu2 / max(total_wb, 1e-9) * 100:.0f}%")
    nd2_tons = f_dom2.groupby("t").tons.sum()
    if not nd2_tons.empty:
        top_t = nd2_tons.idxmax()
        top_lbl = LABELS.get(crop_wb, {}).get(top_t) or _name(top_t, key)
        k3.metric("Top destination", top_lbl[:32])
    GROUP_COLORS = {"Feed/Livestock": "#d97706", "Dairy": "#7c3aed", "Ethanol/Processing": "#0693e3", "Other domestic": "#6b7280"}
    HUB_NAMES = set(HUBS.keys())
    nd2_df = (nd2_tons / ny * bpt_wb / 1e6).reset_index()
    nd2_df.columns = ["t", "mbu"]
    nd2_df["label"] = nd2_df.t.map(lambda x: LABELS.get(crop_wb, {}).get(x) or _name(x, key))
    nd2_df["group"] = nd2_df.t.map(lambda x: _bucket(x, False, crop_wb))
    nd2_df = nd2_df[~nd2_df.group.isin(HUB_NAMES | {"Hidden export", "Hidden domestic"})].nlargest(20, "mbu")
    if not nd2_df.empty:
        nd2_df = nd2_df.sort_values("mbu")
        colors = [GROUP_COLORS.get(g, "#32373c") for g in nd2_df.group]
        fig4 = go.Figure(go.Bar(x=nd2_df.mbu, y=nd2_df.label, orientation="h",
                                marker_color=colors, text=nd2_df.mbu.map("{:,.1f}".format), textposition="outside"))
        fig4.update_layout(height=max(300, len(nd2_df) * 26 + 80), margin=dict(l=10, r=80, t=46, b=10),
                           xaxis_title="M bu/yr",
                           title=dict(text=f"<b>Domestic {W_wb} by rail</b> — top 20 destinations", font=dict(size=13)))
        st.plotly_chart(fig4, use_container_width=True, key=f"rc_s4_bar_{crop}", config=img_config(f"domestic dest {crop_wb}"))
    nd2_n = f_dom2.groupby("t").n.sum()
    nd2_out = pd.DataFrame({
        "BEA": nd2_tons.index,
        "Label": [LABELS.get(crop_wb, {}).get(t) or _name(t, key) for t in nd2_tons.index],
        "Group": [_bucket(t, False, crop_wb) for t in nd2_tons.index],
        "M bu/yr": nd2_tons / ny * bpt_wb / 1e6,
        "Carloads/yr": nd2_n.reindex(nd2_tons.index, fill_value=0) / ny,
        "% of domestic": nd2_tons / nd2_tons.sum() * 100,
    }).sort_values("M bu/yr", ascending=False)
    st.dataframe(nd2_out.style.format({"M bu/yr": "{:,.1f}", "Carloads/yr": "{:,.0f}", "% of domestic": "{:.0f}%"}),
                 hide_index=True, use_container_width=True)
    st.caption(f"Not export-flagged, not cross-border, not via water. Withheld ({hidden_dom2:,.0f} M bu/yr to BEA 000) excluded. "
               "Feed/Livestock = feedyards + poultry + hogs; Dairy; Ethanol/Processing = ethanol + crush + mills.")

    # ── 5. Origin State × Destination Bucket Matrix ──────────────────────────
    st.markdown(f"**5. Origin State × Destination Bucket** (M bu/yr, {y0}-{y1} average)")
    BUCKET_ORDER = ["Pacific Northwest", "Texas Gulf", "Mexico Rail", "Louisiana Rail", "South Atlantic", "Mobile",
                    "Feed/Livestock", "Dairy", "Ethanol/Processing", "Other domestic", "Other export", "Hidden export", "Hidden domestic"]
    bkts = [_bucket(t, e, crop_wb) for t, e in zip(f_wb_yr["t"], f_wb_yr["exp"])]
    grp5 = f_wb_yr.assign(bucket=bkts).groupby(["o", "bucket"]).tons.sum().reset_index()
    grp5["mbu"] = grp5.tons / ny * bpt_wb / 1e6
    state_bucket: dict[str, dict[str, float]] = {}
    for _, r5 in grp5.iterrows():
        o5, bkt5, mbu5 = r5.o, r5.bucket, r5.mbu
        if o5 in split:
            for s, w in split[o5].items():
                if s not in state_bucket:
                    state_bucket[s] = {}
                state_bucket[s][bkt5] = state_bucket[s].get(bkt5, 0.0) + mbu5 * w
        else:
            wk = "(origin withheld)"
            if wk not in state_bucket:
                state_bucket[wk] = {}
            state_bucket[wk][bkt5] = state_bucket[wk].get(bkt5, 0.0) + mbu5
    if state_bucket:
        mtx = pd.DataFrame(state_bucket).T.fillna(0.0)
        cols_ok = [c for c in BUCKET_ORDER if c in mtx.columns] + [c for c in mtx.columns if c not in BUCKET_ORDER]
        mtx = mtx[cols_ok].loc[mtx.sum(axis=1).sort_values(ascending=False).index]
        pct_toggle = st.checkbox("Show as % of state total rail", key=f"rc_s5_pct_{crop}")
        display_mtx = mtx.div(mtx.sum(axis=1), axis=0) * 100 if pct_toggle else mtx
        fmt_str = "{:.0f}" if pct_toggle else "{:,.1f}"
        st.dataframe(display_mtx.style.format(fmt_str, na_rep="-").background_gradient(cmap="YlOrRd", axis=None),
                     use_container_width=True)
        st.caption("M bu/yr (or % of state total) from each origin state to each destination bucket. Mixed BEA areas "
                   "(e.g. Kansas City = MO + KS) are apportioned by the model's county supply — treat those state rows as estimates. Sorted by total.")

    # ── 6. Seasonal rail pattern (MY-aligned) ───────────────────────────────
    m_wb2 = _load_monthly(crop_wb)
    if m_wb2 is not None:
        m_sub = m_wb2[(m_wb2.year >= y0) & (m_wb2.year <= y1)].copy()
        m_sub["my_month"] = ((m_sub.month - 9) % 12) + 1
        m_sub["my_year"] = m_sub.year.where(m_sub.month >= 9, m_sub.year - 1)
        m_sub["my_label"] = m_sub.my_year.astype(str) + "/" + (m_sub.my_year + 1).astype(str).str[-2:]
        flow_filter = st.radio("Traffic to chart", ["All traffic", "Export flagged", "Domestic only"],
                               horizontal=True, key=f"rc_s6_filter_{crop}")
        if flow_filter == "Export flagged":
            m_filt = m_sub[m_sub.exp]
        elif flow_filter == "Domestic only":
            m_filt = m_sub[~m_sub.exp]
        else:
            m_filt = m_sub
        mm_my = m_filt.groupby(["my_label", "my_month"]).tons.sum().reset_index()
        mm_my["mbu"] = mm_my.tons * bpt_wb / 1e6
        my_labels = sorted(mm_my.my_label.unique())
        PALETTE = ["#16a34a", "#0693e3", "#d97706", "#7c3aed", "#dc2626", "#0891b2", "#6b7280"]
        MO_TICKS = {1: "Sep", 2: "Oct", 3: "Nov", 4: "Dec", 5: "Jan", 6: "Feb", 7: "Mar",
                    8: "Apr", 9: "May", 10: "Jun", 11: "Jul", 12: "Aug"}
        fig6 = go.Figure()
        for i, lbl in enumerate(my_labels):
            sub6 = mm_my[mm_my.my_label == lbl].sort_values("my_month")
            is_last = lbl == my_labels[-1]
            fig6.add_scatter(x=sub6.my_month, y=sub6.mbu, name=lbl, mode="lines+markers",
                             line=dict(color=PALETTE[i % len(PALETTE)], width=2.5 if is_last else 1.5),
                             marker=dict(size=5 if is_last else 3))
        fig6.update_layout(height=360, margin=dict(l=10, r=10, t=46, b=10), yaxis_title="M bu",
                           legend=dict(orientation="h", y=1.14, font=dict(size=11)),
                           xaxis=dict(tickmode="array", tickvals=list(range(1, 13)),
                                      ticktext=[MO_TICKS[i] for i in range(1, 13)],
                                      title="Marketing year month (Sep = 1)"),
                           title=dict(text=f"<b>Seasonal {W_wb} rail pattern by marketing year</b>", font=dict(size=13)))
        st.markdown(f"**6. Seasonal rail pattern — {W_wb}** (MY-aligned)")
        st.plotly_chart(fig6, use_container_width=True, key=f"rc_s6_line_{crop}", config=img_config(f"seasonal rail {crop_wb}"))
        st.caption(f"Sep = marketing year month 1, Aug = 12. The harvest surge (months 1–3) and spring window (months 5–8) are the main shipping periods. "
                   "Most recent marketing year shown with a thicker line. Export vs domestic toggle separates the two legs of rail movement.")

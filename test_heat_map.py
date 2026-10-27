"""
Tests for heat_map.py - the pure functions behind the Heat Map tab, run on a toy county map (no Streamlit, no NASS calls).

Run:  python test_heat_map.py        (or: python -m pytest test_heat_map.py)
"""
import copy
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import heat_map as hm


# -- a toy map ---------------------------------------------------------------------------
def box(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def feat(state, county, name, geom, lsad="County"):
    return {"type": "Feature", "properties": {"STATE": state, "COUNTY": county, "NAME": name, "LSAD": lsad}, "geometry": geom}


ADAMS, PIKE, TINY, ADAIR = "17001", "17149", "17999", "19001"
TOY = {"type": "FeatureCollection", "features": [
    feat("17", "001", "Adams", box(-91.5, 39.5, -91.0, 40.0)),            # 0.5 x 0.5 degrees = 25 lattice points
    feat("17", "149", "Pike", box(-91.0, 39.5, -90.5, 40.0)),             # shares its west edge with Adams
    feat("17", "999", "Tiny", box(-90.30, 39.50, -90.28, 39.52)),         # too small to hold a lattice point
    feat("19", "001", "Adair", box(-94.5, 41.0, -94.0, 41.5)),
    feat("17", "163", "St. Clair", box(-90.2, 38.2, -89.7, 38.7)),
    feat("19", "141", "O'Brien", box(-96.0, 43.0, -95.5, 43.5)),
    feat("29", "186", "Ste. Genevieve", box(-90.4, 37.8, -90.0, 38.1)),
    feat("51", "800", "Suffolk", box(-76.8, 36.5, -76.4, 36.9), lsad="city"),
    feat("29", "189", "St. Louis", box(-90.7, 38.4, -90.2, 38.8)),
    feat("29", "510", "St. Louis", box(-90.31, 38.53, -90.18, 38.77), lsad="city"),
    feat("17", "000", "Bad", {"type": "Banana", "coordinates": []}),       # a polygon shapely cannot read
    feat("17", "000", "Empty", {"type": "Polygon", "coordinates": []}),    # an empty one
]}
STATE_ABBR = {"17": "IL", "19": "IA", "29": "MO", "51": "VA", "46": "SD", "02": "AK"}


def toy_index():
    return hm.CountyIndex(TOY)


# -- CountyIndex ------------------------------------------------------------------------------
def test_index_skips_unreadable_and_empty_polygons():
    idx = toy_index()
    assert set(idx.shapes) == {"17001", "17149", "17999", "19001", "17163", "19141", "29186", "51800", "29189", "29510"}
    assert idx.state_of[ADAIR] == "19" and idx.state_of[ADAMS] == "17"
    assert idx.names[ADAMS] == "Adams"


def test_index_state_bounds_cover_every_county():
    idx = toy_index()
    b = idx.state_bounds["17"]
    assert b[0] == -91.5 and b[2] == -89.7 and b[1] == 38.2 and b[3] == 40.0, b
    assert idx.state_bounds["19"] == (-96.0, 41.0, -94.0, 43.5)


# -- lattice_cells / spread_values ----------------------------------------------------------
def test_lattice_points_sit_inside_their_county():
    idx = toy_index()
    cells = hm.lattice_cells(idx.shapes)
    for fips, (lons, lats) in cells.items():
        g = idx.shapes[fips]
        assert len(lons) == len(lats) >= 1
        assert all(g.contains(__import__("shapely").geometry.Point(x, y)) for x, y in zip(lons, lats)) or len(lons) == 1, fips


def test_lattice_counts_and_anchoring():
    cells = hm.lattice_cells(toy_index().shapes)
    assert len(cells[ADAMS][0]) == 25 and len(cells[PIKE][0]) == 25
    for lons, lats in (cells[ADAMS], cells[PIKE], cells[ADAIR]):
        for v in np.concatenate([lons, lats]):
            k = (v - hm.LATTICE_STEP / 2) / hm.LATTICE_STEP
            assert abs(k - round(k)) < 1e-6, v                         # every point is a half-step centre of one shared lattice


def test_lattice_neighbours_do_not_overlap():
    cells = hm.lattice_cells(toy_index().shapes)
    a = set(zip(np.round(cells[ADAMS][0], 6), np.round(cells[ADAMS][1], 6)))
    p = set(zip(np.round(cells[PIKE][0], 6), np.round(cells[PIKE][1], 6)))
    assert not (a & p) and len(a) == len(p) == 25


def test_tiny_county_gets_its_representative_point():
    idx = toy_index()
    lons, lats = hm.lattice_cells(idx.shapes)[TINY]
    assert len(lons) == 1 and idx.shapes[TINY].contains(__import__("shapely").geometry.Point(lons[0], lats[0]))


def test_lattice_is_deterministic():
    a = hm.lattice_cells(toy_index().shapes)
    b = hm.lattice_cells(toy_index().shapes)
    assert a.keys() == b.keys() and all(np.array_equal(a[k][0], b[k][0]) and np.array_equal(a[k][1], b[k][1]) for k in a)


def test_spread_conserves_each_county_value():
    cells = hm.lattice_cells(toy_index().shapes)
    values = {ADAMS: 250_000_000.0, PIKE: 40_000_000.0, TINY: 1_234_567.0, ADAIR: 9_000.0}
    pts = hm.spread_values(cells, values)
    assert list(pts.columns) == ["lon", "lat", "w", "fips"]
    for f, v in values.items():
        got = pts.loc[pts["fips"] == f, "w"].sum()
        assert abs(got - v) <= 0.05 * 25 + 1e-6, (f, got, v)          # the weights are rounded to 0.1, nothing more
    assert abs(pts["w"].sum() - sum(values.values())) < 5
    assert (pts.groupby("fips").size() == pd.Series({ADAMS: 25, PIKE: 25, TINY: 1, ADAIR: 25})).all()


def test_spread_is_even_within_a_county():
    pts = hm.spread_values(hm.lattice_cells(toy_index().shapes), {ADAMS: 2500.0})
    assert set(pts["w"]) == {100.0}


def test_spread_ignores_zero_negative_missing_and_unknown_counties():
    cells = hm.lattice_cells(toy_index().shapes)
    pts = hm.spread_values(cells, {ADAMS: 0, PIKE: -5.0, ADAIR: None, "99999": 1e6, TINY: float("nan"), "17163": 100.0})
    assert set(pts["fips"]) == {"17163"}


def test_spread_nothing_gives_an_empty_frame():
    pts = hm.spread_values(hm.lattice_cells(toy_index().shapes), {})
    assert list(pts.columns) == ["lon", "lat", "w", "fips"] and pts.empty


def test_spread_is_deterministic():
    cells = hm.lattice_cells(toy_index().shapes)
    v = {ADAMS: 1e6, PIKE: 2e6}
    assert hm.spread_values(cells, v).equals(hm.spread_values(cells, v))


# -- gap_states ---------------------------------------------------------------------------------
def _cty(rows):
    return pd.DataFrame(rows, columns=["State", "fips", "Value"])


def test_gap_states_lists_only_states_that_fall_short():
    cur = _cty([("IL", "17001", 600.0), ("IA", "19001", 995.0), ("IN", "18001", 1000.0)])
    gaps = hm.gap_states(cur, {"IL": 1000.0, "IA": 1000.0, "IN": 1000.0, "NE": 500.0})
    assert gaps == {"IL": 400.0, "NE": 500.0}                          # IA is 0.5% short (not over the threshold), IN is complete, NE has no counties at all


def test_gap_states_threshold_is_half_a_percent():
    assert hm.gap_states(_cty([("IL", "17001", 994.9)]), {"IL": 1000.0}) == {"IL": 5.1000000000000227}
    assert hm.gap_states(_cty([("IL", "17001", 995.1)]), {"IL": 1000.0}) == {}


def test_gap_states_ignores_missing_zero_and_overshooting_totals():
    cur = _cty([("IL", "17001", 1010.0), ("IA", "19001", 5.0)])
    assert hm.gap_states(cur, {"IL": 1000.0, "IA": 0, "IN": None, "OH": float("nan"), "KS": -5.0}) == {}


def test_gap_states_with_no_counties_at_all():
    assert hm.gap_states(_cty([]), {"IL": 1000.0}) == {"IL": 1000.0}


# -- fill_withheld ----------------------------------------------------------------------------
def _toy_fill():
    cur = _cty([("IL", "17001", 300.0), ("IL", "17003", 200.0), ("IL", "17005", 100.0)])        # 600 of 1000 published
    past = [(_cty([("IL", "17007", 200.0)]), {"IL": 1000.0}),                                    # 2024: 17007 held 20% of the state
            (_cty([("IL", "17009", 50.0), ("IL", "17007", 999.0)]), {"IL": 500.0})]              # 2023: 17009 held 10% (17007 is already taken by 2024)
    rma = _cty([("IL", "17001", 10.0), ("IL", "17011", 5.0), ("IL", "17007", 3.0), ("IL", "17009", 2.0)])
    return cur, past, rma


def test_fill_adds_up_to_the_state_total_and_keeps_published_numbers():
    cur, past, rma = _toy_fill()
    df, info = hm.fill_withheld(cur, {"IL": 1000.0}, past, rma)
    assert abs(df["Value"].sum() - 1000.0) < 1e-9
    pub = df[~df["Estimated"]].set_index("fips")["Value"]
    assert pub.to_dict() == {"17001": 300.0, "17003": 200.0, "17005": 100.0}              # never scaled
    est = df[df["Estimated"]].set_index("fips")["Value"]
    assert set(est.index) == {"17007", "17009", "17011"}
    # weights: 17007 = 0.20 (2024), 17009 = 0.10 (2023), 17011 = 5/20 = 0.25 (RMA); the 400 left is split in those proportions
    assert math.isclose(est["17007"], 400 * 0.20 / 0.55) and math.isclose(est["17009"], 400 * 0.10 / 0.55) and math.isclose(est["17011"], 400 * 0.25 / 0.55)
    assert info["estimated"] == {"IL": (3, 400.0)} and info["estimated_n"] == 3 and info["estimated_total"] == 400.0 and info["short"] == []
    assert math.isclose(info["coverage"], 0.6)


def test_fill_prefers_the_latest_earlier_year_over_older_years_and_rma():
    cur = _cty([("IL", "17001", 500.0)])
    past = [(_cty([("IL", "17007", 100.0)]), {"IL": 1000.0}),                                    # 10%
            (_cty([("IL", "17007", 900.0), ("IL", "17009", 100.0)]), {"IL": 1000.0})]            # older: 17007 90% (ignored), 17009 10%
    rma = _cty([("IL", "17007", 1.0), ("IL", "17009", 99.0)])
    df, _ = hm.fill_withheld(cur, {"IL": 1000.0}, past, rma)
    est = df[df["Estimated"]].set_index("fips")["Value"]
    assert math.isclose(est["17007"], est["17009"]) and math.isclose(est["17007"] + est["17009"], 500.0)       # 0.1 each: the older year and RMA were not consulted for them


def test_fill_never_touches_a_published_county_even_if_history_has_it():
    cur = _cty([("IL", "17001", 600.0)])
    past = [(_cty([("IL", "17001", 900.0), ("IL", "17003", 100.0)]), {"IL": 1000.0})]
    df, _ = hm.fill_withheld(cur, {"IL": 1000.0}, past)
    assert df.loc[df["fips"] == "17001", "Value"].tolist() == [600.0]
    assert df.loc[df["fips"] == "17003", "Value"].tolist() == [400.0]


def test_fill_treats_a_zero_published_county_as_withheld():
    cur = _cty([("IL", "17001", 600.0), ("IL", "17003", 0.0)])                                  # a (D) arrives as 0
    past = [(_cty([("IL", "17003", 100.0)]), {"IL": 1000.0})]
    df, info = hm.fill_withheld(cur, {"IL": 1000.0}, past)
    assert df.loc[df["fips"] == "17003", "Value"].tolist() == [400.0] and df.loc[df["fips"] == "17003", "Estimated"].tolist() == [True]
    assert info["estimated_n"] == 1 and len(df) == 2


def test_fill_leaves_a_state_short_when_nothing_can_take_the_remainder():
    cur = _cty([("IL", "17001", 600.0), ("IA", "19001", 100.0)])
    past = [(_cty([("IA", "19003", 100.0)]), {"IA": 200.0})]
    df, info = hm.fill_withheld(cur, {"IL": 1000.0, "IA": 200.0}, past)
    assert df.loc[df["State"] == "IL", "Value"].sum() == 600.0                                  # nothing to put the other 400 on: left off, not smeared over the published counties
    assert info["short"] == [("IL", 400.0)] and "IL" not in info["estimated"]
    assert df.loc[df["State"] == "IA", "Value"].sum() == 200.0                                  # IA filled from its history in the same call


def test_fill_a_state_with_no_published_counties_comes_from_history():
    cur = _cty([("IA", "19001", 100.0)])
    past = [(_cty([("NE", "31001", 30.0), ("NE", "31003", 10.0)]), {"NE": 100.0})]
    df, info = hm.fill_withheld(cur, {"IA": 100.0, "NE": 800.0}, past)
    ne = df[df["State"] == "NE"].set_index("fips")["Value"]
    assert math.isclose(ne["31001"], 600.0) and math.isclose(ne["31003"], 200.0)               # shares 0.3 : 0.1 of the full 800
    assert math.isclose(info["coverage"], 100.0 / 900.0)


def test_fill_leaves_a_complete_state_alone():
    cur = _cty([("IL", "17001", 996.0)])
    past = [(_cty([("IL", "17003", 100.0)]), {"IL": 1000.0})]
    df, info = hm.fill_withheld(cur, {"IL": 1000.0}, past)
    assert len(df) == 1 and info["estimated_n"] == 0 and info["short"] == []                  # 0.4% short: inside the threshold


def test_fill_caps_coverage_at_100_percent_when_counties_overshoot():
    df, info = hm.fill_withheld(_cty([("OH", "39001", 1010.0)]), {"OH": 1000.0}, [])
    assert info["coverage"] == 1.0 and len(df) == 1


def test_fill_apply_false_keeps_published_counties_only():
    cur, past, rma = _toy_fill()
    df, info = hm.fill_withheld(cur, {"IL": 1000.0}, past, rma, apply=False)
    assert len(df) == 3 and not df["Estimated"].any() and info["estimated_n"] == 0 and math.isclose(info["coverage"], 0.6)


def test_fill_does_not_change_its_inputs():
    cur, past, rma = _toy_fill()
    snap = (cur.copy(), [(c.copy(), dict(t)) for c, t in past], rma.copy())
    hm.fill_withheld(cur, {"IL": 1000.0}, past, rma)
    assert cur.equals(snap[0]) and rma.equals(snap[2])
    assert all(c.equals(s[0]) and t == s[1] for (c, t), s in zip(past, snap[1]))


def test_fill_ignores_a_history_year_without_a_state_total():
    cur = _cty([("IL", "17001", 500.0)])
    past = [(_cty([("IL", "17003", 100.0)]), {}),                                               # no total for that year: its share cannot be formed
            (_cty([("IL", "17005", 100.0)]), {"IL": 1000.0})]
    df, _ = hm.fill_withheld(cur, {"IL": 1000.0}, past)
    assert set(df[df["Estimated"]]["fips"]) == {"17005"}


def test_fill_states_are_independent():
    cur = _cty([("IL", "17001", 500.0), ("IA", "19001", 100.0)])
    past = [(_cty([("IL", "17003", 100.0), ("IA", "19003", 100.0)]), {"IL": 1000.0, "IA": 1000.0})]
    df, info = hm.fill_withheld(cur, {"IL": 1000.0, "IA": 300.0}, past)
    assert math.isclose(df.loc[df["fips"] == "17003", "Value"].iloc[0], 500.0) and math.isclose(df.loc[df["fips"] == "19003", "Value"].iloc[0], 200.0)
    assert info["estimated"] == {"IL": (1, 500.0), "IA": (1, 200.0)}


def test_fill_with_no_history_and_no_fallback_lists_every_short_state():
    df, info = hm.fill_withheld(_cty([("IL", "17001", 600.0)]), {"IL": 1000.0, "NE": 2000.0}, [])
    assert info["short"] == [("NE", 2000.0), ("IL", 400.0)] and len(df) == 1                   # largest first


def test_fill_empty_county_list():
    df, info = hm.fill_withheld(pd.DataFrame(columns=["State", "County", "fips", "Value"]), {"IL": 1000.0}, [])
    assert df.empty and info["coverage"] == 0.0 and info["short"] == [("IL", 1000.0)]


# -- names -> FIPS ----------------------------------------------------------------------------------
def test_norm_county_spellings():
    same = [("St. Clair", "Saint Clair", "St Clair", "ST CLAIR", "st.clair county"), ("O'Brien", "O’Brien", "O Brien", "OBRIEN"), ("Ste. Genevieve", "Sainte Genevieve", "St Genevieve"),
            ("De Kalb", "DeKalb", "DEKALB County"), ("Miami-Dade", "Miami Dade"), ("Orleans Parish", "Orleans"), ("Kenai Peninsula Borough", "Kenai Peninsula"),
            ("Yukon-Koyukuk Census Area", "Yukon Koyukuk"), ("Juneau City and Borough", "Juneau")]
    for group in same:
        assert len({hm.norm_county(n) for n in group}) == 1, group
    assert hm.norm_county("Prince George's") == "princegeorges" and hm.norm_county(None) == "" and hm.norm_county("  ") == ""
    assert hm.norm_county("St. Louis") != hm.norm_county("Louis")


def test_name_lookup_and_resolver():
    lookup = hm.build_name_lookup(TOY, STATE_ABBR)
    resolve = hm.make_resolver(lookup)
    assert resolve("IL", "Adams") == ADAMS and resolve("IL", "ADAMS COUNTY") == ADAMS
    assert resolve("IL", "Saint Clair") == "17163" and resolve("IA", "O Brien") == "19141" and resolve("MO", "Sainte Genevieve") == "29186"
    assert resolve("IL", "Nowhere") is None and resolve("IA", "Adams") is None                 # a county name only counts inside its own state
    assert resolve("VA", "Suffolk City") == "51800" and resolve("VA", "Suffolk") == "51800"      # an independent city written 'Suffolk City'


def test_resolver_county_beats_a_city_of_the_same_name():
    resolve = hm.make_resolver(hm.build_name_lookup(TOY, STATE_ABBR))
    assert resolve("MO", "St. Louis") == "29189"                                                # the county (29189), not the independent city (29510)
    reordered = {**TOY, "features": list(reversed(TOY["features"]))}
    assert hm.make_resolver(hm.build_name_lookup(reordered, STATE_ABBR))("MO", "St. Louis") == "29189"


def test_resolver_renamed_counties():
    resolve = hm.make_resolver({})
    assert resolve("SD", "Oglala Lakota") == "46113" and resolve("AK", "Kusilvak") == "02270"
    assert resolve("SD", "Shannon") is None


def test_rma_county_values():
    resolve = hm.make_resolver(hm.build_name_lookup(TOY, STATE_ABBR))
    frame = pd.DataFrame([("IL", "Adams", "Irrigated", 100.0), ("IL", "Adams", "Non-Irrigated", 50.0), ("IL", "Pike", "Non-Irrigated", 30.0), ("IL", "Nowhere", "Non-Irrigated", 10.0),
                          ("IL", "Tiny", "Irrigated", 0.0), ("IL", "St. Clair", "Irrigated", 7.0), ("IL", "Saint Clair", "Irrigated", 3.0)], columns=["State", "County", "PG", "Reported Production"])
    vals, miss = hm.rma_county_values(frame, "Reported Production", "All", resolve)
    assert vals == {ADAMS: 150.0, PIKE: 30.0, "17163": 10.0}                                    # two spellings of one county add up; a zero county is dropped
    assert miss == [("IL", "Nowhere")]
    irr, miss_irr = hm.rma_county_values(frame, "Reported Production", "Irrigated", resolve)
    assert irr == {ADAMS: 100.0, "17163": 10.0} and miss_irr == []
    non, _ = hm.rma_county_values(frame, "Reported Production", "Non-Irrigated", resolve)
    assert non == {ADAMS: 50.0, PIKE: 30.0}


# -- outlines ----------------------------------------------------------------------------------------
def test_outline_collection_dissolves_by_group():
    idx = toy_index()
    coll = hm.outline_collection(idx, {ADAMS: "A", PIKE: "A", ADAIR: "B", TINY: None}, {"A": {"name": "Alpha"}, "B": {"name": "Beta"}})
    assert coll["type"] == "FeatureCollection" and len(coll["features"]) == 2
    by = {f["properties"]["key"]: f for f in coll["features"]}
    assert by["A"]["properties"] == {"name": "Alpha", "key": "A"}
    assert by["A"]["geometry"]["type"] == "Polygon"                                             # the two touching counties merged into one outline
    xs = [c[0] for c in by["A"]["geometry"]["coordinates"][0]]
    assert min(xs) == -91.5 and max(xs) == -90.5


def test_outline_collection_rounds_coordinates_and_leaves_ungrouped_out():
    idx = toy_index()
    coll = hm.outline_collection(idx, {ADAMS: "A", "00000": "Z"}, {})
    assert [f["properties"]["key"] for f in coll["features"]] == ["A"]
    fine = hm.CountyIndex({"features": [feat("37", "001", "Fine", box(-80.123456, 35.123456, -79.654321, 35.654321))]})
    flat = [v for ring in hm.outline_collection(fine, {"37001": "F"}, {})["features"][0]["geometry"]["coordinates"] for pt in ring for v in pt]
    assert sorted(set(flat)) == [-80.123, -79.654, 35.123, 35.654]                              # three decimals (about 100 m): the outlines go to the browser


def test_district_groups_keys_names_and_edge_cases():
    frame = pd.DataFrame({"fips": ["17001", "17149", "17149", "19001", "19003", "19005", "19007"], "State": ["IL", "IL", "IL", "IA", "IA", "IA", "IA"],
                          "asd_code": ["10", "10", "10", "10", " ", None, "20"], "asd_desc": ["NORTHWEST", "NORTHWEST", "NORTHWEST", "NORTH CENTRAL", "X", "Y", np.nan]})
    group, props = hm.district_groups(frame)
    assert group == {"17001": "17-10", "17149": "17-10", "19001": "19-10", "19007": "19-20"}   # same code in two states = two districts; blank / missing codes skipped; duplicate fips once
    assert props["17-10"] == {"name": "Northwest, IL", "state": "IL"} and props["19-10"]["name"] == "North Central, IA"
    assert props["19-20"]["name"] == "District 20, IA"                                         # no description: still named


def test_district_groups_without_the_columns():
    assert hm.district_groups(None) == ({}, {}) and hm.district_groups(pd.DataFrame()) == ({}, {})
    assert hm.district_groups(pd.DataFrame({"fips": ["17001"], "State": ["IL"]})) == ({}, {})
    g, p = hm.district_groups(pd.DataFrame({"fips": ["17001"], "State": ["IL"], "asd_code": ["10"]}))
    assert g == {"17001": "17-10"} and p["17-10"]["name"] == "District 10, IL"


def test_attach_values_returns_a_copy():
    coll = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {"name": "A", "key": "k1"}}, {"type": "Feature", "geometry": None, "properties": {"name": "B", "key": "k2"}}]}
    before = copy.deepcopy(coll)
    out = hm.attach_values(coll, {"k1": 12_345_678.0}, lambda v: hm.fmt_amount(v, "bu"))
    assert coll == before                                                                      # a cached collection shared by every session must not change
    p = {f["properties"]["key"]: f["properties"] for f in out["features"]}
    assert p["k1"]["value"] == 12345678.0 and p["k1"]["label"] == "12.3M bu" and p["k1"]["name"] == "A"
    assert p["k2"]["value"] == 0.0 and p["k2"]["label"] == "0 bu"


# -- view, radius, labels -----------------------------------------------------------------------------
def test_view_for():
    assert hm.view_for(None) == hm.US_VIEW
    lat, lon, zoom = hm.view_for((-96.64, 40.37, -90.14, 43.5))                                 # Iowa
    assert math.isclose(lat, 41.935) and math.isclose(lon, -93.39) and 5.0 <= zoom <= 7.0, (lat, lon, zoom)
    assert 2.5 < hm.view_for((-125, 24, -66, 49))[2] < 3.5                                      # the lower 48 fits at about the US default zoom
    assert hm.view_for((-180, -60, 180, 80))[2] == 2.5                                          # the world bottoms out at the floor
    assert hm.view_for((-93.0, 41.0, -92.99, 41.01))[2] == 7.5                                  # a pin-sized box tops out at the ceiling
    assert hm.view_for((-93.0, 41.0, -92.0, 42.0))[2] > hm.view_for((-96.64, 40.37, -90.14, 43.5))[2]       # a smaller area zooms in further


def test_radius_px():
    r = hm.radius_px(70, 41.9, 6.0)
    assert r == round(70000 / (78271.517 * math.cos(math.radians(41.9)) / 64)) == 77
    assert hm.radius_px(200, 38.8, 3.55) < hm.radius_px(200, 38.8, 5.0)                          # zoomed in, the same kilometres are more pixels
    assert hm.radius_px(100, 38.8, 4) > hm.radius_px(50, 38.8, 4)
    assert hm.radius_px(1, 40, 3) == 12 and hm.radius_px(300, 40, 12) == 220                     # clamped at both ends


def test_fmt_amount():
    f = hm.fmt_amount
    assert f(12_345_678, "bu") == "12.3M bu" and f(2.5e9, "bu") == "2.50B bu" and f(12_345, "bu") == "12K bu" and f(999, "bu") == "999 bu" and f(0, "bu") == "0 bu"
    assert f(1_000_000, "ac") == "1.0M ac" and f(-5e6, "bu") == "-5.0M bu" and f(5e6, "") == "5.0M"


# -- the deck -------------------------------------------------------------------------------------------
def test_palettes_are_valid_deck_ramps():
    for name, ramp in hm.PALETTES.items():
        assert len(ramp) == 6 and ramp[0][3] == 0 and ramp[-1][3] == 255, name                  # transparent at the low end so the basemap shows through
        assert all(len(c) == 4 and all(isinstance(v, int) and 0 <= v <= 255 for v in c) for c in ramp), name
        assert [c[3] for c in ramp] == sorted(c[3] for c in ramp), name


def test_build_deck_layers_and_serialisation():
    import pydeck as pdk
    pts = hm.spread_values(hm.lattice_cells(toy_index().shapes), {ADAMS: 1e6, PIKE: 2e6})
    outlines = hm.outline_collection(toy_index(), {ADAMS: "A"}, {"A": {"name": "Alpha", "label": "1.0M bu"}})
    extra = pdk.Layer("ScatterplotLayer", data=[{"lon": -91.2, "lat": 39.7}], get_position="[lon, lat]")
    deck = hm.build_deck(pts, radius=40, palette="Green", outlines=outlines, view=(40.0, -91.0, 6.0), extra_layers=[extra])
    assert [l.type for l in deck.layers] == ["HeatmapLayer", "GeoJsonLayer", "ScatterplotLayer"]    # pins / a basis surface go on top
    heat = deck.layers[0]
    assert isinstance(heat.data, list) and isinstance(heat.data[0], dict) and set(heat.data[0]) == {"lon", "lat", "w"}      # records: pydeck cannot serialise a DataFrame under pandas 3
    assert deck.layers[1].pickable and not heat.pickable
    assert deck.map_style == hm.CARTO_LIGHT and deck.initial_view_state.zoom == 6.0 and "{name}" in deck._tooltip["html"] and "{label}" in deck._tooltip["html"]
    assert heat.color_range == hm.PALETTES["Green"] and heat.radius_pixels == 40
    assert '"HeatmapLayer"' in deck.to_json()                                                    # serialises without error


def test_build_deck_without_points_or_outlines():
    deck = hm.build_deck(pd.DataFrame({"lon": [], "lat": [], "w": []}), outlines={"type": "FeatureCollection", "features": []})
    assert deck.layers == [] and deck.initial_view_state.zoom == hm.US_VIEW[2]
    assert hm.build_deck(hm.spread_values(hm.lattice_cells(toy_index().shapes), {ADAMS: 5.0}), palette="Nope").layers[0].color_range == hm.PALETTES["Heat"]


# -- runner ---------------------------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("  ok   ", name)
        except Exception as e:                                                                   # noqa: BLE001
            failed.append(name)
            print("  FAIL ", name, "->", type(e).__name__, e)
    print(f"\n{len(tests) - len(failed)}/{len(tests)} tests passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)

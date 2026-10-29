"""
River segments for the Draw Territory model: how many soybeans are loaded onto barges in each pool of the river system,
backed out of lock tonnage.

Method (all downbound, annual):
  flow at a lock  = everything loaded upstream of it, so  loadings in a pool = flow(lower lock) - flow(upper lock).
  USDA AgTransport (data/draw/usda_barge_annual.csv) measures downbound corn / soybeans / wheat / other grain, but only at seven
  locks (La Grange, Mississippi 15 / 25 / 26 / 27, Olmsted, Arkansas 1). The Corps (data/draw/corps_lock_farm_tons.csv, LPMS annual
  commodity report) has every lock but only the whole 'Food and Farm Products' group, both directions. So the Corps total at each
  lock is scaled to USDA grain by the USDA / Corps ratio at the anchor locks on the same river (interpolated between anchors by
  position along the river), and soybeans are the interpolated USDA soy share of grain. Where USDA measures a lock, the flow
  equals USDA exactly.
Unmeasured: anything loaded below Lock 27 (St. Louis harbor to Cairo), the Lower Mississippi, the Arkansas, and the Ohio below Olmsted.

    python build_river_segments.py            # prints the table, writes data/draw/river_segments.json
"""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data", "draw")
YEARS = list(range(2021, 2026))
BU_PER_SHORT_TON_SOY = 2000 / 60.0

# (river, lock, name, lat, lon, USDA lock name where measured). Order upstream -> downstream. pos = -lat (IL, MI), -lon (OH).
LOCKS = [
    ("IL", "03", "Brandon Road", 41.51, -88.10, None), ("IL", "04", "Dresden Island", 41.39, -88.27, None),
    ("IL", "05", "Marseilles", 41.33, -88.69, None), ("IL", "06", "Starved Rock", 41.32, -89.01, None),
    ("IL", "07", "Peoria", 40.69, -89.57, None), ("IL", "08", "La Grange", 39.99, -90.54, "IL La Grange"),
    ("MI", "10", "L&D 10 Guttenberg", 42.79, -91.10, None), ("MI", "11", "L&D 11 Dubuque", 42.50, -90.66, None),
    ("MI", "12", "L&D 12 Bellevue", 42.26, -90.42, None), ("MI", "13", "L&D 13 Fulton", 41.90, -90.18, None),
    ("MI", "14", "L&D 14 LeClaire", 41.58, -90.34, None), ("MI", "15", "L&D 15 Rock Island", 41.51, -90.56, "MS Lock 15"),
    ("MI", "16", "L&D 16 Muscatine", 41.40, -91.05, None), ("MI", "17", "L&D 17 New Boston", 41.18, -91.05, None),
    ("MI", "18", "L&D 18 Gladstone", 40.88, -91.07, None), ("MI", "19", "L&D 19 Keokuk", 40.39, -91.38, None),
    ("MI", "20", "L&D 20 Canton", 40.13, -91.52, None), ("MI", "21", "L&D 21 Quincy", 39.89, -91.42, None),
    ("MI", "22", "L&D 22 Saverton", 39.63, -91.26, None), ("MI", "24", "L&D 24 Clarksville", 39.37, -90.91, None),
    ("MI", "25", "L&D 25 Winfield", 39.00, -90.69, "MS Lock 25"), ("MI", "26", "Mel Price (26) Alton", 38.88, -90.16, "MS Lock 26"),
    ("MI", "27", "Chain of Rocks (27)", 38.76, -90.17, "MS Locks 27"),
    ("OH", "41", "Markland", 38.78, -84.96, None), ("OH", "42", "McAlpine", 38.28, -85.79, None),
    ("OH", "75", "Cannelton", 37.91, -86.74, None), ("OH", "76", "Newburgh", 37.93, -87.37, None),
    ("OH", "77", "John T. Myers", 37.79, -87.99, None), ("OH", "78", "Smithland", 37.17, -88.43, None),
    ("OH", "79", "Olmsted", 37.18, -89.06, "OH Olmsted"),
]
POS = lambda river, lat, lon: -lon if river in ("OH", "IL") else -lat      # the IL and Ohio run east-west; the Mississippi north-south

# Pin -> river. Anything not listed is treated as off these three rivers (Missouri, Arkansas, Lower Mississippi, Pacific NW, inland).
IL_PINS = ["Morris", "Seneca", "River Landing", "Ottawa", "Spring Valley", "Peru", "Hennepin", "Lacon", "Creve Coeur", "Havana",
           "Beardstown", "Meredosia", "Naples"]
MI_PINS = ["Savage", "St. Paul", "Winona", "CLAYTON", "Clinton", "ALBANY", "Bettendorf", "Davenport", "Muscatine", "New Boston",
           "Keithsburg", "Meekers", "Oquawka", "Gulfport", "Burlington", "Dallas City", "Nauvoo", "Quincy", "East Hannibal",
           "Lagrange", "Buffalo Island", "Fairmont City", "East St. Louis", "St. Louis", "Sauget", "Cahokia"]
OH_PINS = ["Silver Grove", "Evansville", "Newburgh", "Mt. Vernon, IN", "Rockport", "OWENSBORO", "Henderson", "Paducah"]
FIX = {"CHS Quincy Elevator": (39.936, -91.410)}      # the tracker has this pin in Washington, DC
SKIP = {"ADM Livingston Point, KY"}                     # the tracker has this pin in North Carolina; not placed


def river_of(name):
    if name in SKIP:
        return None
    for tag, lst in (("IL", IL_PINS), ("OH", OH_PINS), ("MI", MI_PINS)):
        if any(t.lower() in name.lower() for t in lst):
            return tag
    return None


# Regional nodes: adjacent pools merged until each is bigger than the noise in the lock-to-lock differences (+/- ~0.3 Mt grain).
NODES = [
    ("IL-1 Joliet / Morris / Seneca", "IL", ["above Brandon Road", "Brandon Road to Dresden Island", "Dresden Island to Marseilles"]),
    ("IL-2 Ottawa (Marseilles to Starved Rock)", "IL", ["Marseilles to Starved Rock"]),
    ("IL-3 Peru / Spring Valley / Hennepin / Lacon (Starved Rock to Peoria)", "IL", ["Starved Rock to Peoria"]),
    ("IL-4 Pekin / Havana / Beardstown (Peoria to La Grange)", "IL", ["Peoria to La Grange"]),
    ("MS-1 Twin Cities / Winona / Clayton (above L&D 10)", "MI", ["above L&D 10 Guttenberg"]),
    ("MS-2 Dubuque / Clinton / Quad Cities (L&D 10 to 15)", "MI", ["L&D 10 Guttenberg to L&D 11 Dubuque", "L&D 11 Dubuque to L&D 12 Bellevue",
                                                              "L&D 12 Bellevue to L&D 13 Fulton", "L&D 13 Fulton to L&D 14 LeClaire",
                                                              "L&D 14 LeClaire to L&D 15 Rock Island"]),
    ("MS-3 Muscatine / Burlington (L&D 15 to 19)", "MI", ["L&D 15 Rock Island to L&D 16 Muscatine", "L&D 16 Muscatine to L&D 17 New Boston",
                                                      "L&D 17 New Boston to L&D 18 Gladstone", "L&D 18 Gladstone to L&D 19 Keokuk"]),
    ("MS-4 Keokuk / Quincy / Hannibal (L&D 19 to 25)", "MI", ["L&D 19 Keokuk to L&D 20 Canton", "L&D 20 Canton to L&D 21 Quincy",
                                                         "L&D 21 Quincy to L&D 22 Saverton", "L&D 22 Saverton to L&D 24 Clarksville",
                                                         "L&D 24 Clarksville to L&D 25 Winfield"]),
    ("MS-5 Alton / Grafton / IL River below La Grange (Pool 26)", "MI", ["Pool 26 + Illinois River below La Grange (Mel Price less Lock 25 less La Grange)"]),
    ("MS-6 Wood River / Granite City + Missouri River (Pool 27)", "MI", ["Pool 27 + Missouri River (Lock 27 less Mel Price)"]),
    ("OH-1 Cincinnati to Cannelton (above Cannelton)", "OH", ["above Markland", "Markland to McAlpine", "McAlpine to Cannelton"]),
    ("OH-2 Owensboro / Rockport (Cannelton to Newburgh)", "OH", ["Cannelton to Newburgh"]),
    ("OH-3 Evansville / Mt. Vernon / Henderson (Newburgh to Myers)", "OH", ["Newburgh to John T. Myers"]),
    ("OH-4 Paducah (Myers to Olmsted)", "OH", ["John T. Myers to Smithland", "Smithland to Olmsted"]),
]


def merge_nodes(segs):
    by = {s["segment"]: s for s in segs}
    out = []
    for name, river, parts in NODES:
        ss = [by[p] for p in parts]
        soy = np.sum([s["soy_mbu_by_year"] for s in ss], axis=0)
        grain = np.sum([s["grain_mtons_by_year"] for s in ss], axis=0)
        out.append({"node": name, "river": river, "soy_mbu": float(soy.mean()), "soy_mbu_min": float(soy.min()), "soy_mbu_max": float(soy.max()),
                    "soy_mbu_2025": float(soy[-1]), "grain_mtons": float(grain.mean()), "pins": sorted({p for s in ss for p in s["pins"]})})
    return out


AK_PINS = ["Wagoner", "Webbers Falls", "Port 33", "Van Buren", "Pine Bluff"]       # McClellan-Kerr: all of it passes Arkansas Lock 1
PNW_PINS = ["PNW export (rail)", "Kennewick"]                                         # beans that leave by rail to the coast
DROP_PINS = ["CHS Macon", "ADM Livingston Point, KY"]                                  # inland / mis-placed in the tracker
EXTRA_PINS = {"MS-5": ["Meredosia", "Naples"]}                                         # IL River terminals below La Grange
FGIS_FALLBACK = {"east_gulf_mbu": 1017.0, "west_coast_mbu": 377.0}


def fgis_soy_exports():
    """Average annual soybean ship inspections (M bu), 2021-25, by FGIS region, from AgTransport sruw-w49i."""
    import urllib.parse
    import urllib.request
    q = {"$select": "fgis_reg,sum(pounds) as lbs", "$group": "fgis_reg", "$limit": "50",
         "$where": "grain='SOYBEANS' and year>=2021 and year<=2025 and type_carrier_text='SHIP'"}
    try:
        rows = json.load(urllib.request.urlopen("https://agtransport.usda.gov/resource/sruw-w49i.json?" + urllib.parse.urlencode(q), timeout=90))
        by = {r["fgis_reg"]: float(r["lbs"]) / 60 / 1e6 / 5 for r in rows}
        return {"east_gulf_mbu": by["EAST GULF"], "west_coast_mbu": by["WEST COAST"], "source": "FGIS ship inspections 2021-25 avg"}
    except Exception as e:                                       # keep the build working offline
        return {**FGIS_FALLBACK, "source": f"fallback constants ({type(e).__name__})"}


def build_groups(segs, nodes, pins, usda, bu):
    """Pin groups with annual soybean targets for draw_model.solve. Every group's pins share the group's target."""
    fg = fgis_soy_exports()
    ak = usda[(usda.lock == "AK Lock 1") & (usda.commodity == "Soybeans") & usda.year.between(YEARS[0], YEARS[-1])].tons.sum() / len(YEARS)
    ak_mbu = ak * bu / 1e6
    placed, groups = set(), []
    for n in nodes:
        pl = list(n["pins"]) + [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in EXTRA_PINS.get(n["node"].split()[0], []))]
        placed.update(pl)
        groups.append({"name": n["node"], "kind": "lock", "pins": pl, "target_mbu": max(0.0, n["soy_mbu"]), "range_mbu": [n["soy_mbu_min"], n["soy_mbu_max"]]})
    for name, tags, tgt, kind in (("AK Arkansas River (Lock 1)", AK_PINS, ak_mbu, "lock"),
                                  ("PNW export by rail", PNW_PINS, fg["west_coast_mbu"], "pnw")):
        pl = [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in tags)]
        placed.update(pl)
        groups.append({"name": name, "kind": kind, "pins": pl, "target_mbu": float(tgt)})
    measured = sum(g["target_mbu"] for g in groups if g["kind"] == "lock")
    pinless = sum(g["target_mbu"] for g in groups if g["kind"] == "lock" and not g["pins"])
    harbor = [p for p in segs if p["segment"].startswith("St. Louis harbor")][0]["pins"]
    lower = [p["key"] for p in pins if p["key"] not in placed and p["key"] not in DROP_PINS]
    groups = [g for g in groups if g["pins"]]
    groups.append({"name": "Below Lock 27 / Lower Mississippi", "kind": "below", "pins": lower,
                   "target_mbu": max(0.0, fg["east_gulf_mbu"] - measured + pinless),
                   "note": "East Gulf soybean ship inspections less barge arrivals the locks measure; includes rail to the Gulf"})
    return {"fgis": fg, "stl_harbor_pins": harbor, "measured_arrivals_mbu": measured, "groups": groups, "dropped": DROP_PINS}


def main():
    corps = pd.read_csv(os.path.join(D, "corps_lock_farm_tons.csv"), dtype={"lock": str})
    usda = pd.read_csv(os.path.join(D, "usda_barge_annual.csv"))
    g = usda.pivot_table(index=["year", "lock"], columns="commodity", values="tons", aggfunc="sum").fillna(0)
    g["grain"] = g.sum(axis=1)
    g["soy"] = g["Soybeans"]
    flow = {}                       # (river, lock, year) -> (grain tons, soy tons)
    for river in ("IL", "MI", "OH"):
        locks = [l for l in LOCKS if l[0] == river]
        anchors = [l for l in locks if l[5]]
        apos = [POS(river, l[3], l[4]) for l in anchors]
        for y in range(2016, 2026):
            ar, asy = [], []
            for l in anchors:
                c = corps[(corps.river == river) & (corps.lock == l[1]) & (corps.year == y)].tons.iloc[0]
                u = g.loc[(y, l[5])]
                ar.append(u["grain"] / c if c else np.nan)
                asy.append(u["soy"] / u["grain"] if u["grain"] else np.nan)
            for l in locks:
                row = corps[(corps.river == river) & (corps.lock == l[1]) & (corps.year == y)]
                if row.empty or not np.isfinite(ar).all():
                    continue
                p = POS(river, l[3], l[4])
                r, s = np.interp(p, apos, ar), np.interp(p, apos, asy)
                flow[(river, l[1], y)] = (row.tons.iloc[0] * r, row.tons.iloc[0] * r * s)

    def avg(fn):
        return float(np.mean([fn(y) for y in YEARS]))

    def f(river, lock, y, k=1):
        return flow[(river, lock, y)][k]

    pins = json.load(open(os.path.join(D, "draw_inputs.json")))["river"]
    segs = []
    for river in ("IL", "MI", "OH"):
        locks = [l for l in LOCKS if l[0] == river]
        bounds = [None] + locks + [None]
        for i in range(len(bounds) - 1):
            up, dn = bounds[i], bounds[i + 1]
            if dn is None or (river == "MI" and dn[1] in ("26", "27")):     # replaced by the Pool 26 / Pool 27 rows below
                continue
            soy = (lambda y: f(river, dn[1], y)) if up is None else (lambda y, u=up, d=dn: f(river, d[1], y) - f(river, u[1], y))
            grain = (lambda y: f(river, dn[1], y, 0)) if up is None else (lambda y, u=up, d=dn: f(river, d[1], y, 0) - f(river, u[1], y, 0))
            lat_hi = -1e9 if up is None else POS(river, up[3], up[4])
            lat_lo = POS(river, dn[3], dn[4])
            here = []
            for p in pins:
                nm = p["key"]
                lat, lon = FIX.get(nm, (p["lat"], p["lon"]))
                if river_of(nm) != river:
                    continue
                if river == "MI" and lat < 38.76:       # below Lock 27 = the St. Louis harbor, handled below
                    continue
                pp = POS(river, lat, lon)
                if lat_hi < pp <= lat_lo:
                    here.append(nm)
            segs.append({"river": river, "segment": f'{"above " if up is None else up[2] + " to "}{dn[2]}',
                         "upper_lock": up[2] if up else None, "lower_lock": dn[2],
                         "grain_mtons": avg(grain) / 1e6, "soy_mtons": avg(soy) / 1e6, "soy_mbu": avg(soy) * BU_PER_SHORT_TON_SOY / 1e6,
                         "soy_mbu_2025": soy(2025) * BU_PER_SHORT_TON_SOY / 1e6, "pins": here,
                         "soy_mbu_by_year": [soy(y) * BU_PER_SHORT_TON_SOY / 1e6 for y in YEARS],
                         "grain_mtons_by_year": [grain(y) / 1e6 for y in YEARS]})
    # the pieces the lock chain does not tile
    pool26 = lambda y, k: f("MI", "26", y, k) - f("MI", "25", y, k) - f("IL", "08", y, k)
    pool27 = lambda y, k: f("MI", "27", y, k) - f("MI", "26", y, k)
    for nm, fn, note in (("Pool 26 + Illinois River below La Grange (Mel Price less Lock 25 less La Grange)", pool26,
                          "Meredosia / Naples / Hardin / Alton; within the data's noise of zero"),
                         ("Pool 27 + Missouri River (Lock 27 less Mel Price)", pool27, "Wood River / Granite City + all Missouri River grain barges")):
        segs.append({"river": "MI", "segment": nm, "upper_lock": "Mel Price" if "27" in nm[:7] else "L&D 25 + La Grange",
                     "lower_lock": "Chain of Rocks (27)" if "27" in nm[:7] else "Mel Price (26)",
                     "grain_mtons": avg(lambda y: fn(y, 0)) / 1e6, "soy_mtons": avg(lambda y: fn(y, 1)) / 1e6,
                     "soy_mbu": avg(lambda y: fn(y, 1)) * BU_PER_SHORT_TON_SOY / 1e6,
                     "soy_mbu_2025": fn(2025, 1) * BU_PER_SHORT_TON_SOY / 1e6,
                     "soy_mbu_by_year": [fn(y, 1) * BU_PER_SHORT_TON_SOY / 1e6 for y in YEARS],
                     "grain_mtons_by_year": [fn(y, 0) / 1e6 for y in YEARS],
                     "pins": [], "note": note})
    harbor = [p["key"] for p in pins if river_of(p["key"]) == "MI" and p["lat"] < 38.76 and p["lat"] > 38.4]
    segs.append({"river": "MI", "segment": "St. Louis harbor (below Lock 27, no lock measures it)", "upper_lock": "Chain of Rocks (27)",
                 "lower_lock": None, "grain_mtons": None, "soy_mtons": None, "soy_mbu": None, "soy_mbu_2025": None, "pins": harbor,
                 "note": "unmeasured by locks"})
    out_nodes = merge_nodes(segs)
    out = {"method": __doc__.strip().split("\n")[0], "years": YEARS, "segments": segs, "nodes": out_nodes,
           "totals": {"soy_mbu_at_L27": avg(lambda y: f("MI", "27", y)) * BU_PER_SHORT_TON_SOY / 1e6,
                      "soy_mbu_at_Olmsted": avg(lambda y: f("OH", "79", y)) * BU_PER_SHORT_TON_SOY / 1e6,
                      "soy_mbu_at_LaGrange": avg(lambda y: f("IL", "08", y)) * BU_PER_SHORT_TON_SOY / 1e6}}
    with open(os.path.join(D, "river_segments.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    with open(os.path.join(D, "river_nodes.json"), "w") as fh:
        json.dump(build_groups(segs, out_nodes, pins, usda, BU_PER_SHORT_TON_SOY), fh, indent=1)
    return out


if __name__ == "__main__":
    o = main()
    print(f'{"river":5} {"segment":62} {"grain Mt":>8} {"soy Mt":>7} {"soy Mbu":>8} {"2025":>6}  pins')
    for s in o["segments"]:
        g = "" if s["grain_mtons"] is None else f'{s["grain_mtons"]:8.2f}'
        print(f'{s["river"]:5} {s["segment"][:62]:62} {g:>8} {"" if s["soy_mtons"] is None else format(s["soy_mtons"], "7.2f"):>7} '
              f'{"" if s["soy_mbu"] is None else format(s["soy_mbu"], "8.0f"):>8} {"" if s["soy_mbu_2025"] is None else format(s["soy_mbu_2025"], "6.0f"):>6}  {len(s["pins"])}')
    print()
    print(f'{"node":70} {"soy Mbu":>8} {"min-max":>11} {"2025":>6}  pins')
    for n in o["nodes"]:
        print(f'{n["node"][:70]:70} {n["soy_mbu"]:8.0f} {n["soy_mbu_min"]:5.0f}-{n["soy_mbu_max"]:<5.0f} {n["soy_mbu_2025"]:6.0f}  {", ".join(n["pins"])}')
    print(o["totals"])

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
# Where Texas Gulf soybeans can come from. No data gives their origin (FGIS records the port, not the farm state), so this is the plains rail belt: the
# Houston lanes in USDA AMS's rail tariff table start in KS, NE and MN, and without it the model would take the Delta's beans, which have the river.
TXG_ORIGIN_STATES = ["TX", "OK", "KS", "NE", "MO", "IA", "MN", "SD", "ND", "CO", "NM"]
# Louisiana's rail beans come from the CN / UP / KCS lines through the upper Midwest: AMS's Louisiana lanes start in IL and IA, and the Delta has little rail
# loading (one loader in Mississippi, not much volume), so the Delta states are left out. The nearest of these states fill it first.
LA_ORIGIN_STATES = ["IL", "IA", "WI", "MN"]
# Mobile is fed by CSX, NS and CN from the eastern Corn Belt (AMS's one lane is Casey, IL); the Delta is left out for the same reason as Louisiana.
MOBILE_ORIGIN_STATES = ["IL", "IN", "OH", "KY", "TN", "IA"]
# Beans that reach the Louisiana export elevators by rail or truck, M bu/yr. Kolten set it to the 110-150M range the Corps receipts imply (waterborne soybean receipts
# at the four Louisiana river ports 917M bu against 990M shipped, 2021-24 average; wcus_louisiana_ports.csv): the midpoint. The residual (East Gulf inspections less the
# barge loadings the locks and port tonnage measure) is about 210M bu, so the barge loadings look roughly 80M bu short or the receipts double count transfers.
LA_RAIL_TRUCK_MBU = 130.0
# Where South Atlantic soybeans come from: the eastern Corn Belt (AMS's Chesapeake lane starts at Marion OH) and the Mid-Atlantic, by CSX and NS. FGIS records
# the port, not the farm state, so this is an assumption; the Delta, the Plains and the western Corn Belt are left out.
SATL_ORIGIN_STATES = ["OH", "IN", "MI", "IL", "KY", "TN", "VA", "NC", "SC", "MD", "DE", "PA", "NJ", "WV", "GA"]
SATL_PINS = ["South Atlantic hub"]
# Container loading hubs. FGIS records the state where each container lot was inspected (loaded), so the hub volumes are by state. Inland: Chicago/Joliet = IL,
# Columbus = OH, Kansas City = KS + MO, Omaha = NE, Iowa, Fargo = ND, and the small Midwest states together. The East Coast transloads are the port-state lots
# (SC, MD, NC, VA, NJ, PA, DE) plus the containers inspected at the South and North Atlantic ports.
CONTAINER_INLAND = [("Container: Chicago/Joliet IL", ["IL"], ["Container hub: Chicago/Joliet IL"]),
                    ("Container: Columbus OH", ["OH"], ["Container hub: Columbus OH"]),
                    ("Container: Kansas City (KS, MO)", ["KS", "MO"], ["Container hub: Kansas City"]),
                    ("Container: Omaha NE", ["NE"], ["Container hub: Omaha NE"]),
                    ("Container: Iowa", ["IA"], ["Container hub: Iowa (Des Moines)"]),
                    ("Container: Fargo ND", ["ND"], ["Container hub: Fargo ND"]),
                    ("Container: other Midwest (WI, MI, MN, IN)", ["WI", "MI", "MN", "IN"],
                     ["Container hub: Milwaukee WI", "Container hub: Detroit MI", "Container hub: Minneapolis MN", "Container hub: Indianapolis IN"])]
CONTAINER_EAST = ("Container: East Coast port transloads (Baltimore to Charleston)", ["SC", "MD", "NC", "VA", "NJ", "PA", "DE", "GA"],
                  ["Container hub: Baltimore MD", "Container hub: Norfolk VA", "Container hub: Charleston SC", "Container hub: New Jersey"])
CONTAINER_FALLBACK = {"states_mbu": {"IL": 55.1, "OH": 31.8, "KS": 6.9, "IA": 6.0, "NE": 5.6, "SC": 5.1, "MD": 5.1, "NC": 5.0, "VA": 4.7, "NJ": 4.3, "ND": 2.5, "PA": 2.3,
                                     "WI": 2.1, "MI": 1.4, "MO": 1.1, "MN": 0.9, "IN": 0.6, "DE": 0.5},
                      "atlantic_port_mbu": 17.0}
CENSUS_COAST = {"LOS ANGELES": "West", "LONG BEACH": "West", "OAKLAND": "West", "TACOMA": "West", "SEATTLE": "West", "PORTLAND OR": "West", "EVERETT": "West",
                    "VANCOUVER BC": "West", "HONOLULU": "West", "NORFOLK": "East", "NEW YORK": "East", "BALTIMORE": "East", "CHARLESTON": "East", "SAVANNAH": "East",
                    "WILMINGTON NC": "East", "PHILADELPHIA": "East", "JACKSONVILLE": "East", "MIAMI": "East", "PT EVERGLADES": "East", "GLOUCESTER CY": "East",
                    "TAMPA": "Gulf", "MOBILE": "Gulf", "HOUSTON": "Gulf", "NEW ORLEANS PORT REGION": "Gulf"}
# Great Lakes soybeans (FGIS LAKES region, ships): Toledo is nearly all of it. They come by truck and rail from northwest Ohio, Indiana and Michigan, so the hub has no
# rail-reach limit; FGIS records the port, not the farm state, so the origin states are an assumption.
LAKES_ORIGIN_STATES = ["OH", "IN", "MI", "IL"]
LAKES_PINS = ["Great Lakes hub"]
# Canada: soybeans that cross by truck or rail (Census customs district of exit, build_canada_exports.py); FGIS does not inspect them. Each gateway draws from the states
# behind it: Detroit from Michigan, Ohio and Indiana; Buffalo (with Ogdensburg and the New England ports) from New York and its neighbours; Pembina from North Dakota and
# Minnesota. The district is where the shipment left, not where it grew, so the origin states are an assumption.
CANADA_HUBS = [("Canada: via Detroit (MI, OH, IN)", "detroit_mbu", "Canada hub: Detroit MI", ["MI", "OH", "IN"]),
               ("Canada: via Buffalo and Ogdensburg (NY)", "buffalo_mbu", "Canada hub: Buffalo NY", ["NY", "PA", "OH", "MI"]),
               ("Canada: via Pembina (ND)", "pembina_mbu", "Canada hub: Pembina ND", ["ND", "MN"])]
CANADA_FALLBACK = {"detroit_mbu": 4.6, "buffalo_mbu": 5.9, "pembina_mbu": 0.5}
TXG_PINS = ["Texas Gulf hub"]
LA_PINS = ["Louisiana rail hub"]                                                       # export elevators below Baton Rouge fed by rail and truck rather than barge
MOBILE_PINS = ["Mobile hub"]                                                          # Houston / Galveston / Corpus Christi, modeled as one hub
MEX_PINS = ["Mexico rail: "]                                                           # border crossings the grain rails into Mexico through
FGIS_FALLBACK = {"east_gulf_mbu": 1043.8, "west_coast_columbia_mbu": 273.6, "west_coast_puget_mbu": 107.9, "west_coast_mbu": 381.5,
                 "west_gulf_mbu": 30.1, "mexico_rail_mbu": 126.4, "east_gulf_port_mbu": 18.6, "south_atlantic_mbu": 47.9, "lakes_mbu": 12.2,
                 "mexico_rail_states_mbu": {"MO": 31.8, "NE": 24.6, "IA": 19.6, "KS": 18.6, "ND": 14.0, "MN": 6.7, "WI": 4.0, "LA": 3.5, "IL": 2.0, "SD": 1.2}}


def fgis_soy_exports():
    """Average annual soybean inspections (M bu) for marketing years 2021/22-2025/26 (certificate month September onward), all carriers: the East
    Gulf, and the West Coast by port (Columbia River, Puget Sound). Same basis as export_scenario.py, from AgTransport sruw-w49i."""
    import urllib.parse
    import urllib.request
    q = {"$select": "year,month,fgis_reg,port,type_carrier_text,sum(pounds) as lbs", "$group": "year,month,fgis_reg,port,type_carrier_text", "$limit": "50000",
         "$where": "grain='SOYBEANS' and year>=2021 and year<=2026"}
    qm = {"$select": "year,month,state,sum(pounds) as lbs", "$group": "year,month,state", "$limit": "50000",
          "$where": "grain='SOYBEANS' and year>=2021 and year<=2026 and type_carrier_text='RAIL' and destination like '%MEXICO%'"}
    try:
        f = pd.DataFrame(json.load(urllib.request.urlopen("https://agtransport.usda.gov/resource/sruw-w49i.json?" + urllib.parse.urlencode(q), timeout=120)))
        f["year"], f["month"] = f.year.astype(int), f.month.astype(int)
        f["my"] = f.year.where(f.month >= 9, f.year - 1)
        f["mbu"] = f.lbs.astype(float) / 60 / 1e6
        x = f[(f.my >= 2021) & (f.my <= 2025)]
        reg = x.groupby("fgis_reg").mbu.sum() / 5
        wc = x[x.fgis_reg == "WEST COAST"].groupby("port").mbu.sum() / 5
        eg_port = x[(x.fgis_reg == "EAST GULF") & (x.port == "EAST GULF")].mbu.sum() / 5
        sa_ship = x[(x.fgis_reg == "EAST COAST") & (x.port == "S. ATLANTIC") & (x.type_carrier_text == "SHIP")].mbu.sum() / 5
        lakes_ship = x[(x.fgis_reg == "LAKES") & (x.type_carrier_text == "SHIP")].mbu.sum() / 5          # Mobile and the other non-Mississippi East Gulf ports
        m = pd.DataFrame(json.load(urllib.request.urlopen("https://agtransport.usda.gov/resource/sruw-w49i.json?" + urllib.parse.urlencode(qm), timeout=120)))
        m["year"], m["month"] = m.year.astype(int), m.month.astype(int)
        m["my"] = m.year.where(m.month >= 9, m.year - 1)
        m["mbu"] = m.lbs.astype(float) / 60 / 1e6
        ms = (m[(m.my >= 2021) & (m.my <= 2025)].groupby("state").mbu.sum() / 5).sort_values(ascending=False)
        return {"east_gulf_mbu": float(reg["EAST GULF"]), "west_coast_columbia_mbu": float(wc["COLUMBIA R."]), "west_coast_puget_mbu": float(wc["PUGET SOUND"]),
                "west_coast_mbu": float(reg["WEST COAST"]), "west_gulf_mbu": float(reg["WEST GULF"]), "mexico_rail_mbu": float(ms.sum()), "east_gulf_port_mbu": float(eg_port), "south_atlantic_mbu": float(sa_ship), "lakes_mbu": float(lakes_ship),
                "mexico_rail_states_mbu": {k: round(float(v), 2) for k, v in ms.items() if v >= 0.05}, "source": "FGIS soybean inspections, marketing years 2021/22-2025/26 average, all carriers"}
    except Exception as e:                                       # keep the build working offline
        return {**FGIS_FALLBACK, "source": f"fallback constants ({type(e).__name__})"}


HUBS = [("Illinois River", "IL-"), ("Upper Mississippi", "MS-"), ("Ohio River", "OH-"), ("Arkansas River", "AK"), ("St. Louis", "St. Louis City"),
        ("St. Louis", "East St. Louis"), ("Lower Mississippi", "Cape Girardeau"), ("Lower Mississippi", "Lower Mississippi"), ("Pacific Northwest", "Pacific Northwest"),
        ("Texas Gulf", "Texas Gulf"), ("Mexico Rail", "Mexico by rail"), ("Louisiana Rail", "Louisiana rail"), ("Mobile", "Mobile"),
        ("South Atlantic", "South Atlantic"), ("Great Lakes", "Great Lakes")]


def container_facts():
    """Soybean containers, 2021/22-2025/26 average M bu: FGIS inspections by state (where the container was loaded) and Census port of exit (where it left; USDA AgTransport
    c353-2zjn, metric tons x 36.7437 bu). FGIS 'INTERIOR' rows carry the state; the South and North Atlantic port rows have none."""
    import urllib.parse
    import urllib.request
    base_ = "https://agtransport.usda.gov/resource/"
    try:
        q = {"$select": "year,month,port,state,sum(pounds) as lbs", "$group": "year,month,port,state", "$limit": "50000",
             "$where": "grain='SOYBEANS' and year>=2021 and year<=2026 and type_carrier_text='CONTAINER'"}
        f = pd.DataFrame(json.load(urllib.request.urlopen(base_ + "sruw-w49i.json?" + urllib.parse.urlencode(q), timeout=120)))
        f["year"], f["month"] = f.year.astype(int), f.month.astype(int)
        f["my"] = f.year.where(f.month >= 9, f.year - 1)
        f["mbu"] = f.lbs.astype(float) / 60 / 1e6
        x = f[(f.my >= 2021) & (f.my <= 2025)]
        states = (x[x.port == "INTERIOR"].groupby("state").mbu.sum() / 5).sort_values(ascending=False)
        atlantic = float(x[x.port.isin(["S. ATLANTIC", "N. ATLANTIC"])].mbu.sum() / 5)
        q2 = {"$where": "commodity='Soybeans'", "$limit": "50000"}
        c = pd.DataFrame(json.load(urllib.request.urlopen(base_ + "c353-2zjn.json?" + urllib.parse.urlencode(q2), timeout=120)))
        c["year"], c["mt"] = c.year.astype(int), pd.to_numeric(c.mt)
        c["my"] = c.year.where(c.month.str[:2].astype(int) >= 9, c.year - 1)
        c = c[(c.my >= 2021) & (c.my <= 2025)]
        ports = (c.groupby("u_s_port_of_export").mt.sum() / 5 * 36.7437 / 1e6).sort_values(ascending=False)
        coast = (ports.groupby(ports.index.map(lambda p: CENSUS_COAST.get(p, "Other"))).sum())
        return {"states_mbu": {k: round(float(v), 2) for k, v in states.items() if v >= 0.05}, "atlantic_port_mbu": round(atlantic, 2),
                "census_ports_mbu": {k: round(float(v), 2) for k, v in ports.items() if v >= 0.05}, "census_coast_mbu": {k: round(float(v), 1) for k, v in coast.items()},
                "source": "FGIS container inspections (state of the lot) and Census port of exit, marketing years 2021/22-2025/26 average"}
    except Exception as e:
        return {**CONTAINER_FALLBACK, "source": f"fallback constants ({type(e).__name__})"}


def hub_of(name):
    """The region a node belongs to: the unit the map and the hub table work in. Individual terminals are only a check on a hub's volume."""
    return next((h for h, prefix in HUBS if name.startswith(prefix)), name)


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
                                  ("Pacific Northwest: Columbia River (Portland, Vancouver, Kalama, Longview)", PNW_PINS, fg["west_coast_columbia_mbu"], "pnw"),
                                  ("Pacific Northwest: Puget Sound (Seattle, Tacoma)", ["Puget Sound"], fg["west_coast_puget_mbu"], "pnw"),
                                  ("Texas Gulf (Houston, Galveston, Corpus Christi)", TXG_PINS, fg["west_gulf_mbu"], "txgulf"),
                                  ("Mexico by rail (Laredo, Eagle Pass, Brownsville, El Paso)", MEX_PINS, fg["mexico_rail_mbu"], "mexico")):
        pl = [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in tags)]
        placed.update(pl)
        g = {"name": name, "kind": kind, "pins": pl, "target_mbu": float(tgt)}
        if kind == "pnw":                                    # where the hub's circle sits on the map: the coast, not an inland loading point
            g["anchor"] = "PNW Puget Sound hub (Seattle/Tacoma)" if "Puget" in name else "PNW export (rail)"
        elif kind == "txgulf":
            g["anchor"] = "Texas Gulf hub (Houston/Galveston)"
            g["origin_states"] = TXG_ORIGIN_STATES
        elif kind == "mexico":
            g["anchor"] = "Mexico rail: Laredo TX"
            g["origin_states_mbu"] = fg["mexico_rail_states_mbu"]                       # FGIS rail inspections for Mexico by state, 2021-25 average
        groups.append(g)
    measured = sum(g["target_mbu"] for g in groups if g["kind"] == "lock")
    unplaced = [{"name": g["name"], "target_mbu": g["target_mbu"]} for g in groups if g["kind"] == "lock" and not g["pins"]]
    groups = [g for g in groups if g["pins"]]
    # Below Lock 27 no lock measures anything, so these groups take the Corps' port tonnage (WCUS shipments, soybeans, 2021-24 average).
    wc = pd.read_csv(os.path.join(D, "wcus_port_grain.csv"))
    wc = wc[(wc.commodity == "Soybeans") & wc.year.between(2021, 2024)].groupby("location_id").shipments_tons.mean() * bu / 1e6

    def port(*ids):
        return float(sum(wc.get(i, 0.0) for i in ids))
    areas = [
        ("St. Louis City (MO)", ["ADM St. Louis", "Richardson Elevator", "Ingram Tyler Street"], port(2341),
         "St. Louis City Port: the city of St. Louis. ADM St. Louis (ADM/Growmark River Systems wharf), Richardson Elevator (formerly Italgrani) and Ingram's "
         "Tyler Street terminal (formerly SCF Lewis & Clark; grain is a minor cargo there)"),
        ("East St. Louis area (IL)", ["Cargill East St. Louis", "Bunge Fairmont City", "ADM Sauget", "Cahokia", "Louis Dreyfus Cahokia", "Bruce Oakley Cahokia"],
         port(2319, 2309),
         "Southwest Regional Port (East St. Louis, Centreville, Canteen, Stites, Sugar Loaf) + America's Central Port. Cahokia holds four separate terminals "
         "(CGB, COFCO's terminal now operated by CHS, Louis Dreyfus, Bruce Oakley) and is not one of the townships the Corps names, so counting it here is an assumption"),
        ("Cape Girardeau to Chester (Scott City, Grand Tower)", ["SCOTT CITY", "GRAND TOWER"], port(2368, 2307, 2351, 2308),
         "Southeast Missouri + Kaskaskia Regional + New Bourbon + Alexandria-Cairo ports; the Kaskaskia and New Bourbon sites have no tracker pin"),
    ]
    in_area = set()
    for name, tags, tgt, note in areas:
        pl = [p["key"] for p in pins if p["key"] not in placed and p["key"] not in DROP_PINS and any(t.lower() in p["key"].lower() for t in tags)]
        in_area.update(pl)
        placed.update(pl)
        groups.append({"name": name, "kind": "area", "pins": pl, "target_mbu": tgt, "note": note})
    la_pl = [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in LA_PINS)]
    mob_pl = [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in MOBILE_PINS)]
    sa_pl = [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in SATL_PINS)]
    lk_pl = [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in LAKES_PINS)]
    placed.update(la_pl + mob_pl + sa_pl + lk_pl + [p["key"] for p in pins if p["key"].startswith(("Container hub:", "Canada hub:"))])
    lower = [p["key"] for p in pins if p["key"] not in placed and p["key"] not in DROP_PINS]
    groups.append({"name": "Lower Mississippi, Cairo to Baton Rouge", "kind": "area", "pins": lower, "target_mbu": port(6034),
                   "note": "Waterway 6034 soybean shipments (Mouth of Ohio River to Baton Rouge); every remaining tracker pin below Lock 27"})
    # The East Gulf rail hubs are what is left of the East Gulf's ship inspections once the barge loadings (lock-measured above Lock 27, Corps port tonnage
    # below it) and the non-Mississippi ports (Mobile, from FGIS) are taken out. A residual carries every error in the barge estimates (about +/-10% of
    # 800M bu), and it also holds trucks that go straight to the Louisiana elevators.
    area_total = sum(g["target_mbu"] for g in groups if g["kind"] == "area")
    mobile_mbu = float(fg["east_gulf_port_mbu"])
    la_residual_mbu = max(0.0, fg["east_gulf_mbu"] - measured - area_total - mobile_mbu)
    la_mbu = LA_RAIL_TRUCK_MBU
    groups.append({"name": "Louisiana rail and truck (Reserve, Convent, Ama, Destrehan, New Orleans)", "kind": "lagulf", "pins": la_pl, "target_mbu": la_mbu,
                   "anchor": "Louisiana rail hub (Reserve/Convent)", "origin_states": LA_ORIGIN_STATES,
                   "note": f"Beans that reached the Louisiana export elevators by rail or truck: set to {LA_RAIL_TRUCK_MBU:.0f}M bu, the midpoint of the 110-150M the Corps' "
                           f"waterborne receipts at the Louisiana ports imply. The inspections-less-barge-loadings residual is {la_residual_mbu:.0f}M bu."})
    groups.append({"name": "Mobile and the other non-Mississippi East Gulf ports", "kind": "mobile", "pins": mob_pl, "target_mbu": mobile_mbu,
                   "anchor": "Mobile hub (Mobile/Pascagoula)", "origin_states": MOBILE_ORIGIN_STATES, "note": "FGIS soybean ship inspections at the East Gulf ports other than the Mississippi River."})
    ct = container_facts()
    sm = ct["states_mbu"]
    for cname, cstates, cpins in CONTAINER_INLAND:
        groups.append({"name": cname, "kind": "container", "pins": cpins, "target_mbu": float(sum(sm.get(s_, 0.0) for s_ in cstates)),
                       "note": f"FGIS soybean container inspections where the state is {'/'.join(cstates)} (the state of the lot, not the city), 2021-25 average"})
    ename, estates, epins = CONTAINER_EAST
    groups.append({"name": ename, "kind": "cont_east", "pins": epins, "origin_states": SATL_ORIGIN_STATES,
                   "target_mbu": float(sum(sm.get(s_, 0.0) for s_ in estates) + ct["atlantic_port_mbu"]),
                   "note": "Containers loaded in the East Coast port states (SC, MD, NC, VA, NJ, PA, DE) plus those inspected at the South and North Atlantic ports, 2021-25 average"})
    try:
        cdf = pd.read_csv(os.path.join(D, "canada_soy_by_my.csv"), index_col=0)
        cavg = {k: float(cdf.loc[2021:2025, k].mean()) for k in CANADA_FALLBACK}
    except OSError:
        cavg = dict(CANADA_FALLBACK)
    for cname, ccol, cpin, cstates in CANADA_HUBS:
        groups.append({"name": cname, "kind": "canada", "pins": [cpin], "target_mbu": cavg[ccol], "anchor": cpin, "origin_states": cstates,
                       "note": "US soybeans exported to Canada by truck and rail through this gateway (Census customs district of exit), 2021/22-2025/26 average. FGIS does not inspect them."})
    groups.append({"name": "Great Lakes (Toledo, Chicago, Lake Ontario)", "kind": "lakes", "pins": lk_pl, "target_mbu": float(fg["lakes_mbu"]),
                   "anchor": "Great Lakes hub (Toledo)", "origin_states": LAKES_ORIGIN_STATES,
                   "note": "FGIS soybean ship inspections at the Great Lakes ports, 2021-25 average. Toledo is 11M bu of it. It fell from 22M bu in 2021/22 to 4-6M bu in "
                           "2023-25, so the average runs well above recent years."})
    groups.append({"name": "South Atlantic (Norfolk, Chesapeake, Charleston, Savannah)", "kind": "satl", "pins": [p["key"] for p in pins if any(t.lower() in p["key"].lower() for t in SATL_PINS)],
                   "target_mbu": float(fg["south_atlantic_mbu"]), "anchor": "South Atlantic hub (Norfolk/Chesapeake)", "origin_states": SATL_ORIGIN_STATES,
                   "note": "FGIS soybean ship inspections at the South Atlantic ports, 2021-25 average (down from 67M bu in 2021/22 to about 35M in 2025/26). Containers (16M bu) are not in it."})
    for g in groups:
        g["hub"] = hub_of(g["name"])
    return {"fgis": fg, "pnw_rail": {"railroads": ["BNSF", "UP"], "note": "BNSF and UP feed nearly all of the Pacific Northwest; CPKC is left out"},
            "containers": ct, "rail_hubs": {"txgulf": ["BNSF", "UP", "CPKC"], "mexico": ["BNSF", "UP", "CPKC"], "lagulf": ["UP", "CPKC", "CN"], "mobile": ["CSXT", "CN", "NS"], "satl": ["CSXT", "NS"], "cont_east": ["CSXT", "NS"]},
            "measured_arrivals_mbu": measured, "area_total_mbu": area_total, "unplaced_lock_nodes": unplaced, "groups": groups,
            "dropped": DROP_PINS,
            "east_gulf_other_mbu": fg["east_gulf_mbu"] - measured - area_total,
            "note": "east_gulf_other_mbu = East Gulf soybean ship inspections less barge loadings above and below Lock 27: beans that reach the Gulf by rail or are otherwise unaccounted"}


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

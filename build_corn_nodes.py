"""
Builds data/draw/river_nodes_corn.json, the export hubs and river terminal groups of the corn Draw Territory model, with the same method as the soybean model:
  * upriver pools: corn on the barges at each lock (USDA AgTransport downbound barge data at the measured locks, the Corps' lock tonnage between them), differenced into pools
  * below Lock 27, the Lower Mississippi: Corps waterborne commerce port and waterway shipments of corn (WCUS)
  * Pacific Northwest, Texas Gulf, South Atlantic, Lakes, Mobile: FGIS corn inspections at the ports; Mexico by rail: FGIS rail inspections for Mexico, with the draw
    by state held to the states the lots were inspected in; Louisiana rail and truck: what is left of the East Gulf's inspections after the barge loadings
  * Canada: US corn crossing into Canada by truck and rail (Census customs-district data, data/draw/canada_corn_by_my.csv)
It does this by running build_river_segments.py's own machinery on corn: the soybean builder's hub and node code is reused with 'Soybeans' swapped for 'Corn' in the
barge and port files, 56 lb to the bushel, and its FGIS function replaced, all in a scratch directory so the soybean files are never touched.

    python build_corn_nodes.py
"""
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request

import pandas as pd

import build_river_segments as R

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data", "draw")
FGIS = "https://agtransport.usda.gov/resource/sruw-w49i.json?"
LB = 56.0
# Which states feed a rail hub, from the STB carload waybill sample (build_rail_waybill.py, corn 2022-24, M bu a year). Where the sample sees the whole hub the draw by state is
# held to it (origin_states_mbu, the way Mexico is held to FGIS); where it sees only a slice the states are only limited (origin_states).
#   Pacific Northwest (Portland, Seattle, Richland): ND 133, MN 122, SD 104 (NE 11, WI 6, IA 5 left out). Distance alone had drawn 28% of it from NE and IA.
#   Texas Gulf (Houston): IL 37, NE 12, IA 3, IN 3, MO 2, WI 1, MN 1. Most of that tonnage has an unknown export flag, so some is Houston-area feed, but the origins are real
#     rail flows into Houston and the model had no way to draw Illinois there.
#   Louisiana: the sample sees New Orleans only (84 of the hub's 322 M bu; Baton Rouge and the rest are withheld): IL 65, IN 10, IA 3, WI 2, NE 2, KY 1. Iowa and Minnesota stay
#     (13% of Iowa's rail corn ends in withheld areas, and the soybean model's states for this hub are IL, IA, WI, MN); Indiana is added; NE and KY are 2% and 1% of what is seen.
CORN_ORIGIN_RULES = {
    "pnw": {"origin_states_mbu": {"ND": 133.0, "MN": 122.0, "SD": 104.0}},
    "txgulf": {"origin_states_mbu": {"IL": 37.0, "NE": 12.0, "IA": 3.0, "IN": 3.0, "MO": 2.0, "WI": 1.0, "MN": 1.0}},
    "lagulf": {"origin_states": ["IL", "IA", "WI", "MN", "IN"]},
}
CORN_ORIGIN_NOTE = {"pnw": "Draw by state held to the STB carload waybill sample (corn to Portland and Seattle, 2022-24): ND 37%, MN 34%, SD 29%.",
                    "txgulf": "Draw by state held to the STB carload waybill sample (corn to Houston, 2022-24): IL 63%, NE 20%, IA 5%, IN 5%, MO 3%, WI 2%, MN 2%.",
                    "lagulf": "Fed from IL, IA, WI, MN and IN (Indiana added from the STB waybill sample, which sees New Orleans only)."}


def apply_origin_rules(groups):
    """Set each rail hub's origin states for corn, replacing the soybean builder's lists."""
    for g in groups:
        rule = CORN_ORIGIN_RULES.get(g["kind"])
        if not rule:
            continue
        g.pop("origin_states", None)
        g.pop("origin_states_mbu", None)
        g.update({k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in rule.items()})
        if CORN_ORIGIN_NOTE[g["kind"]] not in g.get("note", ""):
            g["note"] = (g.get("note", "") + " " + CORN_ORIGIN_NOTE[g["kind"]]).strip()


def fgis_corn_exports():
    """Average annual corn inspections (M bu), marketing years 2021/22-2025/26, shaped like build_river_segments.fgis_soy_exports."""
    q = {"$select": "year,month,fgis_reg,port,type_carrier_text,sum(pounds) as lbs", "$group": "year,month,fgis_reg,port,type_carrier_text", "$limit": "50000",
         "$where": "grain='CORN' and year>=2021 and year<=2026"}
    qm = {"$select": "year,month,state,sum(pounds) as lbs", "$group": "year,month,state", "$limit": "50000",
          "$where": "grain='CORN' and year>=2021 and year<=2026 and type_carrier_text='RAIL' and destination like '%MEXICO%'"}

    def frame(query):
        f = pd.DataFrame(json.load(urllib.request.urlopen(FGIS + urllib.parse.urlencode(query), timeout=180)))
        f["year"], f["month"] = f.year.astype(int), f.month.astype(int)
        f["my"] = f.year.where(f.month >= 9, f.year - 1)
        f["mbu"] = f.lbs.astype(float) / LB / 1e6
        return f[(f.my >= 2021) & (f.my <= 2025)]
    x, m = frame(q), frame(qm)
    reg = x.groupby("fgis_reg").mbu.sum() / 5
    wc = x[x.fgis_reg == "WEST COAST"].groupby("port").mbu.sum() / 5
    ms = (m.groupby("state").mbu.sum() / 5).sort_values(ascending=False)
    ship = x[x.type_carrier_text == "SHIP"]
    return {"east_gulf_mbu": float(reg["EAST GULF"]), "west_coast_columbia_mbu": float(wc.get("COLUMBIA R.", 0.0)), "west_coast_puget_mbu": float(wc.get("PUGET SOUND", 0.0)),
            "west_coast_mbu": float(reg["WEST COAST"]), "west_gulf_mbu": float(reg["WEST GULF"]), "mexico_rail_mbu": float(ms.sum()),
            "mexico_rail_states_mbu": {k: round(float(v), 2) for k, v in ms.items() if v >= 0.05},
            "east_gulf_port_mbu": float(ship[(ship.fgis_reg == "EAST GULF") & (ship.port == "EAST GULF")].mbu.sum() / 5),
            "south_atlantic_mbu": float(ship[(ship.fgis_reg == "EAST COAST") & (ship.port == "S. ATLANTIC")].mbu.sum() / 5),
            "lakes_mbu": float(ship[ship.fgis_reg == "LAKES"].mbu.sum() / 5),
            "source": "FGIS corn inspections, marketing years 2021/22-2025/26 average"}


def main():
    tmp = tempfile.mkdtemp(prefix="corn_nodes_")
    try:
        shutil.copy(os.path.join(D, "corps_lock_farm_tons.csv"), tmp)
        shutil.copy(os.path.join(D, "draw_inputs_corn.json"), os.path.join(tmp, "draw_inputs.json"))
        swap = {"Soybeans": "SoyTmp", "Corn": "Soybeans"}                      # the soybean builder reads the 'Soybeans' rows: give it the corn rows under that name
        for name in ("usda_barge_annual.csv", "wcus_port_grain.csv"):
            df = pd.read_csv(os.path.join(D, name))
            df["commodity"] = df.commodity.replace(swap)
            df.to_csv(os.path.join(tmp, name), index=False)
        cc = os.path.join(D, "canada_corn_by_my.csv")
        if os.path.exists(cc):
            shutil.copy(cc, os.path.join(tmp, "canada_soy_by_my.csv"))         # the file name the soybean code reads: detroit_mbu, buffalo_mbu, pembina_mbu
        R.D = tmp
        R.BU_PER_SHORT_TON_SOY = 2000 / LB
        R.fgis_soy_exports = fgis_corn_exports
        R.container_facts = lambda: {"states_mbu": {}, "atlantic_port_mbu": 0.0, "source": "corn: no container hubs"}
        R.main()
        nodes = json.load(open(os.path.join(tmp, "river_nodes.json")))
        segs = json.load(open(os.path.join(tmp, "river_segments.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    groups = [g for g in nodes["groups"] if g["kind"] not in ("container", "cont_east")]       # corn containers are 30M bu: no hubs for them
    eg_left = max(0.0, nodes["east_gulf_other_mbu"] - next((g["target_mbu"] for g in groups if g["kind"] == "mobile"), 0.0))
    for g in groups:
        if g["kind"] == "lagulf":                                                              # no override for corn: it is what the barges do not explain
            g["target_mbu"] = eg_left
            g["note"] = ("East Gulf corn ship inspections (Mississippi River ports) less the barge loadings above and below Lock 27 and Mobile: corn that reached the "
                         "Louisiana export elevators by rail or truck. A residual, so it carries the barge estimates' errors.")
    apply_origin_rules(groups)                                                                 # corn's own origin states (the soybean builder's lists are not used)
    nodes["groups"] = groups
    nodes["crop"] = "corn"
    with open(os.path.join(D, "river_nodes_corn.json"), "w") as fh:
        json.dump(nodes, fh, indent=1)
    with open(os.path.join(D, "river_segments_corn.json"), "w") as fh:
        json.dump(segs, fh, indent=1)
    for g in groups:
        print(f"{g['kind']:9} {g['hub'][:28]:28} {g['name'][:56]:56} {g['target_mbu']:8.1f}  {len(g['pins'])} pins")
    print({k: round(v, 1) if isinstance(v, float) else v for k, v in nodes["fgis"].items() if isinstance(v, float)})


if __name__ == "__main__":
    main()

"""
Builds data/draw/river_nodes_wheat.json, the export hubs and river terminal groups of the wheat Draw Territory model, with the corn model's method (build_corn_nodes.py):
  * upriver pools: wheat on the barges at each lock (USDA AgTransport downbound barge data at the measured locks, the Corps' lock tonnage between them), differenced into pools
  * below Lock 27, the Lower Mississippi: Corps waterborne commerce port and waterway shipments of wheat (WCUS)
  * Pacific Northwest, Texas Gulf, South Atlantic, Lakes, Mobile: FGIS wheat inspections at the ports (marketing years June-May, 2021/22-2025/26 average); Mexico by rail: FGIS
    rail inspections for Mexico, the draw by state held to the states the lots were inspected in; Louisiana rail and truck: what is left of the East Gulf's inspections after the
    barge loadings
  * no container or Canada hubs: wheat in containers is a few M bu and Canada takes about 4 M bu
Wheat's class structure (which class leaves through which hub) is stored beside the groups as `class_hub_mbu` and used by the tab's class selector; the by-year table is
data/wheat/wheat_export_class_hub_by_my.csv.

    python build_wheat_nodes.py
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
LB = 60.0
MY0, MY1 = 2021, 2025
# Which states feed a rail hub. STB carload waybill sample, wheat 2022-24 (build_rail_waybill.py wheat; M bu a year, origin areas split into states by county supply):
#   Pacific Northwest (Portland, Seattle, Richland): 229 M bu by rail: MT 83, ND 66, WA 26, ID 12, SD 11, MN 7 (KS, CO, NE 8, unknown 13). FGIS West Coast inspections are ~390 M bu,
#     so ~160 M bu arrives by barge down the Columbia-Snake and by truck, which no waybill sees. ASSUMPTION: that remainder comes from WA 80, ID 48, OR 33.
#   Texas Gulf (Houston, Corpus Christi, Beaumont): 77 M bu by rail against FGIS's 114: KS 24, ND 11, OK 4, CO 4, MN 3, NE 3, SD 2, unknown 16. Rail plus the Texas and Oklahoma trucks:
#     the states are limited to the plains belt rather than held, so distance places Texas and Oklahoma wheat.
#   Mexico by rail: FGIS rail inspections by state (held, as for corn and soybeans).
#   Louisiana, Mobile, South Atlantic, Great Lakes: the waybill sees almost nothing (withheld areas), so the states are limited, not held. Louisiana rail and truck carries
#     the soft red wheat that comes down the river system and hard red from Kansas.
WHEAT_ORIGIN_RULES = {
    "pnw": {"origin_states_mbu": {"MT": 83.0, "ND": 66.0, "WA": 106.0, "ID": 60.0, "OR": 33.0, "SD": 11.0, "MN": 7.0}},
    "txgulf": {"origin_states": ["KS", "OK", "TX", "NE", "CO", "ND", "SD", "MN", "MO", "NM"]},
    "lagulf": {"origin_states": ["IL", "IN", "OH", "MO", "KS", "IA", "NE", "WI", "MN", "KY"]},
    "mobile": {"origin_states": ["IL", "IN", "OH", "KY", "TN", "MO", "KS"]},
    "lakes": {"origin_states": ["ND", "MN", "SD", "MT", "OH", "MI", "IN", "IL"]},
    "satl": {"origin_states": ["OH", "IN", "MI", "IL", "KY", "TN", "VA", "NC", "SC", "MD", "DE", "PA", "NJ", "WV", "GA"]},
}
WHEAT_ORIGIN_NOTE = {"pnw": "Draw by state held to the STB waybill sample's rail origins (MT 83, ND 66, WA 26, ID 12, SD 11, MN 7 M bu a year) plus an assumed 160 M bu of barge and truck "
                            "wheat from WA, ID and OR (the waybill sees only rail: about 229 of the hub's 390 M bu).",
                     "txgulf": "Fed from the plains belt (KS, OK, TX, NE, CO, ND, SD, MN, MO, NM); the waybill shows KS 24, ND 11, OK 4, CO 4 M bu by rail to Houston.",
                     "lagulf": "Fed from IL, IN, OH, MO, KS, IA, NE, WI, MN and KY (soft red down the river system, hard red from Kansas by rail).",
                     "lakes": "Duluth-Superior takes ND and MN durum and spring wheat; Toledo and Chicago take eastern wheat.",
                     "mobile": "East Gulf ports other than the Mississippi; small for wheat.", "satl": "Norfolk, Charleston and Savannah; small for wheat."}


def apply_origin_rules(groups):
    for g in groups:
        rule = WHEAT_ORIGIN_RULES.get(g["kind"])
        if not rule:
            continue
        g.pop("origin_states", None)
        g.pop("origin_states_mbu", None)
        g.update({k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in rule.items()})
        note = WHEAT_ORIGIN_NOTE.get(g["kind"], "")
        if note and note not in g.get("note", ""):
            g["note"] = (g.get("note", "") + " " + note).strip()


def fgis_wheat_exports():
    """Average annual wheat inspections (M bu), marketing years June 2021 - May 2026, shaped like build_river_segments.fgis_soy_exports."""
    q = {"$select": "year,month,fgis_reg,port,type_carrier_text,sum(pounds) as lbs", "$group": "year,month,fgis_reg,port,type_carrier_text", "$limit": "50000",
         "$where": "grain='WHEAT' and year>=2021 and year<=2026"}
    qm = {"$select": "year,month,state,sum(pounds) as lbs", "$group": "year,month,state", "$limit": "50000",
          "$where": "grain='WHEAT' and year>=2021 and year<=2026 and type_carrier_text='RAIL' and destination like '%MEXICO%'"}

    def frame(query):
        f = pd.DataFrame(json.load(urllib.request.urlopen(FGIS + urllib.parse.urlencode(query), timeout=180)))
        f["year"], f["month"] = f.year.astype(int), f.month.astype(int)
        f["my"] = f.year.where(f.month >= 6, f.year - 1)                  # the wheat marketing year starts June 1
        f["mbu"] = f.lbs.astype(float) / LB / 1e6
        return f[(f.my >= MY0) & (f.my <= MY1)]
    x, m = frame(q), frame(qm)
    n = MY1 - MY0 + 1
    reg = x.groupby("fgis_reg").mbu.sum() / n
    wc = x[x.fgis_reg == "WEST COAST"].groupby("port").mbu.sum() / n
    ms = (m.groupby("state").mbu.sum() / n).sort_values(ascending=False)
    ship = x[x.type_carrier_text == "SHIP"]
    return {"east_gulf_mbu": float(reg["EAST GULF"]), "west_coast_columbia_mbu": float(wc.get("COLUMBIA R.", 0.0)), "west_coast_puget_mbu": float(wc.get("PUGET SOUND", 0.0)),
            "west_coast_mbu": float(reg["WEST COAST"]), "west_gulf_mbu": float(reg["WEST GULF"]), "mexico_rail_mbu": float(ms.sum()),
            "mexico_rail_states_mbu": {k: round(float(v), 2) for k, v in ms.items() if v >= 0.05},
            "east_gulf_port_mbu": float(ship[(ship.fgis_reg == "EAST GULF") & (ship.port == "EAST GULF")].mbu.sum() / n),
            "south_atlantic_mbu": float(ship[(ship.fgis_reg == "EAST COAST") & (ship.port == "S. ATLANTIC")].mbu.sum() / n),
            "lakes_mbu": float(ship[ship.fgis_reg == "LAKES"].mbu.sum() / n),
            "source": "FGIS wheat inspections, marketing years 2021/22-2025/26 average (June-May)"}


def class_hub_mbu():
    """{model class: {hub kind: M bu a year}} from FGIS inspections by class and hub (build_wheat_export_shares.py), marketing years 2021/22-2025/26 average."""
    d = pd.read_csv(os.path.join(HERE, "data", "wheat", "wheat_export_class_hub_by_my.csv"))
    d = d[d.marketing_year_start.between(MY0, MY1)]
    cls = {"HRW": "HRW", "SRW": "SRW", "HRS": "HRS", "SWW": "White", "HDWH": "White", "XWHT": "White", "DUWH": "Durum"}
    kind = {"Pacific Northwest": "pnw", "Texas Gulf": "txgulf", "Mexico rail": "mexico", "Mississippi River": "river", "Great Lakes": "lakes", "South Atlantic": "satl"}
    d = d[d["class"].isin(cls) & d.hub.isin(kind)]
    g = d.assign(c=d["class"].map(cls), k=d.hub.map(kind)).groupby(["c", "k"]).mbu.sum() / (MY1 - MY0 + 1)
    out = {}
    for (c, k), v in g.items():
        out.setdefault(c, {})[k] = round(float(v), 2)
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="wheat_nodes_")
    try:
        shutil.copy(os.path.join(D, "corps_lock_farm_tons.csv"), tmp)
        shutil.copy(os.path.join(D, "draw_inputs_wheat.json"), os.path.join(tmp, "draw_inputs.json"))
        swap = {"Soybeans": "SoyTmp", "Wheat": "Soybeans"}                     # the soybean builder reads the 'Soybeans' rows: give it the wheat rows under that name
        for name in ("usda_barge_annual.csv", "wcus_port_grain.csv"):
            df = pd.read_csv(os.path.join(D, name))
            df["commodity"] = df.commodity.replace(swap)
            df.to_csv(os.path.join(tmp, name), index=False)
        R.D = tmp
        R.BU_PER_SHORT_TON_SOY = 2000 / LB
        R.fgis_soy_exports = fgis_wheat_exports
        R.container_facts = lambda: {"states_mbu": {}, "atlantic_port_mbu": 0.0, "source": "wheat: no container hubs"}
        R.main()
        nodes = json.load(open(os.path.join(tmp, "river_nodes.json")))
        segs = json.load(open(os.path.join(tmp, "river_segments.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    groups = [g for g in nodes["groups"] if g["kind"] not in ("container", "cont_east", "canada")]
    eg_left = max(0.0, nodes["east_gulf_other_mbu"] - next((g["target_mbu"] for g in groups if g["kind"] == "mobile"), 0.0))
    for g in groups:
        if g["kind"] == "lagulf":                                              # no override for wheat: it is what the barges do not explain
            g["target_mbu"] = eg_left
            g["note"] = ("East Gulf wheat ship inspections (Mississippi River ports) less the barge loadings above and below Lock 27 and Mobile: wheat that reached the "
                         "Louisiana export elevators by rail or truck. A residual, so it carries the barge estimates' errors.")
    apply_origin_rules(groups)
    nodes["groups"] = groups
    nodes["crop"] = "wheat"
    nodes["class_hub_mbu"] = class_hub_mbu()
    with open(os.path.join(D, "river_nodes_wheat.json"), "w") as fh:
        json.dump(nodes, fh, indent=1)
    with open(os.path.join(D, "river_segments_wheat.json"), "w") as fh:
        json.dump(segs, fh, indent=1)
    for g in groups:
        print(f"{g['kind']:9} {g['hub'][:28]:28} {g['name'][:56]:56} {g['target_mbu']:8.1f}  {len(g['pins'])} pins")
    print({k: round(v, 1) if isinstance(v, float) else v for k, v in nodes["fgis"].items() if isinstance(v, float)})
    print(json.dumps(nodes["class_hub_mbu"]))


if __name__ == "__main__":
    main()

"""
Port and waterway grain tonnage from the Corps' Waterborne Commerce of the United States (WCUS) Ports and Waterways web tool,
the same static JSON files the tool at https://ndc.ops.usace.army.mil/wcsc/webpub/ reads. Public government data; about 30 small requests.

Each location has a five-year cargo report per publication year: tons by commodity, split into shipments (loaded out of the port area),
receipts, intra-port moves, through traffic and the total. The 2024 and 2023 reports together give 2019-2024.

    python pull_wcus_ports.py     # writes data/draw/wcus_port_grain.csv
"""
import csv
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://ndc.ops.usace.army.mil/wcsc/webpub/data/"
GRAINS = {6241: "Wheat", 6344: "Corn", 6522: "Soybeans", 6447: "Sorghum", 6653: "Vegetable Oils", 6747: "Grain Mill Products"}
LOCATIONS = [   # id, name, type
    (2367, "St. Louis Metro Port, IL and MO", "PORT"), (2341, "St. Louis City Port, MO", "PORT"), (2342, "St. Louis County, MO", "PORT"),
    (2307, "Kaskaskia Regional Port, IL", "PORT"), (2308, "Alexandria-Cairo Port, IL", "PORT"), (2312, "Heart of Illinois Port, IL", "PORT"),
    (2313, "Illinois Valley Port, IL", "PORT"), (7713, "Illinois Waterway Ports, IL", "PORT"), (2350, "E Iowa and W Illinois, IA IL", "PORT"),
    (2309, "America's Central Port, IL", "PORT"), (2319, "Southwest Regional Port, IL", "PORT"), (2344, "Jefferson County Port, MO", "PORT"),
    (2368, "Southeast Missouri Port, MO", "PORT"), (2351, "New Bourbon Port Authority, MO", "PORT"),
    (2294, "Memphis-Shelby County Port, TN", "PORT"), (2304, "Hickman-Fulton County Port, KY", "PORT"), (2288, "New Madrid County Port, MO", "PORT"),
    (6080, "Mississippi River, Minneapolis to Mouth of Missouri River", "WATERWAY"),
    (6079, "Mississippi River, Mouth of Missouri River to Mouth of Ohio River", "WATERWAY"),
    (6034, "Mississippi River, Mouth of Ohio River to Baton Rouge", "WATERWAY"),
    (7701, "Illinois River, IL", "WATERWAY"), (2078, "Ohio River", "WATERWAY"),
]


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except Exception:
        return None


def main():
    rows = []
    for lid, name, kind in LOCATIONS:
        seen = {}
        for pub in (2024, 2023):                       # each report holds `pub` and the four years before it
            rep = get(f"year/{pub}/region/2/location/{lid}/report/fiveYearCargoReport.json")
            time.sleep(0.3)
            if not rep:
                continue
            for r in rep["ALL_COMMODITIES"]:
                if r["commodityId"] not in GRAINS:
                    continue
                for d in range(5):
                    year = pub - d
                    if (r["commodityId"], year) in seen:
                        continue
                    seen[(r["commodityId"], year)] = 1
                    rows.append({"location_id": lid, "location": name, "type": kind, "commodity": GRAINS[r["commodityId"]], "year": year,
                                 "total_tons": r[f"allDelta{d}"], "shipments_tons": r[f"shipmentsDelta{d}"], "receipts_tons": r[f"receiptsDelta{d}"],
                                 "intra_tons": r[f"intraDelta{d}"], "thru_tons": r[f"thruDelta{d}"]})
    out = os.path.join(HERE, "data", "draw", "wcus_port_grain.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(len(rows), "rows ->", out)


if __name__ == "__main__":
    main()

"""
Distance from every Draw Territory county to the nearest BNSF, UP and CPKC track, from data/rail_map/rail_lines.json (the Rail Map tab's NTAD
lines, each path a short segment). The Pacific Northwest hub is fed almost entirely by BNSF and UP shuttle and unit trains, so the model lets a
county send beans there only if one of those two railroads is within reach; CPKC is kept in the file for reference.

Writes data/draw/county_rail_service.json  {"built": ..., "counties": {fips: [miles to BNSF, UP, CPKC, CN, CSXT, NS]}}
    python build_county_rail_service.py
"""
import datetime
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
CODES = ("BNSF", "UP", "CPKC", "CN", "CSXT", "NS")      # the first three stay where the model expects them (RAIL_KINDS indexes into this order)


def seg_dist(clat, clon, segs):
    """Miles from each county centroid to its nearest segment (flat-earth, fine at county scale). segs = (n, 4) lon1, lat1, lon2, lat2."""
    out = np.full(len(clat), np.inf)
    mi_lat = 69.0
    for i in range(0, len(clat), 60):
        la, lo = clat[i:i + 60, None], clon[i:i + 60, None]
        cosl = np.cos(np.radians(la))
        x1, y1 = (segs[None, :, 0] - lo) * cosl * mi_lat, (segs[None, :, 1] - la) * mi_lat
        x2, y2 = (segs[None, :, 2] - lo) * cosl * mi_lat, (segs[None, :, 3] - la) * mi_lat
        dx, dy = x2 - x1, y2 - y1
        L2 = np.maximum(dx * dx + dy * dy, 1e-12)
        t = np.clip(-(x1 * dx + y1 * dy) / L2, 0.0, 1.0)
        px, py = x1 + t * dx, y1 + t * dy
        out[i:i + 60] = np.sqrt(px * px + py * py).min(axis=1)
    return out


def main():
    lines = json.load(open(os.path.join(D, "rail_map", "rail_lines.json")))
    cty = json.load(open(os.path.join(D, "draw", "draw_inputs.json")))["counties"]
    clat = np.array([c["lat"] for c in cty])
    clon = np.array([c["lon"] for c in cty])
    cols = []
    for code in CODES:
        segs = []
        for path in lines[code]:
            for a, b in zip(path[:-1], path[1:]):
                segs.append((a[0], a[1], b[0], b[1]))
        cols.append(seg_dist(clat, clon, np.array(segs, float)))
        print(code, len(segs), "segments")
    out = {c["fips"]: [round(float(cols[k][i]), 1) for k in range(len(CODES))] for i, c in enumerate(cty)}
    with open(os.path.join(D, "draw", "county_rail_service.json"), "w") as fh:
        json.dump({"built": datetime.date.today().isoformat(), "columns": list(CODES), "counties": out}, fh, separators=(",", ":"))
    near = np.minimum(cols[0], cols[1])
    for r in (10, 25, 50):
        print(f"counties within {r} mi of BNSF or UP: {(near <= r).sum()} of {len(cty)}")


if __name__ == "__main__":
    main()

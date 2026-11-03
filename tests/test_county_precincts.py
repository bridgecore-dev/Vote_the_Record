"""Core logic of the Harris precinct/ZIP collector (no network)."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "collectors" / "states" / "tx"))
import county_precincts as h  # noqa: E402


def square(x0, y0, size, value):
    ring = [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]
    return {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"v": value}}


def test_rasterize_fills_cell_centers_inside_the_polygon_only():
    rows = cols = 10
    grid = [[0] * cols for _ in range(rows)]
    h.rasterize([square(0.002, 0.003, 0.004, 7)], lambda f: f["properties"]["v"], grid, 0.0, 0.0, cols, rows)
    filled = {(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == 7}
    assert filled == {(r, c) for r in range(3, 7) for c in range(2, 6)}


def test_rasterize_leaves_holes_empty():
    outer = [[0, 0], [0.006, 0], [0.006, 0.006], [0, 0.006], [0, 0]]
    hole = [[0.002, 0.002], [0.004, 0.002], [0.004, 0.004], [0.002, 0.004], [0.002, 0.002]]
    feature = {"geometry": {"type": "Polygon", "coordinates": [outer, hole]}, "properties": {}}
    grid = [[0] * 6 for _ in range(6)]
    h.rasterize([feature], lambda f: 1, grid, 0.0, 0.0, 6, 6)
    assert grid[3][3] == 0 and grid[0][0] == 1 and grid[5][5] == 1


def test_areas_for_keeps_only_districts_with_a_contest_on_the_ballot():
    attrs = {"USCong_C2333": "07", "St_Rep_Dis": 134, "St_Sen_Dis": 17, "SBOE_E2106": "04",
             "Comm__Cour": 1, "JP_Constab": 1}
    on_ballot = {"us-house-7", "state-house-134", "state-senate-13", "sboe-4",
                 "harris-county/commissioner-2", "harris-county/jp-1"}
    assert h.areas_for(attrs, on_ballot) == {"us-house": "us-house-7", "state-house": "state-house-134",
                                             "sboe": "sboe-4", "jp": "harris-county/jp-1"}


def test_a_split_precinct_partly_in_a_district_with_no_contest_depends_on_the_address():
    on_ballot = {"brazos-county/commissioner-2", "brazos-county/commissioner-4", "brazos-county/jp-1",
                 "brazos-county/jp-3"}
    # part in Precinct 4 (on the ballot), part in Precinct 1 (no contest): not every voter there has the Pct 4 race
    assert h.split_areas("commissioner", [1, 4], on_ballot, "brazos-county") == ["brazos-county/commissioner-4"]
    assert h.split_areas("jp", [1, 3], on_ballot, "brazos-county") == ["brazos-county/jp-1", "brazos-county/jp-3"]
    assert h.split_areas("commissioner", [4], on_ballot, "brazos-county") == "brazos-county/commissioner-4"
    assert h.split_areas("commissioner", [1, 3], on_ballot, "brazos-county") is None


TLC_PRJ = ('PROJCS["NAD_1983_Lambert_Conformal_Conic",GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
           'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
           'PROJECTION["Lambert_Conformal_Conic"],PARAMETER["False_Easting",1000000.0],PARAMETER["False_Northing",1000000.0],'
           'PARAMETER["Central_Meridian",-100.0],PARAMETER["Standard_Parallel_1",27.41666666666667],'
           'PARAMETER["Standard_Parallel_2",34.91666666666666],PARAMETER["Latitude_Of_Origin",31.16666666666667],'
           'UNIT["Meter",1.0]]')


def test_lcc_inverse_maps_the_false_origin_to_the_projection_center():
    inverse = h.lcc_inverse(TLC_PRJ)
    lon, lat = inverse(1000000.0, 1000000.0)
    assert abs(lon + 100.0) < 1e-9 and abs(lat - 31.16666666666667) < 1e-9


def test_lcc_inverse_moves_east_and_north_with_x_and_y():
    inverse = h.lcc_inverse(TLC_PRJ)
    lon, lat = inverse(1100000.0, 1100000.0)  # 100 km east and north of the center
    assert -99.0 < lon < -98.9 and 32.06 < lat < 32.08


def test_overlay_and_fine_assign_pick_the_district_covering_most_of_a_precinct():
    rows = cols = 10
    pgrid = [[0] * cols for _ in range(rows)]
    precinct = square(0.0, 0.0, 0.010, 5)
    h.rasterize([precinct], lambda f: 5, pgrid, 0.0, 0.0, cols, rows)
    def rect(xa, xb, value):
        ring = [[xa, -0.01], [xb, -0.01], [xb, 0.02], [xa, 0.02], [xa, -0.01]]
        return {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"v": value}}
    west, east = rect(-0.01, 0.008, "A"), rect(0.008, 0.02, "B")  # A covers 80% of the precinct, B 20%
    got = h.overlay(pgrid, [west, east], "v", 0.0, 0.0, cols, rows)
    assert got[5][0] == "A" and abs(got[5][1] - 0.8) < 0.01
    d, share, n = h.fine_assign(precinct, [west, east], "v")
    assert d == "A" and abs(share - 0.8) < 0.02 and n > 100

"""Build a Texas county's precincts.yaml and zips.yaml (and lookup outlines) for one election's ballot.

Usage:
  python src/collectors/states/tx/county_precincts.py --county harris-county --cache <folder> \
      --scotus-sha256 <hash> --ballot-dir data/states/tx/localities/harris-county/elections/2026-11-03 --out <folder>

  Counties are configured in COUNTIES below (precinct layer, district fields, local areas).

  --ballot-dir is only read. Write to a review folder first, then copy the two files into the
  ballot folder (a data/ change, which needs the owner's approval).

Sources (all official, all public):
  - The county's voting precinct layer, with each precinct's commissioner, congressional (plan
    C2333), State House, State Senate, SBOE, and JP/constable districts. Harris: "VPCTs_2026"
    (1,224 precincts). Travis: "Election Precincts" (308 precincts, approved December 4, 2025).
  - U.S. Census Bureau TIGERweb, 2020 ZIP Code Tabulation Areas (ZCTAs), which approximate
    ZIP codes.

precincts.yaml is exact: each precinct's districts come straight from the county layer.
zips.yaml is approximate: both layers are rasterized on a ~110 m grid, and a precinct counts
for a ZIP when it covers at least MIN_SHARE of the ZIP's grid cells inside the county.
When a ZIP's precincts fall in different districts of the same kind, those districts are
written as a nested list ("depends on your address").

Only districts with a contest on the ballot are written (e.g. State Senate districts not up
this year are left out). Everything is written unverified; the owner verifies the sources.
The script refuses to overwrite existing files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

COUNTY_ITEM = "https://harriscounty.maps.arcgis.com/home/item.html?id=d5a840ea0bf9442e946be10c6d43e658"
COUNTY_LAYER = "https://services.arcgis.com/su8ic9KbA7PYVxPS/arcgis/rest/services/VPCTs_2026/FeatureServer/2"
COUNTY_FIELDS = "VPCT,Comm__Cour,USCong_C2333,St_Rep_Dis,St_Sen_Dis,SBOE_E2106,JP_Constab"
ZCTA_LAYER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2"
SCOTUS_ORDER = "https://www.supremecourt.gov/opinions/25pdf/25a608_7khn.pdf"
TIGER_SERVICE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer"
PLACE_LAYER = TIGER_SERVICE + "/28"   # Incorporated Places (cities)
SCHOOL_LAYER = TIGER_SERVICE + "/14"  # Unified School Districts
TCEQ_SERVICE = "https://gisweb.tceq.texas.gov/arcgis/rest/services/iwud/WaterDistricts_PRD/MapServer"
TCEQ_FWSD = TCEQ_SERVICE + "/2"   # Fresh Water Supply Districts
TCEQ_MMD = TCEQ_SERVICE + "/5"    # Municipal Management Districts (incl. improvement districts)
TCEQ_MUD = TCEQ_SERVICE + "/6"    # Municipal Utility Districts (TCEQ also files some WCIDs and PUDs here)
TCEQ_WCID = TCEQ_SERVICE + "/13"  # Water Control & Improvement Districts

HARRIS_LOCAL_AREAS = {
    "city-of-houston": (PLACE_LAYER, "4835000"),
    "city-of-baytown": (PLACE_LAYER, "4806128"),
    "city-of-league-city": (PLACE_LAYER, "4841980"),
    "channelview-isd": (SCHOOL_LAYER, "4813590"),
    "crosby-isd": (SCHOOL_LAYER, "4815750"),
    "cypress-fairbanks-isd": (SCHOOL_LAYER, "4816110"),
    "huffman-isd": (SCHOOL_LAYER, "4823820"),
    "klein-isd": (SCHOOL_LAYER, "4825740"),
    "la-porte-isd": (SCHOOL_LAYER, "4826190"),
    "new-caney-isd": (SCHOOL_LAYER, "4832400"),
    "sheldon-isd": (SCHOOL_LAYER, "4839990"),
    "spring-isd": (SCHOOL_LAYER, "4841220"),
    "tomball-isd": (SCHOOL_LAYER, "4842960"),
    # TCEQ water districts, matched by DISTRICT_ID.
    "harris-county-fwsd-1a": (TCEQ_FWSD, "3639000"),
    "harris-county-mud-50": (TCEQ_MUD, "3737148"),
    "harris-county-mud-127": (TCEQ_MUD, "3737312"),
    "harris-county-mud-130": (TCEQ_MUD, "3737318"),
    "harris-county-mud-189": (TCEQ_MUD, "3737446"),
    "harris-county-mud-553": (TCEQ_MUD, "9000004"),
    "harris-county-wcid-89": (TCEQ_MUD, "4190000"),
    "intercontinental-crossing-mud": (TCEQ_MUD, "4711250"),
    "spanish-cove-pud": (TCEQ_MUD, "7587000"),
    "west-harris-county-mud-15": (TCEQ_MUD, "8472972"),
    "weston-mud": (TCEQ_MUD, "8474000"),
    "old-town-spring-improvement-district": (TCEQ_MMD, "6205100"),
}
# Local areas: area slug -> (Census layer, GEOID). Only areas with a contest on the ballot are used.
TRAVIS_ITEM = "https://www.arcgis.com/home/item.html?id=e1c2deb0b8394b25ba42ceaba057a8eb"
TRAVIS_LAYER = "https://services1.arcgis.com/HGcSYZ5bvjRswoCb/arcgis/rest/services/Election_Precincts_2025/FeatureServer/0"
TRAVIS_LOCAL_AREAS = {
    "city-of-austin": (PLACE_LAYER, "4805000"),
    "city-of-elgin": (PLACE_LAYER, "4823044"),
    "city-of-jonestown": (PLACE_LAYER, "4838020"),
    "city-of-lago-vista": (PLACE_LAYER, "4840264"),
    "city-of-manor": (PLACE_LAYER, "4846440"),
    "city-of-pflugerville": (PLACE_LAYER, "4857176"),
    "city-of-sunset-valley": (PLACE_LAYER, "4871324"),
    "village-of-volente": (PLACE_LAYER, "4875752"),
    "austin-isd": (SCHOOL_LAYER, "4808940"),
    "coupland-isd": (SCHOOL_LAYER, "4815420"),
    "del-valle-isd": (SCHOOL_LAYER, "4816620"),
    "leander-isd": (SCHOOL_LAYER, "4827030"),
    "manor-isd": (SCHOOL_LAYER, "4828890"),
    "pflugerville-isd": (SCHOOL_LAYER, "4834830"),
    "round-rock-isd": (SCHOOL_LAYER, "4838080"),
    "cypress-ranch-wcid-1": (TCEQ_WCID, "2413100"),
    "hero-way-west-mud": (TCEQ_MUD, "4429750"),
    "kennedy-hill-mud": (TCEQ_MUD, "0630202"),
    "north-austin-mud-1": (TCEQ_MUD, "5968600"),
    "northtown-mud": (TCEQ_MUD, "6013100"),
    "sunfield-mud-2": (TCEQ_MUD, "7632800"),
    "travis-county-wcid-17": (TCEQ_WCID, "7963000"),
    "vista-mud": (TCEQ_MUD, "8325625"),
    "williamson-travis-mud-1": (TCEQ_MUD, "8670000"),
}
DALLAS_ITEM = "https://www.arcgis.com/home/item.html?id=17fef6a343574a7bb2fe6902e1a09ebf"
DALLAS_LAYER = "https://services3.arcgis.com/zqe2kwz79KUqUvxC/arcgis/rest/services/Election_Precincts_2025/FeatureServer/0"
DALLAS_LOCAL_AREAS = {
    "city-of-dallas": (PLACE_LAYER, "4819000"),
    "city-of-garland": (PLACE_LAYER, "4829000"),
    "city-of-university-park": (PLACE_LAYER, "4874492"),
    "city-of-wylie": (PLACE_LAYER, "4880356"),
    "irving-isd": (SCHOOL_LAYER, "4824420"),
    "mesquite-isd": (SCHOOL_LAYER, "4830390"),
}

BEXAR_ITEM = "https://gis-bexar.opendata.arcgis.com/datasets/bexar-county-voter-precincts"
BEXAR_LAYER = "https://maps.bexar.org/arcgis/rest/services/EL/VoterPrecincts/MapServer/0"
BEXAR_COMMISSIONER = "https://maps.bexar.org/arcgis/rest/services/CommissionerPrecincts/MapServer/0"
BEXAR_JP = "https://maps.bexar.org/arcgis/rest/services/JusticeofthePeace/MapServer/1"
# Texas Legislative Council plan shapefiles (Capitol Data Portal), used where a county's precinct layer has no
# district fields: the 2026 congressional plan and the current State House and State Senate plans.
TLC_C2333 = ("https://data.capitol.texas.gov/dataset/748c952b-e926-4f44-8d01-a738884b3ec8/resource/"
             "5712ebe1-d777-4d4a-b836-0534e17bca01/download/planc2333.zip")
TLC_H2316 = ("https://data.capitol.texas.gov/dataset/71af633c-21bf-42cf-ad48-4fe95593a897/resource/"
             "a4d3230f-47f2-4253-85f3-51a6c3c9ad0a/download/planh2316.zip")
TLC_S2168 = ("https://data.capitol.texas.gov/dataset/70836384-f10c-423d-a36e-748d7e000872/resource/"
             "8247dbc6-b942-4a29-813c-1ebc603a7236/download/plans2168.zip")
BEXAR_LOCAL_AREAS = {
    "city-of-china-grove": (PLACE_LAYER, "4814716"),
    "city-of-converse": (PLACE_LAYER, "4816468"),
    "town-of-hollywood-park": (PLACE_LAYER, "4834628"),
    "city-of-live-oak": (PLACE_LAYER, "4843096"),
    "city-of-san-antonio": (PLACE_LAYER, "4865000"),
    "city-of-sandy-oaks": (PLACE_LAYER, "4865344"),
    "city-of-schertz": (PLACE_LAYER, "4866128"),
    "city-of-windcrest": (PLACE_LAYER, "4879672"),
    "comal-isd": (SCHOOL_LAYER, "4814730"),
    "east-central-isd": (SCHOOL_LAYER, "4817850"),
    "edgewood-isd": (SCHOOL_LAYER, "4818150"),
    "northside-isd": (SCHOOL_LAYER, "4833120"),
    "san-antonio-isd": (SCHOOL_LAYER, "4838730"),
    "schertz-cibolo-universal-city-isd": (SCHOOL_LAYER, "4839480"),
    "somerset-isd": (SCHOOL_LAYER, "4840740"),
    "south-san-antonio-isd": (SCHOOL_LAYER, "4840680"),
    "bexar-county-mud-1": (TCEQ_MUD, "1274500"),
}
COLLIN_ITEM = "https://www.arcgis.com/home/item.html?id=26cdf0cddc2849e1af691ac234f3340a"
COLLIN_LAYER = "https://services1.arcgis.com/fdWXd5OobWR1E3er/arcgis/rest/services/Voting_Precincts/FeatureServer/0"
COLLIN_LOCAL_AREAS = {
    "city-of-anna": (PLACE_LAYER, "4803300"),
    "city-of-dallas": (PLACE_LAYER, "4819000"),
    "city-of-garland": (PLACE_LAYER, "4829000"),
    "city-of-josephine": (PLACE_LAYER, "4838068"),
    "city-of-lavon": (PLACE_LAYER, "4841800"),
    "city-of-lowry-crossing": (PLACE_LAYER, "4844308"),
    "city-of-plano": (PLACE_LAYER, "4858016"),
    "city-of-princeton": (PLACE_LAYER, "4859576"),
    "city-of-weston": (PLACE_LAYER, "4877740"),
    "city-of-wylie": (PLACE_LAYER, "4880356"),
    "bland-isd": (SCHOOL_LAYER, "4810350"),
    "mckinney-isd": (SCHOOL_LAYER, "4829850"),
    "princeton-isd": (SCHOOL_LAYER, "4835850"),
    "wylie-isd": (SCHOOL_LAYER, "4846530"),
    "blue-meadow-mud-2": (TCEQ_MUD, "1546750"),
    "blue-meadow-mud-4": (TCEQ_MUD, "1546938"),
}
DENTON_SERVICE = "https://gis.dentoncounty.gov/arcgis/rest/services/PoliticalBoundaries_GC/MapServer"
DENTON_ITEM = DENTON_SERVICE + "/9"
DENTON_LAYER = DENTON_SERVICE + "/9"  # Voter Precincts (Effective 1/1/2026)
DENTON_LOCAL_AREAS = {
    "town-of-bartonville": (PLACE_LAYER, "4805768"),
    "city-of-dallas": (PLACE_LAYER, "4819000"),
    "city-of-denton": (PLACE_LAYER, "4819972"),
    "town-of-double-oak": (PLACE_LAYER, "4821028"),
    "town-of-flower-mound": (PLACE_LAYER, "4826232"),
    "town-of-hickory-creek": (PLACE_LAYER, "4833476"),
    "city-of-justin": (PLACE_LAYER, "4838332"),
    "city-of-lake-dallas": (PLACE_LAYER, "4840516"),
    "city-of-plano": (PLACE_LAYER, "4858016"),
    "city-of-the-colony": (PLACE_LAYER, "4872530"),
    "brookfield-wcid": (TCEQ_WCID, "7579300"),
    "denton-county-mud-7": (TCEQ_MUD, "2722080"),
    "northlake-fwsd-1": (TCEQ_FWSD, "5987875"),
    "sanctuary-mud-2": (TCEQ_MUD, "7524625"),
}
WILCO_ELECTIONS = "https://gis.wilco.org/arcgis/rest/services/public/county_elections/MapServer"
WILCO_ITEM = WILCO_ELECTIONS + "/2"
WILCO_LAYER = WILCO_ELECTIONS + "/2"  # Current Voter Precincts
WILCO_ADMIN = "https://gis.wilco.org/arcgis/rest/services/public/county_administrative_boundaries/MapServer"
WILCO_LOCAL_AREAS = {
    "city-of-austin": (PLACE_LAYER, "4805000"),
    "city-of-georgetown": (PLACE_LAYER, "4829336"),
    "city-of-hutto": (PLACE_LAYER, "4835624"),
    "city-of-jarrell": (PLACE_LAYER, "4837396"),
    "city-of-pflugerville": (PLACE_LAYER, "4857176"),
    "city-of-taylor": (PLACE_LAYER, "4871948"),
    "bartlett-isd": (SCHOOL_LAYER, "4809540"),
    "coupland-isd": (SCHOOL_LAYER, "4815420"),
    "florence-isd": (SCHOOL_LAYER, "4819320"),
    "georgetown-isd": (SCHOOL_LAYER, "4820600"),
    "leander-isd": (SCHOOL_LAYER, "4827030"),
    "pflugerville-isd": (SCHOOL_LAYER, "4834830"),
    "round-rock-isd": (SCHOOL_LAYER, "4838080"),
    "block-house-mud": (TCEQ_MUD, "1546000"),
    "highlands-at-mayfield-ranch-mud": (TCEQ_MUD, "4636250"),
    "north-austin-mud-1": (TCEQ_MUD, "5968600"),
    "solana-ranch-mud-1": (TCEQ_MUD, "7576132"),
    "theon-ranches-mud-1": (TCEQ_MUD, "8259495"),
    "williamson-travis-mud-1": (TCEQ_MUD, "8670000"),
}
TARRANT_LAYER = "https://mapit.tarrantcounty.com/arcgis/rest/services/Dynamic/VotingPrecinct/MapServer/0"
TARRANT_ITEM = TARRANT_LAYER
TARRANT_LOCAL_AREAS = {
    "city-of-benbrook": (PLACE_LAYER, "4807552"),
    "town-of-flower-mound": (PLACE_LAYER, "4826232"),
    "city-of-forest-hill": (PLACE_LAYER, "4826544"),
    "city-of-north-richland-hills": (PLACE_LAYER, "4852356"),
    "city-of-river-oaks": (PLACE_LAYER, "4862384"),
    "city-of-white-settlement": (PLACE_LAYER, "4878544"),
    "eagle-mountain-saginaw-isd": (SCHOOL_LAYER, "4817700"),
    "keller-isd": (SCHOOL_LAYER, "4825260"),
}
MONTGOMERY_LAYER = ("https://services1.arcgis.com/PRoAPGnMSUqvTrzq/arcgis/rest/services/Elections_Precincts_2026/"
                    "FeatureServer/0")
MONTGOMERY_LOCAL_AREAS = {
    "city-of-houston": (PLACE_LAYER, "4835000"),
    "city-of-roman-forest": (PLACE_LAYER, "4863044"),
    "conroe-isd": (SCHOOL_LAYER, "4815000"),
    "new-caney-isd": (SCHOOL_LAYER, "4832400"),
    "tomball-isd": (SCHOOL_LAYER, "4842960"),
    "willis-isd": (SCHOOL_LAYER, "4845900"),
    "montgomery-county-mud-94": (TCEQ_MUD, "5858170"),
    "montgomery-county-mud-187": (TCEQ_MUD, "5857709"),
    "montgomery-county-mud-228": (TCEQ_MUD, "4287793"),
}
KC_SERVICE ="https://gismaps.kingcounty.gov/arcgis/rest/services/Districts/KingCo_Electoral_Districts/MapServer"
KC_LAYER = KC_SERVICE + "/0"  # Voting precincts
KC_LOCAL_AREAS = {
    "city-of-bothell": (PLACE_LAYER, "5307380"),
    "city-of-clyde-hill": (PLACE_LAYER, "5313365"),
    "city-of-issaquah": (PLACE_LAYER, "5333805"),
    "city-of-kenmore": (PLACE_LAYER, "5335170"),
    "city-of-lake-forest-park": (PLACE_LAYER, "5337270"),
    "city-of-milton": (PLACE_LAYER, "5346020"),
    "city-of-seatac": (PLACE_LAYER, "5362288"),
    "city-of-seattle": (PLACE_LAYER, "5363000"),
    "city-of-shoreline": (PLACE_LAYER, "5363960"),
    "auburn-sd-408": (SCHOOL_LAYER, "5300300"),
    "highline-sd-401": (SCHOOL_LAYER, "5303540"),
    "kent-sd-415": (SCHOOL_LAYER, "5303960"),
    "king-county-fpd-20": (KC_SERVICE + "/10", "20", "FIRDST"),
}
OVERLAY_SHARE = 0.95  # a precinct must be at least this much inside one district to be assigned it without a flag

# Per county: precinct layer, its field for each district kind, areas every voter shares, local areas.
COUNTIES = {
    "harris-county": {
        "name": "Harris County", "item": COUNTY_ITEM, "layer": COUNTY_LAYER, "precinct_field": "VPCT",
        "fields": {"us-house": "USCong_C2333", "state-house": "St_Rep_Dis", "state-senate": "St_Sen_Dis",
                   "sboe": "SBOE_E2106", "commissioner": "Comm__Cour", "jp": "JP_Constab"},
        "everywhere": ["court-of-appeals-1", "court-of-appeals-14"],  # Gov't Code 22.201(b), (o)
        "local_areas": HARRIS_LOCAL_AREAS,
        "headline": "Harris County voting precincts and their districts (2026)",
        "statement": ("Harris County's GIS layer of voting precincts effective January 1, 2026 (1,224 precincts) lists each "
                      "precinct's Commissioners Court precinct, congressional district under plan C2333, State House, State "
                      "Senate, State Board of Education, and justice of the peace/constable precinct."),
        "title": "Harris County GIS: VPCTs_2026 (Voting Precincts effective 1/1/26)",
        "credits": ("Credits: Harris County Commissioners Court, Office of County Administration, and Tax Office. The layer's "
                    "license says it is informational and not for resale or redistribution; only precinct numbers and district "
                    "numbers derived from it are published here. Voter counts in the layer are not used."),
        "field_note": "This is why precincts use the county layer's USCong_C2333 field rather than USCong_C21.",
    },
    "travis-county": {
        "name": "Travis County", "item": TRAVIS_ITEM, "layer": TRAVIS_LAYER, "precinct_field": "Precinct",
        "fields": {"us-house": "Congressional", "state-house": "Legislative", "state-senate": "Senate",
                   "sboe": "StateBoardOfEducation", "commissioner": "Commissioner", "jp": "JPConstable"},
        "everywhere": ["court-of-appeals-3"],  # Gov't Code 22.201(d)
        "local_areas": TRAVIS_LOCAL_AREAS,
        "headline": "Travis County election precincts and their districts (2026)",
        "statement": ("Travis County's GIS layer of election precincts, approved by the Commissioners Court on December 4, "
                      "2025 and drawn for congressional plan C2333 (308 precincts), lists each precinct's congressional "
                      "district, State Senate and State House districts, Commissioners Court precinct, justice of the "
                      "peace/constable precinct, and State Board of Education district."),
        "title": "Travis County GIS: Election Precincts (approved by Commissioners Court December 4, 2025)",
        "credits": "Voter counts in the layer are not used.",
        "field_note": "The Travis County layer's Congressional field follows plan C2333 (see the layer's item description).",
    },
    "dallas-county": {
        "name": "Dallas County", "item": DALLAS_ITEM, "layer": DALLAS_LAYER, "precinct_field": "NewPrcnt",
        "fields": {"us-house": "Cong", "state-house": "House", "state-senate": "Sen",
                   "sboe": "SBOE", "commissioner": "Comm", "jp": "JP"},
        "everywhere": ["court-of-appeals-5"],  # Gov't Code 22.201(f)
        "local_areas": DALLAS_LOCAL_AREAS,
        "headline": "Dallas County election precincts and their districts (2026)",
        "statement": ("Dallas County's GIS layer of 2026 election precincts (791 precincts), which the layer's item "
                      "says were approved by Dallas County Commissioners on December 4, 2025, lists each precinct's "
                      "congressional district, State House, State Senate, and State Board of Education districts, "
                      "Commissioners Court precinct, and justice of the peace precinct."),
        "title": "Dallas County GIS: Election_Precincts_2026 (approved by Commissioners December 4, 2025)",
        "credits": ("The layer's disclaimer says it is for informational purposes and makes no warranty of absolute "
                    "accuracy. Voter counts in the layer are not used. Constables use the justice of the peace "
                    "precincts (Texas Constitution, Article V, Section 18(a))."),
        "field_note": ("The Dallas County layer's item description does not name a congressional plan; its Cong field "
                       "lists districts 5, 6, 24, 30, 32, and 33, the same six districts on the Dallas County sample ballot."),
    },
    "bexar-county": {
        "name": "Bexar County", "item": BEXAR_ITEM, "layer": BEXAR_LAYER, "precinct_field": "NAME",
        # The Bexar precinct layer has no district fields: each precinct's districts come from overlaying district maps.
        "fields": {kind: kind for kind in ("us-house", "state-house", "state-senate", "commissioner", "jp")},
        "overlays": {
            "us-house": ("shapefile", TLC_C2333, "District", "Texas Legislative Council: PLANC2333 (congressional districts "
                         "enacted 2025, 89th Legislature, 2nd C.S.)"),
            "state-house": ("shapefile", TLC_H2316, "District", "Texas Legislative Council: PLANH2316 (State House "
                            "districts, 2023-2026)"),
            "state-senate": ("shapefile", TLC_S2168, "District", "Texas Legislative Council: PLANS2168 (State Senate "
                             "districts)"),
            "commissioner": ("arcgis", BEXAR_COMMISSIONER, "Comm", "Bexar County GIS: Commissioner Precincts"),
            "jp": ("arcgis", BEXAR_JP, "Precinct", "Bexar County GIS: Justice of the Peace Precincts"),
        },
        "everywhere": ["court-of-appeals-4"],  # Gov't Code 22.201(e)
        "local_areas": BEXAR_LOCAL_AREAS,
        "headline": "Bexar County voting precincts (2026)",
        "statement": ("Bexar County's GIS layer of voting precincts (806 precincts), published by Bexar County "
                      "Elections, gives each precinct's number and outline."),
        "title": "Bexar County GIS: Bexar County Voter Precincts",
        "credits": ("The layer lists only precinct numbers; each precinct's districts were assigned by overlaying the "
                    "district maps named in precincts#S-05."),
        "field_note": ("Bexar County's own congressional layer still shows the pre-2025 districts (it includes District 28), "
                       "so congressional districts come from the Texas Legislative Council's PLANC2333 shapefile instead."),
    },
    "collin-county": {
        "name": "Collin County", "item": COLLIN_ITEM, "layer": COLLIN_LAYER, "precinct_field": "PRECINCT",
        # The Collin layer's congressional field predates plan C2333, so every district comes from an overlay; the
        # commissioner and JP precincts come from the layer's own fields (overlaying a layer on itself is exact).
        "fields": {kind: kind for kind in ("us-house", "state-house", "state-senate", "commissioner", "jp")},
        "overlays": {
            "us-house": ("shapefile", TLC_C2333, "District", "Texas Legislative Council: PLANC2333 (congressional districts "
                         "enacted 2025, 89th Legislature, 2nd C.S.)"),
            "state-house": ("shapefile", TLC_H2316, "District", "Texas Legislative Council: PLANH2316 (State House "
                            "districts, 2023-2026)"),
            "state-senate": ("shapefile", TLC_S2168, "District", "Texas Legislative Council: PLANS2168 (State Senate "
                             "districts)"),
            "commissioner": ("arcgis", COLLIN_LAYER, "COMMISH", "Collin County GIS: Voting Precincts (COMMISH field)"),
            "jp": ("arcgis", COLLIN_LAYER, "JPC", "Collin County GIS: Voting Precincts (JPC field)"),
        },
        "everywhere": ["court-of-appeals-5"],  # Gov't Code 22.201(f)
        "local_areas": COLLIN_LOCAL_AREAS,
        "headline": "Collin County voting precincts (2026)",
        "statement": ("Collin County's GIS layer of voting precincts (273 precincts), published by Collin County GIS, gives "
                      "each precinct's number and outline and its Commissioners Court and justice of the peace precincts; "
                      "the layer says the precincts were approved by the Commissioners Court on 9/25/2023 (Court Order "
                      "#2023-915-09-25), effective 1/1/2024."),
        "title": "Collin County GIS: Voting Precincts",
        "credits": ("Congressional, State House and State Senate districts were assigned by overlaying the district maps "
                    "named in precincts#S-05; the layer's own district fields and officeholder names are not used."),
        "field_note": ("The Collin County layer's precincts took effect 1/1/2024, before the 2025 congressional map, so congressional "
                       "districts come from the Texas Legislative Council's PLANC2333 shapefile instead."),
    },
    "denton-county": {
        "name": "Denton County", "item": DENTON_ITEM, "layer": DENTON_LAYER, "precinct_field": "FullNum",
        # The precinct layer lists only precinct numbers: districts come from overlays (state plans for Congress and the
        # Legislature; the county's own commissioner, JP/constable and State Board of Education layers).
        "fields": {kind: kind for kind in ("us-house", "state-house", "state-senate", "sboe", "commissioner", "jp")},
        "overlays": {
            "us-house": ("shapefile", TLC_C2333, "District", "Texas Legislative Council: PLANC2333 (congressional districts "
                         "enacted 2025, 89th Legislature, 2nd C.S.)"),
            "state-house": ("shapefile", TLC_H2316, "District", "Texas Legislative Council: PLANH2316 (State House "
                            "districts, 2023-2026)"),
            "state-senate": ("shapefile", TLC_S2168, "District", "Texas Legislative Council: PLANS2168 (State Senate "
                             "districts)"),
            "sboe": ("arcgis", DENTON_SERVICE + "/6", "ED_DIST", "Denton County GIS: Texas Educational Districts"),
            "commissioner": ("arcgis", DENTON_SERVICE + "/4", "COMMISH", "Denton County GIS: Commissioner Precincts"),
            "jp": ("arcgis", DENTON_SERVICE + "/5", "JP_C", "Denton County GIS: JP / Constable"),
        },
        "everywhere": ["court-of-appeals-2"],  # Gov't Code 22.201(c)
        "local_areas": DENTON_LOCAL_AREAS,
        "headline": "Denton County voter precincts (2026)",
        "statement": ("Denton County's GIS layer \"Voter Precincts (Effective 1/1/2026)\" (252 precincts), published by Denton "
                      "County GIS, gives each precinct's number and outline."),
        "title": "Denton County GIS: Voter Precincts (Effective 1/1/2026)",
        "credits": ("The layer lists precinct numbers only; each precinct's districts were assigned by overlaying the district "
                    "maps named in precincts#S-05. The county server resets long transfers, so its layers were downloaded in "
                    "small pages; the State Board of Education layer was requested simplified to 0.0005 degrees and the "
                    "commissioner and JP/constable layers to 0.0001 degrees, finer than the 0.001-degree overlay grid."),
        "field_note": ("Congressional districts come from the Texas Legislative Council's PLANC2333 shapefile rather than the "
                       "county's own congressional layer, so the 2025 map is used."),
    },
    "williamson-county": {
        "name": "Williamson County", "item": WILCO_ITEM, "layer": WILCO_LAYER, "precinct_field": "VOTER_PRECINCT",
        # The precinct layer lists each precinct's commissioner precinct (COMMPCT); the county publishes commissioner,
        # constable and JP precincts as one layer, so the JP precinct is the same number. Other districts come from
        # overlays (state plans for Congress and the Legislature; the county's State Board of Education layer).
        "fields": {kind: kind for kind in ("us-house", "state-house", "state-senate", "sboe", "commissioner", "jp")},
        "overlays": {
            "us-house": ("shapefile", TLC_C2333, "District", "Texas Legislative Council: PLANC2333 (congressional districts "
                         "enacted 2025, 89th Legislature, 2nd C.S.)"),
            "state-house": ("shapefile", TLC_H2316, "District", "Texas Legislative Council: PLANH2316 (State House "
                            "districts, 2023-2026)"),
            "state-senate": ("shapefile", TLC_S2168, "District", "Texas Legislative Council: PLANS2168 (State Senate "
                             "districts)"),
            "sboe": ("arcgis", WILCO_ADMIN + "/7", "DISTRICT", "Williamson County GIS: State Board of Education Districts"),
            "commissioner": ("arcgis", WILCO_LAYER, "COMMPCT", "Williamson County GIS: Current Voter Precincts (COMMPCT field)"),
            "jp": ("arcgis", WILCO_LAYER, "COMMPCT", "Williamson County GIS: Current Voter Precincts (COMMPCT field; the county "
                   "publishes commissioner, constable and JP precincts as one layer)"),
        },
        "everywhere": ["court-of-appeals-3"],  # Gov't Code 22.201(d)
        "local_areas": WILCO_LOCAL_AREAS,
        "headline": "Williamson County voter precincts (2026)",
        "statement": ("Williamson County's GIS layer \"Current Voter Precincts\" (177 precincts), published by Williamson "
                      "County GIS, gives each precinct's number, outline, and commissioner precinct."),
        "title": "Williamson County GIS: Current Voter Precincts",
        "credits": ("Each precinct's districts were assigned by overlaying the district maps named in precincts#S-05. The "
                    "county publishes commissioner, constable and justice of the peace precincts as a single layer "
                    "(\"Commissioner, Constable, JP Precincts\"), so each precinct's JP precinct is its commissioner "
                    "precinct."),
        "field_note": ("Congressional districts come from the Texas Legislative Council's PLANC2333 shapefile rather than the "
                       "county's own congressional layer, so the 2025 map is used."),
    },
    "tarrant-county": {
        "name": "Tarrant County", "item": TARRANT_ITEM, "layer": TARRANT_LAYER, "precinct_field": "Precinct",
        "fields": {"us-house": "Congress", "state-house": "House", "state-senate": "Senate", "sboe": "Education",
                   "commissioner": "Commish", "jp": "JP"},
        "everywhere": ["court-of-appeals-2"],  # Gov't Code 22.201(c)
        "local_areas": TARRANT_LOCAL_AREAS,
        "headline": "Tarrant County voting precincts and their districts (2026)",
        "statement": ("Tarrant County's GIS layer \"Voting Precincts\" (707 precincts), published by Tarrant County, lists each "
                      "precinct's congressional, State Senate, State House, State Board of Education, Commissioners Court and "
                      "justice of the peace districts."),
        "title": "Tarrant County GIS: Voting Precincts",
        "credits": ("Only precinct numbers, outlines and district numbers from the layer are used. Its congressional districts "
                    "(6, 12, 24, 25, 30) are the ones Tarrant County Elections lists for the November 3, 2026 election "
                    "(\"Districts in Election\")."),
        "field_note": "The layer's Congress field gives the districts listed for this election under the 2025 map.",
    },
    "montgomery-county": {
        "name": "Montgomery County", "item": MONTGOMERY_LAYER, "layer": MONTGOMERY_LAYER, "precinct_field": "PCTLBL",
        "fields": {"us-house": "USRepNm", "state-house": "STRepNm", "state-senate": "STSenNm", "sboe": "STBOE",
                   "commissioner": "CPNm", "jp": "JPNm"},
        "everywhere": ["court-of-appeals-9"],  # Gov't Code 22.201(j)
        "local_areas": MONTGOMERY_LOCAL_AREAS,
        "headline": "Montgomery County voting precincts and their districts (2026)",
        "statement": ("Montgomery County's GIS layer \"Elections Precincts 2026\" (121 precincts), published on the county's "
                      "open data site with the data source \"MCTX ELECTIONS\" and effective date \"Jan 2026\", lists each "
                      "precinct's congressional, State Senate, State House, State Board of Education, Commissioners Court "
                      "and justice of the peace districts."),
        "title": "Montgomery County GIS: Elections Precincts 2026",
        "credits": "Only precinct numbers, outlines and district numbers from the layer are used.",
        "field_note": "",
    },
    "king-county": {
        "name": "King County", "item": KC_LAYER, "layer": KC_LAYER, "precinct_field": "votdst", "scotus": False,
        # The precinct layer lists precinct numbers only; districts come from the county's own district layers.
        "fields": {kind: kind for kind in ("us-house", "legislative", "council", "district-court", "seattle-council")},
        "patterns": {"legislative": "legislative-district-{}", "council": "king-county/council-{}",
                     "district-court": "king-county/district-court-{s}", "seattle-council": "city-of-seattle/district-{}"},
        "overlays": {
            "us-house": ("arcgis", KC_SERVICE + "/2", "CONGDST", "King County GIS: Congressional districts"),
            "legislative": ("arcgis", KC_SERVICE + "/3", "LEGDST", "King County GIS: Legislative districts"),
            "council": ("arcgis", KC_SERVICE + "/1", "KCCDST", "King County GIS: King County Council districts"),
            "district-court": ("arcgis", KC_SERVICE + "/12", "NAME", "King County GIS: King County District Court divisions"),
            "seattle-council": ("arcgis", KC_SERVICE + "/4", "SCCDST", "King County GIS: Seattle City Council districts"),
        },
        "everywhere": ["court-of-appeals-1-1"],  # RCW 2.06.020: Division One, District One is King County
        "local_areas": KC_LOCAL_AREAS,
        "overlay_title": "King County GIS: electoral district layers",
        "headline": "King County voting precincts (2026)",
        "statement": ("King County's GIS layer \"Voting precincts\" (2,769 precincts), in the county's Electoral Districts map "
                      "service, gives each precinct's number and outline."),
        "title": "King County GIS: Voting precincts",
        "credits": ("Each precinct's districts were assigned by overlaying the county's own district layers named in "
                    "precincts#S-05. Only precinct numbers and outlines are used; the layer's voter counts are not."),
        "field_note": "",
    },
}

# Wayback Machine copies, checked when made (documents: same SHA-256 as the original).
ARCHIVES = {
    SCOTUS_ORDER: "https://web.archive.org/web/20260930221333/https://www.supremecourt.gov/opinions/25pdf/25a608_7khn.pdf",
    ZCTA_LAYER: "https://web.archive.org/web/20260930221535/" + ZCTA_LAYER,
    PLACE_LAYER: "https://web.archive.org/web/20260930221613/" + PLACE_LAYER,
    TIGER_SERVICE: "https://web.archive.org/web/20260930223321/" + TIGER_SERVICE,
    TCEQ_SERVICE: "https://web.archive.org/web/20260930225735/" + TCEQ_SERVICE,
}
SURE_SHARE = 0.99  # a precinct or ZIP at least this much inside an area is treated as fully inside
STEP = 0.001      # grid step in degrees (~110 m north-south)
MIN_SHARE = 0.01  # a precinct must cover 1% of a ZIP's in-county cells to count for that ZIP
MIN_CELLS = 5     # ZIPs with fewer in-county cells than this are left out (map-edge slivers)
SIMPLIFY = 0.00002  # precinct outlines for the address lookup: ~2 m Douglas-Peucker tolerance


def fetch(url: str, params: dict, cache: Path, name: str) -> bytes:
    path = cache / name
    if path.exists():
        return path.read_bytes()
    full = f"{url}/query?{urllib.parse.urlencode(params)}"
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers={"User-Agent": "vote-the-record"}),
                                        timeout=180) as r:
                data = r.read()
            json.loads(data)
            path.write_bytes(data)
            return data
        except Exception as exc:  # network resets happen; retry with backoff
            print(f"  retry {attempt + 1} for {name}: {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"could not download {name}")


def paged(url: str, base: dict, cache: Path, prefix: str, page: int) -> list[dict]:
    feats, offset = [], 0
    while True:
        data = json.loads(fetch(url, {**base, "resultOffset": offset, "resultRecordCount": page},
                                cache, f"{prefix}-{offset}.json"))
        feats += data["features"]
        if not data.get("exceededTransferLimit") and len(data["features"]) < page:
            return feats
        offset += page


def rings(geometry: dict) -> list[list[tuple[float, float]]]:
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    return [[(x, y) for x, y, *_ in ring] for poly in polys for ring in poly]


def rasterize(features, key, grid, x0, y0, cols, rows):
    """Even-odd scanline fill of each feature's rings into grid[row][col] = key(feature)."""
    for f in features:
        rs = rings(f["geometry"])
        ys = [y for r in rs for _, y in r]
        r_lo = max(0, int((min(ys) - y0) / STEP))
        r_hi = min(rows - 1, int((max(ys) - y0) / STEP) + 1)
        value = key(f)
        for row in range(r_lo, r_hi + 1):
            y = y0 + (row + 0.5) * STEP
            xs = []
            for ring in rs:
                for (xa, ya), (xb, yb) in zip(ring, ring[1:] + ring[:1]):
                    if (ya > y) != (yb > y):
                        xs.append(xa + (y - ya) * (xb - xa) / (yb - ya))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                c_lo = max(0, math.ceil((a - x0) / STEP - 0.5))    # first cell center at or after a
                c_hi = min(cols - 1, math.floor((b - x0) / STEP - 0.5))  # last cell center at or before b
                for col in range(c_lo, c_hi + 1):
                    grid[row][col] = value


def lcc_inverse(prj: str):
    """Inverse Lambert Conformal Conic (GRS 1980 ellipsoid, Snyder 1987 eqs. 15-1 to 15-11) for a shapefile .prj."""
    p = {k: float(v) for k, v in re.findall(r'PARAMETER\["(\w+)",([-\d.]+)\]', prj)}
    assert "Lambert_Conformal_Conic" in prj and "GRS_1980" in prj and 'UNIT["Meter"' in prj, "unexpected projection"
    a, f = 6378137.0, 1 / 298.257222101
    e = math.sqrt(2 * f - f * f)
    rad = math.radians
    phi1, phi2, phi0 = rad(p["Standard_Parallel_1"]), rad(p["Standard_Parallel_2"]), rad(p["Latitude_Of_Origin"])
    lam0, fe, fn = rad(p["Central_Meridian"]), p["False_Easting"], p["False_Northing"]

    def m(phi):
        return math.cos(phi) / math.sqrt(1 - (e * math.sin(phi)) ** 2)

    def t(phi):
        s = e * math.sin(phi)
        return math.tan(math.pi / 4 - phi / 2) / ((1 - s) / (1 + s)) ** (e / 2)

    n = (math.log(m(phi1)) - math.log(m(phi2))) / (math.log(t(phi1)) - math.log(t(phi2)))
    big_f = m(phi1) / (n * t(phi1) ** n)
    rho0 = a * big_f * t(phi0) ** n

    def inverse(x, y):
        dx, dy = x - fe, rho0 - (y - fn)
        rho = math.copysign(math.hypot(dx, dy), n)
        tt = (rho / (a * big_f)) ** (1 / n)
        lam = math.atan2(dx, dy) / n + lam0
        phi = math.pi / 2 - 2 * math.atan(tt)
        for _ in range(8):
            s = e * math.sin(phi)
            phi = math.pi / 2 - 2 * math.atan(tt * ((1 - s) / (1 + s)) ** (e / 2))
        return math.degrees(lam), math.degrees(phi)
    return inverse


def read_shapefile(zip_bytes: bytes, field: str, keep=None) -> list[dict]:
    """Polygons from a zipped shapefile (e.g. a Texas Legislative Council plan), as GeoJSON-like features in
    longitude/latitude with properties {field: value}. keep(value) -> bool filters features before reprojecting."""
    import io
    import struct
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    name = next(n for n in z.namelist() if n.lower().endswith(".shp"))
    shp, dbf = z.read(name), z.read(name[:-4] + ".dbf")
    inverse = lcc_inverse(z.read(name[:-4] + ".prj").decode("latin-1"))
    # dBASE table: header, 32-byte field descriptors, then fixed-width records.
    nrec, hlen, rlen = struct.unpack("<IHH", dbf[4:12])
    fields, pos = [], 32
    while dbf[pos] != 0x0D:
        fname = dbf[pos:pos + 11].split(b"\0")[0].decode("latin-1")
        fields.append((fname, dbf[pos + 16]))
        pos += 32
    values, off = [], 0
    for fname, width in fields:
        if fname == field:
            values = [dbf[hlen + i * rlen + 1 + off: hlen + i * rlen + 1 + off + width].decode("latin-1").strip()
                      for i in range(nrec)]
        off += width
    assert values, f"field {field} not in {[f for f, _ in fields]}"
    feats, pos, i = [], 100, 0
    while pos < len(shp):
        _, length = struct.unpack(">ii", shp[pos:pos + 8])
        body = shp[pos + 8: pos + 8 + 2 * length]
        pos += 8 + 2 * length
        value = values[i]
        i += 1
        if struct.unpack("<i", body[:4])[0] != 5 or (keep and not keep(value)):
            continue
        nparts, npoints = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npoints]
        pts = struct.unpack(f"<{2 * npoints}d", body[44 + 4 * nparts: 44 + 4 * nparts + 16 * npoints])
        ring_list = [[list(inverse(pts[2 * k], pts[2 * k + 1])) for k in range(parts[j], parts[j + 1])]
                     for j in range(nparts)]
        feats.append({"type": "Feature", "properties": {field: value},
                      "geometry": {"type": "Polygon", "coordinates": ring_list}})
    return feats


def download(url: str, cache: Path, name: str) -> bytes:
    """A whole file (e.g. a zipped shapefile), cached; retried with resume because large downloads get cut off."""
    path = cache / name
    if path.exists():
        return path.read_bytes()
    data = b""
    for attempt in range(30):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vote-the-record", **({"Range": f"bytes={len(data)}-"} if data else {})})
            with urllib.request.urlopen(req, timeout=300) as r:
                data += r.read()
        except Exception as exc:
            print(f"  retry {attempt + 1} for {name}: {exc}", file=sys.stderr)
            time.sleep(3)
        if data.endswith(b"\0\0") or data[-22:-18] == b"PK\x05\x06" or (name.endswith(".zip") and b"PK\x05\x06" in data[-200:]):
            path.write_bytes(data)
            return data
    raise SystemExit(f"could not download {name}")


def overlay(pgrid, district_feats: list[dict], field: str, x0, y0, cols, rows) -> dict:
    """Precinct -> (district value covering most of it, that share), by rasterizing district outlines on the grid."""
    dgrid = [[None] * cols for _ in range(rows)]
    rasterize(district_feats, lambda f: f["properties"][field], dgrid, x0, y0, cols, rows)
    counts: dict = {}
    for prow, drow in zip(pgrid, dgrid):
        for p, d in zip(prow, drow):
            if p:
                counts.setdefault(p, {}).setdefault(d, 0)
                counts[p][d] += 1
    out = {}
    for p, per in counts.items():
        best = max((d for d in per if d is not None), key=lambda d: per[d], default=None)
        out[p] = (best, per.get(best, 0) / sum(per.values()) if best is not None else 0.0, sum(per.values()))
    return out


def point_in(rings_: list, x: float, y: float) -> bool:
    """Even-odd point-in-polygon test over all rings of a feature."""
    inside = False
    for ring in rings_:
        for (xa, ya), (xb, yb) in zip(ring, ring[1:] + ring[:1]):
            if (ya > y) != (yb > y) and x < xa + (y - ya) * (xb - xa) / (yb - ya):
                inside = not inside
    return inside


def fine_assign(precinct: dict, district_feats: list[dict], field: str, n: int = 60, with_votes: bool = False) -> tuple:
    """(district, share, points) for one precinct from an n x n grid of points over its own outline: used for
    precincts too small or too split for the shared grid."""
    prs = rings(precinct["geometry"])
    xs, ys = [x for r in prs for x, _ in r], [y for r in prs for _, y in r]
    cands = []
    for f in district_feats:
        drs = rings(f["geometry"])
        dx, dy = [x for r in drs for x, _ in r], [y for r in drs for _, y in r]
        if min(dx) <= max(xs) and max(dx) >= min(xs) and min(dy) <= max(ys) and max(dy) >= min(ys):
            cands.append((f["properties"][field], drs))
    votes: dict = {}
    total = 0
    for i in range(n):
        for j in range(n):
            x = min(xs) + (i + 0.5) * (max(xs) - min(xs)) / n
            y = min(ys) + (j + 0.5) * (max(ys) - min(ys)) / n
            if not point_in(prs, x, y):
                continue
            total += 1
            hit = next((d for d, drs in cands if point_in(drs, x, y)), None)
            votes[hit] = votes.get(hit, 0) + 1
    best = max((d for d in votes if d is not None), key=lambda d: votes[d], default=None)
    if with_votes:
        return best, (votes.get(best, 0) / total if total else 0.0), total, votes
    return best, (votes.get(best, 0) / total if total else 0.0), total


def simplify(points: list, tol: float) -> list:
    """Douglas-Peucker line simplification (iterative, keeps the first and last point)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        best, idx = -1.0, a
        for i in range(a + 1, b):
            x0, y0 = points[i]
            d = (abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / length) if length else ((x0 - x1) ** 2 + (y0 - y1) ** 2) ** 0.5
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def shapes(features, source_id: str, pid: str = "VPCT") -> dict:
    """Simplified precinct outlines for the in-browser address lookup (even-odd rings)."""
    out = {}
    for f in sorted(features, key=lambda f: pct_order(f["properties"][pid])):
        rs = []
        for ring in rings(f["geometry"]):
            s = simplify(ring, SIMPLIFY)
            if len(s) >= 4:
                rs.append([[round(x, 5), round(y, 5)] for x, y in s])
        out[str(f["properties"][pid])] = rs
    return {"source": source_id, "note": "Generated by src/collectors/states/tx/county_precincts.py; "
            f"outlines simplified to about {SIMPLIFY} degrees. Unverified.", "precincts": out}


def precinct_id(value) -> int | str:
    """Precinct numbers as integers ("0123" -> 123); a few Dallas precincts include letters ("32A0")."""
    text = str(value).strip().upper()
    return int(text) if text.isdigit() else text


def pct_order(p) -> tuple[int, str]:
    """Numeric order, with lettered precincts after the number they start with ("32A0" after 32)."""
    return int(re.match(r"\d*", str(p)).group() or 0), str(p)


def areas_for(attrs: dict, on_ballot: set[str], county: str = "harris-county") -> dict[str, str]:
    """District kind -> area slug, for the districts that have a contest on this ballot."""
    cfg = COUNTIES[county]
    pattern = {"us-house": "us-house-{}", "state-house": "state-house-{}", "state-senate": "state-senate-{}",
               "sboe": "sboe-{}", "commissioner": county + "/commissioner-{}", "jp": county + "/jp-{}",
               **cfg.get("patterns", {})}  # "{}" takes the district number, "{s}" the value in lower case
    cand = {}
    for kind, field in cfg["fields"].items():
        value = attrs.get(field)
        if value in (None, ""):
            continue
        fmt = pattern[kind]
        cand[kind] = fmt.format(s=str(value).strip().lower()) if "{s}" in fmt else fmt.format(int(re.sub(r"\D", "", str(value))))
    return {k: v for k, v in cand.items() if v in on_ballot}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source(fid, headline, statement, url, title, kind, digest, notes):
    return {"id": fid, "headline": headline, "statement": statement, "label": "official_record",
            "claim_status": "documented", "contradicted_by": [], "source_url": url, "source_title": title,
            "source_kind": kind, "sha256": digest, "archive_url": ARCHIVES.get(url), "event_date": TODAY,
            "retrieved": TODAY, "verification": "unverified", "verified_on": None, "reviewer": None,
            "notes": notes}


TODAY = dt.date.today().isoformat()


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", "null"))


def write(path: Path, comment: str, sources: list, key: str, table: dict, everywhere: list) -> None:
    """Sources in block style; one line per precinct or ZIP, with its areas as a flow list."""
    text = "# Generated by src/collectors/states/tx/county_precincts.py. Unverified.\n# " + comment + "\n"
    text += yaml.dump({"sources": sources}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10000)
    text += f"everywhere: {json.dumps(everywhere)}\n{key}:\n"
    text += "".join(f"  '{k}': {json.dumps(v)}\n" for k, v in table.items())
    yaml.safe_load(text)  # must parse
    path.write_text(text, encoding="utf-8", newline="\n")  # same bytes on every OS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--county", required=True, choices=sorted(COUNTIES), help="which county's precinct layer to use")
    ap.add_argument("--cache", type=Path, required=True, help="folder for raw downloads (outside data/)")
    ap.add_argument("--ballot-dir", type=Path, required=True, help="the ballot folder (holds ballot.yaml); read only")
    ap.add_argument("--out", type=Path, required=True, help="where to write precincts.yaml and zips.yaml")
    ap.add_argument("--scotus-sha256", help="sha256 of the downloaded Supreme Court order PDF (Texas counties)")
    ap.add_argument("--today", help="date to record as retrieved (default: today)")
    args = ap.parse_args(argv)
    global TODAY
    TODAY = args.today or TODAY
    cfg = COUNTIES[args.county]
    if cfg.get("scotus", True) and not args.scotus_sha256:
        raise SystemExit("--scotus-sha256 is required for this county")
    pid = cfg["precinct_field"]
    args.cache.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in ("precincts.yaml", "zips.yaml", "precinct-shapes.json", "area-shapes.json"):
        if (args.out / name).exists():
            raise SystemExit(f"refusing to overwrite {args.out / name}")

    # Areas with a contest on this ballot.
    ballot_dir = args.ballot_dir.resolve()
    repo_states = ballot_dir.parents[4]  # .../data/states
    ballot = yaml.safe_load((ballot_dir / "ballot.yaml").read_text(encoding="utf-8"))
    on_ballot = set()
    for contest in ballot["contests"]:
        folder = repo_states / contest
        race_file = folder / "race.yaml" if (folder / "race.yaml").is_file() else folder / "measure.yaml"
        race = yaml.safe_load(race_file.read_text(encoding="utf-8"))
        parts = contest.split("/")
        default = parts[0] if parts[1] == "statewide" else parts[2]
        on_ballot.add(race.get("area") or default)

    print("downloading county precinct layer...", file=sys.stderr)
    fields = pid if cfg.get("overlays") else ",".join([pid, *cfg["fields"].values()])
    pct = paged(cfg["layer"], {"where": "1=1", "outFields": fields, "returnGeometry": "true",
                               "outSR": 4326, "geometryPrecision": 6, "f": "geojson"}, args.cache, "vpct", 200)
    attrs = [f["properties"] for f in pct]
    for f in pct:  # precinct numbers as integers (Travis stores them as text)
        f["properties"][pid] = precinct_id(f["properties"][pid])
    assert len(attrs) == len({a[pid] for a in attrs}), "duplicate precinct numbers"
    xs = [x for f in pct for r in rings(f["geometry"]) for x, _ in r]
    ys = [y for f in pct for r in rings(f["geometry"]) for _, y in r]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    print("downloading Census ZCTAs...", file=sys.stderr)
    zcta = paged(ZCTA_LAYER, {"where": "1=1", "geometry": f"{x0},{y0},{x1},{y1}", "geometryType": "esriGeometryEnvelope",
                              "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "ZCTA5",
                              "returnGeometry": "true", "outSR": 4326, "geometryPrecision": 6, "f": "geojson"},
                 args.cache, "zcta", 50)

    cols, rows = int((x1 - x0) / STEP) + 1, int((y1 - y0) / STEP) + 1
    print(f"rasterizing {len(pct)} precincts and {len(zcta)} ZCTAs on {cols}x{rows} cells...", file=sys.stderr)
    pgrid = [[0] * cols for _ in range(rows)]
    zgrid = [[None] * cols for _ in range(rows)]
    rasterize(pct, lambda f: f["properties"][pid], pgrid, x0, y0, cols, rows)
    rasterize(zcta, lambda f: f["properties"]["ZCTA5"], zgrid, x0, y0, cols, rows)

    # Counties whose precinct layer has no district fields: assign each precinct the district covering most of it.
    overlay_src, flagged, split_kinds = None, [], {}
    if cfg.get("overlays"):
        raw_overlay, used = [], []
        for kind, (fmt, url, field, title) in cfg["overlays"].items():
            print(f"overlaying {kind}...", file=sys.stderr)
            if fmt == "shapefile":
                data = download(url, args.cache, url.rsplit("/", 1)[1])
                feats = [f for f in read_shapefile(data, field)
                         if any(x0 - 0.5 <= x <= x1 + 0.5 and y0 - 0.5 <= y <= y1 + 0.5
                                for x, y in f["geometry"]["coordinates"][0][::25])]
            else:
                feats = paged(url, {"where": "1=1", "outFields": field, "returnGeometry": "true", "outSR": 4326,
                                    "geometryPrecision": 6, "f": "geojson"}, args.cache, f"ov-{kind}", 200)
                data = b"".join((args.cache / f).read_bytes() for f in sorted(p.name for p in args.cache.glob(f"ov-{kind}-*.json")))
            raw_overlay.append(data)
            used.append(f"{title} ({url})")
            got = overlay(pgrid, feats, field, x0, y0, cols, rows)
            for f in pct:
                p = f["properties"][pid]
                d, share, n = got.get(p, (None, 0.0, 0))
                if share < OVERLAY_SHARE or n < 20:
                    d, share, n = fine_assign(f, feats, field)
                if d is None or share < OVERLAY_SHARE:
                    # Split between districts: keep every district covering at least MIN_SHARE, as "depends on address".
                    _, _, total, votes = fine_assign(f, feats, field, with_votes=True)
                    parts = sorted((v for v in votes if v is not None and votes[v] / total >= MIN_SHARE), key=str)
                    flagged.append((p, kind, d, share))
                    split_kinds.setdefault(p, {})[kind] = parts
                f["properties"][kind] = d
        flag_text = ("; ".join(f"precinct {p}: {kind} {d} covers {share:.0%}" for p, kind, d, share in flagged)
                     if flagged else "none")
        overlay_src = (used, sha(b"".join(raw_overlay)), flag_text)
        attrs = [f["properties"] for f in pct]

    counts: dict[str, dict[int, int]] = {}
    for prow, zrow in zip(pgrid, zgrid):
        for p, z in zip(prow, zrow):
            if p and z:
                counts.setdefault(z, {}).setdefault(p, 0)
                counts[z][p] += 1

    # Local areas (cities): rasterize each outline and measure how much of every precinct and
    # ZIP falls inside it. Mostly inside -> the area; partly inside -> a "maybe" entry.
    local = {slug: spec for slug, spec in cfg["local_areas"].items() if slug in on_ballot}
    local_feats = {}
    for slug, spec in local.items():
        layer, geoid = spec[0], spec[1]
        field = spec[2] if len(spec) > 2 else "DISTRICT_ID" if layer.startswith(TCEQ_SERVICE) else "GEOID"
        prefix = "area" if len(spec) > 2 else "tceq" if field == "DISTRICT_ID" else "place"
        feats = paged(layer, {"where": f"{field}='{geoid}'", "outFields": field, "returnGeometry": "true",
                              "outSR": 4326, "geometryPrecision": 6, "f": "geojson"}, args.cache, f"{prefix}-{geoid}", 10)
        assert feats, f"no boundary found for {slug} ({field} {geoid})"
        # Some districts are stored as several pieces: merge them into one multipolygon.
        polys = []
        for f in feats:
            g = f["geometry"]
            polys += [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        local_feats[slug] = {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": polys}}
    pct_cells: dict[int, int] = {}
    zip_cells: dict[str, int] = {}
    for prow, zrow in zip(pgrid, zgrid):
        for p, z in zip(prow, zrow):
            if p:
                pct_cells[p] = pct_cells.get(p, 0) + 1
                if z:
                    zip_cells[z] = zip_cells.get(z, 0) + 1
    local_pct: dict[int, list] = {}
    local_zip: dict[str, list] = {}
    for slug, feat in sorted(local_feats.items()):
        agrid = [[0] * cols for _ in range(rows)]
        rasterize([feat], lambda f: 1, agrid, x0, y0, cols, rows)
        in_pct: dict[int, int] = {}
        in_zip: dict[str, int] = {}
        for prow, zrow, arow in zip(pgrid, zgrid, agrid):
            for p, z, a in zip(prow, zrow, arow):
                if a and p:
                    in_pct[p] = in_pct.get(p, 0) + 1
                    if z:
                        in_zip[z] = in_zip.get(z, 0) + 1
        area_total = sum(in_pct.values())
        # Partly inside: at least MIN_SHARE of the precinct/ZIP is in the area, or at least MIN_SHARE of the
        # area is in the precinct/ZIP (so small districts are never dropped).
        for p, n in in_pct.items():
            share = n / pct_cells[p]
            if share >= SURE_SHARE:
                local_pct.setdefault(p, []).append(slug)
            elif share >= MIN_SHARE or n / area_total >= MIN_SHARE:
                local_pct.setdefault(p, []).append([slug])
        for z, n in in_zip.items():
            share = n / zip_cells[z]
            if share >= SURE_SHARE:
                local_zip.setdefault(z, []).append(slug)
            elif share >= MIN_SHARE or n / area_total >= MIN_SHARE:
                local_zip.setdefault(z, []).append([slug])

    by_pct = {a[pid]: areas_for(a, on_ballot, args.county) for a in attrs}
    for p, kinds in split_kinds.items():  # precincts split between districts (overlay counties only)
        for kind, parts in kinds.items():
            areas = [areas_for({kind: v}, on_ballot, args.county).get(kind) for v in parts]
            areas = sorted({a for a in areas if a})
            if len(areas) > 1:
                by_pct[p][kind] = areas
            elif areas:
                by_pct[p][kind] = areas[0]
    precincts = {str(p): sorted(x for x in v.values() if isinstance(x, str)) + [x for x in v.values() if isinstance(x, list)]
                 + local_pct.get(p, [])
                 for p, v in sorted(by_pct.items(), key=lambda kv: pct_order(kv[0]))}
    zips = {}
    for z, per in sorted(counts.items()):
        total = sum(per.values())
        if total < MIN_CELLS:
            continue
        kept = [p for p, n in per.items() if n / total >= MIN_SHARE]
        kinds: dict[str, set[str]] = {}
        for p in kept:
            for kind, area in by_pct[p].items():
                kinds.setdefault(kind, set()).update(area if isinstance(area, list) else [area])
        entry = []
        order = ("us-house", "state-senate", "state-house", "sboe", "commissioner", "jp")
        for kind in order + tuple(k for k in cfg["fields"] if k not in order):
            values = sorted(kinds.get(kind, ()))
            if len(values) == 1:
                entry.append(values[0])
            elif values:
                entry.append(values)
        zips[z] = entry + local_zip.get(z, [])

    raw_pct = b"".join((args.cache / f).read_bytes() for f in sorted(p.name for p in args.cache.glob("vpct-*.json")))
    raw_zcta = b"".join((args.cache / f).read_bytes() for f in sorted(p.name for p in args.cache.glob("zcta-*.json")))
    fid = ballot_dir.relative_to(repo_states).as_posix()
    county_src = source(
        f"{fid}/precincts#S-01", cfg["headline"], cfg["statement"], cfg["item"], cfg["title"], "page", None,
        f"Read from {cfg['layer']} on {TODAY}; SHA-256 of the downloaded query responses: {sha(raw_pct)}. "
        + cfg["credits"])
    scotus_src = None if not cfg.get("scotus", True) else source(
        f"{fid}/precincts#S-02", "The 2025 congressional map (plan C2333) is in use for 2026",
        "On December 4, 2025, the U.S. Supreme Court granted Texas's application to stay the November 18, 2025 "
        "district court order that had blocked the new congressional map, allowing it to be used.",
        SCOTUS_ORDER, "Supreme Court of the United States: Abbott v. LULAC, No. 25A608, order of December 4, 2025",
        "document", args.scotus_sha256,
        cfg["field_note"])
    zcta_src = source(
        f"{fid}/zips#S-01", "Census ZIP Code Tabulation Areas (approximate ZIP codes)",
        "The U.S. Census Bureau's TIGERweb service publishes 2020 Census ZIP Code Tabulation Areas, which "
        "approximate U.S. Postal Service ZIP code areas.",
        ZCTA_LAYER, "U.S. Census Bureau TIGERweb: 2020 Census ZIP Code Tabulation Areas", "page", None,
        f"Read on {TODAY}; SHA-256 of the downloaded query responses: {sha(raw_zcta)}. ZIP-to-district links "
        f"were computed by overlaying these areas on the county precinct layer on a {STEP}-degree grid; a "
        f"precinct counts for a ZIP when it covers at least {MIN_SHARE:.0%} of the ZIP's grid cells in "
        f"{cfg['name']}. Results are approximate; the precinct lookup is exact.")
    zip_county = dict(county_src, id=f"{fid}/zips#S-02")
    raw_places = b"".join((args.cache / f).read_bytes() for f in sorted(p.name for p in args.cache.glob("place-*.json")))
    raw_tceq = b"".join((args.cache / f).read_bytes() for f in sorted(p.name for p in args.cache.glob("tceq-*.json")))
    tceq_used = {slug: spec for slug, spec in local.items() if spec[0].startswith(TCEQ_SERVICE)}
    county_used = {slug: spec for slug, spec in local.items() if len(spec) > 2}
    census_used = {slug: spec for slug, spec in local.items() if len(spec) == 2 and not spec[0].startswith(TCEQ_SERVICE)}
    place_src = source(
        f"{fid}/precincts#S-03", "Census boundaries of cities and school districts",
        "The U.S. Census Bureau's TIGERweb service publishes the current boundaries of incorporated places (cities) "
        "and unified school districts.",
        TIGER_SERVICE, "U.S. Census Bureau TIGERweb: Incorporated Places (layer 28) and Unified School Districts (layer 14)",
        "page", None,
        f"Read on {TODAY}; SHA-256 of the downloaded query responses: {sha(raw_places)}. Areas used: "
        + ", ".join(f"{slug} (layer {layer.rsplit('/', 1)[1]}, GEOID {geoid})" for slug, (layer, geoid) in sorted(census_used.items()))
        + f". A precinct or ZIP code at least {SURE_SHARE:.0%} inside an area counts as inside it; one at least "
        f"{MIN_SHARE:.0%} inside, or holding at least {MIN_SHARE:.0%} of the area, is marked as depending on the address. "
        "The address lookup uses the outlines directly.")
    zip_place = dict(place_src, id=f"{fid}/zips#S-03")
    tceq_src = source(
        f"{fid}/precincts#S-04", "TCEQ water district boundaries",
        "The Texas Commission on Environmental Quality's Water Districts map service publishes the boundaries of "
        "water districts, including municipal utility districts, fresh water supply districts, and municipal "
        "management districts.",
        TCEQ_SERVICE, "Texas Commission on Environmental Quality: Water Districts map service", "page", None,
        f"Read on {TODAY}; SHA-256 of the downloaded query responses: {sha(raw_tceq)}. Districts used: "
        + ", ".join(f"{slug} (layer {layer.rsplit('/', 1)[1]}, DISTRICT_ID {geoid})" for slug, (layer, geoid) in sorted(tceq_used.items()))
        + ". Same method as the Census boundaries.")
    zip_tceq = dict(tceq_src, id=f"{fid}/zips#S-04")
    raw_county_areas = b"".join((args.cache / f).read_bytes() for f in sorted(p.name for p in args.cache.glob("area-*.json")))
    county_area_src = source(
        f"{fid}/precincts#S-06", f"{cfg['name']} district boundaries",
        f"{cfg['name']}'s GIS publishes the boundaries of these districts.",
        next(iter(county_used.values()))[0] if county_used else cfg["layer"], f"{cfg['name']} GIS: district layers", "page",
        None, f"Read on {TODAY}; SHA-256 of the downloaded query responses: {sha(raw_county_areas)}. Districts used: "
        + ", ".join(f"{slug} ({spec[0]}, {spec[2]} = {spec[1]})" for slug, spec in sorted(county_used.items()))
        + ". Same method as the Census boundaries.")
    zip_county_areas = dict(county_area_src, id=f"{fid}/zips#S-06")
    extra_src = []
    if overlay_src:
        used, digest, flag_text = overlay_src
        extra_src.append(source(
            f"{fid}/precincts#S-05", "District maps used to assign each precinct its districts",
            f"The {cfg['name']} precinct layer lists precinct numbers only, so each precinct was assigned the district "
            "that covers most of it on these official maps: " + "; ".join(used) + ".",
            cfg["overlays"]["us-house"][1], cfg.get("overlay_title", "Texas Legislative Council and county district maps"), "page", None,
            f"Read on {TODAY}; SHA-256 of the downloaded files: {digest}. Each precinct and district was laid on a "
            f"{STEP}-degree grid (and a finer grid of points inside the precinct for small precincts); a precinct takes "
            f"the district covering at least {OVERLAY_SHARE:.0%} of it. Checked against Harris and Dallas counties, whose "
            "precinct layers list districts: the method matched every Harris precinct and all but one Dallas precinct "
            "(which covered under 95% and would be flagged). Precincts below the threshold are listed as split between "
            f"the districts covering them (\"depends on your address\"): {flag_text}."))
    if len(local) != len({(spec[0], spec[1]) for spec in local.values()}):
        raise SystemExit("two local areas share a GEOID")

    write(args.out / "precincts.yaml", f"Each precinct's districts from the {cfg['name']} precinct layer; a nested "
          "list means the precinct is only partly in those areas (city lines).",
          [county_src] + ([scotus_src] if cfg.get("scotus", True) else []) + ([place_src] if census_used else [])
          + ([tceq_src] if tceq_used else []) + extra_src + ([county_area_src] if county_used else []), "precincts",
          precincts, cfg["everywhere"])
    write(args.out / "zips.yaml", "Approximate: a nested list means the ZIP is split between those districts.",
          [zcta_src, zip_county] + ([zip_place] if census_used else []) + ([zip_tceq] if tceq_used else [])
          + ([zip_county_areas] if county_used else []), "zips", zips,
          cfg["everywhere"])
    if local:
        area_out = {"source": (place_src if census_used else county_area_src)["id"], "note": "Generated by src/collectors/states/tx/county_precincts.py; "
                    f"outlines simplified to about {SIMPLIFY} degrees. Unverified.", "areas": {}}
        for slug, feat in sorted(local_feats.items()):
            rs = []
            for ring in rings(feat["geometry"]):
                s = simplify(ring, SIMPLIFY)
                if len(s) >= 4:
                    rs.append([[round(x, 5), round(y, 5)] for x, y in s])
            area_out["areas"][slug] = rs
        (args.out / "area-shapes.json").write_text(json.dumps(area_out, separators=(",", ":")),
                                                   encoding="utf-8", newline="\n")
    (args.out / "precinct-shapes.json").write_text(
        json.dumps(shapes(pct, county_src["id"], pid), separators=(",", ":")), encoding="utf-8", newline="\n")
    split = sum(1 for e in zips.values() if any(isinstance(x, list) for x in e))
    print(f"wrote {len(precincts)} precincts and {len(zips)} ZIP codes ({split} with at least one split)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

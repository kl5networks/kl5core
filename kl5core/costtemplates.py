"""Costing templates: a whole rate plan loaded onto a console costing
profile in one go -- the bands and the zones that put area codes and
exchanges in them. Pure data plus one function that applies it.

The first one, SE-PA, is the owner's instruction card (a Verizon
"Dialing Instructions" card, 2026-10-02): local calls 25 cents for 3
minutes, overtime 5 cents for 2 more minutes; long distance more the
farther away. Rates are the console's: cents, minutes.

What the board can and cannot be told, which shapes every template:

  * An exchange-level zone (NPA + NXX -> band) needs one of eight
    multi-band NPA slots (ExpressNet's own limit); a template uses them
    for the local area codes and for the toll-free codes that are not
    800 (the board's 1-800 class covers 800 alone, and 888/877/866 would
    otherwise be rated as whatever an unlisted code is).
  * A whole-area-code band lives in the single-band table, which only
    fits OLD-format area codes (N0X / N1X -- the board is a 1990s design).
    So the long-distance tiers below name old-format codes; a newer code
    (267, 484, 570, 724, 878...) that is not in a multi-band slot is rated
    like an unlisted one.
  * **An unlisted area code is rated by band 64**, the single-band
    table's default (every code shows 64 on ExpressNet's screen until it
    is set). So the template writes the local rate into band 64 as well
    as band 0, and the distance tiers are bands 65-68 -- the single-band
    table's own range is 64-79, bar 72. A local code needs no listing of
    its own beyond the exchange-grid slot 215 already has.

The exchange grid takes bands 0-79 (protelzones, measured 2026-10-04); these templates use 0 local and 5 free.
"""
from . import xnetcost

# The owner's card. Durations are minutes.
LOCAL = {"initial_rate": 25, "initial_time": 3, "overtime_rate": 5, "overtime_time": 2}

# Old-format area codes (N0X / N1X), by distance from south-east PA.
REGIONAL = [   # band 1: the rest of PA, NJ, DE, MD
    412, 717, 814,                          # PA
    201, 609, 908,                          # NJ
    302,                                    # DE
    301, 410,                               # MD
]
NEARBY = [     # band 2: NY, New England, OH, WV, VA, DC, NC
    212, 315, 516, 518, 607, 716, 718, 914, 917,      # NY
    203, 413, 508, 617, 401, 802, 603, 207,           # CT MA RI VT NH ME
    216, 419, 513, 614,                               # OH
    304, 703, 804, 202,                               # WV VA DC
    704, 910, 919,                                    # NC
]
FAR = [        # band 3: the rest of the lower 48
    205, 501, 602, 209, 213, 310, 408, 415, 510, 619, 707, 714, 805, 818, 909, 916,
    303, 719, 305, 407, 813, 904, 404, 706, 912, 319, 515, 712, 208, 217, 309, 312,
    618, 708, 815, 219, 317, 812, 316, 913, 502, 606, 318, 504, 313, 517, 616, 810,
    906, 218, 507, 612, 314, 417, 816, 601, 406, 308, 402, 702, 505, 701, 405, 918,
    503, 803, 605, 615, 901, 210, 214, 409, 512, 713, 806, 817, 903, 915, 801, 206,
    509, 414, 608, 715, 307,
]
FARTHEST = [   # band 4: AK, HI, Canada, the Caribbean's old code
    907, 808,
    204, 306, 403, 416, 418, 506, 514, 519, 604, 613, 705, 709, 807, 819, 902, 905,
    809,
]
LOCAL_SLOTS = [215]                          # exchange-level zoning kept open for the home code
TOLL_FREE_SLOTS = [888, 877, 866, 855, 844, 833]   # 800 is the board's own class; these need slots
FREE_BAND = 5
DEFAULT_NPA_BAND = 64                        # every unlisted area code; the single-band tiers are 65..68
TIER_BANDS = {1: 65, 2: 66, 3: 67, 4: 68}

TEMPLATES = {
    "se-pa": {
        "name": "SE PA card",
        "summary": "25c / 3 min local, 5c per 2 min over (bands 0 and 64); long distance 50c, 75c, $1.00, $1.50 "
                   "per 3 min by distance (bands 65-68); toll-free free. From the Verizon instruction card.",
        "desc": "SE PA 25C LOCAL",
        "bands": {
            0: LOCAL,
            DEFAULT_NPA_BAND: LOCAL,
            TIER_BANDS[1]: {"initial_rate": 50, "initial_time": 3, "overtime_rate": 10, "overtime_time": 1},
            TIER_BANDS[2]: {"initial_rate": 75, "initial_time": 3, "overtime_rate": 15, "overtime_time": 1},
            TIER_BANDS[3]: {"initial_rate": 100, "initial_time": 3, "overtime_rate": 20, "overtime_time": 1},
            TIER_BANDS[4]: {"initial_rate": 150, "initial_time": 3, "overtime_rate": 25, "overtime_time": 1},
            FREE_BAND: xnetcost.free_band(),
            # the N11 classes the card prices: 211/311/511/611/711 no charge, 411 "charges may apply"
            80: xnetcost.free_band(), 81: xnetcost.free_band(), 82: xnetcost.coin_band(50),
            83: xnetcost.free_band(), 84: xnetcost.free_band(), 85: xnetcost.free_band(),
        },
        "zones": {
            "multi": {**{npa: {} for npa in LOCAL_SLOTS},
                      **{npa: {nxx: FREE_BAND for nxx in range(200, 1000)} for npa in TOLL_FREE_SLOTS}},
            "single": {**{npa: TIER_BANDS[1] for npa in REGIONAL}, **{npa: TIER_BANDS[2] for npa in NEARBY},
                       **{npa: TIER_BANDS[3] for npa in FAR}, **{npa: TIER_BANDS[4] for npa in FARTHEST}},
        },
    },
}


class CostTemplateError(Exception):
    pass


def get(key):
    t = TEMPLATES.get(str(key or "").lower())
    if t is None:
        raise CostTemplateError("no costing template %r; have %s" % (key, ", ".join(sorted(TEMPLATES))))
    return t


def listing():
    return [{"key": k, "name": t["name"], "summary": t["summary"]} for k, t in sorted(TEMPLATES.items())]


def apply(record, key):
    """The costing record with the template's bands and description
    written in (everything else kept), and the zones to store with it."""
    t = get(key)
    rec = xnetcost.write(record, {"desc": t["desc"], "bands": t["bands"]})
    return rec, t["zones"]

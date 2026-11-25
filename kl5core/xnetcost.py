"""ExpressNet's call costing record: the parts the console edits.

`PARAM/CALLCOST.DAT` holds one 36,781-byte c-tree record per costing
record (the thing a site names at offset 86 of its own record). Its
editor is a tree of menus (routing variables, cost bands, NPA tables,
surcharges, routes, restricted numbers, description), and on 2026-09-30
the pieces that decide what a payphone charges were mapped by scripting
those screens and diffing the file:

  * 138 cost bands, 8 bytes each. Bands 0-79 are the operator's (assigned
    to area codes and exchanges); 80-137 are fixed classes (211..911,
    1-800, 0-, international, card groups, *1-*7). The program keeps TWO
    band tables: one for the whole 0-137 range at 926 (the DD/EXP layout,
    contiguous) and a second copy of 0-79 at 10854 (the DA/DD layout the
    form edits for those bands). The console writes both copies of a user
    band so either phone type reads the same rate; a special band lives
    only in the first table.
  * 50 restricted numbers, 8 bytes each at 6756, packed BCD.
  * 12 speed-dial numbers (*81..*89, *80, *91, *92), 6 bytes each at 106.
  * The description at 14 and the date at 56.

Rates are in nickels (0.25 is 5). A band is free when its initial rate is
0 and its overtime period is at least 1 -- an overtime period of 0
disables the band and every exchange assigned to it. Which exchange is in
which band (the NPA tables) is NOT edited here: ExpressNet keeps those in
a packed structure the program rewrites whole on every change, so the
console drives that screen by keystrokes instead.
"""

RECLEN = 36781
F_NAME = (1, 12)
F_DESC = (14, 40)
F_DATE = (55, 8)         # MM/DD/YY as text

BANDS = 138
USER_BANDS = 80          # 0..79 are the operator's; 80..137 are fixed classes
BAND_TABLE = 926         # all 138 bands, 8 bytes each (DD/EXP layout)
BAND_TABLE_DA = 10854    # bands 0..79 again (DA/DD layout, what the form edits)
BAND_LEN = 8

RESTRICTED_AT, RESTRICTED_N, RESTRICTED_LEN = 6756, 50, 8   # 16 digits packed
SPEED_AT, SPEED_N, SPEED_LEN = 106, 12, 6                   # 11 digits, leading 0 nibble
SPEED_KEYS = ["*81", "*82", "*83", "*84", "*85", "*86", "*87", "*88", "*89", "*80", "*91", "*92"]

# The fixed classes, as the form labels them (bands 80..137).
SPECIAL_LABELS = {80: "211", 81: "311", 82: "411", 83: "511", 84: "611", 85: "711",
                  122: "Card Group 1", 123: "Card Group 2", 124: "Card Group 3", 125: "Card Group 4",
                  126: "Card Group 5", 127: "Card Group 6", 134: "Card Group 13", 135: "Card Group 14",
                  136: "Card Group 15", 137: "*1 - *7"}


class XnetCostError(Exception):
    pass


def _text(rec, off, width):
    return rec[off:off + width].split(b"\0")[0].decode("latin-1").rstrip()


def _pack(digits, length, lead):
    """Packed BCD the way the program writes phone numbers: a 0 digit is A,
    an optional leading 0 nibble, zero-filled to `length` bytes."""
    nib = [0] if lead else []
    nib += [0xa if d == "0" else int(d) for d in digits]
    nib += [0] * (2 * length - len(nib))
    return bytes((nib[k] << 4) | nib[k + 1] for k in range(0, 2 * length, 2))


def _unpack(raw, lead):
    out = []
    for b in raw:
        for n in (b >> 4, b & 0xf):
            out.append(n)
    if lead:
        out = out[1:]
    digits = ""
    for n in out:
        if n == 0:
            break
        digits += "0" if n == 0xa else ("#" if n == 0xf else str(n))
    return digits


def band_at(rec, n):
    return rec[BAND_TABLE + n * BAND_LEN: BAND_TABLE + (n + 1) * BAND_LEN]


def decode_band(raw):
    """One 8-byte band as the form shows it."""
    return {
        "fraud_detect": raw[0] >> 7,
        "discount_table": (raw[0] >> 4) & 7,
        "overtime_time": raw[0] & 0xf,
        "initial_rate": raw[1] * 5,            # cents
        "initial_time": raw[2],
        "overtime_rate": raw[3] * 5,
        "keypad": (raw[4] & 7) + 1,            # the form's 1..8
        "lata": "intra" if raw[4] & 8 else "inter",
        "route": raw[4] >> 4,
        "card_enable": format((raw[5] << 8) | raw[6], "016b"),
        "sac": "%d%d%d" % ((raw[7] >> 4) & 7, raw[7] & 3, (raw[7] >> 7) | (((raw[7] >> 3) & 1) << 1)),
    }


def encode_band(raw, b):
    """Apply a dict of band fields (any subset) to an 8-byte band."""
    raw = bytearray(raw)
    def num(key, lo, hi, step=1):
        if key not in b:
            return None
        try:
            v = int(b[key])
        except (TypeError, ValueError):
            raise XnetCostError("%s must be a number" % key)
        if not lo <= v <= hi or v % step:
            raise XnetCostError("%s must be %d to %d%s" % (key, lo, hi, " in steps of %d" % step if step > 1 else ""))
        return v
    v = num("fraud_detect", 0, 1)
    if v is not None:
        raw[0] = (raw[0] & 0x7f) | (v << 7)
    v = num("discount_table", 0, 7)
    if v is not None:
        raw[0] = (raw[0] & 0x8f) | (v << 4)
    v = num("overtime_time", 0, 15)
    if v is not None:
        raw[0] = (raw[0] & 0xf0) | v
    v = num("initial_rate", 0, 1275, 5)
    if v is not None:
        raw[1] = v // 5
    v = num("initial_time", 0, 255)
    if v is not None:
        raw[2] = v
    v = num("overtime_rate", 0, 1275, 5)
    if v is not None:
        raw[3] = v // 5
    v = num("keypad", 1, 8)
    if v is not None:
        raw[4] = (raw[4] & 0xf8) | (v - 1)
    if "lata" in b:
        if b["lata"] not in ("intra", "inter"):
            raise XnetCostError("lata is intra or inter")
        raw[4] = (raw[4] & 0xf7) | (8 if b["lata"] == "intra" else 0)
    v = num("route", 0, 15)
    if v is not None:
        raw[4] = (raw[4] & 0x0f) | (v << 4)
    if "card_enable" in b:
        s = str(b["card_enable"])
        if len(s) != 16 or set(s) - set("01"):
            raise XnetCostError("card_enable is 16 bits of 0/1")
        n = int(s, 2)
        raw[5], raw[6] = n >> 8, n & 0xff
    if "sac" in b:
        # three digits: SAC 0-7 in bits 4-6, CI 0-3 in bits 0-1, AD 0-3 split over bit 7 (its 1) and bit 3 (its 2)
        s = str(b["sac"])
        if len(s) != 3 or not s.isdigit() or int(s[0]) > 7 or int(s[1]) > 3 or int(s[2]) > 3:
            raise XnetCostError("sac is three digits: SAC 0-7, CI 0-3, AD 0-3")
        ad = int(s[2])
        raw[7] = (int(s[0]) << 4) | int(s[1]) | ((ad & 1) << 7) | ((ad >> 1) << 3)
    v = num("answer_detect", 0, 3)
    if v is not None:
        # AD alone, SAC and CI kept: 0 the "educated guess" (DEFAULT B1's,
        # which a loop reversal alone does not satisfy), 1 loop reversal,
        # 3 none
        raw[7] = (raw[7] & 0x77) | ((v & 1) << 7) | ((v >> 1) << 3)
    return bytes(raw)


def read(rec):
    """The costing record's editable parts as a dict."""
    if len(rec) != RECLEN:
        raise XnetCostError("a costing record is %d bytes, not %d" % (RECLEN, len(rec)))
    bands = []
    for n in range(BANDS):
        d = decode_band(band_at(rec, n))
        d["band"] = n
        d["label"] = SPECIAL_LABELS.get(n, "" if n < USER_BANDS else "special")
        bands.append(d)
    restricted = [_unpack(rec[RESTRICTED_AT + k * RESTRICTED_LEN: RESTRICTED_AT + (k + 1) * RESTRICTED_LEN], lead=False)
                  for k in range(RESTRICTED_N)]
    speed = {SPEED_KEYS[k]: _unpack(rec[SPEED_AT + k * SPEED_LEN: SPEED_AT + (k + 1) * SPEED_LEN], lead=True)
             for k in range(SPEED_N)}
    return {"name": _text(rec, *F_NAME), "desc": _text(rec, *F_DESC), "date": _text(rec, *F_DATE),
            "bands": bands, "restricted": restricted, "speed": speed}


def write(rec, values):
    """A copy of `rec` with `values` applied: desc, date, bands (a dict of
    band number -> fields, or a list of such dicts carrying 'band'),
    restricted (a list of up to 50 numbers), speed (key -> number)."""
    rec = bytearray(rec)
    if len(rec) != RECLEN:
        raise XnetCostError("a costing record is %d bytes, not %d" % (RECLEN, len(rec)))
    for key, value in values.items():
        if key == "desc":
            text = str(value).upper()[:F_DESC[1]]
            rec[F_DESC[0]:F_DESC[0] + F_DESC[1]] = text.ljust(F_DESC[1]).encode("latin-1")
        elif key == "date":
            s = str(value)
            if len(s) != 8 or s[2] != "/" or s[5] != "/" or not (s[:2] + s[3:5] + s[6:]).isdigit():
                raise XnetCostError("date is MM/DD/YY")
            rec[F_DATE[0]:F_DATE[0] + 8] = s.encode()
        elif key == "bands":
            items = value.items() if isinstance(value, dict) else ((b["band"], b) for b in value)
            for n, fields in items:
                n = int(n)
                if not 0 <= n < BANDS:
                    raise XnetCostError("band %d does not exist" % n)
                raw = encode_band(band_at(rec, n), fields)
                rec[BAND_TABLE + n * BAND_LEN: BAND_TABLE + (n + 1) * BAND_LEN] = raw
                if n < USER_BANDS:
                    rec[BAND_TABLE_DA + n * BAND_LEN: BAND_TABLE_DA + (n + 1) * BAND_LEN] = raw
        elif key == "restricted":
            nums = [("".join(c for c in str(v) if c.isdigit())) for v in value]
            nums = [v for v in nums if v]
            if len(nums) > RESTRICTED_N:
                raise XnetCostError("at most %d restricted numbers" % RESTRICTED_N)
            for k in range(RESTRICTED_N):
                v = nums[k] if k < len(nums) else ""
                if len(v) > 16:
                    raise XnetCostError("a restricted number is up to 16 digits")
                rec[RESTRICTED_AT + k * RESTRICTED_LEN: RESTRICTED_AT + (k + 1) * RESTRICTED_LEN] = _pack(v, RESTRICTED_LEN, lead=False)
        elif key == "speed":
            for sk, v in value.items():
                if sk not in SPEED_KEYS:
                    raise XnetCostError("no speed dial %s" % sk)
                v = "".join(c for c in str(v) if c.isdigit())
                if len(v) > 11:
                    raise XnetCostError("a speed dial is up to 11 digits")
                k = SPEED_KEYS.index(sk)
                rec[SPEED_AT + k * SPEED_LEN: SPEED_AT + (k + 1) * SPEED_LEN] = _pack(v, SPEED_LEN, lead=True)
        else:
            raise XnetCostError("no such costing field %r" % key)
    return bytes(rec)


def free_band():
    """The fields that make a band free and unlimited."""
    return {"initial_rate": 0, "overtime_rate": 0, "initial_time": 255, "overtime_time": 1}


def disabled_band():
    """The fields that switch a band OFF: an overtime period of 0 disables
    the band and every number rated in it. On the reference firmware
    (emulator, 2026-10-05) a call rated in a disabled band is refused before
    it is dialled past its first digits -- DEFAULT B1 ships its
    international band (121) exactly like this."""
    return {"initial_rate": 0, "overtime_rate": 0, "initial_time": 0, "overtime_time": 0}


def coin_band(cents):
    """The fields for a flat coin rate, unlimited time (the walkthrough's
    '25 cent unlimited local': initial 0.25, initial time 255, overtime
    0.00 every 1)."""
    return {"initial_rate": int(cents), "overtime_rate": 0, "initial_time": 255, "overtime_time": 1}

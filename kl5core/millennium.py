"""The Millennium's tables, rendered for a download: CARD, RATE and the
call-screening list, and which of them a given phone takes.

This module renders tables that are downloaded **to** the terminal.

## Why this exists

A Millennium decides for itself whether it will even look at a card: it
matches the swiped number against a table in its own memory, and only then
asks the manager. So a KL5 card that is not in that table never produces a
call at all — no authorisation request, nothing in `TAUTH`, no way to tell
from the manager that anybody tried.

The table is downloadable, which is the whole point of having a manager:
we do not modify the phone, we choose what it is told. `mm_manager` sends
whatever is in `tables/<terminal_id>/mm_table_86.bin` (MTR 2.x) or
`mm_table_16.bin` (MTR 1.x), so authoring the table means writing that
file.

## Layout, taken from the compiler rather than from counting

`card_entry_t` is **36 bytes**, 32 of them, 1152 in all; MTR 1.x is 33
bytes and 20 entries, 660 in all. Offsets below are `offsetof` output from
a probe built against `mm_card.h`, not arithmetic done by hand -- the
Millennium's own headers have at least one stale size comment in them
(MTR headers), so structure sizes on this road get
asked of a compiler.

## What makes a card ours

Four bits of `vfy_flags`, and two of them read backwards:

- **`NCCVAL`** -- *"validation must be done by the Millennium Manager
  before the terminal will permit a call to be made."* Set. This is the
  bit that turns a card into a question the manager answers.
- **`MOD10`** -- the Luhn check, done by the terminal. **Cleared**, because
  KL5 card numbers are whatever is printed on the stock we already have;
  only about one in ten random numbers passes Luhn.
- **`ACCS_ROUTING`** -- 0 routes to the Millennium Manager, 1 to ACCS.
  **Zero**, and it is zero when the bit is *clear*, which is the first of
  the two that read backwards.
- **`CALLING_CARD_IND`** -- the second: *"Indicates whether this type of
  card is NOT a calling card. 0 Yes - Calling Card, 1 No."* `mm_card.c`
  prints it XORed for exactly that reason.

## Unused entries are marked by `standard_cd`, not by being blank

`mm_card.c` skips any entry whose `standard_cd` is 0 (`undefined`), so a
short table is padded to full size rather than truncated -- the terminal
reads a fixed-size table and a short file would be read as whatever
followed it.

**What the padding contains is not canonical.** `mm_manager`'s own shipped
`tables/default/mm_table_86.bin` uses twelve entries, then two of all
zeros, then eighteen whose PAN fields are `0xee` -- all with
`standard_cd == 0`. `0xee` is this phone's "absent" filler for BCD
generally; `mm_accounting.c` handles the same value in a card's expiry
month and year. So a byte-for-byte comparison against a shipped table is
the wrong test, and this module pads with zeros: what has to match is the
*used* entries and how a decoder reads the rest. Both are asserted in
`test_millennium_card.py`, the second by running hharte's own `mm_card`
over the output.
"""
import re

# -- the wire layout, from `offsetof` against mm_card.h ---------------------

CARD_ENTRY_LEN = 36
CARD_ENTRY_LEN_MTR1 = 33
CARD_MAX = 32
CARD_MAX_MTR1 = 20
TABLE_LEN = CARD_ENTRY_LEN * CARD_MAX              # 1152
TABLE_LEN_MTR1 = CARD_ENTRY_LEN_MTR1 * CARD_MAX_MTR1   # 660

# mm_manager writes these names; so does this module.
TABLE_FILE = "mm_table_86.bin"          # DLOG_MT_CARD_TABLE_EXP, MTR 2.x
TABLE_FILE_MTR1 = "mm_table_16.bin"     # DLOG_MT_CARD_TABLE, MTR 1.x

# std_card_type, in declaration order -- the value IS the index.
STANDARDS = ("undefined", "mod10", "ansi", "aba", "cba", "boc", "ansi59",
             "ccitt", "pinoff", "hello", "smcard", "reserved", "scgpm416",
             "sc_pcos", "sc_mpcos", "proton")

# vfy_flags, from mm_card.h.
VF_MOD10 = 1 << 0
VF_NCCVAL = 1 << 1
VF_NOT_CALLING_CARD = 1 << 2        # inverted: 0 means it IS a calling card
VF_IMMEDIATE_AUTH = 1 << 3
VF_SERVICE_CD_VALIDATION = 1 << 4
VF_PROMPT_FOR_PIN = 1 << 5
VF_PROMPT_FOR_TELCO_PIN = 1 << 6
VF_ACCS_ROUTING = 1 << 7            # inverted: 0 routes to our manager

PAN_DIGITS = 6                      # pan_start/pan_end are 3 bytes of BCD
REF_NUM_MAX = 62                    # "Range: 0-62"

_DIGITS_RE = re.compile(r"^[0-9]+$")


class CardTableError(ValueError):
    """A card definition that cannot be rendered. Raised at
    save time, never while a terminal is waiting for its download."""


def pack_bcd(digits, nbytes):
    """Digits to packed BCD, high nibble first.

    High nibble first is not a style choice -- getting it backwards makes
    the terminal read a different number than the one printed on the card,
    and the failure looks like the card being rejected. The same trap cost
    a session of testing once.
    """
    text = str(digits or "")
    if not _DIGITS_RE.match(text):
        raise CardTableError("%r is not digits" % (digits,))
    if len(text) != nbytes * 2:
        raise CardTableError("%r must be exactly %d digits" % (digits, nbytes * 2))
    return bytes((int(text[i], 10) << 4) | int(text[i + 1], 10)
                 for i in range(0, len(text), 2))


def unpack_bcd(data):
    return "".join("%x" % (b >> 4) + "%x" % (b & 0x0F) for b in data)


def vfy_flags(validate_with_manager=True, mod10=False, prompt_for_pin=False,
          immediate_auth=False, service_code_validation=False,
          prompt_for_telco_pin=False, calling_card=True,
          route_to_accs=False):
    """The byte, from settings written the way a person would say them.

    Both inverted bits are handled here rather than at every call site,
    so nobody has to remember which two they are: `calling_card=True`
    CLEARS its bit, and `route_to_accs=False` clears its own.
    """
    flags = 0
    if mod10:
        flags |= VF_MOD10
    if validate_with_manager:
        flags |= VF_NCCVAL
    if not calling_card:                 # inverted, see the docstring
        flags |= VF_NOT_CALLING_CARD
    if immediate_auth:
        flags |= VF_IMMEDIATE_AUTH
    if service_code_validation:
        flags |= VF_SERVICE_CD_VALIDATION
    if prompt_for_pin:
        flags |= VF_PROMPT_FOR_PIN
    if prompt_for_telco_pin:
        flags |= VF_PROMPT_FOR_TELCO_PIN
    if route_to_accs:                    # inverted, see the docstring
        flags |= VF_ACCS_ROUTING
    return flags


def describe_flags(byte):
    """The set bits, named, for showing a table back to an operator."""
    names = []
    for bit, name in ((VF_MOD10, "mod10"), (VF_NCCVAL, "nccval"),
                      (VF_NOT_CALLING_CARD, "not-calling-card"),
                      (VF_IMMEDIATE_AUTH, "immediate-auth"),
                      (VF_SERVICE_CD_VALIDATION, "service-code-validation"),
                      (VF_PROMPT_FOR_PIN, "prompt-pin"),
                      (VF_PROMPT_FOR_TELCO_PIN, "prompt-telco-pin"),
                      (VF_ACCS_ROUTING, "route-to-accs")):
        if byte & bit:
            names.append(name)
    return names


# The defaults a KL5 card wants. Separated from the renderer so a test can
# assert them, and so the reasoning above is checkable rather than folded
# into a function body.
KL5_CARD = {
    "standard": "aba",
    "validate_with_manager": True,      # ask us, and do not call without an answer
    "mod10": False,                     # our numbers are not Luhn-valid
    "prompt_for_pin": False,            # the stock has PINs we do not use
    "immediate_auth": False,            # wait for the dialled number, so the
                                        # auth request carries it
    "route_to_accs": False,             # to the Millennium Manager, not ACCS
    "calling_card": True,
}


def card(name, pan_start, pan_end=None, ref_num=0, carrier_ref=0,
         standard=None, lang_code=0, control_info=0, bank_info=0,
         p_exp_date=0, p_init_date=0, p_disc_data=0, **flags):
    """One CARD entry, as a dict, with the KL5 defaults applied.

    `pan_start`/`pan_end` are **six digits** -- the range the terminal
    matches a swipe against, which is the card's leading digits and not the
    whole number. A single issuer range is start == end.
    """
    settings = dict(KL5_CARD)
    settings.update(flags)
    if standard:
        settings["standard"] = standard
    if settings["standard"] not in STANDARDS or settings["standard"] == "undefined":
        raise CardTableError(
            "standard %r is not one of %s" % (settings["standard"],
                                              ", ".join(STANDARDS[1:])))
    if not 0 <= int(ref_num) <= REF_NUM_MAX:
        raise CardTableError("ref_num %r is outside 0..%d" % (ref_num, REF_NUM_MAX))
    standard_name = settings.pop("standard")
    return {
        "name": str(name or ""),
        "pan_start": str(pan_start),
        "pan_end": str(pan_end if pan_end is not None else pan_start),
        "standard": standard_name,
        "vfy_flags": vfy_flags(**settings),
        "ref_num": int(ref_num),
        "carrier_ref": int(carrier_ref),
        "lang_code": int(lang_code),
        "control_info": int(control_info),
        "bank_info": int(bank_info),
        "p_exp_date": int(p_exp_date),
        "p_init_date": int(p_init_date),
        "p_disc_data": int(p_disc_data),
    }


def _entry_bytes(c, mtr1=False):
    out = bytearray()
    out += pack_bcd(c["pan_start"], 3)
    out += pack_bcd(c["pan_end"], 3)
    out.append(STANDARDS.index(c["standard"]))
    out.append(c["vfy_flags"] & 0xFF)
    out.append(c["p_exp_date"] & 0xFF)
    out.append(c["p_init_date"] & 0xFF)
    out.append(c["p_disc_data"] & 0xFF)
    # The 20-byte service_code union. We author zeros -- a KL5 card needs
    # no service codes -- but a table PARSED from an existing file carries
    # the real ones through, because the documented way to build one is to
    # start from mm_manager's shipped table and change what you need. A
    # renderer that zeroed this would quietly strip every service code out
    # of the entries it was not even touching, and the phone would go on
    # working until it met a card that needed one.
    svc = c.get("svc_code") or bytes(20)
    if len(svc) != 20:
        raise CardTableError("svc_code must be 20 bytes, got %d" % len(svc))
    out += bytes(svc)
    out.append(c["ref_num"] & 0xFF)
    out.append(c["carrier_ref"] & 0xFF)
    if not mtr1:
        out.append(c["control_info"] & 0xFF)
        out.append(c["bank_info"] & 0xFF)
        out.append(c["lang_code"] & 0xFF)
    want = CARD_ENTRY_LEN_MTR1 if mtr1 else CARD_ENTRY_LEN
    if len(out) != want:
        raise CardTableError("entry rendered %d bytes, expected %d"
                             % (len(out), want))
    return bytes(out)


def render_card_table(cards, mtr1=False):
    """The table file's bytes: every entry, then zeros to the full size.

    Padded rather than truncated because `mm_card.c` decides an entry is
    unused by `standard_cd == 0`, and the terminal reads a fixed-size
    table -- a short file would be read as whatever followed it.
    """
    limit = CARD_MAX_MTR1 if mtr1 else CARD_MAX
    total = TABLE_LEN_MTR1 if mtr1 else TABLE_LEN
    cards = list(cards or [])
    if len(cards) > limit:
        raise CardTableError(
            "%d cards, but the table holds %d on %s"
            % (len(cards), limit, "MTR 1.x" if mtr1 else "MTR 2.x"))
    # ref_num 0 repeats legitimately: mm_card.h calls it "the unique
    # identifier for the card", but every one of the twelve entries in
    # mm_manager's own shipped tables/default/mm_table_86.bin is 0. So 0
    # is the unset value, and a rule taken from that comment would have
    # rejected Nortel's table. A repeated NON-ZERO ref is still refused --
    # the terminal quotes it back as card_ref_num in the authorisation
    # request, so it is how a manager tells which entry matched, and two
    # entries sharing one makes that answer meaningless.
    seen = set()
    for c in cards:
        ref = c["ref_num"]
        if ref and ref in seen:
            raise CardTableError(
                "ref_num %d used twice -- the terminal quotes it back as "
                "card_ref_num, so a repeat makes the match ambiguous "
                "(0 means unset and may repeat)" % ref)
        seen.add(ref)
    body = b"".join(_entry_bytes(c, mtr1=mtr1) for c in cards)
    return body + bytes(total - len(body))


def parse_card_table(data, mtr1=False):
    """The inverse, so an existing table can be read before it is replaced.

    `mm_manager` ships working tables under `tables/default/`, and starting
    from one beats starting from nothing: this is how you see what is in
    it, and how a round trip can be tested.
    """
    size = CARD_ENTRY_LEN_MTR1 if mtr1 else CARD_ENTRY_LEN
    total = TABLE_LEN_MTR1 if mtr1 else TABLE_LEN
    if len(data) != total:
        raise CardTableError("table is %d bytes, expected %d" % (len(data), total))
    out = []
    for i in range(0, total, size):
        raw = data[i:i + size]
        std = raw[6]
        if std == 0:                       # unused, as mm_card.c reads it
            continue
        entry = {
            "name": "",
            "pan_start": unpack_bcd(raw[0:3]),
            "pan_end": unpack_bcd(raw[3:6]),
            "standard": STANDARDS[std] if std < len(STANDARDS) else "undefined",
            "vfy_flags": raw[7],
            "p_exp_date": raw[8],
            "p_init_date": raw[9],
            "p_disc_data": raw[10],
            "svc_code": bytes(raw[11:31]),
            "ref_num": raw[31],
            "carrier_ref": raw[32],
            "control_info": 0, "bank_info": 0, "lang_code": 0,
        }
        if not mtr1:
            entry["control_info"] = raw[33]
            entry["bank_info"] = raw[34]
            entry["lang_code"] = raw[35]
        out.append(entry)
    return out


# ===========================================================================
# Which tables a phone takes -- by the firmware family its terminal type
# names. Upstream's own lists (mm_manager.c table_list_mtr*), read, not
# guessed: an MTR 1.7 set takes the 20-entry CARD table (0x16) and the
# 60-entry universal call-screening list (0x18); 1.9 to 1.13 keep that CARD
# table and move to the 180-entry list (0x5c); 1.20 and 2.x take the
# 32-entry expanded CARD table (0x86) with the same list. RATE (0x49) is
# common to all of them. Writing a table the phone never asks for is not an
# error anywhere -- it simply never arrives -- so getting this wrong would
# look like an edit the phone ignored.
# ===========================================================================

# term_type -> family, from mm_util.c term_type_mtr[] at the pinned commit
# (2a23e8c). A type upstream maps to MTR_UNKNOWN is absent.
_TYPES_MTR17 = (8, 9, 11, 12, 13, 14, 15, 16, 19, 21, 23, 24, 25, 26, 32, 55)
_TYPES_MTR19 = (6, 10, 27, 28, 29, 30, 31, 33, 36, 37, 42, 43, 44, 45, 46,
                47, 48, 49)
_TYPES_MTR2 = (1, 2, 3, 5, 7, 17, 18, 20, 22, 34, 35, 38, 39, 40, 50, 51,
               52, 53, 54, 56, 57, 58, 59, 60)

# The idle display (ADVERT_PROMPTS, 0x1d) and the speed-dial buttons
# (REP_DIAL_LIST, 0x37) are in every family's list, at the same id.
FAMILY_TABLES = {
    "mtr1.7": {"cards": 0x16, "screening": 0x18, "rates": 0x49,
               "display": 0x1d, "speed": 0x37},
    "mtr1.9": {"cards": 0x16, "screening": 0x5c, "rates": 0x49,
               "display": 0x1d, "speed": 0x37},
    "mtr2":   {"cards": 0x86, "screening": 0x5c, "rates": 0x49,
               "display": 0x1d, "speed": 0x37},
}
FAMILY_LABELS = {"mtr1.7": "MTR 1.6/1.7", "mtr1.9": "MTR 1.9-1.13",
                 "mtr2": "MTR 1.20/2.x"}


def family_for_type(term_type):
    """'mtr1.7', 'mtr1.9', 'mtr2', or '' for a type nobody has mapped."""
    try:
        t = int(term_type)
    except (TypeError, ValueError):
        return ""
    if t in _TYPES_MTR17:
        return "mtr1.7"
    if t in _TYPES_MTR19:
        return "mtr1.9"
    if t in _TYPES_MTR2:
        return "mtr2"
    return ""


def table_file(table_id):
    """mm_manager's own name for a table's file."""
    return "mm_table_%02x.bin" % int(table_id)


# ===========================================================================
# The download itself, as mm_manager sends it: its lists (mm_manager.c
# table_list_mtr17 / _mtr19 / _mtr_2x at the pinned commit), in its order,
# and the tables it builds rather than reads from a file. A listed table
# with no file in the phone's folder or the defaults is skipped by the
# manager, so the live view counts only what will actually go
# (`download_plan`). The 2.x list stands for "mtr2" here; a 1.20 lacks its
# 1.3-only tables, and those are never in the defaults anyway.
# ===========================================================================

_LCD_17 = tuple(range(0x4a, 0x52)) + (0x5a, 0x5b)
TABLE_LISTS = {
    "mtr1.7": (0x15, 0x16, 0x17, 0x18, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x21,
               0x22, 0x23, 0x32, 0x37, 0x3a, 0x3e, 0x49) + _LCD_17 + (0x0d,),
    "mtr1.9": (0x15, 0x16, 0x17, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x21, 0x22,
               0x23, 0x32, 0x37, 0x3a, 0x3e, 0x49, 0x5c, 0x5d)
              + tuple(range(0x65, 0x6c)) + (0x0d,),
    "mtr2":   (0x15, 0x1a, 0x1d, 0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x32, 0x37, 0x3a,
               0x3e, 0x48, 0x49, 0x55, 0x56, 0x5c, 0x5d, 0x86, 0x87)
              + tuple(range(0x88, 0x96)) + (0x96, 0x97, 0xa0, 0xa1, 0x0d),
}
# Built by the manager itself, so sent whether or not a file exists.
GENERATED_TABLES = frozenset({0x0d, 0x15, 0x1e, 0x1f, 0x20, 0x22, 0x23})

# What each table is, in the owner's words, for the live call-in view.
TABLE_NAMES = {
    0x05: "call record receipt", 0x0d: "end of the download",
    0x11: "request for its records", 0x12: "notice that tables are coming",
    0x14: "time and date",
    0x15: "terminal ID and head-office numbers", 0x16: "card table",
    0x17: "carrier table", 0x18: "call-screening list", 0x1a: "feature options",
    0x1b: "display prompts", 0x1c: "second-language prompts",
    0x1d: "idle display message", 0x1e: "keypad and display settings",
    0x1f: "install settings", 0x20: "communication settings",
    0x21: "modem settings", 0x22: "call statistics settings",
    0x23: "call-in schedule", 0x32: "coin table", 0x37: "speed-dial list",
    0x3a: "service-level table", 0x3e: "numbering plan", 0x48: "spare table",
    0x49: "rates", 0x55: "display prompts", 0x56: "second-language prompts",
    0x5c: "call-screening list", 0x5d: "smart-card settings",
    0x86: "card table", 0x87: "carrier table", 0x96: "area-code rates",
    0x97: "international rates", 0x98: "discount table",
}
for _i, _t in enumerate(_LCD_17):
    TABLE_NAMES[_t] = "local calling table %d" % (_i + 1)
for _i in range(7):
    TABLE_NAMES[0x65 + _i] = "local calling table %d" % (_i + 1)
for _i, _t in enumerate(tuple(range(0x88, 0x96)) + (0xa0, 0xa1)):
    TABLE_NAMES[_t] = "local calling table %d" % (_i + 1)


def table_name(table_id):
    return TABLE_NAMES.get(int(table_id), "table 0x%02x" % int(table_id))


def download_plan(family, has_file):
    """The table IDs a full download sends, in order. `has_file(table_id)`
    says whether the phone's folder or the defaults hold it; an unmapped
    family is MTR 1.7, as mm_manager defaults it."""
    return [t for t in TABLE_LISTS.get(family) or TABLE_LISTS["mtr1.7"]
            if t in GENERATED_TABLES or has_file(t)]


# ===========================================================================
# RATE (table 73, 0x49): what each kind of call costs.
#
# The file is the struct without its leading table-ID byte (mm_manager adds
# that when it sends): 6 bytes of timestamp, a telco ID, 32 spare, then 128
# nine-byte entries -- 1191 bytes. An entry is a type and two
# (period, charge) pairs, little-endian. A period with bit 15 set is
# UNLIMITED -- `FLAG_PERIOD_UNLIMITED` -- and charges are in cents.
#
# Other tables point INTO this one by index (the call-screening list's
# carrier_ref, the local-calling tables), so an entry is never moved or
# renumbered here: it is edited where it is, and the index is its identity.
# ===========================================================================

RATE_TABLE_ID = 0x49
RATE_HEADER_LEN = 6 + 1 + 32
RATE_ENTRY_LEN = 9
RATE_MAX = 128
RATE_TABLE_LEN = RATE_HEADER_LEN + RATE_ENTRY_LEN * RATE_MAX   # 1191
PERIOD_UNLIMITED = 1 << 15
# Unlimited is WRITTEN as 0xFFFF, which is what upstream's own tables carry.
# Only bit 15 is tested when reading, but a table rendered with 0x8000 would
# differ from the one the phone already has in every unlimited entry, and a
# diff that noisy hides the one change somebody actually made.
PERIOD_UNLIMITED_WRITTEN = 0xFFFF
PERIOD_MAX = PERIOD_UNLIMITED - 1
CHARGE_MAX = 0xFFFF

# rate_type_t, mm_manager.h. The value is the index. 3 is documented as
# "not used in RATE table" and still appears in upstream's own default, so
# it is kept rather than refused.
RATE_TYPES = ("mm_intra_lata", "lms_rate_local", "fixed_charge_local",
              "not_available", "invalid_npa_nxx", "toll_intra_lata",
              "toll_inter_lata", "mm_inter_lata", "mm_local",
              "mm_international")
RATE_TYPE_LABELS = {
    "mm_intra_lata": "manager-rated intraLATA",
    "lms_rate_local": "local, measured (LMS)",
    "fixed_charge_local": "local, fixed charge",
    "not_available": "not available",
    "invalid_npa_nxx": "invalid NPA-NXX",
    "toll_intra_lata": "toll intraLATA",
    "toll_inter_lata": "toll interLATA",
    "mm_inter_lata": "manager-rated interLATA",
    "mm_local": "manager-rated local",
    "mm_international": "international",
}


class RateTableError(ValueError):
    """A rate table that cannot be rendered, raised at save time."""


def rate_type_name(value):
    """A type's name, or `0x0a` for one upstream's enum does not name --
    its own default carries two such, and they are kept, not guessed at."""
    value = int(value)
    return RATE_TYPES[value] if value < len(RATE_TYPES) else "0x%02x" % value


def _rate_type_value(name):
    if isinstance(name, int):
        value = name
    elif name in RATE_TYPES:
        value = RATE_TYPES.index(name)
    else:
        try:
            value = int(str(name), 0)
        except ValueError:
            raise RateTableError("unknown rate type %r" % (name,))
    if not 0 <= value <= 0xFF:
        raise RateTableError("rate type %r is not a byte" % (name,))
    return value


def _period_in(raw):
    return None if raw & PERIOD_UNLIMITED else raw


def _period_out(value, what):
    if value is None or value == "" or value == "unlimited":
        return PERIOD_UNLIMITED_WRITTEN
    value = int(value)
    if not 0 <= value <= PERIOD_MAX:
        raise RateTableError("%s %r is outside 0..%d seconds" % (what, value, PERIOD_MAX))
    return value


def _charge_out(value, what):
    value = int(value or 0)
    if not 0 <= value <= CHARGE_MAX:
        raise RateTableError("%s %r cents is outside 0..%d" % (what, value, CHARGE_MAX))
    return value


def parse_rate_table(data):
    """{"header": bytes, "entries": [128 dicts]} -- every entry, used or not,
    because the index is what other tables refer to."""
    if len(data) != RATE_TABLE_LEN:
        raise RateTableError("rate table is %d bytes, expected %d"
                             % (len(data), RATE_TABLE_LEN))
    entries = []
    for i in range(RATE_MAX):
        off = RATE_HEADER_LEN + i * RATE_ENTRY_LEN
        raw = data[off:off + RATE_ENTRY_LEN]
        ip = int.from_bytes(raw[1:3], "little")
        ic = int.from_bytes(raw[3:5], "little")
        ap = int.from_bytes(raw[5:7], "little")
        ac = int.from_bytes(raw[7:9], "little")
        entries.append({
            "index": i,
            "used": any(raw),
            "type": rate_type_name(raw[0]),
            "initial_period": _period_in(ip),
            "initial_charge": ic,
            "additional_period": _period_in(ap),
            "additional_charge": ac,
        })
    return {"header": bytes(data[:RATE_HEADER_LEN]), "entries": entries}


def render_rate_table(table):
    """The inverse: the same 1191 bytes for a table that was not changed.

    An entry marked unused is written as zeros whatever its fields say, so
    clearing one from a form cannot leave half an entry behind.
    """
    header = bytes(table.get("header") or bytes(RATE_HEADER_LEN))
    if len(header) != RATE_HEADER_LEN:
        raise RateTableError("rate header is %d bytes, expected %d"
                             % (len(header), RATE_HEADER_LEN))
    entries = list(table.get("entries") or [])
    if len(entries) != RATE_MAX:
        raise RateTableError("%d rate entries, the table has exactly %d"
                             % (len(entries), RATE_MAX))
    out = bytearray(header)
    for i, e in enumerate(entries):
        if not e.get("used", True):
            out += bytes(RATE_ENTRY_LEN)
            continue
        what = "entry %d" % i
        out.append(_rate_type_value(e.get("type", 0)))
        out += _period_out(e.get("initial_period"), what + " initial period").to_bytes(2, "little")
        out += _charge_out(e.get("initial_charge"), what + " initial charge").to_bytes(2, "little")
        out += _period_out(e.get("additional_period"), what + " additional period").to_bytes(2, "little")
        out += _charge_out(e.get("additional_charge"), what + " additional charge").to_bytes(2, "little")
    return bytes(out)


# ===========================================================================
# The call-screening list: numbers that are free, blocked, or priced by a
# pointer into RATE. MTR 1.7 takes the 60-entry universal list (0x18, 12
# bytes an entry); 1.9 and later the 180-entry list (0x5c, 17 bytes, a
# class byte and three spare after the number). Entries match by PREFIX --
# upstream's own default screens every toll-free call with the one entry
# `1800` -- and `F` is a single-digit wildcard, so `1FFF976` is any NPA's 976.
#
# Numbers are one digit per nibble, high nibble first, with **0 written as
# A** because a zero nibble ends the number. Reading `0` as a terminator
# instead of a digit turns 1-800 into "18", which screens every call that
# starts with 18.
# ===========================================================================

SCREEN_UNIVERSAL_ID = 0x18
SCREEN_LIST_ID = 0x5c
SCREEN_LAYOUTS = {
    # table id: (entry length, number bytes, has class+spare)
    SCREEN_UNIVERSAL_ID: (12, 8, False),
    SCREEN_LIST_ID: (17, 9, True),
}
NO_RATE_REF = 0xFF          # "A value of 255 means there is no reference."

# FREE (Free Call) flags, pp. 2-189, named as mm_callscrn.c names them.
SF_FREE = 1 << 0
SF_ANSWER_SUPERVISION = 1 << 1
SF_KEYPAD_ENABLE = 1 << 2
SF_FAIL_TO_POTS_ONLY = 1 << 3
SF_TRANSMITTER_MUTED = 1 << 4
SF_TIMEOUT_BEFORE_DIALING = 1 << 5
SF_COIN_ONLY = 1 << 6
SF_FEATURE_GROUP_B = 1 << 7
SCREEN_FLAG_NAMES = (
    (SF_FREE, "free"), (SF_ANSWER_SUPERVISION, "answer-supervision"),
    (SF_KEYPAD_ENABLE, "keypad"), (SF_FAIL_TO_POTS_ONLY, "fail-to-pots"),
    (SF_TRANSMITTER_MUTED, "mute"), (SF_TIMEOUT_BEFORE_DIALING, "timeout"),
    (SF_COIN_ONLY, "coin-only"), (SF_FEATURE_GROUP_B, "feature-group-b"))

# CALLTYP, low nibble; the high nibble is carried through untouched.
CALL_TYPES = ("incoming", "unanswered", "abandoned", "local", "intra-lata",
              "inter-lata", "international", "operator", "zero-plus",
              "toll-free", "directory", "denied", "unassigned",
              "unassigned-2", "e-purse", "unknown")

_NIBBLE_FOR = {str(d): d for d in range(1, 10)}
_NIBBLE_FOR.update({"0": 0xA, "B": 0xB, "C": 0xC, "D": 0xD, "E": 0xE, "F": 0xF})
_CHAR_FOR = {v: k for k, v in _NIBBLE_FOR.items()}


class ScreenTableError(ValueError):
    """A screening list that cannot be rendered, raised at save time."""


def pack_screen_number(number, nbytes):
    """'555' -> 55 5 0 ..., '1800' -> 18 aa 00 ...; see the note above."""
    text = str(number or "").strip().upper()
    if not text:
        return bytes(nbytes)
    bad = [c for c in text if c not in _NIBBLE_FOR]
    if bad:
        raise ScreenTableError("%r: only digits, F (any digit) and B-E" % (number,))
    # Always leave room for the terminating zero nibble.
    if len(text) > nbytes * 2 - 1:
        raise ScreenTableError("%r is longer than %d digits" % (number, nbytes * 2 - 1))
    nibbles = [_NIBBLE_FOR[c] for c in text]
    nibbles += [0] * (nbytes * 2 - len(nibbles))
    return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))


def unpack_screen_number(data):
    out = []
    for b in data:
        for n in (b >> 4, b & 0x0F):
            if n == 0:
                return "".join(out)
            out.append(_CHAR_FOR[n])
    return "".join(out)


def describe_screen_flags(byte):
    return [name for bit, name in SCREEN_FLAG_NAMES if byte & bit]


def call_type_name(byte):
    return CALL_TYPES[byte & 0x0F]


def _call_type_value(name, keep_high=0):
    if isinstance(name, int):
        return name & 0xFF
    if name not in CALL_TYPES:
        raise ScreenTableError("unknown call type %r" % (name,))
    return (keep_high & 0xF0) | CALL_TYPES.index(name)


def parse_screen_table(data, table_id):
    """Every entry, in place -- position is kept, as with RATE, because a
    `B` in a number links to another entry by its index."""
    if table_id not in SCREEN_LAYOUTS:
        raise ScreenTableError("table 0x%02x is not a call-screening list" % table_id)
    size, nlen, extended = SCREEN_LAYOUTS[table_id]
    if not data or len(data) % size:
        raise ScreenTableError("screening list is %d bytes, not a whole number of "
                               "%d-byte entries" % (len(data or b""), size))
    entries = []
    for i in range(len(data) // size):
        raw = data[i * size:(i + 1) * size]
        number = unpack_screen_number(raw[4:4 + nlen])
        entries.append({
            "index": i,
            "used": any(raw),
            "flags": raw[0],
            "call_type": raw[1],
            "rate_ref": None if raw[2] == NO_RATE_REF else raw[2],
            "ident2": raw[3],
            "number": number,
            # Carried verbatim: the class byte has no documented meaning
            # we could edit safely, and the spare bytes are spare.
            "tail": bytes(raw[4 + nlen:]) if extended else b"",
        })
    return {"table_id": table_id, "entries": entries}


def render_screen_table(table):
    table_id = table["table_id"]
    size, nlen, extended = SCREEN_LAYOUTS[table_id]
    out = bytearray()
    for e in table["entries"]:
        if not e.get("used", True):
            out += bytes(size)
            continue
        ref = e.get("rate_ref")
        if ref is None or ref == "":
            ref = NO_RATE_REF
        ref = int(ref)
        if not 0 <= ref <= 0xFF:
            raise ScreenTableError("entry %d: rate reference %r is not 0..127 or none"
                                   % (e["index"], ref))
        raw = bytearray()
        raw.append(int(e.get("flags", 0)) & 0xFF)
        raw.append(_call_type_value(e.get("call_type", 0)))
        raw.append(ref)
        raw.append(int(e.get("ident2", 0)) & 0xFF)
        raw += pack_screen_number(e.get("number"), nlen)
        if extended:
            tail = bytes(e.get("tail") or bytes(size - 4 - nlen))
            if len(tail) != size - 4 - nlen:
                raise ScreenTableError("entry %d: class/spare is %d bytes"
                                       % (e["index"], len(tail)))
            raw += tail
        out += raw
    return bytes(out)


def free_number(number, call_type="local", keypad=True):
    """The fields for 'this prefix costs nothing', shaped like upstream's own
    toll-free entries (FREE, answer supervision, keypad) and pointing at no
    rate. For example, `555` makes every 555-XXXX free."""
    flags = SF_FREE | SF_ANSWER_SUPERVISION | (SF_KEYPAD_ENABLE if keypad else 0)
    return {"used": True, "flags": flags,
            "call_type": _call_type_value(call_type), "rate_ref": None,
            "ident2": 0, "number": str(number), "tail": b""}


# ===========================================================================
# The idle display: ADVERT_PROMPTS (table 29, 0x1d). What the phone scrolls
# while nobody is using it -- and until the owner changes it, upstream's own
# sample text ("Local coin calls 50-cents ... Your very own Nortel
# Millennium!"), because that is what an INSTALL from tables/default gave it.
#
# Twenty 24-byte entries: display time in 10 ms units (u16 LE), an attribute
# byte (the low six bits are the effect, 0x40 blanks the display first), a
# spare byte, and twenty characters of text padded with spaces. The display
# is a 2 x 20 VFD; text is centred by hand with spaces, so a line keeps the
# spacing it was given rather than being re-centred here.
# ===========================================================================

ADVERT_TABLE_ID = 0x1d
ADVERT_ENTRY_LEN = 24
ADVERT_MAX = 20
ADVERT_TEXT_LEN = 20
ADVERT_TABLE_LEN = ADVERT_ENTRY_LEN * ADVERT_MAX        # 480
ADVERT_BLANK_BEFORE = 0x40

# mm_manager.h ADMESS_ATTR_*: the effect, low six bits.
ADVERT_EFFECTS = {0: "permanent", 1: "flash (same)", 2: "flash (next)",
                  3: "scroll in", 4: "scroll out", 5: "scroll in and out",
                  17: "new prompt", 18: "flash (same) off", 19: "flash (next) 2nd",
                  20: "flash (next) 1st off", 21: "flash (next) 2nd off"}


class DisplayTableError(ValueError):
    """An idle-display or speed-dial table that cannot be rendered."""


def _vfd_text(text, length, what):
    """Printable ASCII only, at most `length`, padded with spaces. The VFD
    has no idea what UTF-8 is; a curly quote pasted from a phone would be
    two garbage cells."""
    text = str(text if text is not None else "")
    bad = [c for c in text if not (32 <= ord(c) < 127)]
    if bad:
        raise DisplayTableError("%s: %r cannot be shown on the display (plain ASCII only)"
                                % (what, "".join(bad)))
    if len(text) > length:
        raise DisplayTableError("%s is %d characters; the display line holds %d"
                                % (what, len(text), length))
    return text.ljust(length).encode("ascii")


def center(text, length=ADVERT_TEXT_LEN):
    """A line centred the way upstream's own tables centre theirs."""
    return str(text or "").strip().center(length)


def parse_advert_table(data):
    if len(data) != ADVERT_TABLE_LEN:
        raise DisplayTableError("display table is %d bytes, expected %d"
                                % (len(data), ADVERT_TABLE_LEN))
    out = []
    for i in range(ADVERT_MAX):
        raw = data[i * ADVERT_ENTRY_LEN:(i + 1) * ADVERT_ENTRY_LEN]
        attr = raw[2]
        out.append({
            "index": i,
            "time": int.from_bytes(raw[0:2], "little"),      # 10 ms units
            "effect": attr & 0x3F,
            "blank_before": bool(attr & ADVERT_BLANK_BEFORE),
            "attr_high": attr & 0x80,                       # carried, unnamed
            "spare": raw[3],
            "text": raw[4:24].decode("latin-1"),
        })
    return out


def render_advert_table(entries):
    if len(entries) != ADVERT_MAX:
        raise DisplayTableError("%d display entries, the table has exactly %d"
                                % (len(entries), ADVERT_MAX))
    out = bytearray()
    for e in entries:
        what = "display line %d" % e["index"]
        t = int(e.get("time") or 0)
        if not 0 <= t <= 0xFFFF:
            raise DisplayTableError("%s: time %r is outside 0..655.35 s" % (what, t))
        effect = int(e.get("effect") or 0)
        if not 0 <= effect <= 0x3F:
            raise DisplayTableError("%s: effect %r is not an effect" % (what, effect))
        attr = effect | (ADVERT_BLANK_BEFORE if e.get("blank_before") else 0) \
            | (int(e.get("attr_high") or 0) & 0x80)
        out += t.to_bytes(2, "little")
        out.append(attr)
        out.append(int(e.get("spare") or 0) & 0xFF)
        out += _vfd_text(e.get("text"), ADVERT_TEXT_LEN, what)
    return bytes(out)


# ===========================================================================
# The carrier tables: CARRIER (0x17, MTR 1.x) and the expanded one (0x87,
# MTR 2.x). Each entry names a carrier in twenty characters, and the phone
# shows that name under "Thank you for using" after a call it rated -- the
# stock names are upstream's placeholders ("C0 PIC  Inter-LATA"), which a
# Discord tester photographed on his phone (2026-11-21). Only the names are
# touched here; references, card masks, control bytes and timers stay.
#
#   0x17   3 default refs, then 21 entries of 32 bytes; name at +6
#   0x87   9 default refs, then 33 entries of 33 bytes; name at +7
# (files hold the table without its id byte, as every table file does)
# ===========================================================================

CARRIER_LAYOUTS = {0x17: (3, 32, 21, 6), 0x87: (9, 33, 33, 7)}
CARRIER_NAME_LEN = 20


def carrier_names(data, table_id):
    """[name] of every entry, trailing spaces kept."""
    head, size, count, at = CARRIER_LAYOUTS[table_id]
    if len(data) < head + size * count:
        raise DisplayTableError("carrier table %#04x is %d bytes, too short" % (table_id, len(data)))
    return [data[head + i * size + at:head + i * size + at + CARRIER_NAME_LEN].decode("latin-1")
            for i in range(count)]


def rename_carriers(data, table_id, name):
    """The table with every named entry shown as `name`. An entry whose name
    is blank is not a carrier the phone uses and stays blank."""
    head, size, count, at = CARRIER_LAYOUTS[table_id]
    text = _vfd_text(name, CARRIER_NAME_LEN, "carrier name")
    out = bytearray(data)
    for i, old in enumerate(carrier_names(data, table_id)):
        if old.strip():
            o = head + i * size + at
            out[o:o + CARRIER_NAME_LEN] = text
    return bytes(out)


# ===========================================================================
# The speed-dial buttons: REP_DIAL_LIST (table 55, 0x37). Ten 57-byte
# entries -- entries 0-4 are the upper five buttons, which only a 10-button
# set has; 5-9 are the lower (or only) five. Each is a CALLTYP byte (call
# type in the low nibble, payment type in the high), a flags byte, a rate or
# carrier index, the number in the same nibble code as the screening list
# (0 is A), forty characters of prompt -- two display lines -- a customer
# reference and a button-indicator mask, both carried untouched.
#
# A speed-dial button: one press for, say, the pizza place.
# ===========================================================================

SPEED_TABLE_ID = 0x37
SPEED_ENTRY_LEN = 57
SPEED_MAX = 10
SPEED_TABLE_LEN = SPEED_ENTRY_LEN * SPEED_MAX            # 570
SPEED_NUMBER_BYTES = 8
SPEED_PROMPT_LEN = 40

# pmt_type_str[], mm_util.c: the high nibble of CALLTYP.
PAYMENT_TYPES = ("unused-0", "unused-1", "no-charge", "coin", "credit-card",
                 "calling-card", "cash-card", "inmate", "mondex",
                 "visa-stored-value", "smart-city", "proton", "undefined-c",
                 "undefined-d", "undefined-e", "undefined-f")


def parse_speed_table(data):
    if len(data) != SPEED_TABLE_LEN:
        raise DisplayTableError("speed-dial table is %d bytes, expected %d"
                                % (len(data), SPEED_TABLE_LEN))
    out = []
    for i in range(SPEED_MAX):
        raw = data[i * SPEED_ENTRY_LEN:(i + 1) * SPEED_ENTRY_LEN]
        prompt = raw[11:51].decode("latin-1")
        out.append({
            "index": i,
            "call_type": CALL_TYPES[raw[0] & 0x0F],
            "payment": PAYMENT_TYPES[raw[0] >> 4],
            "flags": raw[1],
            "rate_ref": None if raw[2] == NO_RATE_REF else raw[2],
            "number": unpack_screen_number(raw[3:11]),
            "line1": prompt[:20],
            "line2": prompt[20:],
            "tail": bytes(raw[51:57]),     # customer ref + button indicators
        })
    return out


def render_speed_table(entries):
    if len(entries) != SPEED_MAX:
        raise DisplayTableError("%d speed-dial entries, the table has exactly %d"
                                % (len(entries), SPEED_MAX))
    out = bytearray()
    for e in entries:
        what = "button %d" % e["index"]
        if e.get("call_type") not in CALL_TYPES:
            raise DisplayTableError("%s: unknown call type %r" % (what, e.get("call_type")))
        if e.get("payment") not in PAYMENT_TYPES:
            raise DisplayTableError("%s: unknown payment type %r" % (what, e.get("payment")))
        ref = e.get("rate_ref")
        ref = NO_RATE_REF if ref is None or ref == "" else int(ref)
        if not 0 <= ref <= 0xFF:
            raise DisplayTableError("%s: rate reference %r is not a byte" % (what, ref))
        tail = bytes(e.get("tail") or bytes(6))
        if len(tail) != 6:
            raise DisplayTableError("%s: reference/indicator bytes are %d long" % (what, len(tail)))
        try:
            number = pack_screen_number(e.get("number"), SPEED_NUMBER_BYTES)
        except ScreenTableError as exc:
            raise DisplayTableError("%s: %s" % (what, exc))
        out.append((PAYMENT_TYPES.index(e["payment"]) << 4) | CALL_TYPES.index(e["call_type"]))
        out.append(int(e.get("flags") or 0) & 0xFF)
        out.append(ref)
        out += number
        out += _vfd_text(e.get("line1"), 20, what + " line 1")
        out += _vfd_text(e.get("line2"), 20, what + " line 2")
        out += tail
    return bytes(out)


# ===========================================================================
# LCD -- Local Call Determination. Which exchanges are a local call.
#
# One table per AREA CODE: the code as BCD (`40 8e` for 408), then a class
# for every exchange 200-999, in exchange order. An area code with no LCD
# table is rated by the NPA table (0x96) as plain long distance, so a phone
# needs tables only for the codes it has local exchanges in. Three encodings,
# one per firmware family, from upstream's mm_lcd.py and checked against the
# shipped tables byte for byte (tests/test_provision_mm.py):
#
#   MTR 1.7   0x4a-0x51, 0x5a, 0x5b   818 bytes: code, 16 zero bytes, 800 x 1 byte
#   MTR 1.9   0x65-0x6b               402 bytes: code, 800 x 4 bits, first exchange high
#   MTR 2.x   0x88-0x95, 0xa0, 0xa1   202 bytes: code, 800 x 2 bits, first exchange high
#
# The classes: 0 local, 2 intra-LATA toll, 3 invalid, 4 inter-LATA toll
# (4 does not fit the 2-bit form; upstream never writes it).
#
# The SLOTS matter as much as the contents. mm_manager sends a terminal its
# own file for a table, else the default's, else nothing -- so a slot this
# phone's folder leaves empty is filled from the default set, which is
# upstream's San Jose area. Every slot the default fills must be filled here.
# ===========================================================================

LCD_LOCAL, LCD_INTRA_LATA, LCD_INVALID, LCD_INTER_LATA = 0, 2, 3, 4
LCD_SLOTS = {
    "mtr1.7": (0x4a, 0x4b, 0x4c, 0x4d, 0x4e, 0x4f, 0x50, 0x51, 0x5a, 0x5b),
    "mtr1.9": (0x65, 0x66, 0x67, 0x68, 0x69, 0x6a, 0x6b),
    "mtr2": tuple(range(0x88, 0x96)) + (0xa0, 0xa1),
}
LCD_LEN = {"mtr1.7": 818, "mtr1.9": 402, "mtr2": 202}
_LCD_PAD = 16                       # MTR 1.7: zero bytes between the code and the classes
NXX_RANGE = range(200, 1000)


class LcdTableError(ValueError):
    pass


def lcd_family(table_id):
    for fam, slots in LCD_SLOTS.items():
        if table_id in slots:
            return fam
    raise LcdTableError("0x%02x is not an LCD table" % table_id)


def _npa_bytes(npa):
    npa = int(npa)
    if not 200 <= npa <= 999:
        raise LcdTableError("an area code is 200-999, not %r" % (npa,))
    h, t, o = npa // 100, npa // 10 % 10, npa % 10
    return bytes([(h << 4) | t, (o << 4) | 0x0e])


def render_lcd_table(family, npa, classes):
    """`classes` is 800 values, one per exchange 200..999."""
    classes = list(classes)
    if len(classes) != len(NXX_RANGE):
        raise LcdTableError("an LCD table classes 800 exchanges, not %d" % len(classes))
    out = bytearray(_npa_bytes(npa))
    if family == "mtr1.7":
        out += bytes(_LCD_PAD)
        out += bytes(classes)
    elif family == "mtr1.9":
        for i in range(0, 800, 2):
            out.append((classes[i] << 4) | classes[i + 1])
    elif family == "mtr2":
        for i in range(0, 800, 4):
            if any(c > 3 for c in classes[i:i + 4]):
                raise LcdTableError("the 2-bit LCD form has no class above 3")
            out.append((classes[i] << 6) | (classes[i + 1] << 4) | (classes[i + 2] << 2) | classes[i + 3])
    else:
        raise LcdTableError("no LCD form for %r" % (family,))
    return bytes(out)


def parse_lcd_table(family, data):
    """(npa, [800 classes])."""
    if len(data) != LCD_LEN.get(family, -1):
        raise LcdTableError("a %s LCD table is %s bytes, not %d" % (family, LCD_LEN.get(family), len(data)))
    npa = (data[0] >> 4) * 100 + (data[0] & 0x0F) * 10 + (data[1] >> 4)
    body = data[2:]
    if family == "mtr1.7":
        classes = list(body[_LCD_PAD:])
    elif family == "mtr1.9":
        classes = [n for b in body for n in (b >> 4, b & 0x0F)]
    else:
        classes = [(b >> s) & 3 for b in body for s in (6, 4, 2, 0)]
    return npa, classes


# ===========================================================================
# CALL-IN SCHEDULE (table 35, 0x23, DLOG_MT_CALL_IN_PARMS): when the phone
# calls its manager by itself.
#
# mm_manager builds this one rather than reading a file (GENERATED_TABLES),
# and what it builds is a call-in every twelve hours from the download,
# retried every fifteen minutes while nobody answers, with an automatic
# call-in once thirty call records are waiting. On our own phones that is
# right: our manager answers. A phone programmed over the public
# provisioning line goes home with that line as its manager, and the line
# answers no modem outside a window (PUBLIC_PROVISIONING §6) -- so the
# schedule turned every beta phone into a caller that rings us four times
# an hour, forever (2026-10-14: 114 unanswered call-ins in a day from the
# owner's own phone, which then went to emergency-operator service).
# patches/0010 lets the manager send a terminal's OWN file instead, and
# this is the file a beta phone gets.
#
# The layout is mm_manager.h's dlog_mt_call_in_params_t at the pinned
# commit, the ID byte left off as in every table file: start date YY MM DD
# and time HH MM SS, interval DD HH MM, call-back retry MM SS, the CDR
# threshold (1-50), expiry date and time, two unknown bytes. YY is years
# since 1900, as upstream writes it (126 for 2026).
#
# No value turns the schedule OFF -- none is documented, and an interval of
# zero could as easily mean "continuously". So the first call-in is moved
# out of reach instead: 2099-12-31. That is a reading of the table, not a
# measurement: no terminal has yet been watched not calling in on it. The
# CDR threshold cannot be switched off either (its range is 1-50), so it
# goes to 50, the most a phone may hold before it calls in by itself.
# ===========================================================================

CALL_IN_TABLE_ID = 0x23
CALL_IN_LEN = 20
CDR_THRESHOLD_MAX = 50


class CallInTableError(ValueError):
    pass


def render_call_in_table(start, interval=(0, 12, 0), retry=(15, 0),
                         cdr_threshold=30, expires=None):
    """The call-in schedule's file bytes. `start` and `expires` are
    (year, month, day, hour, minute, second) with a four-digit year;
    `expires` defaults to a year after `start`, as upstream's does."""
    if expires is None:
        expires = (start[0] + 1,) + tuple(start[1:])
    if not 1 <= int(cdr_threshold) <= CDR_THRESHOLD_MAX:
        raise CallInTableError("CDR threshold %r is outside 1-%d" % (cdr_threshold, CDR_THRESHOLD_MAX))
    out = bytearray()
    for name, when in (("start", start), ("expiry", expires)):
        y, mo, d, h, mi, s = (int(v) for v in when)
        if not (1900 <= y <= 2155 and 1 <= mo <= 12 and 1 <= d <= 31
                and 0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
            raise CallInTableError("%s %r is not a date and time the table can hold" % (name, when))
        if name == "start":
            out += bytes([y - 1900, mo, d, h, mi, s])
            out += bytes(int(v) & 0xFF for v in interval)
            out += bytes(int(v) & 0xFF for v in retry)
            out.append(int(cdr_threshold))
        else:
            out += bytes([y - 1900, mo, d, h, mi, s])
    out += bytes(2)
    return bytes(out)


def parse_call_in_table(data):
    """{start, interval, retry, cdr_threshold, expires} from a file's bytes."""
    if len(data) != CALL_IN_LEN:
        raise CallInTableError("a call-in table is %d bytes, not %d" % (CALL_IN_LEN, len(data)))
    b = data
    return {"start": (1900 + b[0], b[1], b[2], b[3], b[4], b[5]),
            "interval": tuple(b[6:9]), "retry": tuple(b[9:11]),
            "cdr_threshold": b[11],
            "expires": (1900 + b[12], b[13], b[14], b[15], b[16], b[17])}


# What a beta phone is sent: no scheduled call-in before 2099, and the
# fewest automatic ones the CDR threshold allows. See the section above.
NO_SCHEDULED_CALLIN = render_call_in_table((2099, 12, 31, 0, 0, 0),
                                           cdr_threshold=CDR_THRESHOLD_MAX)

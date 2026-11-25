"""ExpressNet's Options & Registers record, field by field.

`PARAM/MISCOPT.DAT` holds one 420-byte c-tree record per options record
(the thing a site names at offset 112 of its own record). ExpressNet's
form for it is 183 fields long; every one of them was mapped on
2026-09-30 by scripting the form -- open the record, move to the field,
change it, save, diff the file -- so this module can read and write the
record without the program. The KL5 project notes hold the
measurements; what matters for the code is:

  * The phone's option registers (111-198 on the keypad, plus the
    unassigned 1A1-1E8) are single bits. Register NNu sits in one byte
    per tens digit and one bit per units digit, in the same odd order
    everywhere: units 1..8 -> bits 0,1,6,2,4,5,7,3. The three 12x
    registers ExpressNet exposes do not follow it (123=bit4, 124=bit6,
    125=bit5 of byte 76), so every field carries its measured byte and
    bit rather than trusting the rule.
  * A few registers are inverted on the form: "Allow U.S. coins: Yes" is
    the bit clear. Each field says which value the bit means.
  * Numbers are stored twice: as the form holds them (binary, or ASCII
    digits for numbers and codes) and again in a download image further
    into the record, mostly packed BCD. The console writes both, the way
    the program does, so the phone is sent what the form shows.
  * The programmable reporting flags are two bytes-for-bytes copies of
    the same six bytes (172-177 and 311-316).

Nothing here touches the index: an options record is edited in place,
by name, and its name never changes. Adding one is a clone-and-append
plus ExpressNet's own File Rebuild, like a site.
"""

RECLEN = 420
F_NAME = (1, 12)
F_DESC = (14, 60)

# Register NNu -> (byte, bit): one byte per tens digit, bit by units digit.
REG_BYTE = {"11": 75, "12": 76, "13": 77, "14": 78, "15": 79, "16": 80, "17": 81, "18": 82, "19": 83,
            "1A": 335, "1B": 336, "1C": 337, "1D": 338, "1E": 339}
REG_BIT = {1: 0, 2: 1, 3: 6, 4: 2, 5: 4, 6: 5, 7: 7, 8: 3}
# ...except these three, measured off the form (the rule would put them elsewhere).
REG_EXCEPT = {"123": (76, 4), "124": (76, 6), "125": (76, 5)}


def reg_pos(reg):
    """(byte, bit) of a register on the keypad's numbering."""
    reg = str(reg).upper()
    if reg in REG_EXCEPT:
        return REG_EXCEPT[reg]
    return REG_BYTE[reg[:2]], REG_BIT[int(reg[2])]


# ---------------------------------------------------------------------------
# The field table. Each entry: key, section, label, spec. Spec kinds:
#   ("bit", register, one_label, zero_label)        one bit, shown as a choice
#   ("bin", form_off, width, lo, hi, image)         a number: binary at form_off;
#                                                   image = None | ("bin", off) | ("bcd", off)
#                                                   | ("nib_hi", off) | ("nib_lo", off)
#   ("digits", ascii_off, width, bcd_off, bcd_len)  a phone number or code, ASCII on
#                                                   the form and packed BCD in the image
#   ("text", off, width)                            free text, space padded
#   ("pick", ...)                                   a list picker (see PICKERS)
# Order is the form's order; the section headers are the form's.
# ---------------------------------------------------------------------------
FIELDS = [
    ("desc", "General", "Description", ("text",) + F_DESC),
    ("reporting_number", "Access Numbers", "Reporting Number", ("digits", 116, 11, 300, 6, "phone")),
    ("misc_number", "Access Numbers", "Miscellaneous Number", ("digits", 131, 11, 282, 6, "phone")),
    ("validation_number", "Access Numbers", "Validation Access Number", ("digits", 320, 10, 288, 6, "phone")),
    ("pbx_access", "Access Numbers", "PBX Access Number", ("digits", 146, 2, 307, 1, "pbx")),
    ("poll_start", "Polling Options", "Window Starting Hour", ("bin", 159, 2, 0, 24, ("bcd", 280))),
    ("poll_length", "Polling Options", "Window Length", ("bin", 161, 2, 0, 24, ("bcd", 281))),
    ("rings_inside", "Polling Options", "Rings Inside Window", ("bin", 155, 2, 0, 15, ("nib_hi", 279))),
    ("rings_default", "Polling Options", "Rings Default", ("bin", 157, 2, 0, 15, ("nib_lo", 279))),
    ("tod_restrict", "Call Restriction by Time of Day", "Call Restrictions by Time of Day", ("bitat", 80, 5, "Yes", "No")),
    ("tod1_start", "Call Restriction by Time of Day", "First Window Starting Hour", ("bin", 340, 2, 0, 23, None)),
    ("tod1_length", "Call Restriction by Time of Day", "First Window Length", ("bin", 341, 2, 0, 24, None)),
    ("tod2_start", "Call Restriction by Time of Day", "Second Window Starting Hour", ("bin", 342, 2, 0, 23, None)),
    ("tod2_length", "Call Restriction by Time of Day", "Second Window Length", ("bin", 343, 2, 0, 24, None)),
    ("tod3_start", "Call Restriction by Time of Day", "Third Window Starting Hour", ("bin", 344, 2, 0, 23, None)),
    ("tod3_length", "Call Restriction by Time of Day", "Third Window Length", ("bin", 345, 2, 0, 24, None)),
    ("cashbox_volume", "Reporting Options", "Cash Box Volume %", ("bin", 149, 3, 0, 100, ("bin", 274))),
    ("day_of_month_report", "Reporting Options", "Day of Month Report", ("bin", 163, 2, 0, 31, ("bcd", 277))),
    ("days_report", "Reporting Options", "Number of Days Report", ("bin", 167, 2, 0, 99, ("bcd", 272))),
    ("peg_count", "Reporting Options", "Peg Count / Inactivity", ("bin", 151, 3, 0, 255, ("bin", 275))),
    ("days_before_full", "Reporting Options", "Days Before Full", ("bin", 334, 3, 0, 255, None)),
    ("day_of_week_report", "Reporting Options", "Day of Week Report", ("bin", 165, 1, 0, 7, ("bcd", 278))),
    ("time_of_day_report", "Reporting Options", "Time of Day Report", ("bin", 169, 2, 0, 23, ("bcd", 273))),
    ("security_code", "Security Codes", "Security Access Code", ("digits", 89, 4, 263, 2, "code")),
    ("collection_code", "Security Codes", "Coin Collection Code (*#1)", ("digits", 94, 4, 266, 2, "code")),
    ("box_amount_code", "Security Codes", "Coin Box Amount Code (*#4 and *#5)", ("digits", 99, 4, 268, 2, "code")),
    ("cdr_code", "Security Codes", "CDR Retrieval Code", ("digits", 104, 4, 270, 2, "code")),
    ("credit_express_code", "Security Codes", "Credit Express Code", ("digits", 109, 4, 308, 2, "code")),
    ("collect_calls_per_number", "Card Options", "Collect Calls per Destination Number", ("bin", 128, 2, 0, 49, None)),
    ("records_before_report", "Card Options", "Records Stored Before Reporting", ("bin", 143, 2, 0, 52, ("bin", 276))),
    ("overtime_periods", "Special Purpose Options", "Limited Local Overtime Periods", ("bin", 145, 3, 0, 127, None)),
    ("box_removal_start", "Special Purpose Options", "Coin Box Removal Starting Hour", ("bin", 331, 2, 0, 23, None)),
    ("box_removal_length", "Special Purpose Options", "Coin Box Removal Window Length", ("bin", 332, 2, 0, 23, None)),
    ("box_report_hour", "Special Purpose Options", "Coin Box Reporting Hour", ("bin", 333, 2, 0, 23, None)),
    ("receiver_volume", "Miscellaneous Registers", "Receiver Volume", ("enum", 153, (("1", "High"), ("2", "Medium"), ("3", "Low")))),
]
# The security access code's download image carries a check byte: 0x55 plus
# its two BCD bytes (measured over four codes; no other field moves it).
SECURITY_CHECK = (265, 0x55, 263, 264)

# The option fields that make the board call its manager on its own schedule:
# the polling window and the day/time it reports. These are what
# phones.scheduled_callin OFF clears from a download so a public/beta phone
# carries no self-poll schedule. Coin-box thresholds (cashbox_volume,
# peg_count, records_before_report) are deliberately NOT here: a 0 there may
# mean "always", not "never", and their firmware semantics are unconfirmed --
# only the time schedule is cleared, and only the config WE send is
# guaranteed; whether a board with a cleared schedule never self-polls is a
# bench fact still to confirm (BENCH `scheduled`).
POLL_SCHEDULE_FIELDS = ("poll_start", "poll_length", "day_of_month_report",
                        "days_report", "day_of_week_report", "time_of_day_report",
                        "box_report_hour")


def strip_poll_schedule(record):
    """Return `record` with the self-poll schedule fields zeroed -- both the
    form copy and the download-image copy of each -- so a download built from
    it carries no polling window or report day/time. Used when a phone's
    scheduled_callin flag is off. Other fields, coin-box thresholds included,
    are untouched. A short record is returned unchanged."""
    by_key = {f[0]: f[3] for f in FIELDS}
    buf = bytearray(record)
    for key in POLL_SCHEDULE_FIELDS:
        spec = by_key.get(key)
        if not spec or spec[0] != "bin":
            continue
        form_off = spec[1]          # one byte, as read() and write() keep it: the width is
        if 0 <= form_off < len(buf):  # the form's digits, and Coin Box Reporting Hour (333,
            buf[form_off] = 0         # width 2) sits right before Days Before Full (334)
        image = spec[5]
        if image and image[0] in ("bcd", "bin"):   # the 1-byte download-image copy
            off = image[1]
            if 0 <= off < len(buf):
                buf[off] = 0
    return bytes(buf)

# Register bits, in form order: (key, section, label, register, one_label, zero_label, byte, bit).
# one_label is what the form shows when the bit is SET.
REGISTERS = [
    ("co_dialtone_delay", "Line Options", "Central Office Dial Tone Delay", "111", "3 s", "1.5 s", 75, 0),
    ("co_dialtone_timeout", "Line Options", "Central Office Dial Tone Timeout", "112", "14 s", "8 s", 75, 1),
    ("dial_mode", "Line Options", "Dialing to Central Office", "113", "Pulse", "DTMF", 75, 6),
    ("pulse_interdigit", "Line Options", "Pulse Interdigit Delay", "153", "500 ms", "800 ms", 79, 6),
    ("receiver_volume_key", "Line Options", "Receiver Volume Adjust with #", "117", "Yes", "No", 75, 7),
    ("coin_tones", "Line Options", "Coin Tones", "114", "Yes", "No", 75, 2),
    ("allow_976", "Anti-Fraud Options", "Allow 976 Numbers", "145", "Yes", "No", 78, 4),
    ("wink_detect", "Anti-Fraud Options", "Enable Wink Detect", "124", "Yes", "No", 76, 6),
    ("antifraud_111", "Anti-Fraud Options", "'111' for Anti-Fraud", "115", "Pulse", "DTMF", 75, 4),
    ("no_10s_delay_0", "Anti-Fraud Options", "Disable 10-Sec Delay for 0- and 00-", "196", "Yes", "No", 83, 5),
    ("wink_dialtone_delay", "Anti-Fraud Options", "Extended Delay for Dial Tone on Wink", "1A1", "Yes", "No", 335, 0),
    ("coin_line", "Coin-Line Phone Options", "Line Operation", "127", "Coin", "Non-coin", 76, 7),
    ("post_pay", "Coin-Line Phone Options", "Post-Pay Mode", "182", "Yes", "No", 82, 1),
    ("coin_check", "Coin-Line Phone Options", "Coin Check", "137", "Ground", "Loop", 77, 7),
    ("refund_signal", "Coin-Line Phone Options", "Refund Signal", "148", "+130 V", "-130 V", 78, 3),
    ("park_tip", "Coin-Line Phone Options", "Park Tip on Ground", "181", "Yes", "No", 82, 0),
    ("coin_tone_twist", "Coin-Line Phone Options", "Use Coin Tone Level/Twist", "171", "Yes", "No", 81, 0),
    ("coin_tone_high", "Coin-Line Phone Options", "Use High Coin Tone Amplitude", "172", "Yes", "No", 81, 1),
    ("reverse_twist", "Coin-Line Phone Options", "Use Reverse Twist", "174", "Yes", "No", 81, 2),
    ("monitor_loop_reversal", "Coin-Line Phone Options", "Monitor Loop Reversal Until Normal", "184", "On", "Off", 82, 2),
    ("no_1plus_on_loop_rev", "Coin-Line Phone Options", "Don't Allow Local or 1+ Calls on Loop Reversal", "188", "On", "Off", 82, 3),
    ("no_co_signal", "Coin-Line Phone Options", "When No CO Signal After 18 s", "191", "Refund", "Collect", 83, 0),
    ("onhook_delay", "Coin-Line Phone Options", "4-Second On-Hook Delay on Drop and Reseize", "192", "Yes", "No", 83, 1),
    ("single_tone", "Coin-Line Phone Options", "Use Single Tone Frequencies", "1A3", "Yes", "No", 335, 6),
    ("bong_0plus", "Card Options", "Bong Tone on 0+ Calls", "118", "No", "Yes", 75, 3),
    ("bong_or_voice", "Card Options", "0+ Prompt", "161", "Voice", "Bong", 80, 0),
    ("validation_service", "Card Options", "Validation Service", "152", "NDC", "CARD*TEL", 79, 1),
    ("fast_validation", "Card Options", "Fast On-Line Validation", "157", "Yes", "No", 79, 7),
    ("revalidate_failed", "Card Options", "Revalidate Previously Failed Cards", "131", "No", "Yes", 77, 0),
    ("revalidate_passed", "Card Options", "Revalidate Previously Passed Cards", "132", "No", "Yes", 77, 1),
    ("pound_on_0plus", "Card Options", "Allow # on 0+ Store & Forward", "156", "No", "Yes", 79, 5),
    ("supercollect_response", "Card Options", "Require Called-Party Response for SuperCollect", "138", "Yes", "No", 77, 3),
    ("deny_incoming_after_collect", "Card Options", "Deny Incoming 10 Min After SuperCollect", "134", "Yes", "No", 77, 2),
    ("forced_price_quote", "Card Options", "Forced Price Quoting", "155", "Yes", "No", 79, 4),
    ("voice_branding", "Card Options", "Voice Branding on 0+", "144", "Yes", "No", 78, 2),
    ("brand_after_bong", "Card Options", "Brand", "142", "After bong", "Before bong", 78, 1),
    ("secondary_brand", "Card Options", "Voice Brand", "168", "Secondary", "Primary", 80, 3),
    ("us_coins", "Miscellaneous Options", "Allow U.S. Coins", "135", "No", "Yes", 77, 4),
    ("canadian_coins", "Miscellaneous Options", "Allow Canadian Coins", "136", "No", "Yes", 77, 5),
    ("npa555_bands", "Miscellaneous Options", "Intra/InterState NPA-555 Same", "116", "No", "Yes", 75, 5),
    ("voice_errors", "Miscellaneous Options", "Voice Error Messages", "146", "Yes", "No", 78, 5),
    ("card_only", "Miscellaneous Options", "Card-Only Mode", "164", "Yes", "No", 80, 2),
    ("no_piezo", "Miscellaneous Options", "Disable Piezo Ringer", "175", "Yes", "No", 81, 4),
    ("lata_0plus", "Miscellaneous Options", "Allow 0+ Separation by LATA", "151", "Yes", "No", 79, 0),
    ("transmit_level", "Miscellaneous Options", "Transmit Level", "141", "Low", "High", 78, 0),
    ("modem_speed", "Miscellaneous Options", "Modem Speed", "133", "1200", "300", 77, 6),
    ("calls_during_jam", "Miscellaneous Options", "All Calls Permitted During Relay Jam", "173", "Yes", "No", 81, 6),
    ("fsk_1200", "Miscellaneous Options", "1200 Baud FSK", "186", "On", "Off", 82, 5),
    ("cuckoo_tone", "Miscellaneous Options", "Use Cuckoo Tone", "183", "Yes", "No", 82, 6),
    ("daily_fill_rate", "Miscellaneous Options", "Use Daily Fill Rate", "187", "Yes", "No", 82, 7),
    ("capture_800_digits", "Miscellaneous Options", "Capture Digits After 1-800/1-950", "194", "Yes", "No", 83, 2),
    ("record_incomplete", "Miscellaneous Options", "Record Incomplete Call in CDR", "198", "Yes", "No", 83, 3),
    ("cellular", "Special Purpose Options", "Enable Cellular Applications", "154", "Yes", "No", 79, 2),
    ("initial_time_msg", "Special Purpose Options", "Enable Initial Time Msg", "123", "Yes", "No", 76, 4),
    ("credit_card_msgs", "Special Purpose Options", "Enable Credit Card Messages", "125", "Yes", "No", 76, 5),
    ("spanish", "Special Purpose Options", "Voice Prompts", "165", "Spanish", "English", 80, 4),
    ("feature_group_f", "Special Purpose Options", "Enable Feature Group F", "163", "Yes", "No", 80, 6),
    ("free_after_overtime", "Special Purpose Options", "After Limited Overtime", "178", "Free", "Restrict", 81, 3),
    ("overtime_msgs", "Special Purpose Options", "Enable Overtime Messages", "1A7", "Yes", "No", 335, 7),
    ("coin_mech_electronic", "Miscellaneous Registers", "Coin Mechanism", "147", "Electronic (ECS)", "Mechanical", 78, 7),
    ("coin_collect_delayed", "Miscellaneous Registers", "Coin Collect Time (mechanical) / ECS I (electronic)", "143", "After delay / ECS I", "Immediately / ECS II", 78, 6),
    ("mode_167", "Unassigned Options", "Mode 167", "167", "On", "Off", 80, 7),
    ("mode_185", "Unassigned Options", "Mode 185", "185", "On", "Off", 82, 4),
    ("mode_193", "Unassigned Options", "Mode 193", "193", "On", "Off", 83, 6),
    ("mode_195", "Unassigned Options", "Mode 195", "195", "On", "Off", 83, 4),
]
# The 1A2..1E8 modes follow the rule exactly (measured one by one); listed so the form shows them.
UNASSIGNED = ["1A2", "1A4", "1A5", "1A6", "1A8"] + ["1%s%d" % (row, u) for row in "BCDE" for u in range(1, 9)]
for _m in UNASSIGNED:
    REGISTERS.append(("mode_" + _m.lower(), "Unassigned Options", "Mode " + _m, _m, "On", "Off") + reg_pos(_m))

# Programmable reporting flags: (code, label, byte, bit) -- bit set = the phone calls in.
# Stored at 171-177 and again at 310-316.
FLAG_COPY = 310 - 171
FLAGS = [
    ("NE", "Audit CDR Error", 173, 6), ("BG", "Bad Ground", 175, 4), ("CE", "Bill CDR Error", 173, 7),
    ("CF", "Bill CDR Full", 172, 7), ("CL", "Bill CDR Limit Reached", 172, 6), ("BM", "Box Missing", 176, 5),
    ("BR", "Box Removed", 176, 1), ("AE", "Call Accounting Error", 172, 5), ("E$", "Cash Box Emptied (*#1)", 171, 0),
    ("CR", "Clock Reset", 175, 5), ("BE", "Coin Box Amount Error", 172, 3), ("$F", "Coin Box Full", 173, 1),
    ("VE", "Coin Box Volume Error", 172, 0), ("$V", "Coin Box Volume Reached", 171, 4), ("CG", "Coin Gate Failure", 173, 2),
    ("ME", "Coin Scanner Error", 173, 4), ("$E", "Coin Totalizer Error", 172, 1), ("DE", "Defective Escrow", 173, 3),
    ("DI", "Dial Inactivity", 176, 6), ("EE", "Download Error", 175, 6), ("HG", "Handset Gone", 174, 7),
    ("HO", "Handset Off-Hook", 174, 4), ("LR", "Loop Reversed", 171, 7), ("LB", "Low Battery", 174, 0),
    ("LA", "Lower Alarm", 174, 6), ("OC", "Opto-Coupler", 176, 0), ("OE", "Overage Error", 172, 4),
    ("PC", "Peg Count Reached", 171, 3), ("PR", "Phone Repair (*#3)", 171, 2), ("RE", "RAM Error", 171, 5),
    ("RC", "Relay Collect Failure", 176, 3), ("RJ", "Relay Jam", 171, 6), ("RR", "Relay Refund Failure", 176, 2),
    ("TE", "Reset Totalizer Error", 172, 2), ("SN", "Silicon Ser #", 176, 4), ("ST", "Status Check (*#2)", 171, 1),
    ("TR", "Time Report", 173, 5), ("TG", "Trouble Gone", 175, 7), ("NF", "Audit CDR Full", 175, 1),
    ("SU", "Signal Unit Bad", 176, 7), ("AC", "ASIC Clock Failure", 177, 1), ("CB", "CTB rate corrupt XNANP", 177, 0),
    ("TB", "Temperature Board failure", 177, 7),
]


class XnetOptsError(Exception):
    pass


def _text(rec, off, width):
    return rec[off:off + width].split(b"\0")[0].decode("latin-1").rstrip()


def _bit(rec, byte, bit):
    return (rec[byte] >> bit) & 1


def _set_bit(rec, byte, bit, on):
    rec[byte] = (rec[byte] | (1 << bit)) if on else (rec[byte] & ~(1 << bit) & 0xff)


def _bcd(digits, length, style):
    """Pack digits as ExpressNet does in the download image.
    "phone": a leading 0 nibble, the digits with 0 written as A (the same
    packing as a site's ANI), zero-filled to `length` bytes.
    "code":  plain BCD, 0 is 0, no leading nibble.
    "pbx":   plain, '#' is F (the stock "##" is 0xff)."""
    def nib(d):
        if d == "#":
            return 0xf
        return 0xa if (d == "0" and style == "phone") else int(d)
    nibbles = ([0] if style == "phone" else []) + [nib(d) for d in digits]
    nibbles += [0] * (2 * length - len(nibbles))
    return bytes((nibbles[k] << 4) | nibbles[k + 1] for k in range(0, 2 * length, 2))


def read(rec):
    """The record as a dict of field key -> value (numbers as int, bits as
    0/1, text as str), plus 'name' and 'flags' (code -> 0/1)."""
    if len(rec) != RECLEN:
        raise XnetOptsError("an options record is %d bytes, not %d" % (RECLEN, len(rec)))
    out = {"name": _text(rec, *F_NAME)}
    for key, _sec, _label, spec in FIELDS:
        kind = spec[0]
        if kind == "text":
            out[key] = _text(rec, spec[1], spec[2])
        elif kind == "digits":
            out[key] = _text(rec, spec[1], spec[2])
        elif kind == "bin":
            out[key] = rec[spec[1]]
        elif kind == "enum":
            out[key] = str(rec[spec[1]])
        elif kind == "bitat":
            out[key] = _bit(rec, spec[1], spec[2])
    for key, _sec, _label, _reg, _one, _zero, byte, bit in REGISTERS:
        out[key] = _bit(rec, byte, bit)
    out["flags"] = {code: _bit(rec, byte, bit) for code, _label, byte, bit in FLAGS}
    return out


def write(rec, values):
    """A copy of `rec` with every key in `values` applied, both where the
    form keeps it and in the download image. Unknown keys are refused;
    out-of-range numbers are refused with the form's own limits."""
    rec = bytearray(rec)
    if len(rec) != RECLEN:
        raise XnetOptsError("an options record is %d bytes, not %d" % (RECLEN, len(rec)))
    fields = {f[0]: f for f in FIELDS}
    regs = {r[0]: r for r in REGISTERS}
    flags = {f[0]: f for f in FLAGS}
    for key, value in values.items():
        if key == "flags":
            for code, on in value.items():
                if code not in flags:
                    raise XnetOptsError("no reporting flag %r" % code)
                _c, _l, byte, bit = flags[code]
                _set_bit(rec, byte, bit, bool(int(on)))
                _set_bit(rec, byte + FLAG_COPY, bit, bool(int(on)))
        elif key in regs:
            _k, _s, _l, _r, _one, _zero, byte, bit = regs[key]
            _set_bit(rec, byte, bit, bool(int(value)))
        elif key in fields:
            _apply(rec, fields[key], value)
        else:
            raise XnetOptsError("no such option %r" % key)
    return bytes(rec)


def _apply(rec, field, value):
    key, _sec, label, spec = field
    kind = spec[0]
    if kind == "text":
        _off, width = spec[1], spec[2]
        text = str(value).upper()[:width]
        rec[_off:_off + width] = text.ljust(width).encode("latin-1")
        rec[_off + width] = 0
    elif kind == "digits":
        ascii_off, width, bcd_off, bcd_len, style = spec[1:]
        digits = "".join(c for c in str(value) if c.isdigit() or c == "#")
        if len(digits) > width:
            raise XnetOptsError("%s takes up to %d digits" % (label, width))
        if style == "code" and len(digits) not in (0, width):
            raise XnetOptsError("%s is %d digits" % (label, width))
        rec[ascii_off:ascii_off + width] = digits.ljust(width).encode()
        rec[bcd_off:bcd_off + bcd_len] = _bcd(digits, bcd_len, style)
        if key == "security_code":
            off, const, a, b = SECURITY_CHECK
            rec[off] = (const + rec[a] + rec[b]) & 0xff
    elif kind == "enum":
        off, choices = spec[1], spec[2]
        if str(value) not in dict(choices):
            raise XnetOptsError("%s is one of %s" % (label, ", ".join(l for _v, l in choices)))
        rec[off] = int(value)
    elif kind == "bin":
        off, _width, lo, hi, image = spec[1:]
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise XnetOptsError("%s must be a number" % label)
        if not lo <= n <= hi:
            raise XnetOptsError("%s must be %d to %d" % (label, lo, hi))
        rec[off] = n
        if image:
            how, ioff = image
            if how == "bin":
                rec[ioff] = n
            elif how == "bcd":
                rec[ioff] = ((n // 10) << 4) | (n % 10)
            elif how == "nib_hi":
                rec[ioff] = (rec[ioff] & 0x0f) | ((n & 0xf) << 4)
            elif how == "nib_lo":
                rec[ioff] = (rec[ioff] & 0xf0) | (n & 0xf)
    elif kind == "bitat":
        _set_bit(rec, spec[1], spec[2], bool(int(value)))


def form():
    """The editor's layout: sections in form order, each a list of
    (key, label, kind, extra) the template can render."""
    sections = []
    def section(name):
        for s in sections:
            if s["name"] == name:
                return s
        sections.append({"name": name, "fields": []})
        return sections[-1]
    for key, sec, label, spec in FIELDS:
        kind = spec[0]
        if kind == "bin":
            section(sec)["fields"].append({"key": key, "label": label, "kind": "number", "lo": spec[3], "hi": spec[4]})
        elif kind == "bitat":
            section(sec)["fields"].append({"key": key, "label": label, "kind": "choice", "one": spec[3], "zero": spec[4], "register": ""})
        elif kind == "enum":
            section(sec)["fields"].append({"key": key, "label": label, "kind": "enum", "choices": list(spec[2])})
        else:
            section(sec)["fields"].append({"key": key, "label": label, "kind": "text", "width": spec[2]})
    for key, sec, label, reg, one, zero, _b, _bit in REGISTERS:
        section(sec)["fields"].append({"key": key, "label": label, "kind": "choice", "one": one, "zero": zero, "register": reg})
    section("Programmable Reporting Flags")["fields"] = [
        {"key": code, "label": label, "kind": "flag"} for code, label, _b, _bit in FLAGS]
    return sections

"""Compose Protel scripts from measured, header-inclusive capture coordinates.

The tables and dense analysis helpers in this module retain the historical
capture view: each chunk includes the E inner checksum at offset zero.
These coordinates are NOT CPU addresses. Within each chunk, capture offset
k >= 1 is written to CPU address start+k-1. There is no global shift across
chunk boundaries. build/build_writes/build_download return that analysis
view; only the script renderers convert to actual CPU data and valid frames.

Production rendering strips each header, recomputes it from actual address
and length, resolves the explicit bank-zero fill plus all writes, and stamps
the genuine checksum at CPU $1000/$1001 over $1002..$2CFF. Historical dense
correlations and image_checksum are analysis only. Independent ROM approval
is still required before transmission; checksum acceptance does not establish
option/zone semantics or physical fidelity.
"""

IMAGE_BASE = 0x1000
IMAGE_END = 0x20ed               # the bulk copy's extent (exclusive)
COST_RECORD_OFFSET = 99          # image[IMAGE_BASE + k] == costing_record[99 + k]

# (image address, costing record offset, length): image[a + k] == record[r + k].
# Later entries win over earlier ones where they overlap -- the bulk copy
# covers the whole image and the three windows after it are the record at
# other offsets. Each measured 2026-10-02 (docstring).
COST_REGIONS = [
    (IMAGE_BASE, COST_RECORD_OFFSET, IMAGE_END - IMAGE_BASE),
    (0x19bb, 4860, 0x1af2 - 0x19bb), # packed numbers (speed dials and the like): record 4860.., same offset as the text block
    (0x1eb8, 6137, 64),              # text block ('0' x 20, site number field, "...PH.000000")
    (0x1ef8, 6727, 429),             # 29 zeros, then the restricted-number table (50 x 8) through 0x20a4
    (0x178b, 7644, 16),              # the NPA table header copy (first NPA's BCD + band list)
]

# Every E write of a download: (address, length or None when it follows the
# record, the board's region end). The first byte of each write is
# (region end - write end) & 0xff -- a length mark the board checks; the
# ends were read off 26 downloads (first byte + length is constant per
# write). The zone tail (0x2148..) is protelzones' and comes after these.
CHUNKS = [
    (0x1000, 170, 0x1152), (0x10d8, 249, 0x1252), (0x11f0, 6, 0x1251), (0x1308, 2, 0x134f),
    (0x1330, 249, 0x144f), (0x1429, 249, 0x154e), (0x1521, 249, 0x164d), (0x1619, 249, 0x174c),
    (0x1711, None, 0x184b), (0x1958, 4, 0x1a49), (0x19ba, 102, 0x1a49), (0x1a49, 8, 0x1b48),
    (0x1ae4, 13, 0x1b48), (0x1d4e, 38, 0x1e45), (0x1d94, 3, 0x1e45), (0x1eb7, None, 0x1f44),
    (0x20a6, 30, 0x2142), (0x20ea, 3, 0x2142),
]

# Bytes no record holds, constant over every one of 26 downloads of this
# board (DD8822): seven 5-byte entries of `05 b0 00 00 00` and one `02 90`
# at 0x1d4f.., and one more `05 b0` at 0x1d95. None is in the options,
# costing or site record. Changing a route on ExpressNet's own Edit Routes
# screen could not be made to stick in the container (2026-10-04), so
# whether this block is the routes is still open; it is carried as-is.
# (The `01 02` once carried at 0x20eb was the first time-of-day window --
# TOD_AT below.)
BOARD_CONSTANTS = {
    0x1d4f: bytes.fromhex("05b0"), 0x1d54: bytes.fromhex("05b0"), 0x1d59: bytes.fromhex("05b0"),
    0x1d5e: bytes.fromhex("05b0"), 0x1d63: bytes.fromhex("05b0"), 0x1d68: bytes.fromhex("05b0"),
    0x1d6d: bytes.fromhex("05b0"), 0x1d72: bytes.fromhex("0290"), 0x1d95: bytes.fromhex("05b0"),
}

# The three call-restriction time-of-day windows (options 340..345, binary
# hours) go out as BCD at 0x20eb.. -- the `01 02` carried here as a board
# constant until the container sweep (2026-10-04) showed it was the first
# window's start and length.
TOD_AT, TOD_OPTIONS = 0x20eb, 340

# ExpressNet trims each of these writes to its last non-zero byte, within
# the bound given (exclusive), and the 0x1eb7 one may also start early, at
# the first non-zero byte from TEXT_REGION_FROM -- measured on the sweep and
# on five other downloads, 2026-10-04.
TRIMMED = {0x1000: 0x10d9, 0x1958: 0x1962, 0x20a6: 0x20ea, 0x20ea: 0x20f1}
# Trimmed writes that never come out shorter than their CHUNKS length: the
# 0x1958 write was always sent whole, security code and all, before it grew.
TRIM_AT_LEAST = {0x1958}

# The reporting schedule the firmware keeps at $10D6/$10D7 (CPU memory):
# Number of Days Report and Time of Day Report, the options record's BCD
# copies at 272/273. FUN_969b plans the next self call-in only when $1333
# (day of month), $1334 (day of week) or $10D6 is non-zero, and takes the
# hour from $10D7. (The same blind spot hid the *#1, *#4/*#5 and CDR codes:
# the 0x1958 write now runs on past the security code to carry them, and
# still comes out at ExpressNet's 4 bytes while they are zero.) They sit at the very end of the 0x1000 write, which
# ExpressNet trims at its last non-zero byte -- every download measured had
# both at zero, so the map never placed them, the builder sent neither, and
# a phone on "every 1 day" got a download that told it never to call
# (2026-10-07: a day of silence from the hallway phone; the emulated board
# leaves its alarm unset on the old download and sets it on this one). In
# this module's header-inclusive coordinates CPU $10D6 is 0x10d7, and $10D7
# is 0x10d8 -- the same number as the next write's header byte, which is
# why the 0x1000 write's bound above is 0x10d9 and image_checksum works in
# CPU addresses. Placed from the firmware, not from an ExpressNet download.
REPORT_SCHEDULE_AT, REPORT_SCHEDULE_OPTIONS = 0x10d7, 272
TEXT_REGION_FROM = 0x1eaf

# The download's checksum: the little-endian word at 0x1001 is SEED plus
# the byte sum of the image with itself and every write's first byte (the
# length mark) left out. Residual exactly 0xAAAA on all 25 full ExpressNet
# downloads (2026-10-01 03:56 .. 2026-10-02 04:05) over every content
# change made on the bench; a KL5-Net session that resent a stale word
# did not fit the rule. Both builders recompute it last.
IMAGE_CHECKSUM_AT = 0x1001
IMAGE_CHECKSUM_SEED = 0xAAAA

# (check byte address, seed, first byte summed, count): image[check] =
# (seed + sum(reporting bytes) + sum(site packed bytes)) & 0xff. Measured
# in the container on three sites (2026-10-04): a site-number
# byte-sum delta of 333 moved the check by 77 = 333 & 0xff, so the SITE's
# own packed number is in the sum, seed 0x55 (the same seed as
# xnetopts.SECURITY_CHECK). The old seed 0x7e over the reporting number
# alone was exactly 0x55 + the hallway phone's own site sum, so it matched
# that one phone and would have mis-stamped the first second-Protel
# download. This form is identical for the hallway phone and correct for
# any site.
REPORTING_CHECK = (0x1067, 0x55, 0x1061, 6)

# The site's own number: packed (packed_ani) at
# 0x105b, and as text "10" + the ten digits at 0x1ecc, over a 27-byte
# field the costing record holds as spaces.
SITE_PACKED_AT = 0x105b
SITE_TEXT_AT = 0x1ecc
SITE_TEXT_LEN = 27

# The dialogue around the writes, constant over 44 of the 61 recorded
# downloads (the rest add a retry or the records read ReplayManager
# splices in itself): Q with the phone's code (filled by kl5net), the
# SW-version read, L, B, the writes, an ACK, two S, the counts read, R,
# the clock (ReplayManager puts now in P), a last S, hang up.
DIALOGUE_HEAD = [("Q", ""), ("D", "ffc515"), ("L", ""), ("B", "41000010601ca000")]
DIALOGUE_TAIL = [("G", ""), ("S", "2400000000000000"), ("S", "0100000000000000"), ("D", "0ff808"),
                 ("R", ""), ("P", ""), ("S", "da1f3ffffffffbff"), ("O", "")]

# E writes whose length follows the record: chunk start -> (exclusive bound
# of what may be sent, the board's region end). Each is sent through its
# last non-zero byte and no further (measured at 0, 1 and 2 restricted
# numbers, and on an NPA header that gained bytes four times), and ITS FIRST
# BYTE IS A LENGTH MARK the board checks: (region end - chunk end) & 0xff.
# 0x1eb7: 76 / 42 / 34 at 0 / 1 / 2 numbers, region end 0x1f44. 0x1711: bc
# b4 b3 b1 b0 at lengths 126 134 135 137 138, region end 0x184b -- found
# the hard way on 2026-10-02 04:18, when a header built from the record
# under the recording's old mark was refused 152 times running.
VARIABLE_CHUNKS = {0x1eb7: (0x20a5, 0x1f44), 0x1711: (0x184b, 0x184b)}

# Image-address windows that are NOT the costing affine copy. Carried from
# the reference download. Measured 2026-10-02 (16:32 capture vs the live
# DEFAULT B1 / DEFAULT COIN records); 32 windows, 253 bytes once windows
# within two bytes of each other are merged for robustness.
# 2026-10-02 02:19: (0x178b, 12), (0x1ee7, 17) and 32 of the 33 bytes at
# 0x1eb7 moved to COST_REGIONS; what is left at 0x1eb7 is the free-space
# mark (computed per chunk in build_writes) and the site's number at 0x1ecc.
# 2026-10-02 04:4x: the site record and a correlation pass against the
# current MISCOPT took the packed-number stretch (0x19bb..), three more
# option splices and the site number out of here; what remains is each
# write's length mark (computed in build_download, carried here) and the
# BOARD_CONSTANTS. The reference path keeps them from its recording.
CARRIED_WINDOWS = [
    (0x1000, 3), (0x105b, 6), (0x10d8, 1), (0x11f0, 1), (0x1308, 1), (0x1330, 1), (0x1429, 1),
    (0x1521, 1), (0x1619, 1), (0x1711, 1), (0x1958, 1), (0x19ba, 1), (0x1a49, 1), (0x1ae4, 1),
    (0x1d4e, 3), (0x1d54, 2), (0x1d59, 2), (0x1d5e, 2), (0x1d63, 2), (0x1d68, 2), (0x1d6d, 2), (0x1d72, 2),
    (0x1d94, 3), (0x1eb7, 1), (0x1ecc, 27), (0x20a6, 1), (0x20ea, 3),
]

# image address -> options record byte, for single registers spliced into
# the image. Overlaid after the carried windows, so a confirmed splice that
# sits inside a carried window (register 133 is inside 0x1000+6) still wins.
# (image address, options record offset, length). Each measured by one
# bench download that changed only that field (CAPTURE_PLAN row 3), the
# export's diff naming the bytes that moved. Nothing here is inferred.
OPTION_SPLICES = [
    (0x1005, 77, 1),     # register 133, Modem Speed. 2026-10-02 (flip 300<->1200)
    (0x20af, 78, 3),     # registers 14x, 15x, 16x (options 78..80): 146 measured 2026-10-02 01:39; 15x (79) by the container sweep 2026-10-04
    (0x104f, 282, 6),    # Miscellaneous Number, the record's packed-BCD copy. 2026-10-02 01:48
    (0x1061, 300, 6),    # the reporting number, packed. By correlation against MISCOPT (2026-10-02 04:4x)
    (0x1331, 274, 8),    # eight option bytes behind the band table's mark; correlation, same pass
    (0x1959, 263, 9),    # the four codes: security + its check (263..265, correlation), then *#1
                         # collection, *#4/*#5 box amount, CDR (266..271) -- placed 2026-10-07 by the
                         # emulator: registers 292, 293, 294 write CPU $195B, $195D, $195F
    (0x1003, 75, 2),     # registers 11x and 12x (options 75, 76): `1a 80` in the record and the image; correlation 2026-10-02 05:xx
    (0x20c2, 81, 3),     # registers 17x, 18x, 19x (options 81..83): 17x/18x by correlation; 19x (83) by the container sweep 2026-10-04
    (0x1068, 307, 1),    # the PBX access number's packed byte (options 307), right after the reporting number's check byte; correlation
    (0x20a7, 310, 7),    # the reporting flags, the record's second copy (options 310..316 = 171..177); 316 placed by the container sweep 2026-10-04
    # Placed by the container sweep, 2026-10-04: one ExpressNet
    # download with every zero field set to its own value, matched byte for byte.
    (0x1055, 288, 6),    # the Validation Access Number, packed
    (0x10ac, 335, 5),    # the unassigned register rows 1A..1E
    (0x1eaf, 331, 3),    # the coin-box removal window: start hour, length (binary); then 333, Coin Box
                         # Reporting Hour (binary) at CPU $1EB0, which the firmware reads beside the window
                         # and checks against 24 ($C115) -- placed from the ROM 2026-10-07
    # Placed 2026-10-07 by keying registers on the emulated board (every
    # ExpressNet download measured had them at zero, so no diff could):
    (0x10d6, 128, 1),    # register 264, Collect Calls per Destination Number (binary, 0-49): CPU $10D5
    (0x1339, 334, 1),    # register 371, Days Before Full (binary, 0-255): CPU $1338
]


class ProtelImageError(Exception):
    pass


def _carried_offsets():
    out = set()
    for addr, length in CARRIED_WINDOWS:
        for a in range(addr, addr + length):
            out.add(a - IMAGE_BASE)
    return out


def image_checksum(writes):
    """The word for 0x1001 over `writes` ((address, data) pairs): SEED plus
    the sum of the CPU memory the writes fill, $1002 on (the word itself is
    $1000/$1001). Little-endian, as the board reads it.

    Each write's first byte is its header check and lands nowhere; data
    byte k lands at CPU address + k - 1. Summed in those coordinates, not
    this module's, because where one write ends at the next one's address
    the last data byte and the next header share a number here: the old
    form let the header shadow the data, which was harmless while that
    byte was always zero and mis-sums once Time of Day Report fills it."""
    mem = {}
    for addr, data in writes:
        for k in range(1, len(data)):
            mem[addr + k - 1] = data[k]
    first = IMAGE_CHECKSUM_AT + 1          # $1002 in CPU addresses
    total = IMAGE_CHECKSUM_SEED + sum(v for a, v in mem.items() if a >= first)
    return total & 0xffff


def _stamp_checks(writes):
    """Put the reporting-number check byte and then the download checksum
    into `writes` (a list of (address, bytes); replaced in place) -- the
    check byte first, since the checksum covers it."""
    check_at, seed, first, count = REPORTING_CHECK
    img = {}
    for addr, data in writes:
        for k, b in enumerate(data):
            img[addr + k] = b
    patches = {}
    if all(first + k in img for k in range(count)) and check_at in img:
        site = sum(img.get(SITE_PACKED_AT + k, 0) for k in range(6))
        patches[check_at] = (seed + sum(img[first + k] for k in range(count)) + site) & 0xff
    for i, (addr, data) in enumerate(writes):
        for a, v in patches.items():
            if addr <= a < addr + len(data):
                buf = bytearray(data)
                buf[a - addr] = v
                writes[i] = (addr, bytes(buf))
                data = writes[i][1]
    word = image_checksum(writes)
    for i, (addr, data) in enumerate(writes):
        if addr <= IMAGE_CHECKSUM_AT and IMAGE_CHECKSUM_AT + 1 < addr + len(data):
            buf = bytearray(data)
            buf[IMAGE_CHECKSUM_AT - addr] = word & 0xff
            buf[IMAGE_CHECKSUM_AT - addr + 1] = word >> 8
            writes[i] = (addr, bytes(buf))
    return writes


def record_offset(addr):
    """The costing record byte behind image address `addr`, or None when
    no region covers it (the 0x2148 tail, anything past IMAGE_END)."""
    found = None
    for a, r, length in COST_REGIONS:
        if a <= addr < a + length:
            found = r + (addr - a)          # later regions win
    return found


def _fill(buf, addr, costing_record, options_record, carried):
    """Overlay the record onto `buf` (image bytes starting at `addr`):
    region bytes from the costing record, carried offsets left alone, then
    the option splices. Mutates and returns `buf`."""
    for i in range(len(buf)):
        a = addr + i
        if (a - IMAGE_BASE) in carried:
            continue                          # keep the reference's byte
        ci = record_offset(a)
        if ci is not None and ci < len(costing_record):
            buf[i] = costing_record[ci]
    if options_record is not None:
        for sa, obyte, length in OPTION_SPLICES:
            for k in range(length):
                a = sa + k
                if addr <= a < addr + len(buf) and obyte + k < len(options_record):
                    buf[a - addr] = options_record[obyte + k]
    return buf


def build(costing_record, reference_image, options_record=None):
    """The download image as bytes, same length as `reference_image`.

    `reference_image` is the historical header-inclusive dense capture view,
    not xnetproto.memory_image (which contains actual CPU data). The
    costing record drives every region-mapped byte; the carried windows
    stay as the reference has them; the confirmed option splices are then
    overlaid from `options_record` when given. Chunk lengths and the
    free-space mark are build_writes' business -- a dense image has no
    chunks.
    """
    return bytes(_fill(bytearray(reference_image), IMAGE_BASE, costing_record, options_record,
                       _carried_offsets()))


def build_writes(costing_record, reference_writes, options_record=None, zones=None):
    """The download as (address, data) writes matching a reference's chunk
    layout. `reference_writes` contains header-inclusive capture chunks,
    not xnetproto.writes(manager_packets) -- the same addresses are reused (and the lengths,
    bar the variable chunks), so the rendered download frames identically;
    only the bytes come from the records.

    Each chunk is built from ITS OWN reference bytes, not a merged image:
    the real download's chunks overlap by a byte at their boundaries (a
    chunk ending at the next one's start address), and the two sides can
    differ there -- the earlier chunk's last byte is a boundary/pad, the
    later chunk's first byte is the real value. Merging would hand both the
    later value; per-chunk keeps each as the board was really sent.

    A chunk in VARIABLE_CHUNKS is built out to its bound and then cut after
    its last non-zero byte, the way the real download sizes it -- so a
    restricted number the console adds lengthens the 0x1eb7 write, and
    removing one shortens it, with no reference that ever held that many.
    Its first byte is then the length mark the board checks.

    With `zones` ({npa: {nxx: band}}, see protelzones), the NPA header is
    generated into the 0x1711 chunk at 0x178b and the reference's tail
    chunks at 0x2148 and after are replaced by the encoder's own."""
    if not reference_writes:
        raise ProtelImageError("no reference writes to shape the download")
    carried = _carried_offsets()
    header, tail = (None, None)
    if zones is not None:
        from . import protelzones
        try:
            header, tail = protelzones.encode(zones)
        except protelzones.ProtelZoneError as exc:
            raise ProtelImageError(str(exc))
    out = []
    for addr, data in reference_writes:
        if header is not None and addr >= protelzones.TAIL_BASE:
            continue                              # the encoder's tail replaces the reference's
        bound, mark_end = VARIABLE_CHUNKS.get(addr, (None, None))
        if bound is not None and bound > addr + len(data):
            buf = bytearray(data) + bytearray(bound - addr - len(data))
        else:
            buf = bytearray(data)
        _fill(buf, addr, costing_record, options_record, carried)
        if header is not None and addr == 0x1711:
            h0 = protelzones.HEADER_BASE - addr
            buf[h0:] = bytearray(len(buf) - h0)   # the record's copy goes; ours follows
            buf[h0:h0 + len(header)] = header
        if bound is not None:
            end = len(buf)
            while end > 1 and buf[end - 1] == 0:
                end -= 1
            buf = buf[:end]
            buf[0] = (mark_end - (addr + len(buf))) & 0xff
        out.append((addr, bytes(buf)))
    if tail:
        out.extend(tail)
    return _stamp_checks(out)


def rebuild_download_script(script, costing_record, options_record=None, zones=None, scheduled_callin=True):
    """A recorded download script with its E writes rebuilt from the records.

    `script` is a stored image's steps -- a list of [type, payload_hex],
    Q D L B E E ... S R P O -- where each E payload is two address bytes
    then the data. The non-E steps (the dialogue: Q, the reads, B, S, R, P,
    O) are left exactly as recorded; only the E payloads are replaced with
    the bytes `build` produces from the records, the recorded image serving
    as the reference for the carried windows. So a console profile edit
    reaches the board, and everything not yet mapped stays as the real
    download had it.

    Returns a new script in the same [type, hex] shape; the input is not
    mutated. The E steps are re-emitted as one run where the first one was:
    their number can change (a zone tail is one write per NPA)."""
    ref = []
    first = None
    for i, step in enumerate(script):
        ptype, payload_hex = step[0], step[1] if len(step) > 1 else ""
        payload = bytes.fromhex(payload_hex or "")
        if ptype == "E":
            from . import xnetproto as xp
            try:
                addr, _ = xp.write_request(xp.Packet("E", payload))
            except ValueError as exc:
                raise ProtelImageError("invalid reference E envelope") from exc
            if first is None:
                first = i
            ref.append((addr, payload[2:]))
    if not ref:
        return [list(step) for step in script]
    if options_record is not None and not scheduled_callin:   # send no self-poll schedule
        from . import xnetopts
        options_record = xnetopts.strip_poll_schedule(options_record)
    built = build_writes(costing_record, ref, options_record, zones)
    out = [list(step) for step in script if step[0] != "E"]
    if any(step[0] == "B" for step in script[first:]):
        raise ProtelImageError("reference fills must precede writes")
    return _render_script(out[:first] + [["E", (addr.to_bytes(2, "big") + data).hex()]
                                      for addr, data in built] + out[first:])


def reconstruct_stats(costing_record, reference_image, options_record=None):
    """How much of `reference_image` the records alone reproduce: build with
    a blank reference (so only record-derived bytes are filled) and count
    matches. The carried windows will not match a blank reference, so this
    is the floor -- the bytes that come from the record with no reference at
    all. A test pins it so a drift in the map or the record layout shows up."""
    blank = bytes(len(reference_image))
    built = build(costing_record, blank, options_record)
    matches = sum(1 for i in range(len(reference_image)) if built[i] == reference_image[i])
    return {"matches": matches, "total": len(reference_image),
            "carried": len(_carried_offsets())}


def build_download(costing_record, options_record, ani, zones=None, scheduled_callin=True):
    """Every E write of a download, from the records alone: no recording.

    The image is the costing record in its four regions, the option
    splices, the site's number twice, the NPA header and tail from
    `zones` (protelzones; None sends the pseudo-NPA alone), and the
    BOARD_CONSTANTS; each write is cut to its CHUNKS length (or after its
    last non-zero byte for the two that follow the record) and given its
    length mark; then the reporting number's check byte and the
    download's checksum word are computed over the result. Built this way from the live records it reproduced the
    04:05 ExpressNet download of 2026-10-02 byte for byte."""
    from . import protelzones, xnetcost, xnetopts
    if len(costing_record) != xnetcost.RECLEN or len(options_record) != xnetopts.RECLEN:
        raise ProtelImageError('complete costing and options records are required')
    if not scheduled_callin:                    # public/beta phone: send no self-poll schedule
        options_record = xnetopts.strip_poll_schedule(options_record)
    img = {}
    for a, r, n in COST_REGIONS:
        for k in range(n):
            if r + k < len(costing_record):
                img[a + k] = costing_record[r + k]
    for a, o, n in OPTION_SPLICES:
        for k in range(n):
            if o + k < len(options_record):
                img[a + k] = options_record[o + k]
    for a, b in BOARD_CONSTANTS.items():
        for k, v in enumerate(b):
            img[a + k] = v
    for k in range(2):
        img[REPORT_SCHEDULE_AT + k] = options_record[REPORT_SCHEDULE_OPTIONS + k]
    for k in range(6):
        hours = options_record[TOD_OPTIONS + k]
        img[TOD_AT + k] = (((hours // 10) % 10) << 4) | (hours % 10)   # BCD; hours are 0-24 on the form
    digits = "".join(c for c in str(ani or "") if c.isdigit())[-10:]
    if len(digits) != 10:
        raise ProtelImageError("the site number is ten digits; have %r" % (ani,))
    packed = packed_ani(digits)
    for k in range(6):
        img[SITE_PACKED_AT + k] = packed[k]
    text = ("10" + digits).encode()
    for k in range(SITE_TEXT_LEN):
        img[SITE_TEXT_AT + k] = text[k] if k < len(text) else 0
    try:
        header, tail = protelzones.encode(zones or {})
    except protelzones.ProtelZoneError as exc:
        raise ProtelImageError(str(exc))
    for a in range(protelzones.HEADER_BASE, 0x184b):
        img[a] = 0
    for k, v in enumerate(header):
        img[protelzones.HEADER_BASE + k] = v
    # The reporting check byte is part of what trimming looks at, so it is
    # computed first (a check byte left at zero could otherwise be trimmed
    # off the end of the 0x1000 write); _stamp_checks recomputes it the same.
    check_at, seed, first, count = REPORTING_CHECK
    img[check_at] = (seed + sum(img.get(first + k, 0) for k in range(count))
                     + sum(img.get(SITE_PACKED_AT + k, 0) for k in range(6))) & 0xff
    out = []
    for key, length, bound in CHUNKS:
        addr = key
        if addr == 0x1eb7:
            early = next((a for a in range(TEXT_REGION_FROM, addr + 1) if img.get(a, 0)), None)
            if early is not None and early - 1 < addr:
                addr = early - 1
        if addr in TRIMMED:
            buf = bytearray(img.get(a, 0) for a in range(addr, TRIMMED[addr]))
            end = len(buf)
            while end > max(1, length if addr in TRIM_AT_LEAST else 1) and buf[end - 1] == 0:
                end -= 1
            buf = buf[:end]
        elif length is None:
            extent = VARIABLE_CHUNKS[key][0]
            buf = bytearray(img.get(a, 0) for a in range(addr, extent))
            end = len(buf)
            while end > 1 and buf[end - 1] == 0:
                end -= 1
            buf = buf[:end]
        else:
            buf = bytearray(img.get(a, 0) for a in range(addr, addr + length))
        if len(buf) < 2:
            continue                     # nothing but zeros: ExpressNet sends no write for it
        buf[0] = (bound - (addr + len(buf))) & 0xff
        out.append((addr, bytes(buf)))
    out.extend(tail)
    return _stamp_checks(out)


# Splitting a write. Every E handler we hold (DD8822 $AD03, DD7018-356
# $A40B, DD7018-800 $A972, DD7019-231 $AA70, DD4318 $A4F6 -- the same
# instructions in all five) reads the address, checks the inner header,
# then stores each data byte through the $20/$21 unlock AS IT ARRIVES,
# counting down the packet's own length byte. There is no table of region
# ends, no buffer and no limit but the length byte's (250 data bytes).
# The "length mark" this module computes as (region end - write end) is
# that inner header check, ~(T + len + E + addr_hi + addr_lo): the two
# agree for every CHUNKS entry, because each "region end" is just
# addr + 0x62 - addr_hi - addr_lo (checked for every entry).
# So a write may be cut anywhere. One thing is read after the bytes: when $105A, the
# ANI's first byte, is zero, the handler zeroes the stored checksum and
# answers as it does a bad packet, so a board with no ANI yet must find
# one inside the first write that reaches $105A, as the unsplit download
# always gave it.
ANI_FIRST = 0x105A
MAX_E_PAYLOAD = 250


def e_header_check(address, count):
    """The inner header check of an E write of `count` data bytes at CPU
    `address`: what the board compares before it stores anything."""
    return (~(0x54 + count + 5 + 0x45 + (address >> 8) + (address & 0xFF))) & 0xFF


def _even_cuts(start, end, pieces):
    return [start + round(k * (end - start) / pieces) for k in range(1, pieces)]


def split_writes(writes, max_payload):
    """`writes` (this module's header-inclusive (address, bytes) pairs) with
    every write longer than `max_payload` data bytes cut into pieces of
    nearly equal size, each with its own header check. The data, its order
    and so the image are unchanged; only the packets are shorter. A write
    holding $105A (ANI_FIRST) keeps it in its first piece. None leaves the
    writes as they are."""
    if max_payload is None:
        return list(writes)
    if not 1 <= max_payload <= MAX_E_PAYLOAD:
        raise ProtelImageError("an E write carries 1..%d data bytes, not %r" % (MAX_E_PAYLOAD, max_payload))
    out = []
    for addr, data in writes:
        body = bytes(data[1:])
        n = len(body)
        if n <= max_payload:
            out.append((addr, bytes(data)))
            continue
        cuts = _even_cuts(0, n, -(-n // max_payload))
        need = ANI_FIRST - addr + 1
        if addr <= ANI_FIRST < addr + n and cuts[0] < need:
            if need > max_payload:
                raise ProtelImageError("the first write must reach $%04X: %d bytes, more than %d"
                                       % (ANI_FIRST, need, max_payload))
            cuts = [need] + _even_cuts(need, n, -(-(n - need) // max_payload))
        edges = [0] + cuts + [n]
        for lo, hi in zip(edges, edges[1:]):
            out.append((addr + lo, bytes([e_header_check(addr + lo, hi - lo)]) + body[lo:hi]))
    return out


def download_script(writes):
    """The whole download as a script -- [type, hex] steps for kl5net's
    build_script and ReplayManager -- around the given writes. The Q's
    payload is empty here (kl5net puts the phone's code in) and so is
    P's (the manager sends the clock as now)."""
    steps = [list(step) for step in DIALOGUE_HEAD]
    steps += [["E", (bytes([addr >> 8, addr & 0xff]) + data).hex()] for addr, data in writes]
    steps += [list(step) for step in DIALOGUE_TAIL]
    return _render_script(steps)



def _render_script(steps):
    """Convert measured chunks to frames; checksum the explicitly filled SRAM."""
    from . import protel_firmware_xnet as wire, xnetproto as xp
    memory = {}
    rendered = [list(step) for step in steps]
    writes = []
    for i, (kind, payload) in enumerate(rendered):
        p = bytes.fromhex(payload)
        if kind == 'B':
            if len(p) != 8 or p[:3] != b'A\0\0':
                raise ProtelImageError('composer requires a bank-zero fill')
            a, n = int.from_bytes(p[3:5], 'big'), int.from_bytes(p[5:7], 'big')
            if not n or a < 0x1000 or a+n > 0x2d00:
                raise ProtelImageError('composer fill outside configuration')
            memory.update((x, p[7]) for x in range(a, a+n))
        elif kind == 'E':
            a = int.from_bytes(p[:2], 'big')
            data = bytearray(p[3:])  # offset two is the capture's inner header
            if not data or a < 0x1000 or a+len(data) > 0x2d00:
                raise ProtelImageError('composer write outside configuration')
            memory.update((a+k, v) for k,v in enumerate(data))
            writes.append((i,a,data))
    if any(a not in memory for a in range(0x1000,0x2d00)):
        raise ProtelImageError('composer requires explicit full configuration coverage')
    word = (0xAAAA + sum(memory[a] for a in range(0x1002,0x2d00))) & 0xffff
    # Patch the last writer of each checksum byte, preserving overlap order.
    for target, value in ((0x1000, word & 255), (0x1001, word >> 8)):
        for _, a, data in reversed(writes):
            if a <= target < a+len(data):
                data[target-a] = value
                break
        else:
            raise ProtelImageError('composer has no checksum writer')
    for i,a,data in reversed(writes):
        blocks = []
        for offset in range(0,len(data),250):
            packet = xp.decode(wire.memory_write(a+offset,data[offset:offset+250]))[0][0]
            blocks.append(['E',packet.payload.hex()])
        rendered[i:i+1] = blocks
    # Independently use the shared decoder to check every envelope and checksum.
    wire.configuration_from_packets([xp.Packet(k,bytes.fromhex(p)) for k,p in rendered])
    return rendered


def packed_ani(ani):
    """The ANI the way the board sends it: 12 nibbles, a leading 0, the ten
    digits with 0 written as A, a trailing 0. 2125550101 measured as
    02 12 55 5a 1a 10."""
    nibbles = [0] + [0xA if c == "0" else int(c) for c in ani] + [0]
    return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, 12, 2))

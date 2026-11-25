"""The NPA/NXX zone tables of a Protel download, built from the console.

Addresses and returned chunks here use the historical header-inclusive
capture view. The lead byte is the E inner header checksum, not CPU data.
protelimage's script renderer strips/recomputes it; only the remaining bytes
are written to SRAM. Per-chunk offsets cannot be shifted as a global image.

ExpressNet keeps which exchange is in which cost band on its own screens
(EDIT NXXs IN MULTI-BAND NPAs) and packs them into the download in
CONVCOST.C. This module is that packer, written from a decompile of
ExpressNet's own code (2026-10-02) and checked against nine real
downloads; no ExpressNet code or data is included here:

  * **The header**, at image 0x178b (the tail of the 0x1711 E write): per
    NPA `[NPA as BCD, 0 as the nibble A][flag][band numbers in use]`,
    band 0 always listed first; `flag = (bits << 5) | (entries - 1)`.
    Then the single-band table's pseudo-NPA `01 11 20 40`, carried as a
    constant until that table is mapped (the console has no single-band
    NPAs yet).
  * **Bits per cell** from the entry count: 1-2 entries -> 1 bit, 3-4 ->
    2, 5-8 -> 3, 9-16 -> 4, more -> 5 (FUN_1058_00e8). The sum over all
    NPAs may not exceed 32 -- ExpressNet's "NPA Too large to fit in new
    format".
  * **A cell is the INDEX of the exchange's band in that NPA's entry
    list**, not the band number (FUN_1058_0140): with entries [0, 5] an
    exchange in band 5 is cell value 1.
  * **Cell order** (FUN_1058_0000): cell = 100 * (hundreds - 2) + k, k
    from a fixed table that ExpressNet derives from its bitmap index
    (NXX mod 100) - 1 through `(k + 89) % 100`, bumped by 10 when that
    ends in 9. Measured: 200, 210, 220, 230, 240 are cells 0, 10, 20,
    30, 40 of their hundred.
  * **Packing** (FUN_1058_0243): 800 cells of `bits` each, MSB-first, one
    NPA after the next, 100 * bits bytes per NPA.
  * **The lead byte** is not CONVCOST's; the download assembler writes it.
    Measured: the first NPA's lead precedes its cells at 0x2148 and is
    `0xf8 - n` with n the bytes through the last non-zero cell byte; the
    second NPA's lead sits ON its first cell byte (which the packer's
    arithmetic puts right after the first NPA's 100*bits bytes) and is
    `0xf8 - (offset + n)`, offset its distance from 0x2148. Two two-NPA
    downloads measured (one and two exchanges in the second NPA).
  * Each NPA goes out as its own E write, cut after its last non-zero
    byte, the way ExpressNet chunks it.
  * **An NPA with no exchanges still owns its 100 * bits bytes.** The
    firmware finds an NPA's cells by adding up the blocks before it, as
    the packer lays them out; only the E write is left out, the bank fill
    zeroing them. Skipping the space (as this packer did until 2026-11-21)
    gave a bandless home area code the toll-free codes' cells, and every
    local call rated in band 8 and errored (DD8822 emulator).
  * **The single-band NPA table** ("Edit Single-Band NPAs": a whole area
    code in one band) is the pseudo-NPA `01 11` that closes the header.
    Its bands are **cost bands 64-79** (the screen shows every area code
    in band 64 until set: "Cost Band# 64" on its status line, 2026-10-02
    03:27), so the entries are the band numbers in use, 64 first, and
    the `0x40` the decompile adds to a 4-bit value IS 64. Band 72 (value
    8) is never listed -- the code skips it. Cells are indexed `101 *
    (first digit - 2) + 10 * middle digit + last digit`, so **only the
    old-format area codes N0X / N1X fit** (160 of them -- ExpressNet 1.55
    predates 267 and 484; the screen lists the others but "NPA Too large
    to fit in new format" is what setting one gets). Packed and appended
    like one more NPA. Measured on the wire 2026-10-02 04:05: 212 and
    410 in band 66 gave header `01 11 21 40 42` and a third block whose
    lead sat on its first cell byte, with cells 12 and 212 set -- what
    this packer emits. An area code nobody lists is rated by band 64 --
    the table's default, not band 0 -- which is why a rate plan sets
    band 64 too.

Bands are 0-79 per NPA, the range ExpressNet's own grid states ("Cost
bands range 0 to 79"), and an entry is the band number itself. This
used to stop at 7 because only bands 0-7 had been measured. On
2026-10-04 ExpressNet in the container downloaded NPA 215 with bands 8, 9,
15, 40, 79 and 3: header `02 15 66 00 03 08 09 0f 28 4f`, seven entries at
three bits, and this packer reproduced the whole download byte for byte
(and the two-band case before it). Pure: a mapping in, bytes out.
"""

TAIL_BASE = 0x2148          # the first NPA's lead byte; its cells from 0x2149
HEADER_BASE = 0x178b
SINGLE_BAND_TAIL = bytes.fromhex("01112040")   # the single-band NPA table, carried
MAX_BITS = 32
MAX_BAND = 79               # the multi-band grid's own range; measured to 79, 2026-10-04
SINGLE_BASE = 64            # the single-band table's bands are cost bands 64..79; 64 is every code's default
SINGLE_MAX_BAND = 79
SINGLE_SKIP_BAND = 72       # FUN_1058_03d6 never lists value 8 (band 72) in the single-band entries


class ProtelZoneError(Exception):
    pass


def _k_table():
    inv = {}
    for k in range(100):
        j = (k + 89) % 100
        if j % 10 == 9:
            j += 10
        inv[j % 100] = k
    return inv


_K = _k_table()


def cell(nxx):
    """The cell index (0..799) of an exchange."""
    nxx = int(nxx)
    if not 200 <= nxx <= 999:
        raise ProtelZoneError("an NXX is 200-999, not %r" % (nxx,))
    h, d = divmod(nxx, 100)
    return 100 * (h - 2) + _K[(d - 1) % 100]


def bits_for(entries):
    n = entries - 1
    return 1 if n < 2 else 2 if n < 4 else 3 if n < 8 else 4 if n < 16 else 5


def npa_bcd(npa):
    npa = int(npa)
    if not 200 <= npa <= 999:
        raise ProtelZoneError("an NPA is 200-999, not %r" % (npa,))
    nib = [0xa if c == "0" else int(c) for c in "%03d" % npa]
    return bytes([nib[0], (nib[1] << 4) | nib[2]])


def single_cell(npa):
    """The cell of an area code in the single-band table: old-format
    N0X / N1X only."""
    npa = int(npa)
    h, rest = divmod(npa, 100)
    mid, d = divmod(rest, 10)
    if not (2 <= h <= 9 and mid <= 1):
        raise ProtelZoneError("the single-band table takes old-format area codes (N0X / N1X), not %d" % npa)
    return 101 * (h - 2) + 10 * mid + d


def normalize(zones):
    """{"multi": {npa: {nxx: band}}, "single": {npa: band}} with int keys
    and values, validated. A plain {npa: {nxx: band}} (the first shape
    stored) is taken as "multi". The multi order is kept (it is the
    header order and the tail order); an NPA with no exchanges is kept
    too, since ExpressNet lists it with no bands."""
    zones = zones or {}
    if "multi" in zones or "single" in zones:
        multi, single = zones.get("multi") or {}, zones.get("single") or {}
    else:
        multi, single = zones, {}
    out = {"multi": {}, "single": {}}
    for npa, m in multi.items():
        npa = int(npa)
        npa_bcd(npa)
        exch = {}
        for nxx, band in (m or {}).items():
            nxx, band = int(nxx), int(band)
            cell(nxx)
            if not 0 <= band <= MAX_BAND:
                raise ProtelZoneError("band %d on %d-%d: the console packs bands 0-%d" % (band, npa, nxx, MAX_BAND))
            if band:
                exch[nxx] = band
        out["multi"][npa] = exch
    for npa, band in single.items():
        npa, band = int(npa), int(band)
        single_cell(npa)
        if not SINGLE_BASE <= band <= SINGLE_MAX_BAND or band == SINGLE_SKIP_BAND:
            raise ProtelZoneError("band %d on area code %d: the single-band table takes bands %d-%d except %d"
                                  % (band, npa, SINGLE_BASE, SINGLE_MAX_BAND, SINGLE_SKIP_BAND))
        if band != SINGLE_BASE:
            out["single"][npa] = band
    return out


def _pack(cells, bits):
    packed = bytearray(100 * bits)
    bp = 0
    for v in cells:
        for i in range(bits - 1, -1, -1):
            if (v >> i) & 1:
                packed[bp >> 3] |= 0x80 >> (bp & 7)
            bp += 1
    return packed


def encode(zones):
    """(header bytes for 0x178b, [(address, bytes) E writes for the tail]).
    `zones` as normalize() takes it."""
    zones = normalize(zones)
    header = b""
    chunks = []
    offset = 0                                    # distance from TAIL_BASE
    total_bits = 0
    first = True

    def emit(packed):
        nonlocal offset, first
        span = max((i + 1 for i, b in enumerate(packed) if b), default=0)
        lead = (0xf8 - (offset + span)) & 0xff
        if first:
            block = bytes([lead]) + bytes(packed)
            first = False
        else:
            packed[0] |= lead
            block = bytes(packed)
        end = len(block)
        while end > 1 and block[end - 1] == 0:
            end -= 1
        if span:                                  # an all-zero NPA is left to the bank fill
            chunks.append((TAIL_BASE + offset, block[:end]))
        offset += len(block)

    for npa, exch in zones["multi"].items():
        bands = sorted(set(exch.values()) | {0})
        bits = bits_for(len(bands))
        total_bits += bits
        if total_bits > MAX_BITS:
            raise ProtelZoneError("NPA tables too large: more than %d bits per exchange over all NPAs" % MAX_BITS)
        header += npa_bcd(npa) + bytes([(bits << 5) | (len(bands) - 1)]) + bytes(bands)
        # A bandless NPA still owns its 100 * bits bytes of the tail: the
        # firmware finds each NPA's cells by adding up the blocks before
        # it, so skipping one handed the home area code the next NPA's
        # cells (all free-band ones), an index past its one entry, and a
        # band read out of the header (8, from 888's BCD): every local
        # call errored (2026-11-21, DD8822 emulator, MNCam's report).
        cells = [0] * 800
        for nxx, band in exch.items():
            cells[cell(nxx)] = bands.index(band)
        emit(_pack(cells, bits))
    single = zones["single"]
    if not single:
        header += SINGLE_BAND_TAIL                # `01 11 20 40`: one entry, band 0, no cells
    else:
        entries = [SINGLE_BASE] + sorted(set(single.values()))
        bits = bits_for(len(entries))
        total_bits += bits
        if total_bits > MAX_BITS:
            raise ProtelZoneError("NPA tables too large: more than %d bits per exchange over all NPAs" % MAX_BITS)
        header += bytes.fromhex("0111") + bytes([(bits << 5) | (len(entries) - 1)]) + bytes(entries)
        cells = [0] * 800
        for npa, band in single.items():
            cells[single_cell(npa)] = entries.index(band)
        emit(_pack(cells, bits))
    return header, chunks


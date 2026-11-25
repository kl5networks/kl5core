"""XNet on the wire, as measured off our own Protel board -- and the bones
of a manager that speaks it without ExpressNet.

Everything here was read from recorded call-ins of our own board
(2026-10-01); nothing is from a manual, because
no manual publishes it. The parts that are measured:

  * **Framing.** `T` (0x54), a length byte, a type letter, the payload,
    a checksum. The length counts every byte after itself, the checksum
    INCLUDED -- `54 02 47 62` is a whole ACK. The checksum is the one's
    complement of the byte sum of everything before it.
  * **Types.** From the board: `!` call-in (its number, firmware, a
    four-digit field, its clock), `G` ack, `D`/`L` answers to the
    manager's reads, `E` "send that again", `N` refusal. From the
    manager: `Q` (opens with a four-digit code), `D addr len` read,
    `L`, `B` begin, `E addr data` sparse memory writes (the download:
    0x1000-0x20ea on our board), `N`, `S` settings, `R`, `P` set the
    board's clock, `O` end.
  * **The dialogue.** The board sends `!` (again after 27.5 s if nothing
    answers); the manager replies and the board ACKs each step with `G`,
    or answers a read with `D`/`L`. A download is the manager's `E`
    writes, each ACKed. The session ends with `P` then `O`.

The parts that are NOT known are left as data, never guessed at: what
the four-digit field in the call-in is for, what the two memory reads
return, some historical upload-record field meanings. The
decoder keeps every byte so the JSON export carries them whole.

`ReplayManager` is the bones of a manager: a pure state machine, no
sockets, that answers a call-in by replaying the manager side of a
recorded session, ACK by ACK, with a fresh clock. It is how a board gets
re-downloaded without the original manager software once the records are understood well enough
to be composed rather than replayed -- and it is tested against the
dialogue shape above, so the day a real session disagrees with it the
test is where the disagreement is written down.

Everything is stdlib and pure, in the same spirit as the uplink: bytes
in, bytes out, no I/O, so every case is a unit test.
"""
import re
import time
from collections import namedtuple

SYNC = 0x54

Packet = namedtuple("Packet", "type payload")

# What each type means, per direction. The same letter is not the same
# thing both ways: E from the manager is a write, E from the board is
# "send that again"; D from the manager is a read, D from the board the
# answer. BOARD is A>B in a relay capture, MANAGER is B>A.
BOARD_TYPES = {
    "!": "call-in", "G": "ack", "D": "read answer", "L": "L answer",
    "E": "send again", "N": "refused",
    # The reply every ROM sends for a letter it has no handler for, and
    # from G when the configuration check fails (7000 $AAF1, DD8822 $AD9B). On a line it
    # is a packet whose T, length or letter was hit.
    "F": "not understood",
}

# What the byte after a board's N says, read from the ROMs (the 7000s and
# the DD8822 alike). The board hangs up after every one of them.
N_REASONS = {
    1: "the manager sent N",
    3: "not logged in: the packet it read was not a Q (a hit on the Q, not a wrong code)",
    4: "no packet for about 20 s",
}


def n_reason(payload):
    """What a board's N payload means, for a capture note."""
    if not payload:
        return "N with no reason"
    return "N %02x: %s" % (payload[0], N_REASONS.get(payload[0], "reason not known"))


MANAGER_TYPES = {
    "Q": "open (code)", "D": "read addr len", "L": "L", "B": "begin",
    "E": "write addr data", "N": "nak", "S": "settings", "R": "R",
    "P": "set clock", "O": "end",
    # Seen 2026-10-01 on the first call-in with records: after reading the
    # call records (D 3a00, len = 23 bytes per record) the manager sends K
    # with two zero bytes, and the board's record count is zero on the next
    # call. Read as "records taken"; the name is ours, not Protel's.
    "K": "records taken",
}

# Memory the manager reads. The CDR block at 0x3a00 is a run of 23-byte
# call records (CDR_RECORD_LEN); the count read at 0x0ff8 says how many.
ADDR_NAMES = {0xffc5: "SW version", 0x0ff8: "CDR counts", 0x3a00: "call records"}
CDR_RECORD_LEN = 23
CDR_RECORDS_ADDR = 0x3a00


def unpack_number(raw):
    """A phone number as the board packs it: nibbles, leading 0, digit 0
    written as A, 0 ends it."""
    out = []
    for b in raw:
        for n in (b >> 4, b & 0xf):
            out.append(n)
    digits = ""
    for n in out[1:]:
        if n == 0:
            break
        digits += "0" if n == 0xa else str(n)
    return digits


CDR_MAX_RECORDS = 10          # one read is one packet: 10 x 23 fits under the 253-byte payload limit
MAX_RESENDS = 8               # the same packet refused this often is refused for good: the session ends `stuck`


CDR_RING_RECORDS = 125        # module 6 files into a ring of 125 at 0x3a00 (seen in an emulator)
CDR_RING_END = CDR_RECORDS_ADDR + CDR_RING_RECORDS * CDR_RECORD_LEN


def cdr_ring(payload):
    """(first, count) from the board's answer to the counts read at 0x0ff8:
    the ring slot of the oldest record not yet taken ($0FFC) and how many
    are waiting ($0FFD), the pair mirrored at $0FFE/$0FFF.

    This used to be "the largest of the eight bytes", which only held while
    `first` was 0 -- a board's first upload, the only kind ever captured
    (2026-10-01: `00 00 00 00 00 04 00 04`). K moves `first` past every
    record it clears, so on the emulated board (2026-10-06) the next
    call-in's counts were `00 00 00 00 0c 02 0c 02`: two new records at
    slots 12-13, read by the old rule as twelve at 0x3a00 -- ten stale ones
    filed again, then K cleared the two new ones unread."""
    address, data = read_answer(Packet('D', payload))
    if address != 0x0ff8 or len(data) != 8:
        raise ValueError('expected eight DD8822 pending-count bytes')
    first, count = data[4], data[5]
    if data[6:8] != data[4:6] or first >= CDR_RING_RECORDS or count > CDR_RING_RECORDS:
        raise ValueError('DD8822 pending counts are not a ring position and its mirror')
    return first, count


def cdr_count(payload):
    """How many records the board says it holds (cdr_ring's count)."""
    return cdr_ring(payload)[1]


def cdr_reads(first, count):
    """The D packets that read `count` records from ring slot `first`:
    one per run of at most CDR_MAX_RECORDS, split where the ring wraps.
    K clears every waiting record, read or not (emulator, 2026-10-06:
    twelve waiting, ten read, K, and the count was 0), so all of them are
    read before it."""
    out = []
    slot, left = first, count
    while left:
        n = min(left, CDR_MAX_RECORDS, CDR_RING_RECORDS - slot)
        addr = CDR_RECORDS_ADDR + slot * CDR_RECORD_LEN
        out.append(Packet("D", bytes([addr >> 8, addr & 0xff, n * CDR_RECORD_LEN])))
        slot, left = (slot + n) % CDR_RING_RECORDS, left - n
    return out


def is_cdr_address(addr):
    """A record's start inside the ring."""
    return CDR_RECORDS_ADDR <= addr < CDR_RING_END and (addr - CDR_RECORDS_ADDR) % CDR_RECORD_LEN == 0


def read_answer(packet):
    """Validate the DD8822 header checksum before exposing D reply data.

    Callers must first verify outer framing with decode(). The checksum
    between the address and data is part of the envelope, never memory.
    """
    payload = bytes(packet.payload)
    if packet.type != 'D' or not 4 <= len(payload) <= 253:
        raise ValueError('expected a DD8822 D answer with header checksum and data')
    header = bytes([SYNC, len(payload) + 2, ord('D')]) + payload[:2]
    if payload[2] != checksum(header):
        raise ValueError('DD8822 D answer header checksum mismatch')
    return int.from_bytes(payload[:2], 'big'), payload[3:]


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def parse_cdr_block(payload):
    """Verified DD8822 answer: address, header checksum, 23-byte records.
    Invalid headers, wrong addresses and partial records yield no records.
    Earlier header-free synthetic fixtures are not firmware reply envelopes.

    The layout, by offset in the header-stripped body -- matched field by
    field against ExpressNet's own decode of four real calls (its
    AUDITCDR.DAT, 2026-10-05: duration, both amounts and the 0x4e byte
    agree on every call):

        number(7, packed)  00 4e 00  MM DD HH MM  HH MM SS  amt1  amt2  ck16
           0-6               7-9      10-13        14-16    17-18 19-20  21-22

    MM DD HH MM is the board's own clock and HH MM SS the call's duration,
    both BCD (the first parse read byte 17 alone as binary seconds: 71
    for a 47 s call). amt1/amt2 are little-endian 16-bit amounts in 5-cent
    units (ExpressNet prints word * 0.05). An emulator settled them
    with deposit != charge: amt1 is the charge, amt2 the coins
    already booked out of escrow when the record is written (0 while the
    initial period is unlimited, the whole charge once a timed period has
    begun) -- the real calls' 25c/25c is a timed band, not a repeat.
    An unanswered call writes no record at all.

    bytes 21-22 are the 16-bit sum of bytes 0-20, little-endian. An early reading took
    byte 21 as an 8-bit checksum and byte 22 as a status (02 answered /
    03 intercept); an emulator later showed that there is no status
    -- byte 22 is the sum's HIGH byte, and the real calls' 02/03 were just
    that byte. check_ok verifies the whole 16 bits.

    The first records were filed with the header's checksum as byte 0,
    which shifted every field one place. The dict keys stay what
    db.file_protel_cdr stores: `flag` is byte 22 (the checksum high byte,
    not a status), secs the duration, w1/w2 the raw duration and amount
    bytes."""
    try:
        address, body = read_answer(Packet('D', payload))
    except ValueError:
        return []
    if not is_cdr_address(address) or len(body) % CDR_RECORD_LEN:
        return []
    out = []
    for k in range(0, len(body) - CDR_RECORD_LEN + 1, CDR_RECORD_LEN):
        r = body[k:k + CDR_RECORD_LEN]
        out.append({**cdr_record_fields(r), "raw": r.hex()})
    return out


def cdr_record_fields(r):
    """The fields of one 23-byte DD8822 call record, by the offsets in
    parse_cdr_block's docstring. Split out so db.reparse_protel_cdr can
    re-align a row off its stored `raw` with the SAME offsets a fresh
    parse uses -- a single source of truth, so a realigned row and a
    freshly parsed one can never read a field differently.

    check_ok is the record's own proof the 23 bytes are a true record:
    bytes 21-22 are the 16-bit sum of bytes 0-20, little-endian (21 low,
    22 high), not an 8-bit sum and a status as first read. It is what tells a re-alignment that a
    stored `raw` really is the record (and not a pre-fix read that kept
    the envelope's header checksum and so lost a byte off the end).

    `flag` is byte 22, kept only because db.file_protel_cdr stores it in a
    column of that name; it is the checksum high byte, NOT a status (there
    is no status byte -- the real calls' 02/03 were that high byte)."""
    return {"flag": r[22], "number": unpack_number(r[0:7]), "const": r[7:10].hex(),
            "when": "%02x/%02x %02x:%02x" % (r[10], r[11], r[12], r[13]),
            "secs": _bcd(r[14]) * 3600 + _bcd(r[15]) * 60 + _bcd(r[16]),
            "w1": r[14:17].hex(), "w2": r[17:21].hex(), "tail": r[21:23].hex(),
            "cents1": ((r[18] << 8) | r[17]) * 5, "cents2": ((r[20] << 8) | r[19]) * 5,
            "check_ok": (sum(r[:21]) & 0xFFFF) == (r[21] | (r[22] << 8))}


def checksum(body):
    """One's complement of the byte sum: what follows the payload."""
    return (~sum(body)) & 0xFF


def frame(ptype, payload=b""):
    """A whole packet: sync, length (type + payload + checksum), type,
    payload, checksum. `frame("G")` is `54 02 47 62`."""
    payload = bytes(payload)
    if len(payload) > 253:
        raise ValueError("payload too long for one packet (%d)" % len(payload))
    body = bytes([SYNC, len(payload) + 2, ord(ptype)]) + payload
    return body + bytes([checksum(body)])


def _callin_after(data, start):
    """Offset of the first verified `!` packet at or after `start`, or None."""
    j = data.find(bytes([SYNC]), start)
    while j != -1 and j + 2 < len(data):
        length = data[j + 1]
        end = j + 2 + length
        if length >= 2 and end <= len(data) and data[j + 2] == ord("!") \
                and checksum(data[j:end - 1]) == data[end - 1]:
            return j
        j = data.find(bytes([SYNC]), j + 1)
    return None


def decode(data, final=True):
    """Packets out of a byte stream.

    Returns (packets, skipped, leftover): `skipped` is how many bytes were
    passed over because no verified packet started there (modem noise,
    carrier-up garbage, a 0x54 inside somebody's data). With `final`
    false the stream is still arriving: a trailing fragment that could be
    the head of an unfinished packet comes back as `leftover` for the
    caller to keep and feed the next bytes behind. With `final` true (a
    whole capture) nothing is held back -- a stray 0x54 near the end
    whose length byte points past the data is noise, and holding it
    would hide every packet after it. A verified packet is a sync byte,
    a length of at least 2, and a checksum that matches; nothing shorter
    is believed, because a bare 0x54 appears in every download's data."""
    data = bytes(data)
    packets, skipped, i, n = [], 0, 0, len(data)
    while i < n:
        if data[i] == SYNC:
            if i + 1 >= n:
                if not final:
                    return packets, skipped, data[i:]
                return packets, skipped + 1, b""
            length = data[i + 1]
            if length >= 2:
                end = i + 2 + length
                if end > n:
                    if not final:
                        # Before waiting on this fragment, look for a whole
                        # call-in behind it: a junk 0x54 with a big length
                        # byte (V.22 training garbage ahead of a polled
                        # board's `!`, 2026-10-06) would otherwise hold the
                        # real call-in until that many more bytes arrived.
                        # Only a `!` is trusted this way -- mid-dialogue a
                        # stray match inside a long packet's data is likelier.
                        later = _callin_after(data, i + 1)
                        if later is not None:
                            skipped += later - i
                            i = later
                            continue
                        return packets, skipped, data[i:]
                    skipped += 1
                    i += 1
                    continue
                body = data[i:end - 1]
                if checksum(body) == data[end - 1]:
                    packets.append(Packet(chr(body[2]), bytes(body[3:])))
                    i = end
                    continue
        skipped += 1
        i += 1
    return packets, skipped, b""


# -- the call-in ------------------------------------------------------------------

CALLIN_PREFIX = 8          # 04 00 00 00 00 00 00 00 ahead of the text, so far constant


def parse_callin(payload):
    """The `!` packet's payload: an 8-byte prefix, then `*`-separated ASCII
    fields. Field 1 is the board's number, 3 its firmware (`DD8822`), 6 its
    clock as YYMMDD, day of week (Sunday = 1), HHMMSS. Field 4 is four
    digits that changed between two downloads; it is reported, not named.
    Everything is returned whole so nothing is lost to a wrong label."""
    prefix = bytes(payload[:CALLIN_PREFIX])
    text = bytes(payload[CALLIN_PREFIX:]).split(b"\x01", 1)[0].decode("ascii", "replace")
    fields = text.split("*")
    out = {"prefix": prefix.hex(), "fields": fields, "ani": "", "firmware": "", "field4": "", "clock": ""}
    if len(fields) > 1:
        out["ani"] = fields[1]
    if len(fields) > 3:
        out["firmware"] = fields[3]
    if len(fields) > 4:
        out["field4"] = fields[4]
    if len(fields) > 6 and re.match(r"^\d{13}$", fields[6]):
        c = fields[6]
        out["clock"] = "20%s-%s-%s %s:%s:%s (dow %s)" % (c[0:2], c[2:4], c[4:6], c[7:9], c[9:11], c[11:13], c[6])
    return out


def clock_payload(t=None):
    """The `P` payload for a time: YYMMDD, day of week with Sunday = 1,
    HHMMSS -- `2610015114005` is Thursday 2026-10-01 11:40:05, which is
    what ExpressNet sent and the board's own call-in agrees on."""
    lt = time.localtime(t if t is not None else time.time())
    dow = 1 if lt.tm_wday == 6 else lt.tm_wday + 2       # Mon=0 .. Sun=6  ->  Sun=1 .. Sat=7
    return time.strftime("%y%m%d", lt).encode() + str(dow).encode() + time.strftime("%H%M%S", lt).encode()


# -- the download image --------------------------------------------------------------

def write_request(packet):
    """Decode a DD8822 E envelope after outer framing has been verified.

    Its third payload byte checks the header; storing it shifts every image
    field and lets the simulator accept writes the real ROM asks to resend.
    """
    payload = bytes(packet.payload)
    if packet.type != 'E' or not 4 <= len(payload) <= 253:
        raise ValueError('expected a DD8822 E request with header checksum and data')
    header = bytes([SYNC, len(payload) + 2, ord('E')]) + payload[:2]
    if payload[2] != checksum(header):
        raise ValueError('DD8822 E request header checksum mismatch')
    address = int.from_bytes(payload[:2], 'big')
    if address + len(payload) - 3 > 0x10000:
        raise ValueError('DD8822 E request crosses the CPU address space')
    return address, payload[3:]


def writes(packets):
    """Verified (address, data) manager E writes, excluding header bytes.

    Bad inner headers are omitted even when their outer frame is valid.
    They are retained unchanged by the raw capture/export paths.
    """
    out = []
    for p in packets:
        if p.type == 'E':
            try:
                out.append(write_request(p))
            except ValueError:
                continue
    return out


def memory_image(packets):
    """address -> byte over every write, later writes winning."""
    image = {}
    for addr, data in writes(packets):
        for k, b in enumerate(data):
            image[addr + k] = b
    return image


def diff_images(before, after):
    """[(address, old or None, new or None)] where the two images differ."""
    out = []
    for a in sorted(set(before) | set(after)):
        if before.get(a) != after.get(a):
            out.append((a, before.get(a), after.get(a)))
    return out


# -- relay captures ------------------------------------------------------------------

A_TO_B = "A>B"      # toward the manager: the BOARD's bytes
B_TO_A = "B>A"      # toward the phone: the MANAGER's bytes

_HEADER = re.compile(r"^# (session|label|A side|B side)\s*: (.*)$")
_BLOCK = re.compile(r"^\[\s*([\d.]+)\] (A>B|B>A) (\d+) bytes?$")
_HEX = re.compile(r"^    [0-9a-f]{4}  ((?:[0-9a-f]{2} ?)+?)\s{2,}\|")
_NOTE = re.compile(r"^\[\s*([\d.]+)\] -- (.*)$")


def parse_capture(text):
    """A relay capture back into bytes (one `# key : value` header, then
    `[seconds] A>B n bytes` blocks of hex dump): the header, the blocks
    as (seconds, direction, bytes) in order, and the relay's own notes.
    Reads the hex column only -- the printable column is for eyes."""
    header, blocks, notes = {}, [], []
    cur = None
    for line in text.splitlines():
        m = _HEADER.match(line)
        if m:
            header[m.group(1)] = m.group(2).strip()
            continue
        m = _BLOCK.match(line)
        if m:
            cur = [float(m.group(1)), m.group(2), bytearray()]
            blocks.append(cur)
            continue
        m = _NOTE.match(line)
        if m:
            notes.append((float(m.group(1)), m.group(2)))
            cur = None
            continue
        m = _HEX.match(line)
        if m and cur is not None:
            cur[2] += bytes.fromhex(m.group(1).replace(" ", ""))
    return {"header": header,
            "blocks": [(t, d, bytes(b)) for t, d, b in blocks],
            "notes": notes}


def records_in_capture(capture):
    """The call records a session carried, ready to file: the board's own
    number from its call-in, its firmware, and every record in its answers
    to the call-records read at 0x3a00, in order.

    A record read twice in one session (the manager asked again after a
    lost ACK) comes back once: the 23 raw bytes include the board's clock
    and a per-record tail, so identical bytes are the same call. Works on
    a capture of either manager alike, because both are the
    relay's format with the board's bytes on A>B."""
    packets, _skipped, _left = decode(stream(capture, A_TO_B))
    ani = firmware = ""
    out, seen = [], set()
    for p in packets:
        if p.type == "!" and not ani:
            c = parse_callin(p.payload)
            ani, firmware = c["ani"], c["firmware"]
        elif p.type == "D" and len(p.payload) >= 2 and is_cdr_address((p.payload[0] << 8) | p.payload[1]):
            for r in parse_cdr_block(p.payload):
                if r["raw"] not in seen:
                    seen.add(r["raw"])
                    out.append(r)
    return {"ani": ani, "firmware": firmware, "records": out}


def stream(capture, direction):
    """Every byte the relay carried one way, in order."""
    return b"".join(b for _t, d, b in capture["blocks"] if d == direction)


def summarize(packets, direction):
    """One dict per packet for the JSON export: type, its name for that
    direction, the payload as hex, and the address for reads and writes."""
    names = BOARD_TYPES if direction == A_TO_B else MANAGER_TYPES
    out = []
    for p in packets:
        d = {"type": p.type, "name": names.get(p.type, "?"), "len": len(p.payload),
             "payload": p.payload.hex()}
        # A read's answer echoes the address, so both directions carry it.
        if p.type in ("D", "E") and len(p.payload) >= 2 and (direction == B_TO_A or p.type == "D"):
            addr = (p.payload[0] << 8) | p.payload[1]
            d["addr"] = "%04x" % addr
            known = ADDR_NAMES.get(addr)
            if known:
                d["name"] = "%s (%s)" % (d["name"], known)
            if direction == A_TO_B and p.type == "D" and is_cdr_address(addr):
                d["records"] = parse_cdr_block(p.payload)
        if direction == A_TO_B and p.type == "!":
            d["callin"] = parse_callin(p.payload)
        out.append(d)
    return out


def decode_capture(capture):
    """Both directions decoded: what the export and the viewer show."""
    out = {}
    for direction in (A_TO_B, B_TO_A):
        raw = stream(capture, direction)
        packets, skipped, leftover = decode(raw)
        out[direction] = {"bytes": len(raw), "hex": raw.hex(), "packets": summarize(packets, direction),
                          "skipped": skipped, "leftover": leftover.hex()}
    return out


# -- the bones of a manager ------------------------------------------------------------

class ReplayManager:
    """The manager side of a call-in, replayed from a recorded session.

    `script` is the manager's packets in order, as decoded off a capture's
    B>A stream (Q, D, L, B, the E writes, S, S, D, R, P, O). The board
    drives the pace: the manager sends its first packet when the `!`
    call-in arrives and the next one each time the board ACKs (`G`) or
    answers a read (`D`, `L`). A board `E` or `F` means send the last
    packet again; a board `N` ends it as refused. `P` is never replayed: the
    board's clock is set to now, because a replayed clock is the one
    thing in a recording that is wrong by construction.

    No sockets: feed bytes in, get bytes out. The relay (or an
    `app_softmodem` socket) is the I/O; this is only the dialogue."""

    def __init__(self, script, clock=None):
        self.script = list(script)
        self.clock = clock or time.time
        self.state = "wait-callin"          # wait-callin | talking | done | refused
        self.callin = None
        self.sent = []                      # every packet sent, in order
        self.last = None
        self.resends = 0
        self.received = 0                   # board packets seen, noise excluded
        self.records_due = 0                # records the board said it holds, when it said so
        self.resends_here = 0               # of the packet in hand; MAX_RESENDS ends the session
        self.last_refusal = ""              # the board's last E, F or !, for the stuck note
        self.refusal = b""                  # a board N's payload: why it refused (n_reason)
        self._buf = b""

    def _next(self):
        if not self.script:
            self.state = "done"
            return []
        pkt = self.script.pop(0)
        if pkt.type == "P":
            pkt = Packet("P", clock_payload(self.clock()))
        self.last = frame(pkt.type, pkt.payload)
        self.sent.append(pkt)
        self.resends_here = 0
        if pkt.type == "O":
            self.state = "done"
        return [self.last]

    def on_packet(self, pkt):
        """One board packet in; zero or more manager packets (as bytes) out."""
        self.received += 1
        if self.state == "wait-callin":
            if pkt.type != "!":
                return []
            self.callin = parse_callin(pkt.payload)
            self.state = "talking"
            return self._next()
        if self.state != "talking":
            return []
        if pkt.type in ("!", "E", "F"):
            # "!": the 27.5 s retry, it did not hear us. "E": send again.
            # "F": the letter it read has no handler, so the T, length or
            # letter was hit on the line and it never had the packet (a
            # DD7019, 2026-10-07 23:50 and 2026-10-08 00:47). From G, F is
            # the configuration check failing, which no copy mends:
            # MAX_RESENDS ends that, and the stuck note says which it was.
            self.last_refusal = pkt.type
            self.resends += 1
            self.resends_here += 1
            if self.resends_here > MAX_RESENDS:
                # A board that refuses the same packet every time will refuse
                # it forever, and every round is traffic, so no idle timeout
                # ends the call (2026-10-02 04:3x: 811 blocks, 20 kB to the
                # phone, 676 back). Stop, and say which packet it would not
                # take; the session files as `stuck`.
                self.state = "stuck"
                return []
            if pkt.type == "!" and not self.last:
                return self._next()
            return [self.last] if self.last else []
        if pkt.type == "N":
            self.state = "refused"
            self.refusal = bytes(pkt.payload)
            return []
        # Never acknowledge records taken after a malformed/short record
        # response. In particular an outer-valid frame can have a bad header.
        if (self.last and self.last[2:3] == b'D' and len(self.last) > 5
                and is_cdr_address((self.last[3] << 8) | self.last[4]) and pkt.type in ('D', 'G', 'L')):
            records = parse_cdr_block(pkt.payload) if pkt.type == 'D' else []
            if not records or len(records) * CDR_RECORD_LEN != self.last[5]:
                self.state = 'refused'
                return []
        if pkt.type == "D" and len(pkt.payload) >= 2 and ((pkt.payload[0] << 8) | pkt.payload[1]) == 0x0ff8:
            # The counts came back. With records waiting, ExpressNet reads them
            # (D at 0x3a00, 23 bytes each) and sends K before carrying on; a
            # replayed script recorded with none waiting has no such step, so
            # it is put in here. Every waiting record is read first, from the
            # ring slot the board names, a packet's worth at a time: K clears
            # them all, read or not (cdr_ring, cdr_reads).
            try:
                first, n = cdr_ring(pkt.payload)
            except ValueError:
                self.state = 'refused'
                return []
            if n:
                self.records_due = n
                self.script[0:0] = cdr_reads(first, n) + [Packet("K", b"\x00\x00")]
        if pkt.type in ("G", "D", "L"):
            return self._next()
        return []

    def on_silence(self):
        """The board has said nothing for longer than the packet in hand
        could take to reach it and be answered: send that packet again.

        2026-10-05 22:14: the first parameter block (176 bytes, 5.9 s on
        the wire) drew no answer, and 26 s after it went out the board sent
        `N 04` and the call was over. The emulated ROM ACKs that exact
        block, so its bytes were right; the line lost something -- part of
        our block, or the board's `G`. Either way a second copy recovers it
        before the board gives up: a duplicate write is idempotent, and a
        board still waiting on a short block takes the copy's first bytes,
        fails the checksum and says `E`. Counted against MAX_RESENDS like
        an `E`, so a dead line still ends `stuck`, not in a loop."""
        if self.state != "talking" or not self.last:
            return []
        self.resends += 1
        self.resends_here += 1
        if self.resends_here > MAX_RESENDS:
            self.state = "stuck"
            return []
        return [self.last]

    def withdraw_resend(self):
        """Take back the copy the last `E` asked for: the caller did not send
        it, because a copy of the same packet was still going out (the relay
        knows the wire; this class does not). Uncounted, so MAX_RESENDS
        counts copies the board was actually given."""
        if self.resends_here:
            self.resends_here -= 1
            self.resends -= 1

    def feed(self, data):
        """Bytes from the board (any framing); returns bytes for the board.
        Keeps an unfinished packet across calls, drops noise."""
        self._buf += bytes(data)
        packets, _skipped, self._buf = decode(self._buf, final=False)
        out = b""
        for p in packets:
            out += b"".join(self.on_packet(p))
        return out

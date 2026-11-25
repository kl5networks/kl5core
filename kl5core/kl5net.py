"""KL5-Net: our own Protel manager, beside ExpressNet.

A Protel's call-in arrives as a byte stream from a softmodem (300 bps
Bell 103, the only speed every board speaks; the 1200 modes are in
kl5-modems). KL5-Net answers it: a TCP listener, one session per
connection, driven by
`xnetproto.ReplayManager` -- the manager side of a recorded ExpressNet
session replayed ACK by ACK with a fresh clock -- and every byte of every
session written as a relay-format capture, so a KL5-Net session
can be read exactly like a capture of an ExpressNet one.

What it can do today is exactly what has been measured: re-download a
board from a recording of ExpressNet downloading it (the two complete
downloads of 2026-10-01), with the phone's own security code in the Q.
What it cannot do yet is anything no capture has shown: a session with
nothing to download, the upload records. ANSWER_ONLY below is the
recorded dialogue with the download left out, and it is marked
experimental on the page for that reason: the capture of the first such
session is how it stops being a guess.

The default port is in the 25xx block: KL5 keeps 23xx for an ExpressNet
relay and 24xx for Millennium, so KL5-Net takes the next hundred.
"""
import asyncio
import logging
import os
import re
import time

from . import xnetproto as xp

log = logging.getLogger("kl5net")

DEFAULT_PORT = 2523          # the listener, tunnel-facing
XNET_RELAY_PORT = 2323       # where ExpressNet's relay listens; BACK TO XNET points here
MM_RELAY_PORT = 2423         # where the Millennium relay listens (reaches mm_manager)
NATIVE_PORT = 2528           # the software UPMS (native_modem), AudioSocket, beside the listener.
                             # Not 2525: that is a common alternate mail submission port.
AUTO_PORT = 2526             # the auto modem (protel_automodem): any Protel, any speed
BOX_OPTIONS = "v(Bell103)l"  # the Softmodem options a Protel wants at 300

# Every modulation a Protel board can be put in, as the box's Softmodem
# options. A 4000/8000 (73K212L) has the first two and nothing else. The
# 7000's own 1200 is not Bell 202 either: its ROM drives the UPMS native FSK
# from the CPU (emulator, 2026-10-06), which no
# Softmodem speaks, so bell202 stays only as an experiment. The answer tone only matters for the
# V.22 target (the Bell 103 mark IS 2225 Hz); a(N) is ours and the box's
# renderer strips it before Softmodem() (protel.answer_tone_secs).
MODEMS = {
    "bell103": {"label": "300 bps Bell 103 FSK", "options": "v(Bell103)l",
                "note": "every Protel board; the one that is proven"},
    "v22": {"label": "1200 bps Bell 212A DPSK", "options": "v(V22)l",
            "note": "4000/8000 at register 133 = 1200, 186 off; the Millennium's mode"},
    "bell202": {"label": "1200 bps Bell 202 FSK (experimental)", "options": "v(Bell202)l",
                "note": "experimental; a 4000/8000 cannot do it, and a 7000's 1200 is the UPMS native FSK"},
    # Not Softmodem at all: the box hands the call to AudioSocket and the
    # hub's software UPMS answers it, then talks to this listener as
    # Softmodem would (protel.is_native_1200). A renderer change: UPGRADE.
    "protel1200": {"label": "1200 Protel native FSK (experimental)", "options": "v(Protel1200)",
                   "port": NATIVE_PORT,
                   "note": "the 7000's 186 mode, through the software UPMS; proven against the ROMs, "
                           "never on a call"},
    # Not Softmodem either: the hub's auto modem listens to how the phone
    # opens its call and answers as Bell 103, 212A or the native FSK.
    "auto": {"label": "Auto: any Protel, 300 or 1200 (experimental)", "options": "v(ProtelAuto)",
             "port": AUTO_PORT,
             "note": "answers as 212A, then Bell's 2225 Hz after 2 s; the emulated 1D called in through it "
                     "at 300 and at 1200, the emulated 7000 at native 1200; never on a call"},
}
DEFAULT_MODEM = "bell103"

# POLL NOW: head office calls the phone instead of waiting for it to call in.
# Measured in the DD8822 emulator (2026-10-06): a ringing board powers up after the ring count its
# options set (rings inside / default, counted by the clock chip while the
# CPU is off), answers as the ANSWERING modem -- 2225 Hz answer tone first --
# classifies the caller as Bell 103 or 212A from its carrier, ignoring its
# own Modem Speed register, and then sends the same `!` call-in a `*#3` does.
# So our side is the CALLING modem (`f`), with no answer tone of our own, and
# KL5-Net answers the session exactly as it answers a call-in.
POLL_MODEMS = {
    "bell103": {"label": "300 (Bell 103)", "options": "v(Bell103)lf"},
    # r(-90): the HT802 mutes a frame now and then on a 1200 carrier
    # (a Grandstream HT802's DTMF detector); r(-90) rides through it.
    "v22": {"label": "1200 (Bell 212A)", "options": "v(V22)lfr(-90)"},
}


POLL_RING_LIMIT = 4    # rings that fit the 30 s ring timeout (CLI originate, the box's Dial): ~6 s a ring + ~2 s to answer


def poll_rings(options_record, scheduled_callin=True):
    """(rings, note) for a poll of a phone downloaded with this OPTIONS
    record: how many rings it will want, from its polling window and ring
    counts. A download without the schedule zeroes the window (start ==
    end), and then the board uses rings-inside always (emulator,
    2026-10-06); so does a 24-hour window. Otherwise it depends on the
    board's clock: rings inside within the window, rings default outside."""
    from . import xnetopts
    try:
        v = xnetopts.read(options_record)
    except xnetopts.XnetOptsError:
        return None, "its OPTIONS record cannot be read"
    inside, default = int(v.get("rings_inside") or 0), int(v.get("rings_default") or 0)
    length = 0 if not scheduled_callin else int(v.get("poll_length") or 0)
    if length % 24 == 0:
        return inside, "%d rings" % inside
    return max(inside, default), ("%d rings inside its %02d:00 window (%d h), %d outside it"
                                  % (inside, int(v.get("poll_start") or 0), length, default))


# A scheduled phone whose OPTIONS profile says nothing readable about how
# often it reports is held to this: a week, plus GRACE. A profile that
# names a cadence wins (callin_interval).
DEFAULT_CALLIN_DAYS = 7
# Session outcomes that mean the phone did NOT get what it called for
# (kl5net_sessions.outcome). `no-callin` is left out on purpose: every
# Protel's short first call lands there, and a hangup is the board's.
BAD_OUTCOMES = ("refused", "stuck", "timeout", "no-script", "no-code", "error")
CALLIN_GRACE_SECS = 86400


def callin_interval(options_record):
    """(seconds, why) -- how often a phone downloaded with this OPTIONS
    record should call in on its own, read from its Reporting Options:
    Number of Days Report (every N days), else Day of Week Report (weekly),
    else Day of Month Report (monthly). The emulated DD8822 plans its next
    call-in from exactly these (FUN_969b, 2026-10-07); no bench has timed a
    board keeping them, so the page says "expected", never "missed". None
    when the record says nothing."""
    from . import xnetopts
    try:
        v = xnetopts.read(options_record)
    except xnetopts.XnetOptsError:
        return None, "its OPTIONS profile cannot be read"
    days = int(v.get("days_report") or 0)
    if days:
        return days * 86400, "every %d day%s (Number of Days Report)" % (days, "" if days == 1 else "s")
    if int(v.get("day_of_week_report") or 0):
        return 7 * 86400, "weekly (Day of Week Report)"
    if int(v.get("day_of_month_report") or 0):
        return 31 * 86400, "monthly (Day of Month Report)"
    return None, "its OPTIONS profile sets no reporting day"




def poll_command(dial, host, port, modem="bell103"):
    """An Asterisk CLI originate for a poll: ring the phone by the same dial
    string an inbound carrier call uses (the phone's ATA), and when it answers, run Softmodem
    in originate mode against this listener."""
    if modem not in POLL_MODEMS:
        raise Kl5NetError("unknown poll modem %r" % modem)
    if not re.fullmatch(r"[A-Za-z0-9_./:@-]+", str(dial or "")):
        raise Kl5NetError("unsafe dial string %r" % dial)
    if not re.fullmatch(r"[0-9a-fA-F.:]+", str(host or "")):
        raise Kl5NetError("unsafe listen host %r" % host)
    return ("channel originate %s application Softmodem %s,%d,%s"
            % (dial, host, int(port), POLL_MODEMS[modem]["options"]))


def target_for(host, port, modem, answer_tone_secs=1):
    """LOCAL MGR for a phone on this listener with this modem choice: the
    listener itself, or the software UPMS in front of it for native 1200."""
    if modem not in MODEMS:
        raise Kl5NetError("unknown modem %r" % modem)
    return target(host, MODEMS[modem].get("port", port), options_for(modem, answer_tone_secs))


def is_kl5net_target(local, host, port):
    """True when LOCAL MGR sends call-ins to KL5-Net: the listener itself,
    or one of the modems in front of it (native 1200, Auto). Checking the
    listener's port alone made a phone on Auto read as "not on KL5-Net",
    so USE KL5-NET never turned into BACK TO XNET (2026-10-07)."""
    return any((local or "").startswith("softmodem:%s:%d/" % (host, p))
               for p in (port, NATIVE_PORT, AUTO_PORT))


# What the auto modem decided, as LIVE shows it (protel_automodem.AutoCall.mode).
AUTO_DECIDED = {"bell103": "300 bps Bell 103 FSK", "v22": "1200 bps Bell 212A DPSK",
                "native": "1200 Protel native FSK"}


def line_modem(via, decided=None, phone_modem=None):
    """The modem a call-in is on, for KL5-NET -> LIVE. ``via`` is which
    modem in front of the listener carried it ("auto", "native", or None for
    the box's own Softmodem straight to the listener); ``decided`` what Auto
    chose; ``phone_modem`` the phone's MODEM, which is all there is to go on
    for a straight Softmodem call."""
    if via == "auto":
        return "AUTO \u2192 " + AUTO_DECIDED.get(decided, "undecided")
    if via == "native":
        return MODEMS["protel1200"]["label"]
    if phone_modem in MODEMS and not MODEMS[phone_modem].get("port"):
        return MODEMS[phone_modem]["label"]
    return ""


def options_for(modem, answer_tone_secs=1):
    """The Softmodem options for a phone's modem choice."""
    if modem not in MODEMS:
        raise Kl5NetError("unknown modem %r" % modem)
    opts = MODEMS[modem]["options"]
    # 1 s is the box's default, so only a different length is written --
    # including 0, which is no tone at all (protel.answer_tone_secs).
    if modem == "v22" and int(answer_tone_secs) != 1:
        opts += "a(%d)" % int(answer_tone_secs)
    return opts
DEFAULT_IDLE_SECS = 90       # a board retries its call-in at 27.5 s; a dead line is silent far longer
# How long past a packet's own time on the wire the board may stay silent
# before KL5-Net sends that packet again (xnetproto.ReplayManager.on_silence).
# The board gave up 20 s after one went unanswered (2026-10-05 22:14), so a
# copy has to be on the wire well inside that. Over the 22:31 session's 32
# packets the slowest answer began 0.86 s after our packet ended (the two
# memory reads), so 4 s is over four times that. It was 8 s, and each of
# that session's two line hits cost a full 8 s of nothing.
#
# Re-checked on the DD7019 through the public auto modem (2026-10-07/08):
# its G lands 1.3-1.4 s after our packet ends and its E 1.8-2.2 s after
# (the E waits for the line to go quiet first). 4 s still clears both. What looked like
# answers arriving after the timer were copies queued behind copies: the
# timer ran from when a copy was WRITTEN, and Softmodem had not started
# sending it. It now runs from the end of the copy on the wire (Pacer).
REPLY_GRACE_SECS = 4
SLOWEST_ANSWER_SECS = 0.86   # measured, 2026-10-05 22:31: the reads at 0xffc5 and 0x3a00
SLOWEST_SEND_AGAIN_SECS = 2.2  # measured, DD7019 2026-10-07 22:40: E after a damaged 251-byte write ended
BOARD_GIVES_UP_SECS = 20     # measured, 2026-10-05 22:14: N 04 20.5 s after an unanswered block
WIRE_BPS = 300               # the slowest modem a board speaks: a timer sized for it is never early
# How long a copy sent AGAIN waits after the last one has left. A board
# that lost part of a packet hunts for a T in the rest of it; a T among
# the data starts a packet that is not there, whose handler reads until
# the line has been quiet for three 0.32 s ticks and then says E (7000
# $B4FD with $7B = $83, INFERRED). A copy that starts inside that quiet
# time is read as the tail of the phantom packet and lost with it. So the
# copy waits until the board's E for the last one would have come.
RESEND_GAP_SECS = 2.5
RESEND_ASKS = ("E", "F", "!")
HELD_SLACK_SECS = 0.5        # past a held fragment's own airtime before it is taken for noise     # what a board says when it did not get the packet in hand


def airtime(nbytes, bps=WIRE_BPS):
    """Seconds a packet of `nbytes` takes on the wire, 8N1."""
    return nbytes * 10.0 / bps


FAST_BPS = 1200
FAST_ABOVE_BPS = 700         # a call-in measured faster than this came in at 1200


def callin_bps(nbytes, span):
    """The speed the board called in at, from how long its `!` took to
    arrive: 300 or 1200. Our packets go back over the same modem, so this
    is also how fast they leave. `span` is first byte to last; a packet
    that arrived in one read (span 0) is fast. Measured off real call-ins:
    the 300 bps 1D's 69 bytes took 2.36 s (292 bps), the 1200 bps one's
    0.63 s. Anything in between is called 300, the side a timer is never
    early on."""
    if span <= 0:
        return FAST_BPS
    return FAST_BPS if (nbytes - 1) * 10.0 / span > FAST_ABOVE_BPS else WIRE_BPS


class Pacer:
    """When KL5-Net's bytes may go on the wire, and which of the board's
    answers the dialogue (xnetproto.ReplayManager) gets to hear. Pure:
    `hear(packet, now)` and `poll(now)`, no sockets, no clock of its own.

    The DD7019 that called the public line on 2026-10-07/08 had copies
    stacked behind copies: an E that arrived 0.6 s into an 8.5 s write
    drew a second copy at once, Softmodem queued it behind the first, the
    board got them back to back, and every answer after that was matched
    to the wrong copy. So:

    1. One copy on the wire at a time, by our own airtime arithmetic from
       when it was written at 300 baud. Anything due sooner waits -- unless
       the board has already answered that copy, which proves it has left
       (a 1200 call is faster than the arithmetic; a copy sent again on
       one still waits the 300-baud time, which only costs time).
    2. A copy sent again waits RESEND_GAP_SECS past the end of the last
       (never longer than the silence timer would have).
    3. An E, F or ! heard while a copy is already waiting to go is the
       same request again: absorbed, not counted, no second copy.
    4. A G, D or L cannot answer the copy still on the wire: the board
       answers after the checksum, the last byte. Heard during a second
       or later copy it answers an earlier copy of the same packet, so
       the dialogue moves on -- and since the copy on
       the wire will draw an answer of its own, the next packet waits the
       gap too and whatever is heard before it goes is dropped.
    5. The silence timer runs from the end of the copy on the wire.

    A dead line still ends `stuck`: only the timer and the answers the
    dialogue hears count against MAX_RESENDS, and every copy is one."""

    def __init__(self, mgr, reply_grace=REPLY_GRACE_SECS, gap=RESEND_GAP_SECS, bps=WIRE_BPS):
        self.mgr = mgr
        self.reply_grace = reply_grace
        self.gap = min(gap, reply_grace)
        self.bps = bps
        self.sent_at = None           # when the copy last written went to the socket
        self.busy_until = None        # ... and when it has left the modem
        self.answer_by = None         # ... and when its answer is overdue
        self.queued = []              # packets (bytes) waiting for the wire
        self.not_before = 0.0
        self.queued_again = False     # the queue is a copy sent again
        self.copy = 0                 # copies of the packet in hand written so far
        self.dropped = 0              # answers to copies already superseded (rule 4)
        self.absorbed = 0             # requests already being met (rule 3)
        self.notes = []
        self._buf = b""
        self._held = b""              # the fragment decode held back last time
        self._held_at = 0.0           # ... and when it first was

    def _what(self):
        last = self.mgr.sent[-1] if self.mgr.sent else None
        return "%s %s" % (last.type, last.payload[:2].hex()) if last else "?"

    def _offer(self, chunks, now, again, hold=False):
        if not chunks:
            return
        if self.busy_until is None:
            start = now
        else:
            start = max(now, self.busy_until + (self.gap if again or hold else 0))
        self.queued, self.not_before, self.queued_again = list(chunks), start, again
        if not again:
            self.copy = 0

    def _deliver(self, pkt, now, hold=False):
        before = self.mgr.resends
        out = self.mgr.on_packet(pkt)
        self._offer(out, now, self.mgr.resends > before, hold)

    def hear(self, pkt, now):
        """One board packet, at `now`."""
        if pkt.type == "N":
            self.queued = []
            return self._deliver(pkt, now)
        if self.mgr.state != "talking":           # the call-in, or anything after the end
            return self._deliver(pkt, now)
        on_wire = self.busy_until is not None and now < self.busy_until
        what = "%s %s" % (pkt.type, pkt.payload[:2].hex())
        if self.queued and not self.queued_again:
            self.dropped += 1
            self.notes.append("%s dropped: it answers the copy of the last packet still on the wire" % what)
            return
        if pkt.type in RESEND_ASKS:
            if self.queued_again:
                self.absorbed += 1
                self.notes.append("%s while a copy of %s waits for the wire: already being sent again"
                                  % (what, self._what()))
                return
            self._deliver(pkt, now)
            if self.queued and now < self.not_before:
                self.notes.append("%s %.1f s before %s has left: sending it again then"
                                  % (pkt.type, self.not_before - now, self._what()))
            return
        if on_wire and self.copy > 1:
            # an earlier copy of the packet in hand was taken; the one on the
            # wire will be answered too, before the next packet goes
            self.notes.append("%s answers an earlier copy of %s; the next packet waits for this one"
                              % (what, self._what()))
            return self._deliver(pkt, now, hold=True)
        if self.queued_again:
            self.queued, self.queued_again = [], False       # the copy that ended was taken after all
        if on_wire:
            # An answer to the only copy there is: the board had its last
            # byte, so it has left -- the modem is faster than 300 (a 1200
            # call through the auto modem), and the next packet need not
            # wait out 300-baud arithmetic.
            self.busy_until = now
        self._deliver(pkt, now)

    def feed(self, data, now):
        """Bytes from the board (any framing); noise is dropped."""
        self._buf += bytes(data)
        self._read(now)

    def _read(self, now):
        """Decode what has arrived. A fragment held for the rest of its
        packet is given that packet's 300-baud airtime and no longer: a
        stray 0x54 in modem noise with a big length byte after it is a
        packet that never comes. The public line, 2026-10-23 19:29: junk
        `54 cd` during V.22 trouble claimed 205 bytes, the decoder waited
        for them, and the board's E and G for the B behind it went into
        that wait unread -- nine copies of B "unanswered", session stuck."""
        past_stray = False
        while True:
            packets, _skipped, rest = xp.decode(self._buf, final=False)
            for p in packets:
                self.hear(p, now)
            if not past_stray and not (self._held and rest.startswith(self._held)):
                self._held_at = now          # a new fragment, or none
            self._buf = self._held = rest
            due = self._held_due()
            if due is None or now < due:
                return
            self.notes.append("a stray T claimed %d bytes that never came: read past it" % rest[1])
            self._buf = rest[1:]
            past_stray = True                # what follows arrived no earlier: keep the clock

    def _held_due(self):
        """When the held fragment, if any, is overdue as a packet."""
        if len(self._buf) < 2:
            return None
        return self._held_at + airtime(self._buf[1] + 2) + HELD_SLACK_SECS

    def poll(self, now):
        """The packets to write now (a list, maybe empty). Runs the silence
        timer first: a packet unanswered past its grace is sent again."""
        if self._buf:
            self._read(now)          # an answer stuck behind a stray T is heard before any timer
        if (self.mgr.state == "talking" and not self.queued and self.answer_by is not None
                and now >= self.answer_by):
            self.notes.append("no answer to %s in %.0f s: sending it again" % (self._what(), now - self.sent_at))
            self._offer(self.mgr.on_silence(), now, True)
        if not self.queued or now < self.not_before:
            return []
        out, self.queued = self.queued, []
        self.copy = self.copy + 1 if self.queued_again else 1
        self.queued_again = False
        self.sent_at = now
        self.busy_until = now + airtime(sum(len(c) for c in out), self.bps)
        self.answer_by = self.busy_until + self.reply_grace
        return out

    def wake_at(self):
        """When poll() next has something to do, or None."""
        held = self._held_due()
        if self.queued:
            due = self.not_before
        elif self.mgr.state == "talking" and self.answer_by is not None:
            due = self.answer_by
        else:
            due = None
        return min(d for d in (due, held) if d is not None) if (due, held) != (None, None) else None

    @property
    def heard(self):
        return self.mgr.received + self.dropped + self.absorbed

# The recorded dialogue with the download (B + the E writes) left out:
# Q, read the status block, L, two S, the verify read, R, the clock, O.
# A guess until a captured ExpressNet session with nothing to download
# says otherwise -- see the module docstring.
ANSWER_ONLY = [("Q", ""), ("D", "ffc515"), ("L", ""), ("S", "2400000000000000"),
               ("S", "0100000000000000"), ("D", "0ff808"), ("R", ""), ("P", ""), ("O", "")]

CODE_RE = re.compile(r"^\d{4}$")
LISTEN_RE = re.compile(r"^([A-Za-z0-9_.:-]+):(\d{1,5})$")


class Kl5NetError(Exception):
    pass


def target(host, port=DEFAULT_PORT, options=BOX_OPTIONS):
    """The LOCAL MGR value that sends a phone's call-ins here."""
    return "softmodem:%s:%d/%s" % (host, int(port), options)


def parse_listen(text, default_host):
    """'host:port' from the setting; blank means the hub's tunnel address
    and the default port."""
    text = (text or "").strip()
    if not text:
        return default_host, DEFAULT_PORT
    m = LISTEN_RE.match(text)
    if not m or not 1 <= int(m.group(2)) <= 65535:
        raise Kl5NetError("listen address must be HOST:PORT, got %r" % text)
    return m.group(1), int(m.group(2))


def code_payload(code):
    """A four-digit security code as the two BCD bytes the Q carries
    (ExpressNet sent `90 02` for 9002)."""
    code = (code or "").strip()
    if not CODE_RE.match(code):
        raise Kl5NetError("security code must be four digits")
    return bytes.fromhex(code)


# -- download images --------------------------------------------------------------

def script_from_capture(text):
    """The manager side of a recorded session as a script: [(type, payload
    hex)] in order, plus what the session was -- the board that called,
    how many writes, how many bytes of image, the address range. Raises
    when the capture has no manager packets (a session ExpressNet never
    answered is not a download)."""
    cap = xp.parse_capture(text)
    manager, skipped, _left = xp.decode(xp.stream(cap, xp.B_TO_A))
    if not manager:
        raise Kl5NetError("no manager packets in this capture: nothing to replay")
    board, _s, _l = xp.decode(xp.stream(cap, xp.A_TO_B))
    callin = next((xp.parse_callin(p.payload) for p in board if p.type == "!"), None)
    image = xp.memory_image(manager)
    return {
        "script": [(p.type, p.payload.hex()) for p in manager],
        "ani": callin["ani"] if callin else "",
        "firmware": callin["firmware"] if callin else "",
        "writes": len(xp.writes(manager)),
        "bytes": len(image),
        "range": ("%04x-%04x" % (min(image), max(image))) if image else "",
        "types": "".join(p.type for p in manager),
        "started": cap["header"].get("session", ""),
        "skipped": skipped,
    }


def build_script(steps, code=None):
    """Packets for ReplayManager from a stored script. The Q's recorded
    code is replaced when the phone has its own; P is left to the manager,
    which always sends the clock as now. Validate every E before returning
    any packet: a bad later header must fail before even Q is sent."""
    out = []
    for ptype, payload_hex in steps:
        try:
            payload = bytes.fromhex(payload_hex or "")
        except ValueError:
            raise Kl5NetError("script step %s has a payload that is not hex" % ptype)
        if ptype == "Q" and code:
            payload = code_payload(code)
        packet = xp.Packet(ptype, payload)
        if ptype == 'E':
            try:
                address, data = xp.write_request(packet)
                if address < 0x1000 or address + len(data) > 0x8000:
                    raise ValueError('write is outside the DD8822 SRAM window')
            except ValueError as exc:
                # Report the failure without putting private image bytes into
                # a session note. Historical captures themselves stay intact.
                raise Kl5NetError('download write preflight failed: %s' % exc) from exc
        out.append(packet)
    if not out or out[0].type != "Q":
        raise Kl5NetError("a script starts with Q; this one starts with %r" % (out[0].type if out else ""))
    return out


# -- captures in the relay's format -----------------------------------------------

class CaptureWriter:
    """relay.py's capture format, written by the hub: the same header,
    the same `[ sec] A>B n bytes` blocks and hexdump lines, the same
    notes -- so xnetproto.parse_capture, the XNET viewer and the research
    export read a KL5-Net session without knowing which program answered."""

    def __init__(self, path, label, a_desc, b_desc):
        self.path = path
        self.started = time.monotonic()
        self.counts = {xp.A_TO_B: 0, xp.B_TO_A: 0}
        self._fh = open(path, "w", buffering=1)
        self._fh.write("# session : %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        self._fh.write("# label   : %s\n" % (label or "(none)"))
        self._fh.write("# A side  : %s\n" % (a_desc or "?"))
        self._fh.write("# B side  : %s\n" % (b_desc or "?"))
        self._fh.write("# A>B is toward ExpressNet, B>A is toward the phone.\n#\n")

    def _stamp(self):
        return time.monotonic() - self.started

    def record(self, direction, data):
        if not data:
            return
        self.counts[direction] += len(data)
        self._fh.write("[%9.3f] %s %d byte%s\n" % (self._stamp(), direction, len(data),
                                                   "" if len(data) == 1 else "s"))
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            text = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in chunk)
            self._fh.write("    %04x  %-47s  |%s|\n" % (off, " ".join("%02x" % b for b in chunk), text))

    def note(self, text):
        self._fh.write("[%9.3f] -- %s\n" % (self._stamp(), text))

    def close(self):
        self.note("session ended: %d bytes %s, %d bytes %s"
                  % (self.counts[xp.A_TO_B], xp.A_TO_B, self.counts[xp.B_TO_A], xp.B_TO_A))
        self._fh.close()


def capture_name(label, now=None):
    slug = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:48] or "kl5net"
    return "%s-%s.log" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), slug)


# -- one session --------------------------------------------------------------------

async def run_session(reader, writer, plan, captures_dir, idle_secs=DEFAULT_IDLE_SECS,
                      clock=time.time, label="kl5net", reply_grace=REPLY_GRACE_SECS,
                      on_capture=None, resend_gap=RESEND_GAP_SECS, wire_bps=WIRE_BPS):
    """Answer one call-in on an open connection.

    Reads until the board's `!` arrives (noise before it is dropped, the
    way the relay drops it with --ring-on), asks `plan(callin)` what to
    send this board -- `{"script": [...], "code": "", "label": ...,
    "phone_id": ..., "image_id": ...}` -- and lets ReplayManager drive the
    rest. Returns the session row to record: outcome is `done`, `refused`
    (the board sent N), `hangup` (it closed mid-dialogue), `no-callin`
    (it closed or went quiet before any `!`), or `timeout`."""
    peer = writer.get_extra_info("peername") or ("?", 0)
    at = int(clock())
    name = capture_name(label, at)
    os.makedirs(captures_dir, exist_ok=True)
    cap = CaptureWriter(os.path.join(captures_dir, name), label,
                        "board via %s:%s" % (peer[0], peer[1]), "KL5-Net (this hub)")
    cap.note("both ends connected")
    if on_capture is not None:
        # KL5-NET -> LIVE follows the session from here, not from the plan:
        # a board that never sends `!` (one on the wrong speed, 2026-10-05
        # 22:5x: 52 s of 0x55 at 1200) is still a session worth watching.
        on_capture(name)
    row = {"at": at, "peer": "%s:%s" % (peer[0], peer[1]), "capture": name, "ani": "", "firmware": "",
           "outcome": "no-callin", "a_bytes": 0, "b_bytes": 0, "sent": 0, "received": 0,
           "resends": 0, "phone_id": None, "image_id": None, "provision_phone_id": None,
           "label": label, "duration": 0}
    buf = b""
    mgr = None
    pacer = None
    dropped = 0
    loop = asyncio.get_running_loop()
    heard_at = loop.time()             # the board's last byte: what idle_secs counts from
    bps = WIRE_BPS                     # until the call-in says otherwise (callin_bps)
    arrivals = []                      # (time, bytes so far) per read, until the call-in
    heard_total = 0

    def notes():
        for text in pacer.notes:
            cap.note(text)
        pacer.notes.clear()

    async def pump():
        """Write whatever the pacer lets go now."""
        out = pacer.poll(loop.time())
        notes()
        for data in out:
            writer.write(data)
            cap.record(xp.B_TO_A, data)
            row["b_bytes"] += len(data)
            row["sent"] += 1
        if out:
            await writer.drain()
        row["received"], row["resends"] = pacer.heard, mgr.resends

    try:
        while True:
            now = loop.time()
            wait = heard_at + idle_secs - now
            wake = pacer.wake_at() if pacer is not None else None
            if wake is not None:
                wait = min(wait, wake - now)
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=max(wait, 0))
            except asyncio.TimeoutError:
                if pacer is None or loop.time() - heard_at >= idle_secs:
                    cap.note("idle for %d s: giving up" % idle_secs)
                    row["outcome"] = "timeout" if mgr else "no-callin"
                    break
                await pump()                   # a copy due, or the silence timer
                if mgr.state == "stuck":
                    cap.note("no answer after %d copies; giving up" % (xp.MAX_RESENDS + 1))
                    row["outcome"] = "stuck"
                    break
                continue
            heard_at = loop.time()
            if not data:
                if mgr is None:
                    cap.note("board closed before any call-in (%d bytes dropped)" % dropped)
                elif mgr.state not in ("done", "refused"):
                    cap.note("board closed mid-dialogue in state %s" % mgr.state)
                    row["outcome"] = "hangup"
                break
            cap.record(xp.A_TO_B, data)
            row["a_bytes"] += len(data)
            if mgr is None:
                heard_total += len(data)
                arrivals.append((heard_at, heard_total))
                buf += data
                packets, skipped, buf = xp.decode(buf, final=False)
                dropped += skipped
                callin = next((p for p in packets if p.type == "!"), None)
                if callin is None:
                    continue
                # Where the `!` began in the stream, so its arrival time says
                # what speed this call is on (callin_bps).
                ends = heard_total - len(buf) - sum(len(p.payload) + 4 for p in packets[packets.index(callin) + 1:])
                size = len(callin.payload) + 4
                first = next(t for t, n in arrivals if n > ends - size)
                bps = callin_bps(size, heard_at - first)
                arrivals = []
                parsed = xp.parse_callin(callin.payload)
                row["ani"], row["firmware"] = parsed["ani"], parsed["firmware"]
                cap.note("call-in from %s (%s) after %d dropped byte%s"
                         % (parsed["ani"] or "?", parsed["firmware"] or "?", dropped, "" if dropped == 1 else "s"))
                # The capture's name rides along so a plan can point a live
                # view at this session while it happens.
                chosen = plan(dict(parsed, capture=name, peer=tuple(peer[:2])))
                row["phone_id"], row["image_id"] = chosen.get("phone_id"), chosen.get("image_id")
                # A public provisioning phone: ownership is
                # written down now, while we still know which window this
                # call claimed -- a Protel carries nothing to work it out later.
                row["provision_phone_id"] = chosen.get("provision_phone_id")
                cap.note("plan: %s" % chosen.get("label", "?"))
                try:
                    if chosen.get('error'):
                        raise Kl5NetError(chosen['error'])
                    script = build_script(chosen["script"], chosen.get("code") or None)
                    if chosen.get('rom_preflight') is not None:
                        try:
                            chosen['rom_preflight'].require(script, parsed['firmware'])
                        except ValueError as exc:
                            raise Kl5NetError('download check: ' + str(exc)) from exc
                except Kl5NetError as exc:
                    cap.note("no usable script: %s" % exc)
                    row["outcome"] = "no-script"
                    break
                if not script[0].payload:
                    # The first KL5-Net session (2026-10-01 17:05): no image picked,
                    # no code set, so the Q went out with nothing in it -- and the
                    # board hung up 1.7 s later without an N. Refusing here says
                    # what was missing instead of filing a hangup to puzzle over.
                    cap.note("no security code: the Q would be empty, and a board hangs up on one without a word")
                    row["outcome"] = "no-code"
                    break
                mgr = xp.ReplayManager(script, clock=clock)
                # The Pacer times copies at 300 baud and lets an answer end the
                # wait early, so a 1200 call is not held to 300-baud arithmetic;
                # the measured speed goes in the capture, for reading a session.
                cap.note("call-in measured at %d bps" % bps)
                pacer = Pacer(mgr, reply_grace=reply_grace, gap=resend_gap, bps=wire_bps)
                now = loop.time()
                pacer.hear(callin, now)
                # anything decoded behind the call-in in the same read
                for p in packets[packets.index(callin) + 1:]:
                    pacer.hear(p, now)
                pacer.feed(buf, now)
                buf = b""
            else:
                pacer.feed(data, loop.time())
            await pump()
            if mgr.state == "stuck":
                last = mgr.sent[-1] if mgr.sent else None
                what = "%s %s" % (last.type, last.payload[:3].hex()) if last else "?"
                why = ""
                if mgr.last_refusal == "F" and last is not None and last.type == "G":
                    why = ": F to G is the board's configuration check failing"
                cap.note("board refused the same packet %d times (%s); giving up%s" % (xp.MAX_RESENDS, what, why))
                row["outcome"] = "stuck"
                break
            if mgr.state == "refused":
                row["outcome"] = "refused"
                cap.note("manager refused after %d packets sent (%s)" % (len(mgr.sent), xp.n_reason(mgr.refusal)))
                break
            if mgr.state == "done":
                # O may still be waiting behind a copy on the wire: it goes,
                # and the board gets a moment to read it before we hang up.
                while pacer.queued:
                    await asyncio.sleep(max(0.0, pacer.not_before - loop.time()))
                    await pump()
                row["outcome"] = "done"
                cap.note("manager done after %d packets sent" % len(mgr.sent))
                await asyncio.sleep(max(0.5, pacer.busy_until - loop.time() + 0.5)
                                    if pacer.busy_until is not None else 0.5)
                break
    finally:
        row["duration"] = int(clock()) - at
        cap.close()
        try:
            writer.close()
        except Exception:    # noqa: BLE001 -- the socket may already be gone
            pass
    return row


# -- the listener --------------------------------------------------------------------

class Service:
    """The in-process listener. `plan(callin)` decides what each board is
    sent; `on_session(row)` files what happened. `state()` is what the
    page shows, and `why` is never blank when it is not listening --
    a feature that has "never worked" on this fleet has always been one
    quiet line at startup."""

    def __init__(self, captures_dir, plan, on_session, idle_secs=DEFAULT_IDLE_SECS, clock=time.time,
                 reply_grace=REPLY_GRACE_SECS):
        self.captures_dir = captures_dir
        self.plan = plan
        self.on_session = on_session
        self.idle_secs = idle_secs
        self.reply_grace = reply_grace
        self.resend_gap = RESEND_GAP_SECS
        self.wire_bps = WIRE_BPS      # tests run a 300-baud dialogue faster by raising it
        self.clock = clock
        self.server = None
        self.host, self.port = "", 0
        self.why = "not started"
        self.sessions = 0
        self.active = 0
        self.last = None
        self.current = ""             # the capture of the session that started last

    async def _handle(self, reader, writer):
        self.active += 1
        try:
            row = await run_session(reader, writer, self.plan, self.captures_dir,
                                    idle_secs=self.idle_secs, clock=self.clock,
                                    reply_grace=self.reply_grace, resend_gap=self.resend_gap,
                                    wire_bps=self.wire_bps,
                                    on_capture=lambda name: setattr(self, "current", name))
        except Exception as exc:          # noqa: BLE001 -- one bad session must not stop the listener
            log.exception("kl5net session failed")
            row = {"at": int(self.clock()), "outcome": "error", "error": str(exc), "capture": "",
                   "ani": "", "a_bytes": 0, "b_bytes": 0, "peer": "?"}
            try:
                writer.close()
            except Exception:    # noqa: BLE001
                pass
        finally:
            self.active -= 1
        self.sessions += 1
        self.last = row
        try:
            self.on_session(row)
        except Exception:        # noqa: BLE001 -- filing is best effort; the capture is on disk
            log.exception("kl5net could not file a session")

    async def start(self, host, port):
        await self.stop()
        try:
            self.server = await asyncio.start_server(self._handle, host, port)
        except OSError as exc:
            self.server = None
            self.why = "cannot listen on %s:%s: %s" % (host, port, exc.strerror or exc)
            log.warning("kl5net %s", self.why)
            return False
        sock = self.server.sockets[0].getsockname()
        self.host, self.port = sock[0], sock[1]
        self.why = ""
        log.info("kl5net listening on %s:%d", self.host, self.port)
        return True

    async def stop(self):
        if self.server is not None:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:    # noqa: BLE001
                pass
            self.server = None
        self.why = self.why or "stopped"

    def state(self):
        return {"listening": self.server is not None, "host": self.host, "port": self.port,
                "why": self.why if self.server is None else "", "sessions": self.sessions,
                "active": self.active, "last": self.last, "idle_secs": self.idle_secs}

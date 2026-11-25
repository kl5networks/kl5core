"""KL5-Net: our own Protel manager. The listener and the session loop run
for real over loopback with a fake board on the other end. No Asterisk,
no ExpressNet."""
import asyncio
import os
import tempfile
import time
import unittest



from kl5core import xnetproto as xp, protel_firmware_xnet as wire     # noqa: E402
from kl5core import kl5net              # noqa: E402

ACK = xp.frame("G")
CALLIN_TEXT = b"*5555555555*82140*DD8822*1234*000*2610015114005*00000*"
CALLIN = xp.frame("!", b"\x04" + b"\0" * 7 + CALLIN_TEXT + b"\x01\0\0")
SCRIPT = [("Q", "1234"), ("D", "ffc515"), ("L", ""), ("B", "41000010601ca000"),
          ("E", xp.decode(wire.memory_write(0x1000, bytes.fromhex("a84b")))[0][0].payload.hex()), ("E", xp.decode(wire.memory_write(0x1061, bytes.fromhex("021555512340")))[0][0].payload.hex()), ("S", "2400000000000000"),
          ("S", "0100000000000000"), ("D", "0ff808"), ("R", ""), ("P", b"2610015055800".hex()), ("O", "")]


def capture_text(blocks, label="b1 download", stamp="2026-10-01 09:51:28"):
    text = ("# session : %s\n# label   : %s\n# A side  : listen:192.0.2.1:2323\n"
            "# B side  : connect:127.0.0.1:2324\n# A>B is toward ExpressNet, B>A is toward the phone.\n#\n"
            "[    0.000] -- both ends connected\n" % (stamp, label))
    for t, d, data in blocks:
        text += "[%9.3f] %s %d byte%s\n" % (t, d, len(data), "" if len(data) == 1 else "s")
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            text += "    %04x  %-47s  |%s|\n" % (off, " ".join("%02x" % b for b in chunk), "." * len(chunk))
    a = sum(len(b) for _t, d, b in blocks if d == "A>B")
    b = sum(len(b) for _t, d, b in blocks if d == "B>A")
    text += "[   41.220] -- session ended: %d bytes A>B, %d bytes B>A\n" % (a, b)
    return text


def recorded_session():
    """A whole ExpressNet session as the relay would have captured it."""
    blocks = [(1.2, "A>B", CALLIN)]
    t = 2.0
    for ptype, hexpayload in SCRIPT:
        blocks.append((t, "B>A", xp.frame(ptype, bytes.fromhex(hexpayload))))
        t += 0.5
        if ptype == "D":
            blocks.append((t, "A>B", xp.frame("D", bytes.fromhex(hexpayload[:4]) + b"\x89\x30")))
        elif ptype == "L":
            blocks.append((t, "A>B", xp.frame("L", b"\x02\x02")))
        else:
            blocks.append((t, "A>B", ACK))
        t += 0.5
    return capture_text(blocks)


# -- the pure parts -------------------------------------------------------------

class ScriptTest(unittest.TestCase):
    def test_a_capture_becomes_a_download_image(self):
        info = kl5net.script_from_capture(recorded_session())
        self.assertEqual(info["types"], "QDLBEESSDRPO")
        self.assertEqual((info["ani"], info["firmware"]), ("5555555555", "DD8822"))
        self.assertEqual((info["writes"], info["bytes"], info["range"]), (2, 8, "1000-1066"))
        self.assertEqual(info["script"][0], ("Q", "1234"))

    def test_a_session_expressnet_never_answered_is_refused(self):
        with self.assertRaises(kl5net.Kl5NetError):
            kl5net.script_from_capture(capture_text([(1.2, "A>B", CALLIN)]))

    def test_the_phones_own_code_replaces_the_recorded_q(self):
        pk = kl5net.build_script(SCRIPT, code="9002")
        self.assertEqual(pk[0], xp.Packet("Q", b"\x90\x02"))
        self.assertEqual(kl5net.build_script(SCRIPT)[0], xp.Packet("Q", b"\x12\x34"))
        with self.assertRaises(kl5net.Kl5NetError):
            kl5net.build_script(SCRIPT, code="90")
        with self.assertRaises(kl5net.Kl5NetError):
            kl5net.build_script([("D", "ffc515")])

    def test_every_modem_choice_renders_a_valid_target(self):
        for key in kl5net.MODEMS:
            t = kl5net.target("192.0.2.1", 2523, kl5net.options_for(key))
            self.assertTrue(t.startswith("softmodem:192.0.2.1:2523/v("))
        self.assertEqual(kl5net.options_for("bell103"), "v(Bell103)l")
        self.assertEqual(kl5net.options_for("v22"), "v(V22)l")
        self.assertEqual(kl5net.options_for("v22", 4), "v(V22)la(4)")
        self.assertEqual(kl5net.options_for("bell103", 4), "v(Bell103)l")     # the tone is V.22's only
        with self.assertRaises(kl5net.Kl5NetError):
            kl5net.options_for("v21")

    def test_write_preflight_rejects_bad_headers_and_windows_without_repair(self):
        invalid = ['10004edead', '1000dead', '']
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaisesRegex(kl5net.Kl5NetError, 'write preflight'):
                kl5net.build_script([('Q', '1234'), ('E', payload)])
        # Independently calculated, valid inner headers outside the window.
        for payload in ('7fffe10102', '0fff5201'):
            with self.subTest(payload=payload), self.assertRaisesRegex(kl5net.Kl5NetError, 'SRAM window'):
                kl5net.build_script([('Q', '1234'), ('E', payload)])
        packet = xp.decode(wire.memory_write(0x7f06, bytes(range(250))))[0][0]
        steps = [('Q', '1234'), ('E', packet.payload.hex())]
        before = list(steps)
        result = kl5net.build_script(steps, code='9002')
        self.assertEqual(result[1], packet)
        self.assertEqual(steps, before)
        self.assertEqual(result[0].payload, bytes.fromhex('9002'))

    def test_listen_and_target(self):
        self.assertEqual(kl5net.parse_listen("", "192.0.2.1"), ("192.0.2.1", 2523))
        self.assertEqual(kl5net.parse_listen("127.0.0.1:2600", "x"), ("127.0.0.1", 2600))
        with self.assertRaises(kl5net.Kl5NetError):
            kl5net.parse_listen("nope", "x")
        self.assertEqual(kl5net.target("192.0.2.1"), "softmodem:192.0.2.1:2523/v(Bell103)l")

    def test_the_capture_writer_matches_the_relays_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = kl5net.CaptureWriter(os.path.join(tmp, "x.log"), "test", "board", "KL5-Net")
            w.record(xp.A_TO_B, CALLIN)
            w.record(xp.B_TO_A, xp.frame("Q", b"\x12\x34"))
            w.note("plan: download")
            w.close()
            with open(os.path.join(tmp, "x.log")) as fh:
                text = fh.read()
        cap = xp.parse_capture(text)
        self.assertEqual(cap["header"]["label"], "test")
        self.assertEqual(xp.stream(cap, xp.A_TO_B), CALLIN)
        self.assertEqual(xp.stream(cap, xp.B_TO_A), xp.frame("Q", b"\x12\x34"))
        self.assertIn("plan: download", [n for _t, n in cap["notes"]])


# -- the listener, for real over loopback ---------------------------------------

class FakeBoard:
    """Dials in, sends the call-in, ACKs everything, answers reads."""

    def __init__(self, port, refuse_at=None, hang_up_at=None, records=(), deaf_at=None,
                 again_at=(), byte_gap=0, callin=CALLIN):
        self.port = port
        self.callin = callin
        self.again_at = set(again_at)       # the n-th packets it answers E (send again)
        self.byte_gap = byte_gap            # seconds between call-in bytes: a slow line
        self.at = []                        # when each packet arrived
        self.deaf_at = deaf_at              # the n-th packet it never hears (a line hit)
        self.refuse_at = refuse_at
        self.hang_up_at = hang_up_at
        self.records = list(records)        # 23-byte call records it is holding
        self.got = []

    def answer(self, read):
        """The board's answer to a read: the SW version, the counts (a
        constant 0x53 then eight bytes, as measured), or the records."""
        addr = (read[0] << 8) | read[1]
        if addr == 0x0ff8:
            counts = bytearray(8)
            if self.records:
                counts[5] = counts[7] = len(self.records)
            return read[:2] + b"\x53" + bytes(counts)
        if addr == xp.CDR_RECORDS_ADDR:
            data = b"".join(self.records)[:read[2]]
            header = bytes([xp.SYNC, len(data) + 5, ord('D')]) + read[:2]
            return read[:2] + bytes([xp.checksum(header)]) + data
        return read[:2] + b"\x89\x30"

    async def run(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        if self.byte_gap:
            for b in b"\x00\x0a\xb0" + self.callin:
                w.write(bytes([b]))
                await w.drain()
                await asyncio.sleep(self.byte_gap)
        else:
            w.write(b"\x00\x0a\xb0" + self.callin)   # carrier-up noise, then the call-in
            await w.drain()
        buf = b""
        while True:
            try:
                data = await asyncio.wait_for(r.read(4096), timeout=3)
            except asyncio.TimeoutError:
                break
            if not data:
                break
            buf += data
            packets, _s, buf = xp.decode(buf, final=False)
            for p in packets:
                self.got.append(p)
                self.at.append(time.monotonic())
                n = len(self.got)
                if self.hang_up_at == n:
                    w.close()
                    return
                if self.deaf_at == n:
                    continue
                if self.refuse_at == n:
                    w.write(xp.frame("N", b"\x01"))
                elif n in self.again_at:
                    w.write(xp.frame("E", b""))
                elif p.type == "D":
                    w.write(xp.frame("D", self.answer(p.payload)))
                elif p.type == "K":
                    self.records = []
                    w.write(ACK)
                elif p.type == "L":
                    w.write(xp.frame("L", b"\x02\x02"))
                elif p.type == "O":
                    await w.drain()
                    await asyncio.sleep(0.2)
                    w.close()
                    return
                else:
                    w.write(ACK)
                await w.drain()
        w.close()


class NoisyBoard(FakeBoard):
    """Answers the first write with `replies` the instant its bytes arrive
    -- long before 300 baud could have delivered them all, as the DD7019's
    mid-copy E and F did -- and behaves after that. `times` gets
    (loop time, packet) for every packet it hears."""

    def __init__(self, port, replies, times):
        super().__init__(port)
        self.replies = list(replies)
        self.times = times

    async def run(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b"\x00\x0a\xb0" + self.callin)
        await w.drain()
        buf = b""
        while True:
            try:
                data = await asyncio.wait_for(r.read(4096), timeout=3)
            except asyncio.TimeoutError:
                break
            if not data:
                break
            buf += data
            packets, _s, buf = xp.decode(buf, final=False)
            for p in packets:
                self.got.append(p)
                self.times.append((asyncio.get_running_loop().time(), p))
                if p.type == "E" and self.replies:
                    for reply in self.replies:
                        w.write(reply)
                    self.replies = []
                elif p.type == "D":
                    w.write(xp.frame("D", self.answer(p.payload)))
                elif p.type == "L":
                    w.write(xp.frame("L", b"\x02\x02"))
                elif p.type == "O":
                    await asyncio.sleep(0.2)
                    w.close()
                    return
                else:
                    w.write(ACK)
                await w.drain()
        w.close()


class ListenerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.filed = []
        self.plans = []

        def plan(callin):
            self.plans.append(callin)
            return {"script": SCRIPT, "code": "9002", "label": "test download", "phone_id": 7, "image_id": 3}
        self.svc = kl5net.Service(self.tmp.name, plan, self.filed.append, idle_secs=2,
                                  clock=lambda: time.mktime((2026, 10, 2, 8, 0, 0, 4, 275, -1)))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, board_factory):
        async def go():
            self.assertTrue(await self.svc.start("127.0.0.1", 0))
            board = board_factory(self.svc.port)
            await board.run()
            for _ in range(50):
                if self.filed:
                    break
                await asyncio.sleep(0.05)
            await self.svc.stop()
            return board
        return asyncio.run(go())

    def test_a_provisioning_owner_in_the_plan_is_filed_with_the_session(self):
        """The planner stamps ownership at call-in; a Protel carries
        nothing that would let it be worked out afterwards."""
        self.svc.plan = lambda callin: {"script": SCRIPT, "code": "9002", "label": "beta",
                                        "phone_id": None, "provision_phone_id": 42}
        self._run(FakeBoard)
        self.assertEqual((self.filed[0]["phone_id"], self.filed[0]["provision_phone_id"]), (None, 42))

    def test_a_fleet_session_has_no_provisioning_owner(self):
        self._run(FakeBoard)
        self.assertIsNone(self.filed[0]["provision_phone_id"])

    def test_a_whole_download_is_replayed_and_filed_and_captured(self):
        board = self._run(FakeBoard)
        self.assertEqual("".join(p.type for p in board.got), "QDLBEESSDRPO")
        self.assertEqual(board.got[0].payload, b"\x90\x02")                 # the phone's code, not the recording's
        self.assertEqual(board.got[-2].payload, b"2610026080000")          # the clock is now, not the recording's
        self.assertEqual(self.plans[0]["ani"], "5555555555")
        row = self.filed[0]
        self.assertEqual((row["outcome"], row["ani"], row["phone_id"], row["image_id"]), ("done", "5555555555", 7, 3))
        self.assertEqual((row["sent"], row["received"], row["resends"]), (12, 12, 0))   # no ACK after O: it hangs up
        cap = xp.parse_capture(open(os.path.join(self.tmp.name, row["capture"])).read())
        board_pk, _s, _l = xp.decode(xp.stream(cap, xp.A_TO_B))
        self.assertEqual(board_pk[0].type, "!")
        self.assertIn("plan: test download", [n for _t, n in cap["notes"]])
        self.assertEqual(self.svc.state()["sessions"], 1)

    def test_records_waiting_are_read_and_taken(self):
        # 5555001234, packed: lead 0, digit 0 as A, 0 ends.
        body = (bytes.fromhex("05555aa1234000") + bytes.fromhex("004e00") + bytes.fromhex("10011834")
                + bytes.fromhex("000047") + bytes.fromhex("05000500"))
        rec = body + (sum(body) & 0xFFFF).to_bytes(2, "little")   # bytes 21-22: 16-bit sum, LE
        board = self._run(lambda port: FakeBoard(port, records=[rec, rec[:-1] + b"\xa5"]))
        self.assertEqual("".join(p.type for p in board.got), "QDLBEESSDDKRPO")
        read = next(p for p in board.got if p.type == "D" and p.payload[:2] == b"\x3a\x00")
        self.assertEqual(read.payload[2], 2 * xp.CDR_RECORD_LEN)
        self.assertEqual(board.records, [])                                  # K took them
        row = self.filed[0]
        self.assertEqual(row["outcome"], "done")
        cap = xp.parse_capture(open(os.path.join(self.tmp.name, row["capture"])).read())
        found = xp.records_in_capture(cap)
        self.assertEqual([r["number"] for r in found["records"]], ["5555001234", "5555001234"])

    def test_a_write_lost_on_the_line_is_sent_again(self):
        # 2026-10-05 22:14: the first E drew nothing, and the board gave up
        # 26 s later with N 04. Now a copy goes out once the board has had
        # the packet's airtime plus the grace to answer.
        self.svc.reply_grace = 0.3
        board = self._run(lambda port: FakeBoard(port, deaf_at=5))
        self.assertEqual("".join(p.type for p in board.got), "QDLBEEESSDRPO")
        self.assertEqual(board.got[4], board.got[5])                        # the same write twice
        row = self.filed[0]
        self.assertEqual((row["outcome"], row["resends"]), ("done", 1))
        cap = xp.parse_capture(open(os.path.join(self.tmp.name, row["capture"])).read())
        self.assertTrue(any(n.startswith("no answer to E ") for _t, n in cap["notes"]), cap["notes"])

    def test_a_copy_is_never_queued_behind_one_still_going_out(self):
        # 2026-10-09 00:34, the DD7019 at 300: copies of a 255-byte write
        # were written while earlier ones were still on the wire, so they
        # went out back to back; the board answered none of the stacked
        # ones, and they used up MAX_RESENDS. Here the board says E twice,
        # each time at once (on loopback, long before the copy could have
        # left a modem). Every copy must wait for the one before it.
        self.svc.reply_grace = 0.3
        self.svc.idle_secs = 10
        orig = kl5net.WIRE_BPS
        kl5net.WIRE_BPS = 200                # slow enough that a 9-byte E is 0.45 s on the wire
        try:
            board = self._run(lambda port: FakeBoard(port, again_at=(5, 6), byte_gap=0.06))
        finally:
            kl5net.WIRE_BPS = orig
        self.assertEqual("".join(p.type for p in board.got), "QDLBEEEESSDRPO")
        self.assertEqual(board.got[4], board.got[6])
        airtime = kl5net.airtime(len(xp.frame("E", board.got[4].payload)), 200)
        for k in (5, 6):
            self.assertGreaterEqual(board.at[k] - board.at[k - 1], airtime)
        row = self.filed[0]
        self.assertEqual((row["outcome"], row["resends"]), ("done", 2))   # copies sent, not E's heard
        cap = xp.parse_capture(open(os.path.join(self.tmp.name, row["capture"])).read())
        self.assertTrue(any("has left: sending it again then" in n for _t, n in cap["notes"]), cap["notes"])

    def test_the_call_in_says_how_fast_the_line_is(self):
        # Measured: the DD7019's 69-byte `!` took 2.36 s at 300; at 1200 the
        # DD8822's took 0.63 s; in one read it is loopback-fast.
        self.assertEqual(kl5net.callin_bps(69, 2.36), 300)
        self.assertEqual(kl5net.callin_bps(70, 0.63), 1200)
        self.assertEqual(kl5net.callin_bps(69, 0), 1200)
        self.assertEqual(kl5net.callin_bps(69, 1.0), 300)    # in between: the side never early

    def test_the_resend_wait_sits_between_a_slow_answer_and_the_board_giving_up(self):
        # Too short and a slow answer meets a copy it did not need; too long
        # and the board's own timer ends the call first.
        self.assertGreaterEqual(kl5net.REPLY_GRACE_SECS, 4 * kl5net.SLOWEST_ANSWER_SECS)
        self.assertLessEqual(kl5net.REPLY_GRACE_SECS, kl5net.BOARD_GIVES_UP_SECS / 4)

    def _noisy(self, replies):
        self.svc.reply_grace, self.svc.resend_gap = 1.0, 0.3
        times = []
        board = self._run(lambda port: NoisyBoard(port, replies, times))
        return board, times

    def test_a_send_again_heard_mid_copy_waits_for_the_copy_to_leave(self):
        # 2026-10-08 00:47, 76.5 s: an E 2.1 s into an 8.5 s write drew a
        # copy at once, and Softmodem queued it behind the first. Two E's in
        # a row here (the second a misframe in the tail) make ONE copy, and
        # it is written only after the first has had its airtime.
        board, times = self._noisy([xp.frame("E"), xp.frame("E")])
        self.assertEqual("".join(p.type for p in board.got), "QDLBEEESSDRPO")
        first = next(i for i, (_t, p) in enumerate(times) if p.type == "E")
        gap = times[first + 1][0] - times[first][0]
        nbytes = len(xp.frame(board.got[first].type, board.got[first].payload))
        self.assertGreaterEqual(gap, kl5net.airtime(nbytes) + 0.3 - 0.05)
        row = self.filed[0]
        self.assertEqual((row["outcome"], row["resends"]), ("done", 1))

    def test_f_is_sent_again(self):
        # 2026-10-07 23:50, 39.6 s: F, ignored, then 13 s of waiting for nothing.
        board, _times = self._noisy([xp.frame("F")])
        self.assertEqual("".join(p.type for p in board.got), "QDLBEEESSDRPO")
        self.assertEqual(board.got[4], board.got[5])
        self.assertEqual((self.filed[0]["outcome"], self.filed[0]["resends"]), ("done", 1))

    def test_a_refusal_names_its_reason(self):
        board = self._run(lambda port: FakeBoard(port, refuse_at=1))
        cap = xp.parse_capture(open(os.path.join(self.tmp.name, self.filed[0]["capture"])).read())
        self.assertTrue(any("N 01: the manager sent N" in n for _t, n in cap["notes"]), cap["notes"])

    def test_a_refusal_is_filed_as_refused(self):
        board = self._run(lambda port: FakeBoard(port, refuse_at=1))
        self.assertEqual("".join(p.type for p in board.got), "Q")
        self.assertEqual(self.filed[0]["outcome"], "refused")

    def test_bad_final_write_is_rejected_before_any_manager_bytes_are_sent(self):
        # A real loopback session, with a valid Q and earlier writes. The bad
        # final write must prevent all transmission, not fail mid-download.
        steps = list(SCRIPT) + [('E', '10004edead')]
        self.svc.plan = lambda callin: {'script': steps, 'code': '9002',
                                       'phone_id': 7, 'label': 'invalid download'}
        board = self._run(FakeBoard)
        self.assertEqual(board.got, [])
        row = self.filed[0]
        self.assertEqual((row['outcome'], row['sent'], row['b_bytes'], row['resends']),
                         ('no-script', 0, 0, 0))
        with open(os.path.join(self.tmp.name, row['capture'])) as capture:
            cap = xp.parse_capture(capture.read())
        self.assertEqual(xp.stream(cap, xp.B_TO_A), b'')
        notes = '\n'.join(note for _, note in cap['notes'])
        self.assertIn('download write preflight failed', notes)
        self.assertNotIn('dead', notes)

    def test_a_board_that_drops_mid_dialogue_is_a_hangup(self):
        self._run(lambda port: FakeBoard(port, hang_up_at=4))
        self.assertEqual(self.filed[0]["outcome"], "hangup")

    def test_an_empty_q_is_refused_before_it_is_sent(self):
        # The first KL5-Net session (2026-10-01 17:05): no image picked and no
        # code set, so the Q went out with no payload and the board hung up
        # 1.7 s later without an N. That is filed as no-code now, not hangup.
        self.svc.plan = lambda callin: {"script": kl5net.ANSWER_ONLY, "code": "", "label": "answer only", "phone_id": 7}
        board = self._run(FakeBoard)
        self.assertEqual(board.got, [])
        row = self.filed[0]
        self.assertEqual((row["outcome"], row["sent"], row["phone_id"]), ("no-code", 0, 7))
        cap = xp.parse_capture(open(os.path.join(self.tmp.name, row["capture"])).read())
        self.assertTrue(any("no security code" in n for _t, n in cap["notes"]), cap["notes"])
        # with a code, the same plan is answered
        self.filed.clear()
        self.svc.plan = lambda callin: {"script": kl5net.ANSWER_ONLY, "code": "4242", "label": "answer only", "phone_id": 7}
        board = self._run(FakeBoard)
        self.assertEqual(board.got[0].payload, b"\x42\x42")
        self.assertEqual(self.filed[0]["outcome"], "done")

    def test_a_connection_with_no_callin_is_filed_as_such(self):
        class Short:
            def __init__(self, port):
                self.port = port
                self.got = []

            async def run(self):
                _r, w = await asyncio.open_connection("127.0.0.1", self.port)
                w.write(b"\x00\xff\x54")      # the Protel's 3 s first call: noise at most
                await w.drain()
                await asyncio.sleep(0.1)
                w.close()
        self._run(Short)
        self.assertEqual(self.filed[0]["outcome"], "no-callin")
        self.assertEqual(self.plans, [])

    def test_a_port_in_use_is_reported_not_raised(self):
        async def go():
            other = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
            port = other.sockets[0].getsockname()[1]
            ok = await self.svc.start("127.0.0.1", port)
            other.close()
            return ok, self.svc.state()
        ok, state = asyncio.run(go())
        self.assertFalse(ok)
        self.assertFalse(state["listening"])
        self.assertIn("cannot listen on 127.0.0.1:", state["why"])


# -- the page and its API -----------------------------------------------------------





if __name__ == "__main__":
    unittest.main()

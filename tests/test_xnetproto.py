"""XNet framing, the capture reader, the download image, and the replay
manager -- all pure, all against bytes measured off the board on
2026-10-01 (numbers and codes replaced; the shapes are the real ones)."""
import os
import sys
import time
import unittest


from kl5core import xnetproto as xp, protel_firmware_xnet as wire     # noqa: E402

ACK = bytes.fromhex("54024762")
CALLIN_TEXT = b"*5555555555*82140*DD8822*1234*000*2610015114005*00000*"
CALLIN = xp.frame("!", b"\x04" + b"\0" * 7 + CALLIN_TEXT + b"\x01\0\0")


class FramingTest(unittest.TestCase):
    def test_the_ack_is_four_bytes_and_the_length_counts_the_checksum(self):
        # 54 02 47 62 off the wire: the length byte says 2 and there are
        # two bytes after it, the type and the checksum. A decoder that
        # read the length as "payload after the type" found nothing all
        # night.
        self.assertEqual(xp.frame("G"), ACK)
        self.assertEqual(xp.frame("Q", b"\x12\x34"), bytes.fromhex("540451123410"))
        self.assertEqual(xp.checksum(bytes.fromhex("540247")), 0x62)

    def test_decode_round_trips_and_names_the_types(self):
        pk, skipped, left = xp.decode(ACK + xp.frame("D", b"\xff\xc5\x15") + xp.frame("O"))
        self.assertEqual([(p.type, p.payload) for p in pk], [("G", b""), ("D", b"\xff\xc5\x15"), ("O", b"")])
        self.assertEqual((skipped, left), (0, b""))

    def test_noise_is_skipped_and_a_sync_inside_data_does_not_fool_it(self):
        # A download's data is full of 0x54; only a verified packet counts.
        noise = b"\x00\x0a\xb0\x54\x07\xf1"
        pk, skipped, left = xp.decode(noise + ACK + b"\x54" + ACK)
        self.assertEqual([p.type for p in pk], ["G", "G"])
        self.assertEqual(skipped, len(noise) + 1)
        self.assertEqual(left, b"")

    def test_an_unfinished_packet_is_leftover_not_lost(self):
        whole = xp.frame("E", b"\x10\x00" + bytes(range(40)))
        pk, skipped, left = xp.decode(ACK + whole[:20], final=False)
        self.assertEqual([p.type for p in pk], ["G"])
        self.assertEqual(left, whole[:20])
        pk, _s, left = xp.decode(left + whole[20:], final=False)
        self.assertEqual(pk[0].payload[:2], b"\x10\x00")
        self.assertEqual(left, b"")

    def test_a_whole_capture_holds_nothing_back(self):
        # A stray 0x54 whose length byte points past the end is noise in a
        # finished capture, not an unfinished packet: holding it would hide
        # every packet after it (the first cut of this decoder did).
        pk, skipped, left = xp.decode(b"\x54" + ACK + b"\x54")
        self.assertEqual([p.type for p in pk], ["G"])
        self.assertEqual((skipped, left), (2, b""))

    def test_payload_limit(self):
        xp.frame("E", bytes(253))
        with self.assertRaises(ValueError):
            xp.frame("E", bytes(254))


class CallinTest(unittest.TestCase):
    def test_fields_are_kept_whole_and_the_known_ones_named(self):
        pk, _s, _l = xp.decode(CALLIN)
        c = xp.parse_callin(pk[0].payload)
        self.assertEqual(c["ani"], "5555555555")
        self.assertEqual(c["firmware"], "DD8822")
        self.assertEqual(c["field4"], "1234")
        self.assertEqual(c["clock"], "2026-10-01 11:40:05 (dow 5)")
        self.assertEqual(c["fields"][1:4], ["5555555555", "82140", "DD8822"])
        self.assertEqual(c["prefix"], "0400000000000000")

    def test_clock_payload_matches_what_expressnet_sent(self):
        # ExpressNet's P on 2026-10-01 11:40:05 (a Thursday) was 2610015114005.
        t = time.mktime((2026, 10, 1, 11, 40, 5, 3, 274, -1))
        self.assertEqual(xp.clock_payload(t), b"2610015114005")
        sun = time.mktime((2026, 10, 4, 0, 0, 0, 6, 277, -1))
        self.assertEqual(xp.clock_payload(sun)[6:7], b"1")


class RecordsTest(unittest.TestCase):
    def test_the_call_records_read_at_3a00_decode_23_bytes_each(self):
        # The upload is not a U packet: the manager READS 0x3a00, len 23 per
        # record, then sends K. Four coin calls on 2026-10-01 (numbers
        # replaced; the shapes are the real ones).
        def rec(num, when, duration):
            body = (bytes.fromhex(num) + bytes.fromhex("004e00") + bytes.fromhex(when) + bytes.fromhex(duration)
                    + bytes.fromhex("05000500"))
            return body + (sum(body) & 0xFFFF).to_bytes(2, "little")   # bytes 21-22: 16-bit sum, LE
        block = b"\x3a\x00" + rec("02155aa5a1a000", "10011834", "000047") + rec("03a2aa5a512000", "10011841", "000121")
        header = bytes([xp.SYNC, len(block) + 3, ord('D')]) + block[:2]
        block = block[:2] + bytes([xp.checksum(header)]) + block[2:]
        out = xp.parse_cdr_block(block)
        self.assertEqual([(r["number"], r["when"], r["secs"], r["w1"]) for r in out],
                         [("2155005010", "10/01 18:34", 47, "000047"), ("3020050512", "10/01 18:41", 81, "000121")])
        self.assertEqual([(r["cents1"], r["cents2"], r["check_ok"]) for r in out], [(25, 25, True)] * 2)
        self.assertEqual(xp.unpack_number(bytes.fromhex("02159aa6114000")), "2159006114")
        self.assertEqual(xp.parse_cdr_block(b"\x3a"), [])
        # the capture summary names the read and carries the records
        board = [xp.Packet("D", block)]
        d = xp.summarize(board, xp.A_TO_B)
        self.assertEqual(d[0]["name"], "read answer (call records)")
        self.assertEqual(len(d[0]["records"]), 2)
        mgr = xp.summarize([xp.Packet("D", b"\x3a\x00\x5c"), xp.Packet("K", b"\x00\x00")], xp.B_TO_A)
        self.assertEqual([m["name"] for m in mgr], ["read addr len (call records)", "records taken"])


class ImageTest(unittest.TestCase):
    def test_writes_build_a_sparse_image_and_diff_by_address(self):
        packet = lambda addr, data: xp.decode(wire.memory_write(addr, bytes.fromhex(data)))[0][0]
        a = [packet(0x1000, "d33d"), packet(0x1060, "0000")]
        b = [packet(0x1000, "4b3f"), packet(0x1060, "0888"), packet(0x20ea, "55")]
        ia, ib = xp.memory_image(a), xp.memory_image(b)
        self.assertEqual(xp.writes(a)[0], (0x1000, bytes.fromhex("d33d")))
        self.assertEqual(xp.diff_images(ia, ib),
                         [(0x1000, 0xd3, 0x4b), (0x1001, 0x3d, 0x3f), (0x1060, 0, 8), (0x1061, 0, 0x88), (0x20ea, None, 0x55)])

    def test_outer_valid_bad_inner_header_never_enters_the_image(self):
        # Independent header vector: complement of 54+07+45+10+00 is 4f.
        valid = xp.Packet('E', bytes.fromhex('10004fdead'))
        self.assertEqual(xp.write_request(valid), (0x1000, bytes.fromhex('dead')))
        bad = xp.decode(xp.frame('E', bytes.fromhex('10004edead')))[0][0]
        with self.assertRaises(ValueError):
            xp.write_request(bad)
        self.assertEqual(xp.memory_image([bad, valid]), {0x1000: 0xde, 0x1001: 0xad})
        self.assertEqual(xp.writes([xp.Packet('E', b''), bad]), [])

    def test_repeated_overlapping_writes_use_actual_data_addresses(self):
        packets = [xp.decode(wire.memory_write(a, d))[0][0]
                   for a, d in [(0x1000, b'abc'), (0x1001, b'XY'), (0x1001, b'XY')]]
        self.assertEqual(xp.memory_image(packets), {0x1000: ord('a'), 0x1001: ord('X'), 0x1002: ord('Y')})



def capture_text(blocks, label="baseline"):
    text = ("# session : 2026-10-01 09:51:28\n# label   : %s\n# A side  : listen:127.0.0.1:2323\n"
            "# B side  : connect:127.0.0.1:2324\n# A>B is toward ExpressNet, B>A is toward the phone.\n#\n"
            "[    0.000] -- both ends connected\n" % label)
    for t, d, data in blocks:
        text += "[%9.3f] %s %d byte%s\n" % (t, d, len(data), "" if len(data) == 1 else "s")
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            text += "    %04x  %-47s  |%s|\n" % (off, " ".join("%02x" % b for b in chunk), "." * len(chunk))
    text += "[   41.220] -- session ended: 1 bytes A>B, 2 bytes B>A\n"
    return text


class CaptureTest(unittest.TestCase):
    def test_the_relay_format_comes_back_as_bytes_per_direction(self):
        # A packet split across two of the relay's read() blocks is whole
        # again in the stream -- which is why the decoder reads the stream
        # and not the blocks (the 253-byte chunks never verified block by block).
        big = xp.frame("E", b"\x13\x30" + bytes(range(200)))
        blocks = [(1.2, "A>B", CALLIN), (2.0, "B>A", big[:100]), (2.1, "B>A", big[100:]), (2.5, "A>B", ACK)]
        cap = xp.parse_capture(capture_text(blocks))
        self.assertEqual(cap["header"]["label"], "baseline")
        self.assertEqual([d for _t, d, _b in cap["blocks"]], ["A>B", "B>A", "B>A", "A>B"])
        self.assertEqual(xp.stream(cap, xp.B_TO_A), big)
        self.assertEqual([n for _t, n in cap["notes"]], ["both ends connected", "session ended: 1 bytes A>B, 2 bytes B>A"])
        d = xp.decode_capture(cap)
        self.assertEqual([p["type"] for p in d["A>B"]["packets"]], ["!", "G"])
        self.assertEqual(d["A>B"]["packets"][0]["callin"]["ani"], "5555555555")
        self.assertEqual(d["B>A"]["packets"][0]["addr"], "1330")
        self.assertEqual(d["B>A"]["packets"][0]["name"], "write addr data")
        self.assertEqual(d["A>B"]["packets"][1]["name"], "ack")
        self.assertEqual(d["B>A"]["skipped"], 0)


SCRIPT = [xp.Packet("Q", b"\x12\x34"), xp.Packet("D", b"\xff\xc5\x15"), xp.Packet("L", b""),
          xp.Packet("B", bytes.fromhex("41000010601ca000")), xp.Packet("E", b"\x10\x00\xa8\x4b"),
          xp.Packet("S", bytes(8)), xp.Packet("P", b"2610015055800"), xp.Packet("O", b"")]


class ReplayManagerTest(unittest.TestCase):
    def test_a_recorded_session_is_replayed_ack_by_ack_with_a_fresh_clock(self):
        clock = lambda: time.mktime((2026, 10, 2, 8, 0, 0, 4, 275, -1))      # Friday
        m = xp.ReplayManager(SCRIPT, clock=clock)
        self.assertEqual(m.feed(b"\x00\xff"), b"")                       # noise before the call-in
        self.assertEqual(m.feed(CALLIN), xp.frame("Q", b"\x12\x34"))     # the call-in opens it
        self.assertEqual(m.callin["ani"], "5555555555")
        self.assertEqual(m.feed(ACK), xp.frame("D", b"\xff\xc5\x15"))
        answer = xp.frame("D", b"\xff\xc5\x89\x30\x30")                   # the board answers the read
        self.assertEqual(m.feed(answer), xp.frame("L"))
        self.assertEqual(m.feed(xp.frame("L", b"\x02\x02")), xp.frame("B", bytes.fromhex("41000010601ca000")))
        self.assertEqual(m.feed(ACK), xp.frame("E", b"\x10\x00\xa8\x4b"))
        self.assertEqual(m.feed(ACK), xp.frame("S", bytes(8)))
        self.assertEqual(m.feed(ACK), xp.frame("P", b"2610026080000"))     # not the recorded clock
        self.assertEqual(m.state, "talking")
        self.assertEqual(m.feed(ACK), xp.frame("O"))
        self.assertEqual(m.state, "done")
        self.assertEqual(m.feed(ACK), b"")
        self.assertEqual([p.type for p in m.sent], ["Q", "D", "L", "B", "E", "S", "P", "O"])

    # a script with the counts read (D 0ff8) and an R after it, like the real downloads
    COUNTS_SCRIPT = [xp.Packet("Q", b"\x12\x34"), xp.Packet("D", b"\x0f\xf8\x08"), xp.Packet("R", b""), xp.Packet("O", b"")]

    def _to_counts(self, m):
        """Drive a COUNTS_SCRIPT manager up to just after it sends the
        0ff8 read, and return the counts-read frame it last sent."""
        m.feed(CALLIN)                                      # sends Q
        return m.feed(ACK)                                  # Q acked -> sends D 0ff8

    def test_records_waiting_add_a_read_and_a_k_before_the_script_goes_on(self):
        m = xp.ReplayManager(self.COUNTS_SCRIPT)
        self.assertEqual(self._to_counts(m), xp.frame("D", b"\x0f\xf8\x08"))
        counts = xp.frame("D", bytes.fromhex("0ff853") + bytes.fromhex("0000000000040004"))   # four waiting (16:46)
        self.assertEqual(m.feed(counts), xp.frame("D", bytes([0x3a, 0x00, 4 * xp.CDR_RECORD_LEN])))
        self.assertEqual(m.records_due, 4)
        self.assertEqual(m.feed(xp.frame("D", b"\x3a\x00\xcc" + bytes(92))), xp.frame("K", b"\x00\x00"))
        self.assertEqual(m.feed(ACK), xp.frame("R"))                   # then the script carries on
        # none waiting: the script goes straight on to R, no extra step
        m2 = xp.ReplayManager(self.COUNTS_SCRIPT)
        self._to_counts(m2)
        self.assertEqual(m2.feed(xp.frame("D", bytes.fromhex("0ff853") + bytes(8))), xp.frame("R"))
        self.assertEqual(m2.records_due, 0)
        # the counts are a ring position and a count, mirrored: $0FFC first, $0FFD count
        self.assertEqual(xp.cdr_ring(bytes.fromhex("0ff853") + bytes([0, 0, 0, 0, 0, 30, 0, 30])), (0, 30))
        # a board holding more than one packet's worth is read a packet at a
        # time, ALL of it before K -- K clears every waiting record (emulator,
        # 2026-10-06: twelve waiting, ten read, K, and records 11-12 were gone)
        m3 = xp.ReplayManager(self.COUNTS_SCRIPT)
        self._to_counts(m3)
        twelve = xp.frame("D", self._counts(0, 12))
        self.assertEqual(m3.feed(twelve), xp.frame("D", bytes([0x3a, 0x00, 10 * xp.CDR_RECORD_LEN])))
        self.assertEqual(m3.feed(self._records(0x3a00, 10)), xp.frame("D", bytes([0x3a, 0xe6, 2 * xp.CDR_RECORD_LEN])))
        self.assertEqual(m3.feed(self._records(0x3ae6, 2)), xp.frame("K", b"\x00\x00"))
        self.assertEqual(m3.records_due, 12)

    @staticmethod
    def _counts(first, count):
        body = bytes([0x0f, 0xf8])
        data = bytes([0, 0, 0, 0, first, count, first, count])
        header = bytes([xp.SYNC, len(body) + 1 + len(data) + 2, ord("D")]) + body
        return body + bytes([xp.checksum(header)]) + data

    @staticmethod
    def _records(addr, n):
        data = bytes(23) * n
        body = bytes([addr >> 8, addr & 0xff])
        header = bytes([xp.SYNC, len(body) + 1 + len(data) + 2, ord("D")]) + body
        return xp.frame("D", body + bytes([xp.checksum(header)]) + data)

    def test_records_after_a_k_are_read_from_where_the_ring_has_got_to(self):
        # After K the board's counts name the slot of the first new record
        # (`00 00 00 00 0c 02 0c 02`, emulator 2026-10-06). The old "largest
        # of the eight" read that as twelve records at 0x3a00 -- ten stale
        # ones filed again -- and K then cleared the two new ones unread.
        m = xp.ReplayManager(self.COUNTS_SCRIPT)
        self._to_counts(m)
        self.assertEqual(m.feed(xp.frame("D", self._counts(12, 2))),
                         xp.frame("D", bytes([0x3b, 0x14, 2 * xp.CDR_RECORD_LEN])))
        self.assertEqual(m.records_due, 2)
        # and a run that reaches the end of the 125-slot ring wraps to its start
        self.assertEqual(xp.cdr_reads(120, 8), [
            xp.Packet("D", bytes([0x44, 0xc8, 5 * 23])), xp.Packet("D", bytes([0x3a, 0x00, 3 * 23]))])
        # counts whose mirror disagrees are refused: no read, and no K
        m2 = xp.ReplayManager(self.COUNTS_SCRIPT)
        self._to_counts(m2)
        bad = bytearray(self._counts(0, 4))
        bad[-1] = 5
        header = bytes([xp.SYNC, len(bad) + 2, ord("D")]) + bytes(bad[:2])
        bad[2] = xp.checksum(header)
        self.assertEqual(m2.feed(xp.frame("D", bytes(bad))), b"")
        self.assertEqual(m2.state, "refused")

    def test_invalid_record_envelope_never_sends_records_taken(self):
        data = bytes(23)
        header = bytes([xp.SYNC, 28, ord('D'), 0x3a, 0])
        valid = b'\x3a\x00' + bytes([xp.checksum(header)]) + data
        corrupt = valid[:2] + bytes([valid[2] ^ 1]) + valid[3:]
        for response in (xp.frame('D', corrupt), xp.frame('D', valid[:-1]),
                         xp.frame('D', b'\x3a\x00' + data), ACK,
                         xp.frame('D', b'\x3b\x00' + valid[2:])):
            m = xp.ReplayManager(self.COUNTS_SCRIPT)
            self._to_counts(m)
            m.feed(xp.frame('D', bytes.fromhex('0ff8530000000000010001')))
            self.assertEqual(m.feed(response), b'')
            self.assertEqual(m.state, 'refused')
            self.assertNotIn('K', [p.type for p in m.sent])

    def test_invalid_pending_count_header_is_refused_before_record_read(self):
        m = xp.ReplayManager(self.COUNTS_SCRIPT)
        self._to_counts(m)
        self.assertEqual(m.feed(xp.frame('D', bytes.fromhex('0ff8520000000000010001'))), b'')
        self.assertEqual(m.state, 'refused')
        self.assertNotIn('K', [p.type for p in m.sent])

    def test_the_board_asking_again_gets_the_same_packet(self):
        m = xp.ReplayManager(SCRIPT)
        m.feed(CALLIN)
        self.assertEqual(m.feed(xp.frame("E")), xp.frame("Q", b"\x12\x34"))
        self.assertEqual(m.resends, 1)
        # the 27.5 s retry of the call-in means it never heard us: say it again
        self.assertEqual(m.feed(CALLIN), xp.frame("Q", b"\x12\x34"))
        self.assertEqual(m.resends, 2)

    def test_the_same_packet_refused_too_often_ends_it(self):
        # 2026-10-02 04:3x: a board answering "send again" to one write, 800 rounds, 20 kB, no end
        m = xp.ReplayManager(SCRIPT)
        m.feed(CALLIN)
        for _ in range(xp.MAX_RESENDS):
            self.assertEqual(m.feed(xp.frame("E")), xp.frame("Q", b"\x12\x34"))
        self.assertEqual(m.feed(xp.frame("E")), b"")
        self.assertEqual(m.state, "stuck")
        self.assertEqual(m.sent[-1].type, "Q")                   # the packet it would not take
        # an ACK after a resend or two is fine: the count is per packet
        m = xp.ReplayManager(SCRIPT)
        m.feed(CALLIN)
        m.feed(xp.frame("E")); m.feed(xp.frame("E"))
        m.feed(ACK)
        self.assertEqual(m.resends_here, 0)
        self.assertEqual(m.state, "talking")

    def test_silence_sends_the_packet_in_hand_again(self):
        # 2026-10-05 22:14: no answer to the first write, then N 04 at 26 s
        m = xp.ReplayManager(SCRIPT)
        self.assertEqual(m.on_silence(), [])                     # nothing sent yet: nothing to repeat
        m.feed(CALLIN)
        self.assertEqual(m.on_silence(), [xp.frame("Q", b"\x12\x34")])
        self.assertEqual((m.resends, m.resends_here), (1, 1))
        m.feed(ACK)                                              # the copy got through
        self.assertEqual(m.resends_here, 0)
        # a line that stays dead ends stuck, counted with the E resends
        for _ in range(xp.MAX_RESENDS):
            self.assertTrue(m.on_silence())
        self.assertEqual(m.on_silence(), [])
        self.assertEqual(m.state, "stuck")

    def test_junk_ahead_of_the_callin_does_not_hold_it(self):
        # V.22 training junk can carry a 0x54 with a big length byte; the
        # real `!` behind it must not wait for that many bytes (2026-10-06).
        junk = bytes([xp.SYNC, 0xf0, 0x12, 0x34])
        packets, skipped, left = xp.decode(junk + CALLIN, final=False)
        self.assertEqual([p.type for p in packets], ["!"])
        self.assertEqual((skipped, left), (len(junk), b""))
        # a fragment with no call-in behind it is still kept, not dropped
        packets, skipped, left = xp.decode(junk, final=False)
        self.assertEqual((packets, left), ([], junk))
        # and a manager-side fragment mid-dialogue is not jumped over
        g = xp.frame("G")
        packets, _s, left = xp.decode(junk + g, final=False)
        self.assertEqual((packets, left), ([], junk + g))

    def test_a_refusal_ends_it(self):
        m = xp.ReplayManager(SCRIPT)
        m.feed(CALLIN)
        self.assertEqual(m.feed(xp.frame("N", b"\x01")), b"")
        self.assertEqual(m.state, "refused")
        self.assertEqual(m.feed(ACK), b"")

    def test_a_packet_split_across_reads_is_handled(self):
        m = xp.ReplayManager(SCRIPT)
        self.assertEqual(m.feed(CALLIN[:10]), b"")
        self.assertEqual(m.feed(CALLIN[10:]), xp.frame("Q", b"\x12\x34"))


if __name__ == "__main__":
    unittest.main()



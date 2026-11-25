"""Building a Protel download image from the console's records.

The map was measured off the live records against a real download
(common/protelimage.py docstring); these tests pin the mechanics with
synthetic records so CI needs no proprietary data:

  * the costing record drives the region-mapped bytes (the bulk affine
    copy and the three windows at other record offsets),
  * the carried windows stay as the reference has them,
  * a confirmed option splice overlays from the options record,
  * the two variable chunks grow and shrink with the record -- a restricted
    number lengthens the 0x1eb7 write and sets its free-space mark,
  * reconstruct_stats counts what the record alone reproduces.

The real-data check (reproduces the 16:32 download byte-for-byte, 95% from
the records alone) was run by hand against ExpressNet's own MISCOPT.DAT / CALLCOST.DAT;
those files are third-party and are not part of this repository.
"""
import unittest


from kl5core import protelimage as pi     # noqa: E402
from kl5core import xnetcost               # noqa: E402
from kl5core import xnetopts               # noqa: E402

IMG_LEN = 0x20ed - 0x1000                      # the real download's span


def synthetic():
    """A costing record whose byte i is (i & 0xff), and a reference image
    that is the record mapped through every region with each carried
    window stamped with a sentinel the record would never produce there."""
    costing = bytes((i & 0xff) for i in range(xnetcost.RECLEN))
    ref = bytearray(IMG_LEN)
    for k in range(IMG_LEN):
        ref[k] = costing[pi.record_offset(pi.IMAGE_BASE + k)]
    for off in pi._carried_offsets():
        ref[off] = 0xEE                        # a carried byte the regions never make here
    return costing, bytes(ref)


class BuildTest(unittest.TestCase):
    def test_build_on_its_own_reference_reproduces_it(self):
        costing, ref = synthetic()
        self.assertEqual(pi.build(costing, ref), ref)

    def test_costing_drives_the_affine_bytes(self):
        costing, ref = synthetic()
        # a blank reference: every affine byte comes from the record, carried stay blank
        built = pi.build(costing, bytes(len(ref)))
        carried = pi._carried_offsets()
        for k in range(len(ref)):
            if k in carried:
                self.assertEqual(built[k], 0, "carried offset %04x should stay from reference" % (k + pi.IMAGE_BASE))
            else:
                self.assertEqual(built[k], costing[pi.record_offset(pi.IMAGE_BASE + k)])

    def test_the_later_regions_win_over_the_bulk_copy(self):
        # the first restricted slot is record 6756, not the bulk copy's 99 + 0xf15
        self.assertEqual(pi.record_offset(0x1f15), xnetcost.RESTRICTED_AT)
        self.assertEqual(pi.record_offset(0x1f15 + 8), xnetcost.RESTRICTED_AT + 8)
        self.assertEqual(pi.record_offset(0x178b), 7644)                   # the NPA header copy
        self.assertEqual(pi.record_offset(0x1400), pi.COST_RECORD_OFFSET + 0x400)
        self.assertIsNone(pi.record_offset(0x2148))                       # the zone tail: not in the record

    def test_carried_windows_come_from_the_reference_not_the_record(self):
        costing, ref = synthetic()
        built = pi.build(costing, ref)
        for off in pi._carried_offsets():
            self.assertEqual(built[off], 0xEE)      # the sentinel, not costing[99+off]

    def test_an_option_splice_overlays_from_the_options_record(self):
        costing, ref = synthetic()
        options = bytearray(128)
        options[77] = 0x60                          # register 133 = 1200 (bit 6)
        built = pi.build(costing, ref, options)
        self.assertEqual(built[0x1005 - pi.IMAGE_BASE], 0x60)
        # without an options record the splice is left as the reference has it
        self.assertEqual(pi.build(costing, ref)[0x1005 - pi.IMAGE_BASE], ref[0x1005 - pi.IMAGE_BASE])

    def test_a_multi_byte_splice_copies_the_whole_run(self):
        # the Miscellaneous Number: six packed bytes at options 282.. land at 0x104f..
        costing, ref = synthetic()
        options = bytearray(420)
        options[282:288] = bytes.fromhex("055598765430")
        built = pi.build(costing, ref, options)
        self.assertEqual(built[0x104f - pi.IMAGE_BASE:0x1055 - pi.IMAGE_BASE], bytes.fromhex("055598765430"))
        # and through build_writes, chunk by chunk, the same
        (addr, data), = pi.build_writes(costing, [(0x1040, ref[0x40:0x40 + 32])], options)
        self.assertEqual(data[0x104f - 0x1040:0x1055 - 0x1040], bytes.fromhex("055598765430"))

    def test_reconstruct_stats_counts_record_derived_bytes(self):
        costing, ref = synthetic()
        st = pi.reconstruct_stats(costing, ref)
        self.assertEqual(st["total"], IMG_LEN)
        self.assertEqual(st["carried"], len(pi._carried_offsets()))
        # affine bytes match a blank reference's rebuild; the sentinel windows do not
        self.assertEqual(st["matches"], IMG_LEN - len(pi._carried_offsets()))


class WritesTest(unittest.TestCase):
    def test_build_writes_keeps_the_reference_chunk_layout(self):
        costing, ref = synthetic()
        # a chunk layout like a real download: a few E writes at set addresses
        reference_writes = [(0x1000, ref[0:170]), (0x10d8, ref[0xd8:0xd8 + 100]),
                            (0x20ea, ref[0x20ea - 0x1000:0x20ea - 0x1000 + 3])]
        out = pi.build_writes(costing, reference_writes)
        self.assertEqual([(a, len(d)) for a, d in out], [(a, len(d)) for a, d in reference_writes])
        # the 0x10d8 chunk's bytes are the costing affine (0x10d8 is not a carried window start)
        a, d = out[1]
        self.assertEqual(d[2], costing[pi.COST_RECORD_OFFSET + (0x10d8 - 0x1000) + 2])

    def test_build_writes_maps_each_chunk_by_its_absolute_address(self):
        # a chunk anywhere in the image is filled from the costing affine at
        # its own address; an empty list is the only refusal.
        costing, _ = synthetic()
        (addr, data), = pi.build_writes(costing, [(0x1500, bytes(4))])
        self.assertEqual(addr, 0x1500)
        self.assertEqual(data[1], costing[pi.COST_RECORD_OFFSET + (0x1500 - pi.IMAGE_BASE) + 1])
        with self.assertRaises(pi.ProtelImageError):
            pi.build_writes(costing, [])


class VariableChunkTest(unittest.TestCase):
    """The restricted-number write grows with the record and carries the
    board's free-space mark; the NPA-header write grows with a band entry."""

    def setUp(self):
        # a real-shape costing record holding only the text block the real
        # one has (record 6137.. and 6184..), and a reference 0x1eb7 chunk
        # of 65 bytes ending in '0' as the real one does
        rec = bytearray(xnetcost.RECLEN)
        rec[6137:6157] = b"0" * 20
        rec[6157:6184] = b" " * 27                      # the field the site's number is spliced over
        rec[6184:6201] = b"00000000PH.000000"
        self.costing = bytes(rec)
        chunk = bytearray(65)
        chunk[0] = 76                                   # the mark the real download carried at 0 numbers
        chunk[1:21] = b"0" * 20
        chunk[21:33] = b"105555550123"                  # the site's number (carried)
        chunk[48:65] = b"00000000PH.000000"
        self.ref = [(0x1eb7, bytes(chunk))]

    def test_no_numbers_sends_the_65_byte_chunk_with_76_free(self):
        (addr, data), = pi.build_writes(self.costing, self.ref)
        self.assertEqual((addr, len(data), data[0]), (0x1eb7, 65, 76))
        self.assertEqual(data[21:33], b"105555550123")            # carried, the record holds spaces there

    def test_a_number_lengthens_the_chunk_and_lowers_the_mark(self):
        rec = xnetcost.write(self.costing, {"restricted": ["5551234567"]})
        (addr, data), = pi.build_writes(rec, self.ref)
        self.assertEqual(len(data), 99)                            # 65 + 29 zeros + 5 packed bytes, as measured
        self.assertEqual(data[-5:], bytes.fromhex("5551234567"))
        self.assertEqual(data[0], 42)                              # 0x1f44 - 0x1f1a
        rec = xnetcost.write(self.costing, {"restricted": ["5551234567", "5556696699"]})
        (addr, data), = pi.build_writes(rec, self.ref)
        self.assertEqual(len(data), 107)
        self.assertEqual(data[0x1f1d - 0x1eb7:], bytes.fromhex("5556696699"))
        self.assertEqual(data[0], 34)
        # removing the numbers shrinks it back, even from a reference that held them
        back, = pi.build_writes(self.costing, [(0x1eb7, data)])
        self.assertEqual((len(back[1]), back[1][0]), (65, 76))

    def test_the_npa_header_chunk_follows_its_last_non_zero_byte(self):
        rec = bytearray(xnetcost.RECLEN)
        rec[7644:7656] = bytes.fromhex("021520000 3a2200001112040".replace(" ", ""))
        ref = [(0x1711, bytes(134))]
        (addr, data), = pi.build_writes(bytes(rec), ref)
        self.assertEqual(len(data), 134)                           # last non-zero at 0x1796
        rec[7644:7657] = bytes.fromhex("0215210001 03a2200001112040".replace(" ", ""))
        (addr, data), = pi.build_writes(bytes(rec), ref)
        self.assertEqual(len(data), 135)                           # the band entry pushed it one byte
        self.assertEqual(data[0x178b - 0x1711:], bytes.fromhex("0215210001 03a2200001112040".replace(" ", "")))


class ZoneTest(unittest.TestCase):
    """With zones, the NPA header is built into the 0x1711 chunk and the
    tail replaces the reference's 0x2148.. chunks."""

    def test_zones_replace_the_header_copy_and_the_tail(self):
        costing = bytearray(xnetcost.RECLEN)
        costing[7644:7657] = bytes.fromhex("02152100 0103a2200001112040".replace(" ", ""))   # a stale header copy
        ref = [(0x1711, bytes(134)), (0x2148, bytes.fromhex("f780"))]
        out = pi.build_writes(bytes(costing), ref, zones={215: {200: 1, 210: 1}, 302: {}})
        self.assertEqual([a for a, _ in out], [0x1711, 0x2148])
        hdr = out[0][1][0x178b - 0x1711:]
        self.assertEqual(hdr, bytes.fromhex("0215210001 03a22000 01112040".replace(" ", "")))
        self.assertEqual(out[1], (0x2148, bytes.fromhex("f68020")))
        # without zones the reference's tail and the record's header copy are kept
        out = pi.build_writes(bytes(costing), ref)
        self.assertEqual(out[1], (0x2148, bytes.fromhex("f780")))

    def test_the_script_grows_a_write_per_npa(self):
        from kl5core import protel_firmware_xnet as wire, xnetproto as xp
        payload = lambda a,d: xp.decode(wire.memory_write(a,d))[0][0].payload.hex()
        script = [["Q", "1234"], ["B", "41000010001d0000"], ["E", payload(0x1000,bytes(169))], ["E", payload(0x1711,bytes(133))], ["E", payload(0x2148,b'\x80')], ["S", "00"], ["O", ""]]
        costing = bytes(xnetcost.RECLEN)
        out = pi.rebuild_download_script(script, costing, zones={215: {200: 1}, 302: {210: 1}})
        self.assertEqual([s[0] for s in out], ["Q", "B", "E", "E", "E", "E", "S", "O"])
        # 215 at one bit is 101 bytes from 0x2148, so 302 starts at 0x21ad; its
        # 210 is cell 10 (byte 1, 0x20) and its lead, 0xf8 - (101 + 2), sits on cell byte 0
        self.assertEqual(xp.write_request(xp.Packet("E",bytes.fromhex(out[5][1]))), (0x21ad,b"\x20"))


class SweepPlacementTest(unittest.TestCase):
    """Where the options sweep put every field that is zero in a stock
    record. Measured against ExpressNet in the container on 2026-10-04:
    with these values the builder reproduced ExpressNet's download byte for
    byte. Addresses are the module's capture coordinates (CPU + 1)."""



class ScheduledCallinTest(unittest.TestCase):
    """phones.scheduled_callin OFF builds a download with no self-poll
    schedule: build_download(scheduled_callin=False) is exactly what a
    poll-stripped options record builds, and differs from the flag on."""

    def test_off_strips_the_schedule_and_on_is_the_default(self):
        cost = bytes(xnetcost.RECLEN)
        opts = xnetopts.write(bytes(xnetopts.RECLEN),
                              {"poll_start": 9, "poll_length": 4, "day_of_month_report": 15,
                               "cashbox_volume": 80})
        on = list(pi.build_download(cost, opts, "5555550123", scheduled_callin=True))
        off = list(pi.build_download(cost, opts, "5555550123", scheduled_callin=False))
        self.assertNotEqual(on, off)                                   # the schedule reached the image
        self.assertEqual(on, list(pi.build_download(cost, opts, "5555550123")))   # default: on
        stripped = xnetopts.strip_poll_schedule(opts)
        self.assertEqual(off, list(pi.build_download(cost, stripped, "5555550123", scheduled_callin=True)))
        # a record with no schedule to begin with is unaffected by the flag
        plain = bytes(xnetopts.RECLEN)
        self.assertEqual(list(pi.build_download(cost, plain, "5555550123", scheduled_callin=True)),
                         list(pi.build_download(cost, plain, "5555550123", scheduled_callin=False)))


class ReportScheduleTest(unittest.TestCase):
    """Number of Days Report and Time of Day Report reach the board at
    CPU $10D6/$10D7, where FUN_969b reads them. Before 2026-10-07 the
    builder sent neither, so a phone set to report every day was told
    never to call."""

    def image(self, fields, scheduled_callin=True):
        from kl5core import protel_firmware_xnet as wire
        from kl5core import kl5net
        cost = bytes(xnetcost.RECLEN)
        opts = xnetopts.write(bytes(xnetopts.RECLEN), dict(fields, reporting_number="2155551234"))
        writes = pi.build_download(cost, opts, "3027700234", scheduled_callin=scheduled_callin)
        image = wire.configuration_from_packets(kl5net.build_script(pi.download_script(writes), "9002"))
        self.assertEqual(image[0] | (image[1] << 8), wire.configuration_checksum(image))
        return image

    def test_days_and_hour_land_in_bcd_at_10d6(self):
        image = self.image({"days_report": 14, "time_of_day_report": 23})
        self.assertEqual(image[0x10d6 - 0x1000:0x10d8 - 0x1000], bytes([0x14, 0x23]))

    def test_an_hour_alone_still_goes_out(self):
        # $10D7 is the 0x1000 write's last byte, so the write must reach it
        # even when the days byte before it is zero
        image = self.image({"time_of_day_report": 3, "day_of_week_report": 2})
        self.assertEqual(image[0x10d6 - 0x1000:0x10d8 - 0x1000], bytes([0x00, 0x03]))
        self.assertEqual(image[0x1334 - 0x1000], 0x02)

    def test_scheduled_callin_off_sends_neither(self):
        image = self.image({"days_report": 1, "time_of_day_report": 3}, scheduled_callin=False)
        self.assertEqual(image[0x10d6 - 0x1000:0x10d8 - 0x1000], bytes(2))

    def test_the_three_keypad_codes_follow_the_security_code(self):
        # registers 291..294 on the emulated board: $1958 security (+ check),
        # $195B *#1 collection, $195D *#4/*#5 box amount, $195F CDR
        image = self.image({"security_code": "9002", "collection_code": "1234",
                            "box_amount_code": "5678", "cdr_code": "0042"})
        self.assertEqual(image[0x1958 - 0x1000:0x1961 - 0x1000].hex(), "9002e7123456780042")

    def test_the_last_reporting_and_card_options(self):
        # 264 / $10D5 collect calls per number, 371 / $1338 days before full
        # (both keyed on the emulated board), $1EB0 coin box reporting hour
        # (read by the firmware beside the removal window at $1EAE/$1EAF)
        image = self.image({"collect_calls_per_number": 12, "days_before_full": 200,
                            "box_removal_start": 2, "box_removal_length": 3, "box_report_hour": 7})
        self.assertEqual(image[0x10d5 - 0x1000], 12)
        self.assertEqual(image[0x1338 - 0x1000], 200)
        self.assertEqual(image[0x1eae - 0x1000:0x1eb1 - 0x1000], bytes([2, 3, 7]))
        # the hour alone still goes out: the 0x1eb7 write starts early to reach it
        image = self.image({"box_report_hour": 7})
        self.assertEqual(image[0x1eb0 - 0x1000], 7)

    def test_the_report_hour_is_a_schedule_and_stripped_with_it(self):
        image = self.image({"box_report_hour": 7, "days_before_full": 200}, scheduled_callin=False)
        self.assertEqual(image[0x1eb0 - 0x1000], 0)
        self.assertEqual(image[0x1338 - 0x1000], 200)        # a threshold, not a schedule: kept

    def test_no_schedule_builds_what_it_always_built(self):
        cost, opts = bytes(xnetcost.RECLEN), bytes(xnetopts.RECLEN)
        writes = dict(pi.build_download(cost, opts, "3027700234"))
        self.assertLess(0x1000 + len(writes[0x1000]), 0x10d8)   # trimmed short, as ExpressNet sends it
        self.assertEqual(len(writes[0x1958]), 4)                 # header, code, check: no keypad codes


class ScratchBuildTest(unittest.TestCase):
    """A download from the records alone: every write of CHUNKS with its
    length mark, the splices, the site's number, the dialogue around it."""

    def setUp(self):
        self.costing = bytes((i * 7) & 0xff for i in range(xnetcost.RECLEN))
        self.options = bytes((i * 3) & 0xff for i in range(420))

    def test_every_chunk_comes_out_with_its_mark(self):
        writes = pi.build_download(self.costing, self.options, "5555550123")
        addrs = [a for a, _ in writes]
        # this record's options 331/332 (the coin-box window) are non-zero, so
        # the 0x1eb7 write starts early at the window, as ExpressNet's does
        expected = [0x1eae if a == 0x1eb7 else a for a, _l, _b in pi.CHUNKS]
        self.assertEqual(addrs[:len(pi.CHUNKS)], expected)
        for (addr, length, bound), (a, data) in zip(pi.CHUNKS, writes):
            if a in pi.TRIMMED:
                self.assertNotEqual(data[-1], 0)                  # cut after its last non-zero byte
                self.assertLessEqual(a + len(data), pi.TRIMMED[a])
            elif length is not None:
                self.assertEqual(len(data), length)
            self.assertEqual(data[0], (bound - (a + len(data))) & 0xff)
        # the bulk is the costing record at its offset; the second region at its own
        d = dict(writes)
        self.assertEqual(d[0x10d8][5], self.costing[pi.COST_RECORD_OFFSET + 0xd8 + 5])
        self.assertEqual(d[0x19ba][3], self.costing[4860 + 2])
        # an option splice and a board constant
        self.assertEqual(d[0x1000][0x61 - 0x00 - 0:0x67][:6], self.options[300:306])
        # the reporting number's check byte: seed 0x55 plus the reporting
        # number AND the site's own packed number (measured in the container
        # on three sites, 2026-10-04 -- the site number is in the sum)
        site = sum(bytes.fromhex("0555555a1230"))
        self.assertEqual(d[0x1000][0x67], (0x55 + sum(self.options[300:306]) + site) & 0xff)
        self.assertEqual(d[0x1000][0x68], self.options[307])
        self.assertEqual(d[0x1000][3:5], self.options[75:77])
        self.assertEqual(d[0x20a6][1:7], self.options[310:316])
        # the site's number twice
        self.assertEqual(d[0x1000][0x5b:0x61], bytes.fromhex("0555555a1230"))
        self.assertEqual(d[0x1eae][0x1ecc - 0x1eae:0x1ecc - 0x1eae + 12], b"105555550123")

    def test_the_checksum_word_is_the_byte_sum_without_the_marks(self):
        writes = pi.build_download(self.costing, self.options, "5555550123")
        d = dict(writes)
        word = d[0x1000][1] | (d[0x1000][2] << 8)
        self.assertEqual(word, pi.image_checksum(writes))
        # by hand: the firmware's own sum (FB8E) over the CPU memory the
        # frames fill, decoded independently of this module. This synthetic
        # record has no zero bytes, so every write ends with data where the
        # next one's header sits in protelimage's coordinates -- the case
        # the old header-shadowing sum got wrong.
        from kl5core import protel_firmware_xnet as wire
        from kl5core import kl5net
        image = wire.configuration_from_packets(kl5net.build_script(pi.download_script(writes), "9002"))
        self.assertEqual(word, image[0] | (image[1] << 8))
        self.assertEqual(word, wire.configuration_checksum(image))
        # one content byte up by 3 moves the word by 3
        costing = bytearray(self.costing)
        costing[pi.COST_RECORD_OFFSET + 0x340] = (costing[pi.COST_RECORD_OFFSET + 0x340] + 3) & 0xff   # image 0x1340, inside the 0x1330 write
        d2 = dict(pi.build_download(bytes(costing), self.options, "5555550123"))
        delta = (d2[0x1000][1] | (d2[0x1000][2] << 8)) - word
        self.assertIn(delta & 0xffff, (3, (3 - 256) & 0xffff))

    def test_stamping_restores_a_real_download_from_its_zeroed_checks(self):
        # shaped like the first write of a recorded ExpressNet download, with
        # a fictional reporting number in place of the real one (the rest of
        # the download summed into the word is stood in for by a second
        # write)
        # site 3027700234 (packed 03a277aa2340) sits at image 0x105b; the
        # check 0x9e is 0x55 + the reporting number + that site number, and
        # the word is the seed plus the rest.
        site = bytes.fromhex("03a277aa2340")
        head = (bytes.fromhex("a84b3f1a8060020000") + bytes(0x5b - 9) + site
                + bytes(0x61 - 0x5b - 6) + bytes.fromhex("0215555123409eff"))
        self.assertEqual(len(head), 0x69)
        writes = [(0x1000, head), (0x1d94, bytes.fromhex("ae05b0"))]
        zeroed = [(0x1000, head[:1] + b"\0\0" + head[3:0x67] + b"\0" + head[0x68:]), writes[1]]
        out = pi._stamp_checks(zeroed)
        self.assertEqual(out[0][1][0x67], 0x9e)
        self.assertEqual(out[0][1][1] | (out[0][1][2] << 8),
                         (0xAAAA + sum(head[3:]) + 0x05 + 0xb0) & 0xffff)

    def test_zones_add_their_writes_and_a_bad_number_is_refused(self):
        writes = pi.build_download(self.costing, self.options, "5555550123", {"multi": {215: {200: 1}}})
        self.assertEqual(writes[-1][0], 0x2148)
        with self.assertRaises(pi.ProtelImageError):
            pi.build_download(self.costing, self.options, "123")

    def test_the_script_wraps_the_writes_in_the_dialogue(self):
        writes = pi.build_download(self.costing, self.options, "5555550123")
        script = pi.download_script(writes)
        self.assertEqual([s[0] for s in script[:4]], ["Q", "D", "L", "B"])
        self.assertGreaterEqual(sum(1 for s in script if s[0] == "E"), len(writes))
        self.assertEqual([s[0] for s in script[-8:]], ["G", "S", "S", "D", "R", "P", "S", "O"])
        self.assertEqual(script[1][1], "ffc515")


if __name__ == "__main__":
    unittest.main()


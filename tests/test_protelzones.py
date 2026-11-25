"""The NPA/NXX zone packer, written from ExpressNet's CONVCOST.C and
pinned here with the byte values nine real downloads showed (area codes
and exchanges only -- nothing a board holds). See common/protelzones.py.
"""
import unittest


from kl5core import protelzones as pz     # noqa: E402


class CellTest(unittest.TestCase):
    def test_the_measured_exchanges_land_in_their_cells(self):
        # five downloads, one exchange each: 200, 210, 220, 230, 240 were cells 0, 10, 20, 30, 40
        self.assertEqual([pz.cell(n) for n in (200, 210, 220, 230, 240)], [0, 10, 20, 30, 40])
        self.assertEqual(pz.cell(302), 112)                 # the next hundred, same table
        self.assertEqual(len({pz.cell(n) for n in range(200, 1000)}), 800)   # a bijection

    def test_bits_per_cell_follows_the_entry_count(self):
        self.assertEqual([pz.bits_for(n) for n in (1, 2, 3, 4, 5, 8, 9, 16, 17)], [1, 1, 2, 2, 3, 3, 4, 4, 5])

    def test_npa_bcd_writes_zero_as_a(self):
        self.assertEqual(pz.npa_bcd(215), bytes.fromhex("0215"))
        self.assertEqual(pz.npa_bcd(302), bytes.fromhex("03a2"))


class EncodeTest(unittest.TestCase):
    def test_no_bands_is_the_bare_header_and_no_tail(self):
        h, t = pz.encode({215: {}, 302: {}})
        self.assertEqual(h, bytes.fromhex("021520 00 03a220 00 01112040".replace(" ", "")))
        self.assertEqual(t, [])

    def test_one_exchange_in_band_1(self):
        # the 01:52 download: 200 into band 1 -> header grows a band entry, tail f7 80
        h, t = pz.encode({215: {200: 1}, 302: {}})
        self.assertEqual(h, bytes.fromhex("0215210001 03a22000 01112040".replace(" ", "")))
        self.assertEqual(t, [(0x2148, bytes.fromhex("f780"))])

    def test_more_exchanges_lengthen_the_tail_and_lower_the_lead(self):
        _h, t = pz.encode({215: {200: 1, 210: 1, 220: 1, 230: 1}})
        self.assertEqual(t, [(0x2148, bytes.fromhex("f480200802"))])

    def test_a_second_band_doubles_the_cells_and_a_second_npa_follows(self):
        # the 03:02 download: 240 into band 2, and 302's 210 into band 1
        h, t = pz.encode({215: {200: 1, 210: 1, 220: 1, 230: 1, 240: 2}, 302: {210: 1}})
        self.assertEqual(h, bytes.fromhex("021542000102 03a2210001 01112040".replace(" ", "")))
        self.assertEqual(t, [(0x2148, bytes.fromhex("ed4000040000400004000080")),
                             (0x2211, bytes.fromhex("2d20"))])

    def test_a_bandless_npa_still_owns_its_cells(self):
        # The home area code goes first with no exchanges, then a free
        # code. The firmware finds an NPA's cells by adding up the blocks
        # before it, so the free code's cells start 100 bytes in; packed
        # at 0x2148 instead, the home code read them and every local call
        # errored (2026-11-21, DD8822 emulator).
        h, t = pz.encode({763: {}, 888: {nxx: 5 for nxx in range(200, 1000)}})
        self.assertEqual(h[:4], bytes.fromhex("07632000"))
        self.assertEqual(len(t), 1)
        addr, data = t[0]
        self.assertEqual(addr, 0x2148 + 101)
        self.assertEqual(data[1:], b"\xff" * 99)

    def test_a_second_exchange_in_the_second_npa(self):
        # the 03:26 check: 302's 300 is cell 100, twelve bytes in; the lead moves with the span
        _h, t = pz.encode({215: {200: 1, 210: 1, 220: 1, 230: 1, 240: 2}, 302: {210: 1, 300: 1}})
        self.assertEqual(t[1], (0x2211, bytes.fromhex("2220" + "00" * 10 + "08")))

    def test_a_cell_is_the_index_in_the_entry_list_not_the_band(self):
        # band 5 alone: entries [0, 5], one bit per cell, the exchange is cell value 1
        h, t = pz.encode({215: {200: 5}})
        self.assertEqual(h[:5], bytes.fromhex("0215210005"))
        self.assertEqual(t, [(0x2148, bytes.fromhex("f780"))])

    def test_bands_above_7_match_expressnet(self):
        # ExpressNet in the container, 2026-10-04: NPA 215 with these six
        # exchanges downloaded this header; the whole download matched.
        h, t = pz.encode({215: {200: 8, 210: 9, 220: 15, 230: 40, 240: 79, 250: 3}})
        self.assertEqual(h, bytes.fromhex("02156600030809" "0f284f" "01112040"))
        h2, _ = pz.encode({215: {200: 8, 210: 9}})
        self.assertEqual(h2, bytes.fromhex("0215420008090" "1112040"))

    def test_refusals(self):
        with self.assertRaises(pz.ProtelZoneError):
            pz.encode({215: {200: 80}})                    # past the grid's 0-79
        with self.assertRaises(pz.ProtelZoneError):
            pz.encode({215: {199: 1}})
        with self.assertRaises(pz.ProtelZoneError):
            pz.encode({100: {}})
        # 32 bits over all NPAs: eleven NPAs at 3 bits each is 33
        with self.assertRaises(pz.ProtelZoneError):
            pz.encode({npa: {200: 1, 201: 2, 202: 3, 203: 4} for npa in range(201, 212)})

    def test_normalize_takes_json_shaped_input(self):
        self.assertEqual(pz.normalize({"215": {"200": "1", "210": 0}}), {"multi": {215: {200: 1}}, "single": {}})
        self.assertEqual(pz.normalize({"multi": {"215": {}}, "single": {"412": "65", "212": 64}}),
                         {"multi": {215: {}}, "single": {412: 65}})           # 64 is the default, not a listing


class SingleBandTest(unittest.TestCase):
    """The single-band area-code table, from the decompile (not yet on the wire)."""

    def test_old_format_codes_only(self):
        self.assertEqual([pz.single_cell(n) for n in (200, 210, 219, 300, 919)], [0, 10, 19, 101, 7 * 101 + 19])
        for bad in (267, 484, 199, 1000):
            with self.assertRaises(pz.ProtelZoneError):
                pz.single_cell(bad)
        with self.assertRaises(pz.ProtelZoneError):
            pz.normalize({"single": {412: 72}})              # the band the code never lists
        for bad in (1, 63, 80):
            with self.assertRaises(pz.ProtelZoneError):
                pz.normalize({"single": {412: bad}})

    def test_the_measured_single_band_download(self):
        # 04:05: 212 and 410 into band 66 on ExpressNet's screen; the download's third block
        # was `b0 08 00 .. 00 08` (27 bytes) at 0x2275, lead on cell byte 0, cells 12 and 212
        _h, t = pz.encode({"multi": {215: {200: 1, 210: 1, 220: 1, 230: 1, 240: 2}, 302: {210: 1, 300: 1}},
                           "single": {212: 66, 410: 66}})
        self.assertEqual(_h[-5:], bytes.fromhex("0111214042"))
        self.assertEqual(t[2], (0x2275, bytes.fromhex("b008") + bytes(24) + bytes.fromhex("08")))

    def test_the_pseudo_npa_closes_the_header_and_packs_like_an_npa(self):
        # 412 in band 65, 212 in band 66: entries 40 41 42 (64, 65, 66), two bits, cells 212 and 412
        h, t = pz.encode({"multi": {215: {}}, "single": {412: 65, 212: 66}})
        self.assertEqual(h, bytes.fromhex("02152000 0111 42 404142".replace(" ", "")))
        # 212: cell 12 -> bits 24-25 = 10b (entry 2) -> byte 3 = 0x80; 412: cell 214 -> bits 428-429 = 01b -> byte 53 = 0x04
        # 215 has no exchanges but still owns its 100 bytes, so the table's
        # block comes second: its lead sits on its first cell byte
        self.assertEqual(len(t), 1)
        addr, data = t[0]
        self.assertEqual(addr, 0x2148 + 101)
        self.assertEqual(data[0], (0xf8 - (101 + 54)) & 0xff)
        self.assertEqual(data[1:], bytes(2) + b"\x80" + bytes(49) + b"\x04")


if __name__ == "__main__":
    unittest.main()

"""ExpressNet's call costing record, the parts the console edits.

The band layout, the two band tables, the restricted-number and
speed-dial slots were measured by scripting the program's screens and
diffing the file (2026-09-30). These pin them against the record the
program ships (DEFAULT COIN) and against bytes the program itself wrote.
"""
import unittest
from pathlib import Path


from kl5core import xnetcost  # noqa: E402

VENDOR = Path(__file__).resolve().parents[2] / "xnet" / "vendor" / "XNET-1.55"
CALLCOST = VENDOR / "PARAM" / "CALLCOST.DAT"
RL = xnetcost.RECLEN


def vendor_record(n=2):
    data = CALLCOST.read_bytes()
    return data[RL * n:RL * (n + 1)]       # 1 = DEFAULT B1, 2 = DEFAULT COIN, 3 = PHOENIX_602


class BandCodecTest(unittest.TestCase):
    def test_the_shipped_band_zero_decodes_as_the_form_shows_it(self):
        # DEFAULT COIN band 0: 0.25 for 255, overtime 0.00 every 1, keypad 8, intra, card enable 0111..., SAC 000
        d = xnetcost.decode_band(bytes.fromhex("0105ff000f7fff00"))
        self.assertEqual(d, {"fraud_detect": 0, "discount_table": 0, "overtime_time": 1, "initial_rate": 25,
                             "initial_time": 255, "overtime_rate": 0, "keypad": 8, "lata": "intra", "route": 0,
                             "card_enable": "0111111111111111", "sac": "000"})

    def test_each_field_lands_where_the_program_put_it(self):
        raw = bytes.fromhex("0105ff000f7fff00")
        self.assertEqual(xnetcost.encode_band(raw, {"initial_rate": 35})[1], 7)             # nickels
        self.assertEqual(xnetcost.encode_band(raw, {"initial_time": 123})[2], 123)
        self.assertEqual(xnetcost.encode_band(raw, {"overtime_rate": 15})[3], 3)
        self.assertEqual(xnetcost.encode_band(raw, {"overtime_time": 12})[0], 0x0c)
        self.assertEqual(xnetcost.encode_band(raw, {"fraud_detect": 1})[0], 0x81)
        self.assertEqual(xnetcost.encode_band(raw, {"discount_table": 3})[0], 0x31)
        self.assertEqual(xnetcost.encode_band(raw, {"keypad": 3})[4], 0x0a)                  # keypad - 1, intra kept
        self.assertEqual(xnetcost.encode_band(raw, {"lata": "inter"})[4], 0x07)
        self.assertEqual(xnetcost.encode_band(raw, {"route": 5})[4], 0x5f)
        self.assertEqual(xnetcost.encode_band(raw, {"card_enable": "1010101010101010"})[5:7], b"\xaa\xaa")
        for sac, byte in (("010", 0x01), ("020", 0x02), ("100", 0x10), ("300", 0x30), ("700", 0x70),
                          ("001", 0x80), ("003", 0x88), ("013", 0x89)):
            out = xnetcost.encode_band(raw, {"sac": sac})
            self.assertEqual(out[7], byte, sac)
            self.assertEqual(xnetcost.decode_band(out)["sac"], sac)

    def test_bad_band_values_are_refused(self):
        raw = bytes(8)
        for bad in ({"initial_rate": 26}, {"initial_rate": 1280}, {"keypad": 0}, {"keypad": 9}, {"lata": "x"},
                    {"card_enable": "1"}, {"sac": "800"}, {"sac": "04"}, {"overtime_time": 16}, {"route": "r"}):
            with self.assertRaises(xnetcost.XnetCostError, msg=bad):
                xnetcost.encode_band(raw, bad)

    def test_free_and_coin_presets(self):
        self.assertEqual(xnetcost.free_band(), {"initial_rate": 0, "overtime_rate": 0, "initial_time": 255, "overtime_time": 1})
        self.assertEqual(xnetcost.coin_band(25)["initial_rate"], 25)




if __name__ == "__main__":
    unittest.main()

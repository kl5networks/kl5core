"""Costing templates: a rate plan loaded onto a profile in one go. The
SE-PA one is the owner's instruction card; these pin that it is sound --
every area code old-format where it must be, the bands valid, the whole
table within the board's 32 bits -- and that loading it writes the bands
and stores the zones."""
import unittest



from kl5core import costtemplates as ct, protelzones as pz, xnetcost     # noqa: E402


class TemplateTest(unittest.TestCase):
    def test_se_pa_is_the_card(self):
        t = ct.get("se-pa")
        self.assertEqual(t["bands"][0], {"initial_rate": 25, "initial_time": 3, "overtime_rate": 5, "overtime_time": 2})
        rates = [t["bands"][ct.TIER_BANDS[b]]["initial_rate"] for b in (1, 2, 3, 4)]
        self.assertEqual(rates, sorted(rates))                      # farther is dearer
        self.assertEqual(t["bands"][ct.DEFAULT_NPA_BAND], t["bands"][0])   # an unlisted code is local
        self.assertEqual(t["bands"][ct.FREE_BAND]["initial_rate"], 0)

    def test_every_single_band_code_is_old_format_and_listed_once(self):
        codes = ct.REGIONAL + ct.NEARBY + ct.FAR + ct.FARTHEST
        self.assertEqual(len(codes), len(set(codes)))
        for c in codes:
            pz.single_cell(c)                                        # raises on a new-format code
        self.assertNotIn(215, codes)                                 # local stays band 0 (unlisted)

    def test_it_packs_within_the_board(self):
        rec, zones = ct.apply(bytes(xnetcost.RECLEN), "se-pa")
        header, chunks = pz.encode(zones)
        self.assertLessEqual(len(ct.LOCAL_SLOTS) + len(ct.TOLL_FREE_SLOTS), 8)
        self.assertTrue(chunks)
        bands = {b["band"]: b for b in xnetcost.read(rec)["bands"]}
        self.assertEqual((bands[0]["initial_rate"], bands[0]["initial_time"]), (25, 3))
        self.assertEqual(bands[82]["initial_rate"], 50)              # 411, "charges may apply"
        self.assertEqual(xnetcost.read(rec)["desc"], "SE PA 25C LOCAL")
        with self.assertRaises(ct.CostTemplateError):
            ct.get("nope")




if __name__ == "__main__":
    unittest.main()

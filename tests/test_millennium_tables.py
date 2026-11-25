"""The Millennium's RATE and call-screening tables, and which a phone takes.

The fixtures are mm_manager's own shipped tables (tests/fixtures/mm_manager,
MIT): what a phone receives when it has no table of its own, and so what
every edit starts from. The first thing asserted about each is that
parsing and rendering it changes NOTHING -- a byte nobody edited that comes
out different is a phone running a table nobody chose.
"""
import os
import sys
import unittest


from kl5core import millennium as mm             # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "mm_manager")


def fixture(table_id):
    with open(os.path.join(FIXTURES, mm.table_file(table_id)), "rb") as f:
        return f.read()


class FamilyTest(unittest.TestCase):
    def test_the_bench_phone_is_mtr17(self):
        """Terminal type 13 is what this Millennium reported (06CAA17). It
        takes the 20-entry card table and the 60-entry universal list; the
        wrong pair is a table written that the phone never asks for."""
        self.assertEqual(mm.family_for_type(13), "mtr1.7")
        self.assertEqual(mm.FAMILY_TABLES["mtr1.7"],
                         {"cards": 0x16, "screening": 0x18, "rates": 0x49,
                          "display": 0x1d, "speed": 0x37})

    def test_the_families_disagree_where_upstream_does(self):
        self.assertEqual(mm.family_for_type(27), "mtr1.9")
        self.assertEqual(mm.FAMILY_TABLES["mtr1.9"]["screening"], 0x5c)
        self.assertEqual(mm.family_for_type(40), "mtr2")
        self.assertEqual(mm.FAMILY_TABLES["mtr2"]["cards"], 0x86)

    def test_an_unmapped_type_is_not_guessed(self):
        for t in (0, 4, 41, 61, None, "x"):
            self.assertEqual(mm.family_for_type(t), "", t)

    def test_file_names_are_the_managers(self):
        self.assertEqual(mm.table_file(0x49), "mm_table_49.bin")
        self.assertEqual(mm.table_file(0x16), "mm_table_16.bin")


class RateTableTest(unittest.TestCase):
    def setUp(self):
        self.raw = fixture(0x49)
        self.table = mm.parse_rate_table(self.raw)

    def test_round_trip_is_exact(self):
        self.assertEqual(mm.render_rate_table(self.table), self.raw)

    def test_what_upstream_charges_for_a_local_call(self):
        first = self.table["entries"][0]
        self.assertEqual(first["type"], "fixed_charge_local")
        self.assertIsNone(first["initial_period"])       # unlimited
        self.assertEqual(first["initial_charge"], 50)

    def test_unlimited_is_written_as_the_phone_already_has_it(self):
        """Only bit 15 is read, but upstream writes 0xFFFF. Writing 0x8000
        would change every unlimited entry in a table somebody edited in one
        place, and a diff that noisy hides the edit."""
        self.assertEqual(self.raw[40:42], b"\xff\xff")

    def test_types_upstream_does_not_name_survive(self):
        """Entry 6 is type 0x0a in upstream's own table. It is kept as it
        is rather than refused or 'corrected'."""
        self.assertEqual(self.table["entries"][6]["type"], "0x0a")

    def test_editing_one_entry_changes_only_that_entry(self):
        self.table["entries"][0]["initial_charge"] = 25
        out = mm.render_rate_table(self.table)
        changed = [i for i in range(len(out)) if out[i] != self.raw[i]]
        self.assertEqual(changed, [42])          # 39 + 0*9 + 3, low byte
        self.assertEqual(mm.parse_rate_table(out)["entries"][0]["initial_charge"], 25)

    def test_an_unused_entry_is_all_zeros(self):
        e = self.table["entries"][0]
        e["used"] = False
        out = mm.render_rate_table(self.table)
        self.assertEqual(out[39:48], bytes(9))

    def test_out_of_range_is_refused_at_save(self):
        self.table["entries"][0]["initial_charge"] = 70000
        with self.assertRaises(mm.RateTableError):
            mm.render_rate_table(self.table)
        self.table["entries"][0]["initial_charge"] = 25
        self.table["entries"][0]["initial_period"] = 40000
        with self.assertRaises(mm.RateTableError):
            mm.render_rate_table(self.table)

    def test_the_wrong_size_is_refused(self):
        with self.assertRaises(mm.RateTableError):
            mm.parse_rate_table(self.raw[:-1])


class ScreenNumberTest(unittest.TestCase):
    def test_zero_is_written_as_a(self):
        """A zero nibble ends the number, so 0 is 0xA. Read the other way,
        1-800 becomes `18` and screens every call that starts with 18."""
        self.assertEqual(mm.pack_screen_number("1800", 8)[:2], b"\x18\xaa")
        self.assertEqual(mm.unpack_screen_number(b"\x18\xaa\x00"), "1800")

    def test_wildcards_and_links_round_trip(self):
        for n in ("1FFF976", "18005551212B22", "555", "911"):
            self.assertEqual(mm.unpack_screen_number(mm.pack_screen_number(n, 8)), n)

    def test_the_terminator_always_fits(self):
        with self.assertRaises(mm.ScreenTableError):
            mm.pack_screen_number("1" * 16, 8)
        mm.pack_screen_number("1" * 15, 8)

    def test_anything_else_is_refused(self):
        for bad in ("555-1212", "A", "#", "12x"):
            with self.assertRaises(mm.ScreenTableError, msg=bad):
                mm.pack_screen_number(bad, 8)


class ScreenTableTest(unittest.TestCase):
    def test_both_layouts_round_trip(self):
        for tid, count in ((0x18, 60), (0x5c, 180)):
            raw = fixture(tid)
            table = mm.parse_screen_table(raw, tid)
            self.assertEqual(len(table["entries"]), count)
            self.assertEqual(mm.render_screen_table(table), raw, hex(tid))

    def test_upstream_screens_911_free(self):
        t = mm.parse_screen_table(fixture(0x18), 0x18)
        e = t["entries"][0]
        self.assertEqual(e["number"], "911")
        self.assertIn("free", mm.describe_screen_flags(e["flags"]))
        self.assertEqual(mm.call_type_name(e["call_type"]), "local")
        self.assertIsNone(e["rate_ref"])

    def test_411_points_at_a_rate(self):
        t = mm.parse_screen_table(fixture(0x18), 0x18)
        e = [x for x in t["entries"] if x["number"] == "411"][0]
        self.assertEqual(e["rate_ref"], 12)
        self.assertNotIn("free", mm.describe_screen_flags(e["flags"]))

    def test_making_an_exchange_free(self):
        """Making every 555 number free of charge.
        One prefix entry, in a slot upstream leaves empty, and nothing else
        in the table moves."""
        raw = fixture(0x18)
        t = mm.parse_screen_table(raw, 0x18)
        slot = [e for e in t["entries"] if not e["used"]][0]
        slot.update(mm.free_number("555"))
        out = mm.render_screen_table(t)
        size = 12
        i = slot["index"]
        self.assertEqual(out[:i * size], raw[:i * size])
        self.assertEqual(out[(i + 1) * size:], raw[(i + 1) * size:])
        back = mm.parse_screen_table(out, 0x18)["entries"][i]
        self.assertEqual(back["number"], "555")
        self.assertIn("free", mm.describe_screen_flags(back["flags"]))
        self.assertEqual(mm.call_type_name(back["call_type"]), "local")

    def test_the_class_bytes_ride_through(self):
        raw = fixture(0x5c)
        t = mm.parse_screen_table(raw, 0x5c)
        self.assertTrue(any(e["tail"] != bytes(4) for e in t["entries"] if e["used"]))

    def test_a_partial_entry_is_refused(self):
        with self.assertRaises(mm.ScreenTableError):
            mm.parse_screen_table(fixture(0x18)[:-1], 0x18)
        with self.assertRaises(mm.ScreenTableError):
            mm.parse_screen_table(fixture(0x18), 0x49)


class CardFixtureTest(unittest.TestCase):
    """The existing CARD renderer against the tables a phone really has."""

    def test_mtr1_used_entries_survive(self):
        raw = fixture(0x16)
        cards = mm.parse_card_table(raw, mtr1=True)
        again = mm.parse_card_table(mm.render_card_table(cards, mtr1=True), mtr1=True)
        self.assertEqual(cards, again)

    def test_mtr2_used_entries_survive(self):
        raw = fixture(0x86)
        cards = mm.parse_card_table(raw)
        again = mm.parse_card_table(mm.render_card_table(cards))
        self.assertEqual(cards, again)


class CallInTableTest(unittest.TestCase):
    """Table 0x23, the call-in schedule: the layout of mm_manager.h's
    dlog_mt_call_in_params_t at the pinned commit, as upstream fills it."""

    def test_upstreams_schedule_round_trips(self):
        # What generate_call_in_parameters sends a phone downloaded at
        # 2026-10-14 05:06:46: 12 h interval, 15 min retry, 30 CDRs.
        data = mm.render_call_in_table((2026, 10, 14, 5, 6, 46), expires=(2027, 10, 14, 2, 0, 0))
        self.assertEqual(data.hex(), "7e0a0e05062e000c000f001e7f0a0e0200000000")
        self.assertEqual(mm.parse_call_in_table(data)["retry"], (15, 0))

    def test_the_beta_schedule_is_out_of_reach(self):
        s = mm.parse_call_in_table(mm.NO_SCHEDULED_CALLIN)
        self.assertEqual(s["start"], (2099, 12, 31, 0, 0, 0))
        self.assertEqual(s["cdr_threshold"], 50)
        self.assertEqual(len(mm.NO_SCHEDULED_CALLIN), mm.CALL_IN_LEN)

    def test_it_refuses_what_the_table_cannot_hold(self):
        with self.assertRaises(mm.CallInTableError):
            mm.render_call_in_table((2026, 13, 1, 0, 0, 0))
        with self.assertRaises(mm.CallInTableError):
            mm.render_call_in_table((2026, 1, 1, 0, 0, 0), cdr_threshold=51)
        with self.assertRaises(mm.CallInTableError):
            mm.parse_call_in_table(bytes(19))


if __name__ == "__main__":
    unittest.main()

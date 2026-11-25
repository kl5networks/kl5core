"""Rendering the Millennium's CARD table.

This is the table that decides whether the phone will look at a KL5 card
at all — a number outside it produces no authorisation request, nothing in
`TAUTH`, and no way to tell from the manager that anybody swiped. So the
tests here are mostly about the wire bytes being exactly right, including
the two flag bits that read backwards and the BCD nibble order.

The layout constants are `offsetof` output from a probe compiled against
`mm_card.h`, not arithmetic: the Millennium's headers carry at least one
stale size comment (the MTR headers).
"""
import os
import sys
import unittest


from kl5core import millennium                   # noqa: E402


class Bcd(unittest.TestCase):
    def test_high_nibble_first(self):
        """Backwards, the terminal reads a different number than the one
        printed on the card, and it presents as the card being rejected.
        The same trap cost a session on the statistics harness."""
        self.assertEqual(millennium.pack_bcd("410414", 3), b"\x41\x04\x14")
        self.assertEqual(millennium.unpack_bcd(b"\x41\x04\x14"), "410414")

    def test_round_trip(self):
        for digits in ("000000", "999999", "123456", "500009"):
            packed = millennium.pack_bcd(digits, 3)
            self.assertEqual(millennium.unpack_bcd(packed), digits)

    def test_wrong_length_is_refused(self):
        with self.assertRaises(millennium.CardTableError):
            millennium.pack_bcd("41041", 3)          # five digits
        with self.assertRaises(millennium.CardTableError):
            millennium.pack_bcd("4104140", 3)        # seven
        with self.assertRaises(millennium.CardTableError):
            millennium.pack_bcd("41041x", 3)


class Flags(unittest.TestCase):
    def test_the_kl5_defaults(self):
        """Exactly two bits: ask the manager, and do not Luhn-check.

        Our card numbers are whatever is printed on the stock we already
        have, and only about one in ten random numbers passes Luhn.
        """
        f = millennium.vfy_flags()
        self.assertEqual(f, millennium.VF_NCCVAL)
        self.assertEqual(millennium.describe_flags(f), ["nccval"])

    def test_calling_card_is_inverted(self):
        """The header: "Indicates whether this type of card is *NOT* a
        calling card. 0 Yes - Calling Card, 1 No." mm_card.c prints it
        XORed for the same reason."""
        self.assertFalse(millennium.vfy_flags(calling_card=True)
                         & millennium.VF_NOT_CALLING_CARD)
        self.assertTrue(millennium.vfy_flags(calling_card=False)
                        & millennium.VF_NOT_CALLING_CARD)

    def test_accs_routing_is_inverted(self):
        """0 routes to the Millennium Manager, 1 to ACCS. Ours must be 0,
        which means the bit stays CLEAR — set it and the phone stops
        asking us anything."""
        self.assertFalse(millennium.vfy_flags(route_to_accs=False)
                         & millennium.VF_ACCS_ROUTING)
        self.assertTrue(millennium.vfy_flags(route_to_accs=True)
                        & millennium.VF_ACCS_ROUTING)

    def test_mod10_is_off_unless_asked(self):
        self.assertFalse(millennium.vfy_flags() & millennium.VF_MOD10)
        self.assertTrue(millennium.vfy_flags(mod10=True) & millennium.VF_MOD10)

    def test_pin_prompt_is_off(self):
        """The gift-card stock has PINs on some cards; we do not use them,
        and a phone that prompts for one is a phone nobody can use."""
        self.assertFalse(millennium.vfy_flags() & millennium.VF_PROMPT_FOR_PIN)


class Entries(unittest.TestCase):
    def test_a_card_renders_36_bytes_in_the_documented_order(self):
        c = millennium.card("gift", "410414", ref_num=1, carrier_ref=7)
        raw = millennium._entry_bytes(c)
        self.assertEqual(len(raw), millennium.CARD_ENTRY_LEN)
        self.assertEqual(raw[0:3], b"\x41\x04\x14")          # pan_start
        self.assertEqual(raw[3:6], b"\x41\x04\x14")          # pan_end
        self.assertEqual(raw[6], millennium.STANDARDS.index("aba"))
        self.assertEqual(raw[7], millennium.VF_NCCVAL)
        self.assertEqual(raw[31], 1)                         # ref_num
        self.assertEqual(raw[32], 7)                         # carrier_ref

    def test_mtr1_entries_are_three_bytes_shorter(self):
        """MTR 1.x drops control_info, bank_info and lang_code."""
        c = millennium.card("gift", "410414")
        self.assertEqual(len(millennium._entry_bytes(c, mtr1=True)),
                         millennium.CARD_ENTRY_LEN_MTR1)

    def test_a_range_spans_start_to_end(self):
        c = millennium.card("range", "500000", "500009")
        raw = millennium._entry_bytes(c)
        self.assertEqual(raw[0:3], b"\x50\x00\x00")
        self.assertEqual(raw[3:6], b"\x50\x00\x09")

    def test_one_pan_means_start_equals_end(self):
        c = millennium.card("single", "410414")
        self.assertEqual(c["pan_start"], c["pan_end"])

    def test_an_unknown_standard_is_refused(self):
        with self.assertRaises(millennium.CardTableError):
            millennium.card("x", "410414", standard="visa")
        with self.assertRaises(millennium.CardTableError):
            millennium.card("x", "410414", standard="undefined")

    def test_ref_num_range(self):
        millennium.card("x", "410414", ref_num=62)
        with self.assertRaises(millennium.CardTableError):
            millennium.card("x", "410414", ref_num=63)


class Table(unittest.TestCase):
    def test_the_table_is_padded_to_full_size(self):
        """A terminal reads a fixed-size table; a short file would be read
        as whatever followed it."""
        raw = millennium.render_card_table([millennium.card("one", "410414")])
        self.assertEqual(len(raw), millennium.TABLE_LEN)
        self.assertEqual(len(raw), 1152)

    def test_padding_entries_read_as_unused(self):
        """mm_card.c decides by standard_cd == 0, so every padded entry
        must carry a zero there."""
        raw = millennium.render_card_table([millennium.card("one", "410414")])
        for i in range(1, millennium.CARD_MAX):
            off = i * millennium.CARD_ENTRY_LEN
            self.assertEqual(raw[off + 6], 0, "entry %d" % i)

    def test_mtr1_table_is_660_bytes(self):
        raw = millennium.render_card_table([millennium.card("one", "410414")],
                                           mtr1=True)
        self.assertEqual(len(raw), millennium.TABLE_LEN_MTR1)
        self.assertEqual(len(raw), 660)

    def test_too_many_cards_is_refused_per_mtr(self):
        many = [millennium.card("c%d" % i, "41041%d" % (i % 10), ref_num=0)
                for i in range(21)]
        millennium.render_card_table(many)                  # 21 fits in 32
        with self.assertRaises(millennium.CardTableError):
            millennium.render_card_table(many, mtr1=True)   # but not in 20

    def test_a_repeated_nonzero_ref_num_is_refused(self):
        """The terminal quotes it back as card_ref_num, so it is how the
        manager tells which entry matched."""
        cards = [millennium.card("a", "410414", ref_num=3),
                 millennium.card("b", "510414", ref_num=3)]
        with self.assertRaises(millennium.CardTableError):
            millennium.render_card_table(cards)

    def test_repeated_zero_ref_num_is_allowed(self):
        """Not a style choice: every one of the twelve entries in
        mm_manager's own shipped table is 0, so a uniqueness rule taken
        from the header comment would reject Nortel's table.
        """
        cards = [millennium.card("a", "410414", ref_num=0),
                 millennium.card("b", "510414", ref_num=0)]
        self.assertEqual(len(millennium.render_card_table(cards)),
                         millennium.TABLE_LEN)

    def test_parse_skips_unused_entries(self):
        raw = millennium.render_card_table([millennium.card("one", "410414")])
        self.assertEqual(len(millennium.parse_card_table(raw)), 1)

    def test_parse_render_round_trip_keeps_the_used_entries(self):
        cards = [millennium.card("gift", "410414", ref_num=1),
                 millennium.card("calling", "500000", "500009", ref_num=2)]
        raw = millennium.render_card_table(cards)
        again = millennium.render_card_table(millennium.parse_card_table(raw))
        self.assertEqual(raw, again)

    def test_service_codes_survive_a_round_trip(self):
        """The documented way to build a table is to start from the one
        mm_manager ships and change what you need. A renderer that zeroed
        this union would strip service codes out of entries it was not
        even touching, and the phone would work until it met a card that
        needed one.
        """
        cards = [millennium.card("x", "410414")]
        raw = bytearray(millennium.render_card_table(cards))
        raw[11:31] = bytes(range(20))            # as if the file had them
        parsed = millennium.parse_card_table(bytes(raw))
        self.assertEqual(parsed[0]["svc_code"], bytes(range(20)))
        self.assertEqual(millennium.render_card_table(parsed)[11:31],
                         bytes(range(20)))

    def test_a_wrong_sized_table_is_refused(self):
        with self.assertRaises(millennium.CardTableError):
            millennium.parse_card_table(b"\x00" * 100)


class Layout(unittest.TestCase):
    def test_the_sizes_are_the_compilers(self):
        """From a probe built against mm_card.h, not counted by hand."""
        self.assertEqual(millennium.CARD_ENTRY_LEN, 36)
        self.assertEqual(millennium.CARD_ENTRY_LEN_MTR1, 33)
        self.assertEqual(millennium.CARD_MAX, 32)
        self.assertEqual(millennium.CARD_MAX_MTR1, 20)
        self.assertEqual(millennium.TABLE_LEN, 36 * 32)
        self.assertEqual(millennium.TABLE_LEN_MTR1, 33 * 20)

    def test_standard_values_are_indices(self):
        """std_card_type is a plain C enum, so the position is the value."""
        self.assertEqual(millennium.STANDARDS.index("undefined"), 0)
        self.assertEqual(millennium.STANDARDS.index("mod10"), 1)
        self.assertEqual(millennium.STANDARDS.index("aba"), 3)
        self.assertEqual(millennium.STANDARDS.index("smcard"), 10)

    def test_the_table_filenames_are_the_ones_mm_manager_reads(self):
        self.assertEqual(millennium.TABLE_FILE, "mm_table_86.bin")
        self.assertEqual(millennium.TABLE_FILE_MTR1, "mm_table_16.bin")


if __name__ == "__main__":
    unittest.main()

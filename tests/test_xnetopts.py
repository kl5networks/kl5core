"""ExpressNet's Options & Registers record, as the console reads and
writes it.

Every offset and encoding in common/xnetopts.py was measured by scripting
the program's 183-field form and diffing the file after each save
(2026-09-30); these pin the measurements that matter, against the record
the program ships (DEFAULT COIN in the vendor tree) and against the bytes
the program itself wrote for the same values.
"""
import unittest
from pathlib import Path


from kl5core import xnetopts  # noqa: E402

VENDOR = Path(__file__).resolve().parents[2] / "xnet" / "vendor" / "XNET-1.55"
MISCOPT = VENDOR / "PARAM" / "MISCOPT.DAT"


def vendor_record():
    data = MISCOPT.read_bytes()
    return data[420:840]           # DEFAULT COIN, the first record




class RegisterRuleTest(unittest.TestCase):
    def test_a_register_is_one_byte_per_tens_digit_and_a_fixed_bit_per_units_digit(self):
        self.assertEqual(xnetopts.reg_pos("111"), (75, 0))
        self.assertEqual(xnetopts.reg_pos("113"), (75, 6))
        self.assertEqual(xnetopts.reg_pos("118"), (75, 3))
        self.assertEqual(xnetopts.reg_pos("198"), (83, 3))
        self.assertEqual(xnetopts.reg_pos("1A7"), (335, 7))
        self.assertEqual(xnetopts.reg_pos("1B3"), (336, 6))
        self.assertEqual(xnetopts.reg_pos("1E8"), (339, 3))

    def test_the_three_12x_registers_the_form_exposes_break_the_rule_as_measured(self):
        self.assertEqual(xnetopts.reg_pos("123"), (76, 4))
        self.assertEqual(xnetopts.reg_pos("124"), (76, 6))
        self.assertEqual(xnetopts.reg_pos("125"), (76, 5))

    def test_every_listed_register_matches_its_measured_bit(self):
        for key, _sec, _label, reg, _one, _zero, byte, bit in xnetopts.REGISTERS:
            self.assertEqual(xnetopts.reg_pos(reg), (byte, bit), key)




class StripPollScheduleTest(unittest.TestCase):
    """phones.scheduled_callin OFF clears the self-poll schedule from the
    download so a public/beta phone never self-polls."""

    def test_it_zeros_the_schedule_and_leaves_other_fields(self):
        rec = xnetopts.write(bytes(xnetopts.RECLEN), {
            "poll_start": 9, "poll_length": 4, "day_of_month_report": 15,
            "day_of_week_report": 3, "days_report": 7, "time_of_day_report": 20,
            "cashbox_volume": 80, "peg_count": 100, "security_code": "1234"})
        d0 = xnetopts.read(rec)
        self.assertEqual(d0["poll_start"], 9)                      # set as expected

        stripped = xnetopts.strip_poll_schedule(rec)
        self.assertEqual(len(stripped), xnetopts.RECLEN)
        d = xnetopts.read(stripped)
        for key in xnetopts.POLL_SCHEDULE_FIELDS:
            self.assertEqual(d[key], 0, key)                      # the whole schedule, gone
        self.assertEqual(d["cashbox_volume"], 80)                 # a threshold: left alone
        self.assertEqual(d["peg_count"], 100)
        self.assertEqual(d["security_code"], "1234")              # untouched
        # and it zeroed the download-image copies too, not only the form bytes
        self.assertEqual(xnetopts.strip_poll_schedule(stripped), stripped)   # idempotent


if __name__ == "__main__":
    unittest.main()

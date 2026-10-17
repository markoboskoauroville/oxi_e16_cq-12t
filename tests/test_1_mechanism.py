"""
Test 1 of the Four -- THE MECHANISM ALONE.

Every message builder is checked against the worked examples printed in the
Allen & Heath "CQ MIDI Protocol V1.2 Issue 4" document.  If any of these
fail, the bytes we would put on the wire are wrong, independent of any
mapping or I/O.
"""

import unittest

from oxi_cq_bridge import cq_params as P
from oxi_cq_bridge import cq_protocol as cq


def hx(messages):
    return cq.flatten(messages).hex(" ").upper()


class TestProtocolAgainstPDF(unittest.TestCase):
    # ---- Levels (PDF section 2.4 examples) --------------------------------
    def test_level_examples(self):
        self.assertEqual(hx(cq.level_db("Ip1>LR", 0)),
                         "B0 63 40 B0 62 00 B0 06 62 B0 26 00")
        self.assertEqual(hx(cq.level_db("Ip1>LR", -20)),
                         "B0 63 40 B0 62 00 B0 06 2E B0 26 40")
        self.assertEqual(hx(cq.level_db("Ip12>Out2", -5)),
                         "B0 63 41 B0 62 49 B0 06 4E B0 26 40")
        self.assertEqual(hx(cq.level_db("Out5/6", 5)),
                         "B0 63 4F B0 62 05 B0 06 73 B0 26 40")
        self.assertEqual(hx(cq.level_db("FX2>Out1", -10)),
                         "B0 63 46 B0 62 20 B0 06 3E B0 26 00")
        self.assertEqual(hx(cq.level_db("FX1>FX2", -15)),
                         "B0 63 4E B0 62 05 B0 06 36 B0 26 00")
        self.assertEqual(hx(cq.level_db("DCA1", -40)),
                         "B0 63 4F B0 62 20 B0 06 0F B0 26 40")

    def test_level_relative(self):
        self.assertEqual(hx(cq.level_step("Ip1>LR", True)),
                         "B0 63 40 B0 62 00 B0 60 00")
        self.assertEqual(hx(cq.level_step("Ip1>LR", False)),
                         "B0 63 40 B0 62 00 B0 61 00")
        self.assertEqual(hx(cq.level_step("FX3>Out3/4", True)),
                         "B0 63 46 B0 62 2E B0 60 00")

    # ---- Mutes (PDF 2.3) ---------------------------------------------------
    def test_mute_examples(self):
        self.assertEqual(hx(cq.mute("Ip1", True)),
                         "B0 63 00 B0 62 00 B0 06 00 B0 26 01")
        self.assertEqual(hx(cq.mute("MainLR", False)),
                         "B0 63 00 B0 62 44 B0 06 00 B0 26 00")
        self.assertEqual(hx(cq.mute_toggle("Ip3")),
                         "B0 63 00 B0 62 02 B0 60 00")

    # ---- Pan / balance (PDF 2.5) ------------------------------------------
    def test_pan_examples(self):
        self.assertEqual(hx(cq.pan_position("Ip1", -100)),
                         "B0 63 50 B0 62 00 B0 06 00 B0 26 00")
        self.assertEqual(hx(cq.pan_position("Ip1", 0)),
                         "B0 63 50 B0 62 00 B0 06 40 B0 26 00")
        self.assertEqual(hx(cq.pan_position("Ip1", 100)),
                         "B0 63 50 B0 62 00 B0 06 7F B0 26 7F")

    # ---- Get (PDF 2.6) -----------------------------------------------------
    def test_get_examples(self):
        self.assertEqual(hx(cq.get_mute("Ip1")), "B0 63 00 B0 62 00 B0 60 7F")
        self.assertEqual(hx(cq.get_level("Ip1>LR")), "B0 63 40 B0 62 00 B0 60 7F")

    # ---- Scenes (PDF 2.1) --------------------------------------------------
    def test_scene_examples(self):
        self.assertEqual(hx(cq.scene_recall(1)), "B0 00 00 C0 00")
        self.assertEqual(hx(cq.scene_recall(7)), "B0 00 00 C0 06")
        self.assertEqual(hx(cq.scene_recall(64)), "B0 00 00 C0 3F")

    # ---- Soft keys (PDF 2.2) ----------------------------------------------
    def test_softkey_examples(self):
        self.assertEqual(hx(cq.softkey_press(1)), "90 30 7F")
        self.assertEqual(hx(cq.softkey_press(2)), "90 31 7F")
        self.assertEqual(hx(cq.softkey_press(3)), "90 32 7F")
        self.assertEqual(hx(cq.softkey_release(1)), "80 30 00")
        self.assertEqual(hx(cq.softkey_tap(1)), "90 30 7F 80 30 00")


class TestConversions(unittest.TestCase):
    def test_db_taper_exact_points(self):
        # every PDF taper breakpoint must round-trip exactly
        for db, vc, vf in P.LEVEL_TAPER:
            if db is None:
                continue
            self.assertEqual(cq.db_to_vcvf(db), (vc, vf), f"dB={db}")

    def test_pan_taper_exact_points(self):
        for pos, vc, vf in P.PAN_TAPER:
            self.assertEqual(cq.position_to_pan_vcvf(pos), (vc, vf), f"pos={pos}")

    def test_fader_fraction_endpoints(self):
        self.assertEqual(cq.fraction_to_level_vcvf(0.0), (0x00, 0x00))     # -inf
        self.assertEqual(cq.fraction_to_level_vcvf(1.0), (0x7F, 0x40))     # +10 dB
        # monotonic non-decreasing code across the throw
        last = -1
        for i in range(0, 128):
            vc, vf = cq.fraction_to_level_vcvf(i / 127)
            code = vc * 128 + vf
            self.assertGreaterEqual(code, last)
            last = code

    def test_minus_inf(self):
        self.assertEqual(cq.db_to_vcvf(None), (0x00, 0x00))


class TestFraming(unittest.TestCase):
    def test_all_bytes_in_range(self):
        samples = (cq.level_db("Ip8>LR", -13) + cq.mute("DCA4", True)
                   + cq.pan_fraction("Ip5", 0.25) + cq.scene_recall(128)
                   + cq.softkey_tap(9))
        for msg in samples:
            for b in msg:
                self.assertTrue(0 <= b <= 255)
            for b in msg[1:]:
                self.assertTrue(0 <= b <= 127, f"data byte out of range: {msg}")

    def test_describe_roundtrip(self):
        msgs = cq.level_db("Ip1>LR", 0)
        self.assertEqual(cq.describe(msgs),
                         "B0 63 40 | B0 62 00 | B0 06 62 | B0 26 00")


if __name__ == "__main__":
    unittest.main()

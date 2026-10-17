"""
Test 3 of the Four -- THE UGLY EDGE CASES.

Out-of-range values, clamping, bad targets, relative-encoder encodings,
mute release semantics, and partial output failure.
"""

import unittest

from oxi_cq_bridge import cq_protocol as cq
from oxi_cq_bridge.mapping import (MappingEngine, MappingError, decode_relative)
from oxi_cq_bridge.midi_io import (CaptureOutput, MidiEvent, MidiOutput,
                                   TeeOutput)


class TestValueClamping(unittest.TestCase):
    def test_fraction_clamps(self):
        self.assertEqual(cq.fraction_to_level_vcvf(5.0), (0x7F, 0x40))
        self.assertEqual(cq.fraction_to_level_vcvf(-1.0), (0x00, 0x00))
        self.assertEqual(cq.fraction_to_pan_vcvf(2.0), (0x7F, 0x7F))
        self.assertEqual(cq.fraction_to_pan_vcvf(-2.0), (0x00, 0x00))

    def test_db_clamps(self):
        self.assertEqual(cq.db_to_vcvf(1000), cq.db_to_vcvf(10))   # +10 ceiling
        self.assertEqual(cq.db_to_vcvf(-1000), (0x01, 0x40))       # -89 floor

    def test_pan_clamps(self):
        self.assertEqual(cq.position_to_pan_vcvf(999), (0x7F, 0x7F))
        self.assertEqual(cq.position_to_pan_vcvf(-999), (0x00, 0x00))


class TestBadInput(unittest.TestCase):
    def test_scene_range(self):
        with self.assertRaises(ValueError):
            cq.scene_recall(0)
        with self.assertRaises(ValueError):
            cq.scene_recall(129)

    def test_softkey_index(self):
        with self.assertRaises(ValueError):
            cq.softkey_press(0)

    def test_byte_range_guard(self):
        with self.assertRaises(ValueError):
            cq.nrpn_set(200, 0, 0, 0)        # msb out of 0..127

    def test_unknown_targets(self):
        with self.assertRaises(KeyError):
            cq.resolve_level("Nope>LR")
        with self.assertRaises(KeyError):
            cq.resolve_mute("Nope")


class TestRelativeDecode(unittest.TestCase):
    def test_twos_complement(self):
        self.assertEqual(decode_relative(1, "twos_complement"), 1)
        self.assertEqual(decode_relative(3, "twos_complement"), 3)
        self.assertEqual(decode_relative(127, "twos_complement"), -1)
        self.assertEqual(decode_relative(125, "twos_complement"), -3)

    def test_binary_offset(self):
        self.assertEqual(decode_relative(65, "binary_offset"), 1)
        self.assertEqual(decode_relative(63, "binary_offset"), -1)
        self.assertEqual(decode_relative(64, "binary_offset"), 0)

    def test_signed_bit(self):
        self.assertEqual(decode_relative(0x01, "signed_bit"), 1)
        self.assertEqual(decode_relative(0x41, "signed_bit"), -1)


class TestMappingErrors(unittest.TestCase):
    def _doc(self, controls, **extra):
        base = {"schema_version": 1, "name": "t", "input_channel": 1,
                "pages": [{"index": 0, "name": "p", "channel": 1,
                           "controls": controls}]}
        base.update(extra)
        return base

    def test_conflicting_bindings(self):
        doc = self._doc([
            {"slot": 1, "label": "a",
             "turn": {"cc": 16, "action": "level", "target": "Ip1>LR"}},
            {"slot": 2, "label": "b",
             "turn": {"cc": 16, "action": "level", "target": "Ip2>LR"}},
        ])
        with self.assertRaises(MappingError):
            MappingEngine(doc)

    def test_unknown_action(self):
        doc = self._doc([{"slot": 1, "label": "a",
                          "turn": {"cc": 16, "action": "warp", "target": "x"}}])
        with self.assertRaises(MappingError):
            MappingEngine(doc)

    def test_unknown_target(self):
        doc = self._doc([{"slot": 1, "label": "a",
                          "turn": {"cc": 16, "action": "level", "target": "Z>LR"}}])
        with self.assertRaises(MappingError):
            MappingEngine(doc)

    def test_missing_cc_or_note(self):
        doc = self._doc([{"slot": 1, "label": "a",
                          "turn": {"action": "level", "target": "Ip1>LR"}}])
        with self.assertRaises(MappingError):
            MappingEngine(doc)


class TestMuteSemantics(unittest.TestCase):
    def _engine(self, behavior):
        doc = {"schema_version": 1, "name": "t", "input_channel": 1,
               "pages": [{"index": 0, "name": "p", "channel": 1, "controls": [
                   {"slot": 1, "label": "m", "push": {"note": 36, "action": "mute",
                    "target": "Ip1", "behavior": behavior}}]}]}
        return MappingEngine(doc)

    def test_toggle_ignores_release(self):
        eng = self._engine("toggle")
        on = eng.process(MidiEvent("note_on", 1, 36, 127))
        self.assertTrue(on.messages)                         # toggles
        # a toggle mute binds only note_on, so release is simply unmapped
        off = eng.process(MidiEvent("note_off", 1, 36, 0))
        self.assertIsNone(off)                               # release = ignored

    def test_momentary_mutes_on_press_unmutes_on_release(self):
        eng = self._engine("momentary")
        on = eng.process(MidiEvent("note_on", 1, 36, 127))
        self.assertEqual(cq.flatten(on.messages).hex(" ").upper(),
                         cq.flatten(cq.mute("Ip1", True)).hex(" ").upper())
        off = eng.process(MidiEvent("note_off", 1, 36, 0))
        self.assertEqual(cq.flatten(off.messages).hex(" ").upper(),
                         cq.flatten(cq.mute("Ip1", False)).hex(" ").upper())


class TestRelativeLevelMapping(unittest.TestCase):
    def test_multiple_steps(self):
        doc = {"schema_version": 1, "name": "t", "input_channel": 1,
               "relative_mode": "twos_complement",
               "pages": [{"index": 0, "name": "p", "channel": 1, "controls": [
                   {"slot": 1, "label": "f", "turn": {"cc": 16, "mode": "relative",
                    "action": "level", "target": "Ip1>LR"}}]}]}
        eng = MappingEngine(doc)
        d = eng.process(MidiEvent("cc", 1, 16, 3))      # +3 steps
        # 3 increments, each a 3-message NRPN string
        self.assertEqual(len(d.messages), 9)
        d2 = eng.process(MidiEvent("cc", 1, 16, 127))   # -1 step
        self.assertEqual(cq.flatten(d2.messages).hex(" ").upper(),
                         cq.flatten(cq.level_step("Ip1>LR", False)).hex(" ").upper())


class TestTeeOutput(unittest.TestCase):
    def test_partial_failure_still_delivers(self):
        good = CaptureOutput("good")

        class Bad(MidiOutput):
            name = "bad"
            def send_bytes(self, data):
                raise OSError("down")

        tee = TeeOutput([Bad(), good])
        with self.assertRaises(OSError):
            tee.send(cq.level_db("Ip1>LR", 0))
        self.assertEqual(len(good.records), 1)       # good output still got it


class TestCaptureTruncation(unittest.TestCase):
    def test_ring_buffer(self):
        cap = CaptureOutput("c", keep=10)
        for i in range(25):
            cap.send_bytes(bytes([0xB0, 0x00, i & 0x7F]))
        self.assertEqual(len(cap.records), 10)


if __name__ == "__main__":
    unittest.main()

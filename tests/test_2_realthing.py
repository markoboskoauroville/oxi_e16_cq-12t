"""
Test 2 of the Four -- THE REAL THING, ONCE.

Drive the whole pipeline the way it actually runs: the factory mapping, a
simulated E16 feeding the Bridge, a capture sink standing in for the mixer,
and the real HTTP server.  Assert that a realistic session produces exactly
the MIDI the CQ-12T expects.
"""

import json
import unittest
import urllib.request

from oxi_cq_bridge import cq_protocol as cq
from oxi_cq_bridge.bridge import Bridge, make_input, make_output
from oxi_cq_bridge.mapping import MappingEngine
from oxi_cq_bridge.midi_io import SimulatedInput
from oxi_cq_bridge.server import serve_in_thread

MAPPING = "mappings/default_oxi_e16.json"


class TestEndToEndSimulated(unittest.TestCase):
    def setUp(self):
        self.engine = MappingEngine.from_file(MAPPING)
        self.sim = SimulatedInput()
        self.cap = make_output("dry")
        self.bridge = Bridge(self.engine, self.sim, self.cap)
        self.bridge.start()

    def tearDown(self):
        self.bridge.stop()

    def test_fader_mute_scene_softkey_session(self):
        # Page 1, Ch1 fader to the top (ch1 turn == CC16)
        self.sim.send_cc(16, 127, channel=1)
        self.assertEqual(self.cap.last_hex,
                         cq.flatten(cq.level_fraction("Ip1>LR", 1.0)).hex(" ").upper())

        # Page 1, Ch1 mute (push == Note36, note_on) -> toggle
        self.sim.send_note_on(36, 127, channel=1)
        self.assertEqual(self.cap.last_hex,
                         cq.flatten(cq.mute_toggle("Ip1")).hex(" ").upper())

        # Page 6 fixed scene 3 (slot3 push == Note38 on ch6)
        self.sim.send_note_on(38, 127, channel=6)
        self.assertEqual(self.cap.last_hex,
                         cq.flatten(cq.scene_recall(3)).hex(" ").upper())

        # Page 6 soft key 1 momentary (slot9 push == Note44 on ch6)
        self.sim.send_note_on(44, 127, channel=6)
        self.assertEqual(self.cap.last_hex, "90 30 7F")
        self.sim.send_note_off(44, 0, channel=6)
        self.assertEqual(self.cap.last_hex, "80 30 00")

        self.assertEqual(self.bridge.status()["sent"], 5)
        self.assertEqual(self.bridge.status()["unmapped"], 0)

    def test_unmapped_event_ignored(self):
        self.sim.send_cc(100, 10, channel=1)        # nothing bound here
        self.assertEqual(self.bridge.status()["unmapped"], 1)
        self.assertEqual(len(self.cap.records), 0)

    def test_output_error_does_not_crash(self):
        class Boom(make_output("dry").__class__):
            connected = False
            def send_bytes(self, data):
                raise OSError("mixer unreachable")
        bridge = Bridge(self.engine, SimulatedInput(), Boom())
        bridge.start()
        bridge.inject(0, 1, "turn", 100)
        self.assertIsNotNone(bridge.last_error)
        self.assertIn("mixer unreachable", bridge.last_error)
        self.assertEqual(bridge.status()["sent"], 0)   # not counted as sent
        bridge.stop()


class TestHttpServer(unittest.TestCase):
    def setUp(self):
        self.engine = MappingEngine.from_file(MAPPING)
        self.cap = make_output("dry")
        self.bridge = Bridge(self.engine, make_input("sim"), self.cap)
        self.bridge.start()
        self.httpd, _ = serve_in_thread(self.bridge, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.bridge.stop()

    def _post(self, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/inject",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=3))

    def _get(self, path):
        return json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=3))

    def test_state_endpoint(self):
        st = self._get("/api/state")
        self.assertEqual(st["app"]["name"], "OXI-CQ Bridge")
        self.assertEqual(len(st["mapping"]["pages"]), 6)
        self.assertEqual(st["mapping"]["pages"][0]["slots"][0]["label"], "Ch 1")

    def test_inject_endpoint_emits_midi(self):
        r = self._post({"page": 0, "slot": 1, "role": "turn", "value": 127})
        self.assertTrue(r["ok"])
        self.assertEqual(
            self.cap.last_hex,
            cq.flatten(cq.level_fraction("Ip1>LR", 1.0)).hex(" ").upper())

    def test_inject_bad_control_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post({"page": 42, "slot": 42, "role": "turn", "value": 1})
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

"""
Test 4 of the Four -- THE UPGRADE PATH.

A mapping is data that outlives any one build.  These tests pin the loader's
compatibility contract: what versions load, how unknown fields are treated,
what the defaults are, and that the shipped factory JSON still matches the
builder that generates it (so regenerating never silently changes it).
"""

import json
import unittest
from pathlib import Path

from oxi_cq_bridge.default_mapping import build_default
from oxi_cq_bridge.mapping import (SUPPORTED_SCHEMA_VERSIONS, MappingEngine,
                                   MappingError)

MAPPING_FILE = Path("mappings/default_oxi_e16.json")


class TestSchemaContract(unittest.TestCase):
    def _minimal(self, **over):
        doc = {"schema_version": 1, "name": "m", "input_channel": 1,
               "pages": [{"index": 0, "name": "p", "channel": 1, "controls": [
                   {"slot": 1, "label": "a",
                    "turn": {"cc": 16, "action": "level", "target": "Ip1>LR"}}]}]}
        doc.update(over)
        return doc

    def test_missing_schema_version_rejected(self):
        doc = self._minimal()
        del doc["schema_version"]
        with self.assertRaises(MappingError):
            MappingEngine(doc)

    def test_unsupported_version_rejected(self):
        with self.assertRaises(MappingError):
            MappingEngine(self._minimal(schema_version=999))

    def test_supported_versions_known(self):
        self.assertIn(1, SUPPORTED_SCHEMA_VERSIONS)

    def test_minimal_document_loads(self):
        eng = MappingEngine(self._minimal())
        self.assertEqual(len(eng.bindings), 1)

    def test_unknown_toplevel_fields_tolerated(self):
        # forward-compat: a newer writer may add fields we do not know yet
        eng = MappingEngine(self._minimal(future_feature={"x": 1},
                                          author="someone"))
        self.assertEqual(len(eng.bindings), 1)

    def test_unknown_control_fields_preserved(self):
        doc = self._minimal()
        doc["pages"][0]["controls"][0]["turn"]["color"] = "orange"
        eng = MappingEngine(doc)
        binding = eng.by_control[(0, 1, "turn")]
        self.assertEqual(binding.extra.get("color"), "orange")

    def test_defaults(self):
        eng = MappingEngine(self._minimal())
        self.assertEqual(eng.relative_mode, "twos_complement")
        self.assertEqual(eng.scene_count, 128)
        self.assertEqual(eng.input_channel, 1)


class TestFactoryMappingStable(unittest.TestCase):
    def test_shipped_json_matches_builder(self):
        shipped = json.loads(MAPPING_FILE.read_text("utf-8"))
        self.assertEqual(shipped, build_default(),
                         "mappings/default_oxi_e16.json is stale -- regenerate "
                         "with: python -m oxi_cq_bridge.default_mapping")

    def test_factory_mapping_is_valid(self):
        eng = MappingEngine.from_file(str(MAPPING_FILE))
        self.assertEqual(len(eng.pages), 6)
        # every binding's target resolves (checked at compile time already),
        # and the control index covers every page/slot/role triple.
        self.assertTrue(len(eng.by_control) >= 16 * 3)


if __name__ == "__main__":
    unittest.main()

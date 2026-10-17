"""
default_mapping.py -- build the factory OXI E16 -> CQ-12T mapping.

Rather than hand-maintain a huge JSON file, the default mapping is generated
here and serialised to mappings/default_oxi_e16.json.  Regenerate with:

    python -m oxi_cq_bridge.default_mapping > mappings/default_oxi_e16.json

E16 transmit convention this mapping expects
--------------------------------------------
Each E16 *page* transmits on its own MIDI channel (page 1 -> ch 1, ...).
Within a page, physical encoder *slot* S (1..16):

    turn  -> CC (15 + S)     i.e. CC16..CC31
    push  -> Note (35 + S)   i.e. Note36..Note51

All of this is just data -- edit the JSON (or this builder) to match a
different E16 setup.  See the README "Setting up the E16" section.
"""

from __future__ import annotations

import json
from typing import Any

from .mapping import SCHEMA_VERSION

TURN_CC_BASE = 15      # slot S turn  -> CC (TURN_CC_BASE + S)
PUSH_NOTE_BASE = 35    # slot S push  -> Note (PUSH_NOTE_BASE + S)

# 16 mix sources in physical slot order (CQ-12T surface).
# (level/pan source name, mute target name, display label)
INPUT_SOURCES = [
    ("Ip1", "Ip1", "Ch 1"), ("Ip2", "Ip2", "Ch 2"), ("Ip3", "Ip3", "Ch 3"),
    ("Ip4", "Ip4", "Ch 4"), ("Ip5", "Ip5", "Ch 5"), ("Ip6", "Ip6", "Ch 6"),
    ("Ip7", "Ip7", "Ch 7"), ("Ip8", "Ip8", "Ch 8"), ("Ip9", "Ip9", "Ch 9"),
    ("Ip10", "Ip10", "Ch 10"), ("Ip11", "Ip11", "Ch 11"), ("Ip12", "Ip12", "Ch 12"),
    ("St1", "St1", "ST 1"), ("St2", "St2", "ST 2"),
    ("USB", "USB", "USB"), ("BT", "BT", "BT"),
]

# Output / FX-return / DCA masters (level master name, mute name, label).
MASTERS = [
    ("MainLR", "MainLR", "Main LR"),
    ("Out1", "Out1", "Out 1"), ("Out2", "Out2", "Out 2"), ("Out3", "Out3", "Out 3"),
    ("Out4", "Out4", "Out 4"), ("Out5", "Out5", "Out 5"), ("Out6", "Out6", "Out 6"),
    ("FX1in", "FX1", "FX1 Ret"), ("FX2in", "FX2", "FX2 Ret"),
    ("FX3in", "FX3", "FX3 Ret"), ("FX4in", "FX4", "FX4 Ret"),
    ("DCA1", "DCA1", "DCA 1"), ("DCA2", "DCA2", "DCA 2"),
    ("DCA3", "DCA3", "DCA 3"), ("DCA4", "DCA4", "DCA 4"),
]


def _turn_cc(slot: int) -> int:
    return TURN_CC_BASE + slot


def _push_note(slot: int) -> int:
    return PUSH_NOTE_BASE + slot


def _level_page(index: int, name: str, dest_suffix: str) -> dict[str, Any]:
    """Inputs -> <dest> level on turn, mute on push."""
    controls = []
    for i, (src, mute, label) in enumerate(INPUT_SOURCES, start=1):
        controls.append({
            "slot": i, "label": label,
            "turn": {"cc": _turn_cc(i), "mode": "absolute",
                     "action": "level", "target": f"{src}{dest_suffix}"},
            "push": {"note": _push_note(i), "action": "mute",
                     "target": mute, "behavior": "toggle"},
        })
    return {"index": index, "channel": index + 1, "name": name, "controls": controls}


def _pan_page(index: int) -> dict[str, Any]:
    controls = []
    for i, (src, mute, label) in enumerate(INPUT_SOURCES, start=1):
        controls.append({
            "slot": i, "label": label,
            "turn": {"cc": _turn_cc(i), "mode": "absolute",
                     "action": "pan", "target": src},
            "push": {"note": _push_note(i), "action": "mute",
                     "target": mute, "behavior": "toggle"},
        })
    return {"index": index, "channel": index + 1,
            "name": "Input Pan -> Main LR", "controls": controls}


def _masters_page(index: int) -> dict[str, Any]:
    controls = []
    for i, (lvl, mute, label) in enumerate(MASTERS, start=1):
        controls.append({
            "slot": i, "label": label,
            "turn": {"cc": _turn_cc(i), "mode": "absolute",
                     "action": "level", "target": lvl},
            "push": {"note": _push_note(i), "action": "mute",
                     "target": mute, "behavior": "toggle"},
        })
    # slot 16: scene scrub on turn, soft key 1 tap on push
    controls.append({
        "slot": 16, "label": "Scene/SK1",
        "turn": {"cc": _turn_cc(16), "mode": "absolute", "action": "scene"},
        "push": {"note": _push_note(16), "action": "softkey",
                 "mode": "tap", "target": "1"},
    })
    return {"index": index, "channel": index + 1,
            "name": "Output / FX / DCA Masters", "controls": controls}


def _scenes_softkeys_page(index: int) -> dict[str, Any]:
    controls = []
    # slot 1 turn: scrub through scenes
    controls.append({
        "slot": 1, "label": "Scene",
        "turn": {"cc": _turn_cc(1), "mode": "absolute", "action": "scene"},
        "push": {"note": _push_note(1), "action": "scene",
                 "mode": "fixed", "target": "1"},
    })
    # slots 2..8 push: fixed scene recall 2..8
    for i in range(2, 9):
        controls.append({
            "slot": i, "label": f"Scene {i}",
            "push": {"note": _push_note(i), "action": "scene",
                     "mode": "fixed", "target": str(i)},
        })
    # slots 9..16 push: soft keys 1..8 (momentary)
    for n, i in enumerate(range(9, 17), start=1):
        controls.append({
            "slot": i, "label": f"Soft {n}",
            "push": {"note": _push_note(i), "action": "softkey",
                     "mode": "momentary", "target": str(n)},
        })
    return {"index": index, "channel": index + 1,
            "name": "Scenes & Soft Keys", "controls": controls}


def build_default() -> dict[str, Any]:
    pages = [
        _level_page(0, "Input Faders -> Main LR", ">LR"),
        _pan_page(1),
        _masters_page(2),
        _level_page(3, "Input Sends -> FX1", ">FX1"),
        _level_page(4, "Input Sends -> FX2", ">FX2"),
        _scenes_softkeys_page(5),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "OXI E16 -> CQ-12T (factory default)",
        "controller": "OXI Instruments E16",
        "target": "Allen & Heath CQ-12T",
        "input_channel": 1,
        "relative_mode": "twos_complement",
        "scene_count": 128,
        "notes": "E16 pages transmit on channel == page number; "
                 "slot S turn=CC(15+S), push=Note(35+S). Fully remappable.",
        "pages": pages,
    }


if __name__ == "__main__":
    print(json.dumps(build_default(), indent=2))

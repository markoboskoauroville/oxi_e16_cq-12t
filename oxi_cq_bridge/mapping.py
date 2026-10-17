"""
mapping.py -- translate OXI E16 controller events into CQ-12T actions.

A *mapping* is data (a JSON document, see mappings/default_oxi_e16.json)
describing how each E16 encoder turn and push should drive the mixer.  The
mapping is compiled into a flat lookup keyed by the incoming MIDI event, so
dispatch at runtime is a single dict lookup plus a pure protocol call.

Design goals
------------
* Data-driven: remapping is editing JSON, never code.
* Pure: `MappingEngine.process()` has no I/O; it returns the MIDI messages
  to send plus a display update.  The bridge does the sending.
* Forgiving upgrades: older/newer `schema_version`s load if compatible;
  unknown targets fail loudly with context, never silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import cq_protocol as cq
from .midi_io import MidiEvent

SCHEMA_VERSION = 1
# schema versions this build knows how to load (the upgrade path)
SUPPORTED_SCHEMA_VERSIONS = {1}


# ---------------------------------------------------------------------------
# Relative-encoder decoding
# ---------------------------------------------------------------------------
def decode_relative(value: int, mode: str) -> int:
    """Decode a relative-encoder CC value into a signed step count.

    The E16 can transmit endless-encoder movement in several encodings; pick
    the one that matches the E16's "Relative" setting (see the README).

      twos_complement : 1..63 = +1..+63, 65..127 = -63..-1  (65..127 -> v-128)
      binary_offset   : centred on 64;   step = value - 64
      signed_bit      : bit6 is sign;    0x01=+1, 0x41=-1
    """
    v = int(value) & 0x7F
    if mode == "binary_offset":
        return v - 64
    if mode == "signed_bit":
        return -(v & 0x3F) if (v & 0x40) else v
    # default: twos_complement
    return v - 128 if v >= 64 else v


# ---------------------------------------------------------------------------
# Compiled binding
# ---------------------------------------------------------------------------
@dataclass
class Binding:
    id: str
    page: int
    slot: int               # physical encoder 1..16 (0 for page-wide keys)
    label: str
    role: str               # 'turn' | 'push'
    action: str             # 'level' | 'pan' | 'mute' | 'scene' | 'softkey'
    target: str             # e.g. 'Ip1>LR', 'MainLR', 'Ip1', scene index, SK idx
    mode: str               # 'absolute' | 'relative' | 'toggle' | 'momentary' | 'tap'
    event_type: str         # 'cc' | 'note_on' | 'note_off'
    number: int             # cc number or note number
    channel: int = 1        # MIDI channel (1..16) the E16 transmits this on
    behavior: str = ""
    invert: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dispatch:
    """Result of processing one event."""
    binding: Binding
    messages: list[cq.Message]
    detail: str                      # human/UI description of what was sent
    display: dict[str, Any]          # UI state update for this binding


# ---------------------------------------------------------------------------
# Mapping document + engine
# ---------------------------------------------------------------------------
class MappingError(ValueError):
    pass


class MappingEngine:
    def __init__(self, document: dict[str, Any]) -> None:
        self.doc = document
        self._validate_header()
        self.input_channel: int = int(document.get("input_channel", 1))
        self.relative_mode: str = document.get("relative_mode", "twos_complement")
        self.scene_count: int = int(document.get("scene_count", 128))
        self.name: str = document.get("name", "mapping")
        self.bindings: dict[tuple[str, int, int], Binding] = {}
        self.by_control: dict[tuple[int, int, str], Binding] = {}
        self.pages: list[dict[str, Any]] = []
        self.state: dict[str, dict[str, Any]] = {}
        self._compile()

    # -- loading -------------------------------------------------------------
    @classmethod
    def from_file(cls, path: str | Path) -> "MappingEngine":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def _validate_header(self) -> None:
        ver = self.doc.get("schema_version")
        if ver is None:
            raise MappingError("mapping is missing 'schema_version'")
        if ver not in SUPPORTED_SCHEMA_VERSIONS:
            raise MappingError(
                f"mapping schema_version {ver} is not supported by this "
                f"build (supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )

    # -- compilation ---------------------------------------------------------
    def _compile(self) -> None:
        for page in self.doc.get("pages", []):
            pidx = int(page.get("index", len(self.pages)))
            pchan = int(page.get("channel", self.input_channel))
            self.pages.append({"index": pidx, "name": page.get("name", f"Page {pidx + 1}"),
                               "channel": pchan, "controls": page.get("controls", [])})
            for ctrl in page.get("controls", []):
                slot = int(ctrl.get("slot", 0))
                label = ctrl.get("label", f"slot{slot}")
                cchan = int(ctrl.get("channel", pchan))
                if "turn" in ctrl:
                    self._add_binding(pidx, slot, label, "turn", ctrl["turn"], cchan)
                if "push" in ctrl:
                    self._add_binding(pidx, slot, label, "push", ctrl["push"], cchan)

    def _add_binding(self, page: int, slot: int, label: str, role: str,
                     spec: dict[str, Any], channel: int) -> None:
        action = spec.get("action")
        if action not in {"level", "pan", "mute", "scene", "softkey"}:
            raise MappingError(f"{label}/{role}: unknown action {action!r}")

        # Determine the triggering MIDI event(s).
        if "cc" in spec:
            event_type, number = "cc", int(spec["cc"])
        elif "note" in spec:
            event_type, number = "note_on", int(spec["note"])
        else:
            raise MappingError(f"{label}/{role}: needs a 'cc' or 'note'")

        mode = spec.get("mode", _default_mode(action, event_type))
        target = str(spec.get("target", ""))
        channel = int(spec.get("channel", channel))
        self._check_target(label, role, action, target)

        bid = f"p{page}.s{slot}.{role}"
        binding = Binding(
            id=bid, page=page, slot=slot, label=label, role=role,
            action=action, target=target, mode=mode,
            event_type=event_type, number=number, channel=channel,
            behavior=spec.get("behavior", ""),
            invert=bool(spec.get("invert", False)),
            extra={k: v for k, v in spec.items()
                   if k not in {"cc", "note", "mode", "action", "target",
                                "behavior", "invert"}},
        )
        self._register(binding)
        self.by_control[(page, slot, role)] = binding

        # Soft keys / momentary mutes also need the matching note-off event.
        if event_type == "note_on" and (
            action == "softkey" and mode in {"momentary", ""}
            or (action == "mute" and binding.behavior == "momentary")
        ):
            off = Binding(**{**binding.__dict__, "event_type": "note_off",
                             "id": bid + ".off"})
            self._register(off)

    def _register(self, binding: Binding) -> None:
        key = (binding.event_type, binding.channel, binding.number)
        if key in self.bindings:
            prev = self.bindings[key]
            raise MappingError(
                f"conflicting bindings for {binding.event_type} "
                f"#{binding.number} on ch{binding.channel}: "
                f"{prev.id} and {binding.id}"
            )
        self.bindings[key] = binding
        self.state.setdefault(binding.id, {})

    def _check_target(self, label: str, role: str, action: str, target: str) -> None:
        try:
            if action == "level":
                cq.resolve_level(target)
            elif action == "pan":
                cq.resolve_pan(target)
            elif action == "mute":
                cq.resolve_mute(target)
            elif action == "scene":
                pass  # target unused; value/step selects the scene
            elif action == "softkey":
                int(target)  # soft-key index
        except (KeyError, ValueError) as exc:
            raise MappingError(f"{label}/{role}: {exc}") from None

    # -- UI helpers ----------------------------------------------------------
    def event_for(self, page: int, slot: int, role: str, value: int
                  ) -> MidiEvent | None:
        """Build the MIDI event a given control would emit (virtual E16)."""
        b = self.by_control.get((page, slot, role))
        if b is None:
            return None
        if b.event_type == "cc":
            return MidiEvent("cc", b.channel, b.number, int(value))
        if int(value) > 0:
            return MidiEvent("note_on", b.channel, b.number, int(value))
        return MidiEvent("note_off", b.channel, b.number, 0)

    def view(self) -> list[dict[str, Any]]:
        """A JSON-serialisable description of pages/slots for the web UI."""
        def _b(page: int, slot: int, role: str) -> dict[str, Any] | None:
            b = self.by_control.get((page, slot, role))
            if b is None:
                return None
            return {"action": b.action, "target": b.target, "mode": b.mode,
                    "event": b.event_type, "number": b.number,
                    "channel": b.channel}

        out = []
        for page in self.pages:
            pidx = page["index"]
            slots = []
            for ctrl in page["controls"]:
                s = int(ctrl.get("slot", 0))
                slots.append({
                    "slot": s, "label": ctrl.get("label", f"slot{s}"),
                    "turn": _b(pidx, s, "turn"),
                    "push": _b(pidx, s, "push"),
                })
            out.append({"index": pidx, "name": page["name"],
                        "channel": page["channel"], "slots": slots})
        return out

    # -- runtime -------------------------------------------------------------
    def process(self, event: MidiEvent) -> Dispatch | None:
        """Translate one controller event, or return None if unmapped."""
        binding = self.bindings.get(event.key)
        if binding is None:
            return None
        handler = getattr(self, f"_do_{binding.action}")
        return handler(binding, event)

    # Each handler returns a Dispatch.
    def _do_level(self, b: Binding, ev: MidiEvent) -> Dispatch:
        if b.mode == "relative":
            steps = decode_relative(ev.data2, self.relative_mode)
            if b.invert:
                steps = -steps
            msgs: list[cq.Message] = []
            for _ in range(abs(steps)):
                msgs += cq.level_step(b.target, up=(steps > 0))
            cur = self.state[b.id].get("steps", 0) + steps
            self.state[b.id]["steps"] = cur
            detail = f"{b.target} {'+' if steps >= 0 else ''}{steps} dB step(s)"
            return Dispatch(b, msgs, detail, {"id": b.id, "kind": "level_rel",
                                              "steps": cur, "label": b.label})
        frac = ev.data2 / 127.0
        if b.invert:
            frac = 1.0 - frac
        msgs = cq.level_fraction(b.target, frac)
        pct = round(frac * 100)
        return Dispatch(b, msgs, f"{b.target} level {pct}%",
                        {"id": b.id, "kind": "level", "percent": pct,
                         "label": b.label})

    def _do_pan(self, b: Binding, ev: MidiEvent) -> Dispatch:
        if b.mode == "relative":
            steps = decode_relative(ev.data2, self.relative_mode)
            if b.invert:
                steps = -steps
            msgs = []
            for _ in range(abs(steps)):
                msgs += cq.pan_step(b.target, right=(steps > 0))
            detail = f"{b.target} pan {'R' if steps >= 0 else 'L'}{abs(steps)}"
            return Dispatch(b, msgs, detail,
                            {"id": b.id, "kind": "pan_rel", "label": b.label})
        frac = ev.data2 / 127.0
        if b.invert:
            frac = 1.0 - frac
        position = round((frac * 2 - 1) * 100)
        msgs = cq.pan_fraction(b.target, frac)
        tag = "C" if position == 0 else (f"R{position}" if position > 0 else f"L{-position}")
        return Dispatch(b, msgs, f"{b.target} pan {tag}",
                        {"id": b.id, "kind": "pan", "position": position,
                         "label": b.label})

    def _do_mute(self, b: Binding, ev: MidiEvent) -> Dispatch:
        behavior = b.behavior or "toggle"
        if behavior == "momentary":
            on = (ev.type == "note_on")
            msgs = cq.mute(b.target, on)
            muted = on
        elif behavior in {"on", "off"}:
            on = (behavior == "on")
            msgs = cq.mute(b.target, on)
            muted = on
        else:  # toggle on press; ignore release
            if ev.type != "note_on":
                return Dispatch(b, [], f"{b.target} mute (release ignored)",
                                {"id": b.id, "kind": "noop"})
            msgs = cq.mute_toggle(b.target)
            muted = not bool(self.state[b.id].get("muted", False))
        self.state[b.id]["muted"] = muted
        return Dispatch(b, msgs, f"{b.target} mute {'ON' if muted else 'OFF'}",
                        {"id": b.id, "kind": "mute", "muted": muted,
                         "label": b.label})

    def _do_scene(self, b: Binding, ev: MidiEvent) -> Dispatch:
        if b.mode == "relative":
            steps = decode_relative(ev.data2, self.relative_mode)
            cur = int(self.state[b.id].get("scene", 1)) + steps
        elif b.event_type == "note_on":
            cur = int(b.target) if b.target else 1      # fixed-scene button
        else:  # absolute: map 0..127 across available scenes
            cur = 1 + round(ev.data2 / 127.0 * (self.scene_count - 1))
        cur = max(1, min(self.scene_count, cur))
        self.state[b.id]["scene"] = cur
        return Dispatch(b, cq.scene_recall(cur), f"recall scene {cur}",
                        {"id": b.id, "kind": "scene", "scene": cur,
                         "label": b.label})

    def _do_softkey(self, b: Binding, ev: MidiEvent) -> Dispatch:
        idx = int(b.target)
        if b.mode == "tap":
            if ev.type != "note_on":
                return Dispatch(b, [], "softkey (release ignored)",
                                {"id": b.id, "kind": "noop"})
            return Dispatch(b, cq.softkey_tap(idx), f"soft key {idx} tap",
                            {"id": b.id, "kind": "softkey", "label": b.label})
        if ev.type == "note_on":
            msgs, what = cq.softkey_press(idx), "press"
        else:
            msgs, what = cq.softkey_release(idx), "release"
        return Dispatch(b, msgs, f"soft key {idx} {what}",
                        {"id": b.id, "kind": "softkey", "down": ev.type == "note_on",
                         "label": b.label})


def _default_mode(action: str, event_type: str) -> str:
    if action == "level" or action == "pan":
        return "absolute"
    if action == "mute":
        return "toggle"
    if action == "scene":
        return "absolute" if event_type == "cc" else "fixed"
    if action == "softkey":
        return "momentary"
    return "absolute"

"""
bridge.py -- the runtime that wires input -> mapping -> output together.

The Bridge owns a MappingEngine, a MIDI input (the E16) and a MIDI output
(the CQ-12T, over USB or TCP, or a capture sink for dry runs).  For every
incoming controller event it asks the engine what to do, sends the
resulting MIDI to the mixer, records the dispatch, and publishes an update
to any subscribers (the web UI's live feed).

It is deliberately small and I/O-tolerant: a mixer that drops off the
network never crashes the bridge; the error is recorded and the next send
reconnects.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any

from . import __app_name__, __controller__, __target__, __version__
from . import cq_protocol as cq
from .mapping import MappingEngine
from .midi_io import (CaptureOutput, MidiEvent, MidiInput, MidiOutput,
                      MidoInput, MidoOutput, SimulatedInput, TcpMidiOutput)


class Bridge:
    def __init__(self, engine: MappingEngine, midi_in: MidiInput,
                 midi_out: MidiOutput, log_size: int = 200) -> None:
        self.engine = engine
        self.midi_in = midi_in
        self.midi_out = midi_out
        self.log: deque[dict[str, Any]] = deque(maxlen=log_size)
        self.sent_count = 0
        self.unmapped_count = 0
        self.last_error: str | None = None
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._running = False

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.midi_in.start(self.handle_event)
        self._running = True
        self._publish({"type": "status", "status": self.status()})

    def stop(self) -> None:
        self._running = False
        try:
            self.midi_in.stop()
        finally:
            self.midi_out.close()

    @property
    def running(self) -> bool:
        return self._running

    # -- event handling ------------------------------------------------------
    def handle_event(self, event: MidiEvent) -> dict[str, Any] | None:
        """Translate and forward one controller event."""
        dispatch = self.engine.process(event)
        if dispatch is None:
            self.unmapped_count += 1
            return None

        sent_ok = True
        if dispatch.messages:
            try:
                self.midi_out.send(dispatch.messages)
                self.sent_count += 1
                self.last_error = None
            except Exception as exc:                        # noqa: BLE001
                sent_ok = False
                self.last_error = f"{type(exc).__name__}: {exc}"

        record = {
            "type": "dispatch",
            "t": time.time(),
            "page": dispatch.binding.page,
            "slot": dispatch.binding.slot,
            "role": dispatch.binding.role,
            "label": dispatch.binding.label,
            "detail": dispatch.detail,
            "hex": cq.describe(dispatch.messages),
            "display": dispatch.display,
            "sent": sent_ok,
        }
        with self._lock:
            self.log.append(record)
        self._publish(record)
        if not sent_ok:
            self._publish({"type": "status", "status": self.status()})
        return record

    def inject(self, page: int, slot: int, role: str, value: int
               ) -> dict[str, Any] | None:
        """Drive a control from the UI/tests as if the E16 had moved it."""
        ev = self.engine.event_for(page, slot, role, value)
        if ev is None:
            return None
        return self.handle_event(ev)

    # -- pub/sub for the web UI ---------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def _publish(self, message: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass  # a slow client simply misses an update

    # -- introspection -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "input": self.midi_in.name,
            "output": self.midi_out.name,
            "connected": bool(getattr(self.midi_out, "connected", True)),
            "sent": self.sent_count,
            "unmapped": self.unmapped_count,
            "error": self.last_error,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            log = list(self.log)
        return {
            "app": {"name": __app_name__, "version": __version__,
                    "controller": __controller__, "target": __target__},
            "status": self.status(),
            "mapping": {"name": self.engine.name,
                        "relative_mode": self.engine.relative_mode,
                        "scene_count": self.engine.scene_count,
                        "pages": self.engine.view()},
            "log": log,
        }


# ---------------------------------------------------------------------------
# Output factory
# ---------------------------------------------------------------------------
def make_output(spec: str) -> MidiOutput:
    """Create an output from a short spec string:

      'dry'                -> CaptureOutput (no mixer; simulate only)
      'tcp:HOST[:PORT]'    -> MIDI over TCP/IP to the CQ
      'usb[:PORT_NAME]'    -> real USB MIDI port via mido
    """
    spec = spec.strip()
    if spec in ("", "dry", "capture"):
        return CaptureOutput("Dry run (no mixer)")
    if spec.startswith("tcp:"):
        rest = spec[4:]
        if ":" in rest:
            host, port = rest.rsplit(":", 1)
            return TcpMidiOutput(host, int(port))
        return TcpMidiOutput(rest)
    if spec.startswith("usb"):
        name = spec[4:] if spec.startswith("usb:") else None
        return MidoOutput(name or None)
    raise ValueError(f"unknown output spec {spec!r} "
                     "(use dry | tcp:HOST[:PORT] | usb[:NAME])")


def make_input(spec: str) -> MidiInput:
    """Create an input from a short spec string:

      'sim'                -> SimulatedInput (virtual E16 via the UI/tests)
      'usb[:PORT_NAME]'    -> real USB/Bluetooth MIDI port via mido
    """
    spec = spec.strip()
    if spec in ("", "sim", "simulator"):
        return SimulatedInput()
    if spec.startswith("usb"):
        name = spec[4:] if spec.startswith("usb:") else None
        return MidoInput(name or None)
    raise ValueError(f"unknown input spec {spec!r} (use sim | usb[:NAME])")

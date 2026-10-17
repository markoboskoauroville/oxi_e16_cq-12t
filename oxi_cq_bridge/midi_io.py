"""
midi_io.py -- pluggable MIDI input and output backends.

The bridge talks to two things:

  * INPUT  -- the OXI E16 controller (incoming CC / Note messages).
  * OUTPUT -- the Allen & Heath CQ-12T mixer (outgoing NRPN / PC / Note).

Both are abstracted so the exact same bridge logic runs against real
hardware, a network socket, or a pure in-memory simulation used by the
tests and the web UI's virtual-E16 panel.

Backends
--------
Input :  SimulatedInput (stdlib)        -- feed events programmatically
         MidoInput (needs python-rtmidi) -- a real USB/Bluetooth MIDI port
Output:  CaptureOutput (stdlib)         -- records everything, for tests/UI
         TcpMidiOutput (stdlib)         -- MIDI over TCP/IP to CQ port 51325
         MidoOutput (needs python-rtmidi)-- a real USB MIDI port
         TeeOutput (stdlib)             -- fan out to several outputs

`mido`/`python-rtmidi` are imported lazily, so nothing here fails to import
on a machine that only ever uses the simulator / network / capture paths.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .cq_protocol import Message, describe, flatten

CQ_TCP_PORT = 51325              # A&H MIDI-over-TCP/IP port (one client max)


# ---------------------------------------------------------------------------
# Normalised input event
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MidiEvent:
    """A controller event, normalised across every input backend.

    `type` is one of: 'cc', 'note_on', 'note_off', 'program_change'.
    `channel` is 1-based (1..16).  `data1`/`data2` are the two data bytes
    (controller/value, note/velocity, program/0).
    """

    type: str
    channel: int
    data1: int
    data2: int = 0

    @property
    def key(self) -> tuple[str, int, int]:
        """The lookup key used by the mapping engine: (type, channel, data1)."""
        return (self.type, self.channel, self.data1)


EventCallback = Callable[[MidiEvent], None]


# ---------------------------------------------------------------------------
# Input backends
# ---------------------------------------------------------------------------
class MidiInput:
    """Abstract input port."""

    name: str = "input"

    def start(self, callback: EventCallback) -> None:   # pragma: no cover
        raise NotImplementedError

    def stop(self) -> None:                             # pragma: no cover
        pass


class SimulatedInput(MidiInput):
    """In-memory input you drive by calling send_*; used by tests and the UI."""

    def __init__(self, name: str = "Simulated E16") -> None:
        self.name = name
        self._callback: EventCallback | None = None

    def start(self, callback: EventCallback) -> None:
        self._callback = callback

    def stop(self) -> None:
        self._callback = None

    # -- driving methods -----------------------------------------------------
    def emit(self, event: MidiEvent) -> None:
        if self._callback is not None:
            self._callback(event)

    def send_cc(self, controller: int, value: int, channel: int = 1) -> None:
        self.emit(MidiEvent("cc", channel, controller, value))

    def send_note_on(self, note: int, velocity: int = 127, channel: int = 1) -> None:
        self.emit(MidiEvent("note_on", channel, note, velocity))

    def send_note_off(self, note: int, velocity: int = 0, channel: int = 1) -> None:
        self.emit(MidiEvent("note_off", channel, note, velocity))

    def send_program_change(self, program: int, channel: int = 1) -> None:
        self.emit(MidiEvent("program_change", channel, program, 0))


class MidoInput(MidiInput):
    """A real MIDI input port via mido/python-rtmidi (lazy import)."""

    def __init__(self, port_name: str | None = None) -> None:
        self.port_name = port_name
        self.name = port_name or "auto"
        self._port = None

    def start(self, callback: EventCallback) -> None:
        import mido  # lazy: only needed for real hardware

        def _dispatch(msg) -> None:
            ev = _mido_to_event(msg)
            if ev is not None:
                callback(ev)

        name = self.port_name or _pick_port(mido.get_input_names(), "input")
        self.name = name
        self._port = mido.open_input(name, callback=_dispatch)

    def stop(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None


def _mido_to_event(msg) -> MidiEvent | None:
    t = msg.type
    ch = getattr(msg, "channel", 0) + 1
    if t == "control_change":
        return MidiEvent("cc", ch, msg.control, msg.value)
    if t == "note_on":
        # velocity 0 is the common "note off" encoding
        if msg.velocity == 0:
            return MidiEvent("note_off", ch, msg.note, 0)
        return MidiEvent("note_on", ch, msg.note, msg.velocity)
    if t == "note_off":
        return MidiEvent("note_off", ch, msg.note, msg.velocity)
    if t == "program_change":
        return MidiEvent("program_change", ch, msg.program, 0)
    return None


# ---------------------------------------------------------------------------
# Output backends
# ---------------------------------------------------------------------------
class MidiOutput:
    """Abstract output port.  Subclasses implement send_bytes()."""

    name: str = "output"
    connected: bool = True

    def send(self, messages: Sequence[Message]) -> None:
        if messages:
            self.send_bytes(flatten(messages))

    def send_bytes(self, data: bytes) -> None:          # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass


@dataclass
class CaptureRecord:
    when: float
    data: bytes
    hex: str


class CaptureOutput(MidiOutput):
    """Records everything sent -- the backbone of tests and the UI's log."""

    def __init__(self, name: str = "Capture", keep: int = 500) -> None:
        self.name = name
        self.keep = keep
        self.records: list[CaptureRecord] = []
        self._lock = threading.Lock()

    def send_bytes(self, data: bytes) -> None:
        rec = CaptureRecord(time.time(), data, data.hex(" ").upper())
        with self._lock:
            self.records.append(rec)
            if len(self.records) > self.keep:
                del self.records[: -self.keep]

    @property
    def last_hex(self) -> str:
        with self._lock:
            return self.records[-1].hex if self.records else ""

    def all_bytes(self) -> bytes:
        with self._lock:
            return b"".join(r.data for r in self.records)

    def clear(self) -> None:
        with self._lock:
            self.records.clear()


class TcpMidiOutput(MidiOutput):
    """MIDI over TCP/IP to the CQ's network port (default 51325).

    The CQ accepts a single TCP/IP MIDI client; raw MIDI bytes are written
    straight to the stream.  Connection is lazy and self-healing: a failed
    write marks the socket disconnected and the next send retries.
    """

    def __init__(self, host: str, port: int = CQ_TCP_PORT,
                 timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.name = f"CQ@{host}:{port}"
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        with self._lock:
            self._connect_locked()

    def _connect_locked(self) -> None:
        if self._sock is not None:
            return
        s = socket.create_connection((self.host, self.port), self.timeout)
        s.settimeout(self.timeout)
        self._sock = s

    def send_bytes(self, data: bytes) -> None:
        with self._lock:
            try:
                self._connect_locked()
                assert self._sock is not None
                self._sock.sendall(data)
            except OSError:
                # drop the socket so the next send reconnects
                self._close_locked()
                raise

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()


class MidoOutput(MidiOutput):
    """A real MIDI output port via mido/python-rtmidi (lazy import)."""

    def __init__(self, port_name: str | None = None) -> None:
        self.port_name = port_name
        self.name = port_name or "auto"
        self._port = None
        self._mido = None

    def _ensure_open(self):
        if self._port is None:
            import mido  # lazy
            self._mido = mido
            name = self.port_name or _pick_port(mido.get_output_names(), "output")
            self.name = name
            self._port = mido.open_output(name)
        return self._port

    @property
    def connected(self) -> bool:
        return self._port is not None

    def send_bytes(self, data: bytes) -> None:
        port = self._ensure_open()
        for msg in self._mido.parse_all(data):
            port.send(msg)

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None


class TeeOutput(MidiOutput):
    """Fan out every send to several outputs (e.g. real port + UI capture).

    A failure on one output never stops the others; the first error is
    re-raised after all have been attempted so the UI can surface it.
    """

    def __init__(self, outputs: Sequence[MidiOutput], name: str = "Tee") -> None:
        self.outputs = list(outputs)
        self.name = name

    @property
    def connected(self) -> bool:
        return all(getattr(o, "connected", True) for o in self.outputs)

    def send_bytes(self, data: bytes) -> None:
        first_error: Exception | None = None
        for out in self.outputs:
            try:
                out.send_bytes(data)
            except Exception as exc:                    # noqa: BLE001
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def close(self) -> None:
        for out in self.outputs:
            out.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pick_port(names: list[str], kind: str) -> str:
    """Pick a sensible default port, preferring anything that looks like a CQ
    (for output) or an OXI/E16 (for input)."""
    if not names:
        raise RuntimeError(f"no MIDI {kind} ports found")
    preferred = ("cq", "oxi", "e16") if kind == "input" else ("cq", "allen")
    for want in preferred:
        for n in names:
            if want in n.lower():
                return n
    return names[0]


def list_ports() -> dict[str, list[str]]:
    """Return available real MIDI ports, or an explanation if unavailable."""
    try:
        import mido
    except Exception as exc:                            # noqa: BLE001
        return {"inputs": [], "outputs": [], "error": str(exc)}
    return {"inputs": list(mido.get_input_names()),
            "outputs": list(mido.get_output_names())}


def describe_messages(messages: Sequence[Message]) -> str:
    return describe(messages)

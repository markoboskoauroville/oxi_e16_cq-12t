"""
cq_protocol.py -- encode control actions into Allen & Heath CQ-12T MIDI.

This module is the *mechanism*: pure functions that turn a high-level
intent ("set Ip1 to Main LR to -20 dB", "toggle mute on Out1", "recall
scene 7") into the exact sequence of MIDI bytes defined by the CQ MIDI
Protocol V1.2 Issue 4.

A single "action" expands to one or more complete MIDI messages.  A message
is represented as a tuple of 7-bit-clean integers (its raw on-wire bytes),
2 or 3 bytes long.  An action returns a list of such messages.

    >>> level_db("Ip1>LR", 0.0)
    [(176, 99, 64), (176, 98, 0), (176, 6, 98), (176, 38, 0)]
    >>> flatten(level_db("Ip1>LR", 0.0)).hex(" ")
    'b0 63 40 b0 62 00 b0 06 62 b0 26 00'

Nothing here performs I/O; transports in midi_io.py send the bytes.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from . import cq_params as P

Message = tuple[int, ...]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
def _b(value: int, what: str = "byte") -> int:
    """Validate and return a 7-bit MIDI data byte (0..127)."""
    iv = int(value)
    if not 0 <= iv <= 127:
        raise ValueError(f"{what} out of MIDI range 0..127: {value}")
    return iv


def _cc(controller: int, value: int) -> Message:
    return (P.CC_STATUS, _b(controller, "controller"), _b(value, "value"))


def flatten(messages: Iterable[Message]) -> bytes:
    """Concatenate messages into the exact byte stream to put on the wire."""
    out = bytearray()
    for msg in messages:
        out.extend(msg)
    return bytes(out)


def describe(messages: Sequence[Message]) -> str:
    """Human/UI friendly hex rendering, e.g. 'B0 63 40 | B0 62 00'."""
    return " | ".join(" ".join(f"{b:02X}" for b in m) for m in messages)


# ---------------------------------------------------------------------------
# Raw NRPN builders (take an already-resolved parameter number)
# ---------------------------------------------------------------------------
def nrpn_set(msb: int, lsb: int, vc: int, vf: int) -> list[Message]:
    """Absolute NRPN write: parameter = (vc coarse, vf fine)."""
    return [
        _cc(P.CC_NRPN_MSB, msb),
        _cc(P.CC_NRPN_LSB, lsb),
        _cc(P.CC_DATA_MSB, vc),
        _cc(P.CC_DATA_LSB, vf),
    ]


def nrpn_increment(msb: int, lsb: int) -> list[Message]:
    """Relative +1 step (also toggles a mute)."""
    return [_cc(P.CC_NRPN_MSB, msb), _cc(P.CC_NRPN_LSB, lsb),
            _cc(P.CC_DATA_INC, 0x00)]


def nrpn_decrement(msb: int, lsb: int) -> list[Message]:
    """Relative -1 step."""
    return [_cc(P.CC_NRPN_MSB, msb), _cc(P.CC_NRPN_LSB, lsb),
            _cc(P.CC_DATA_DEC, 0x00)]


def nrpn_get(msb: int, lsb: int) -> list[Message]:
    """Request the current value (increment message with data 0x7F)."""
    return [_cc(P.CC_NRPN_MSB, msb), _cc(P.CC_NRPN_LSB, lsb),
            _cc(P.CC_DATA_INC, 0x7F)]


# ---------------------------------------------------------------------------
# Value conversions (dB / pan position / fraction  ->  coarse, fine)
# ---------------------------------------------------------------------------
def _interp_code(x: float, table: list[tuple[float, int]]) -> int:
    """Piecewise-linear interpolation in 14-bit code space.

    `table` is a sorted list of (x, code) breakpoints.  Values outside the
    range clamp to the nearest end.
    """
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, c0), (x1, c1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return c1
            frac = (x - x0) / (x1 - x0)
            return round(c0 + frac * (c1 - c0))
    return table[-1][1]


def _code_to_vcvf(code: int) -> tuple[int, int]:
    code = max(0, min(0x7F * 128 + 0x7F, int(round(code))))
    return (code >> 7) & 0x7F, code & 0x7F


# Precomputed (dB -> code) breakpoints, excluding the -inf sentinel.
_LEVEL_DB_TABLE = [
    (db, vc * 128 + vf) for (db, vc, vf) in P.LEVEL_TAPER if db is not None
]
# Precomputed (pan position -> code) breakpoints.
_PAN_POS_TABLE = [
    (pos, vc * 128 + vf) for (pos, vc, vf) in P.PAN_TAPER
]


def db_to_vcvf(db: float | None) -> tuple[int, int]:
    """Convert a dB level to (coarse, fine).  `None` means -inf (off)."""
    if db is None:
        return 0x00, 0x00
    return _code_to_vcvf(_interp_code(db, _LEVEL_DB_TABLE))


def fraction_to_level_vcvf(frac: float) -> tuple[int, int]:
    """Map a fader position 0.0..1.0 to (coarse, fine).

    Linear in the device's own 14-bit code, which already encodes an audio
    taper -- so travel feels like a real fader (fine at the top, steep at
    the bottom).  0.0 == -inf, 1.0 == +10 dB.
    """
    frac = max(0.0, min(1.0, float(frac)))
    return _code_to_vcvf(round(frac * P.LEVEL_CODE_MAX))


def position_to_pan_vcvf(position: float) -> tuple[int, int]:
    """Map a pan position -100..+100 (centre 0) to (coarse, fine)."""
    position = max(-100.0, min(100.0, float(position)))
    return _code_to_vcvf(_interp_code(position, _PAN_POS_TABLE))


def fraction_to_pan_vcvf(frac: float) -> tuple[int, int]:
    """Map 0.0..1.0 (0.5 == centre) to a pan (coarse, fine)."""
    frac = max(0.0, min(1.0, float(frac)))
    return position_to_pan_vcvf((frac * 2.0 - 1.0) * 100.0)


# ---------------------------------------------------------------------------
# Name resolution against the reference tables
# ---------------------------------------------------------------------------
def resolve_mute(name: str) -> tuple[int, int]:
    try:
        return P.MUTE_PARAMS[name]
    except KeyError:
        raise KeyError(f"unknown mute target {name!r}") from None


def resolve_level(name: str) -> tuple[int, int]:
    """Resolve a level target: a send ('Ip1>LR') or a master ('MainLR')."""
    if name in P.LEVEL_SEND_PARAMS:
        return P.LEVEL_SEND_PARAMS[name]
    if name in P.LEVEL_MASTER_PARAMS:
        return P.LEVEL_MASTER_PARAMS[name]
    raise KeyError(f"unknown level target {name!r}")


def resolve_pan(name: str) -> tuple[int, int]:
    try:
        return P.PAN_LR_PARAMS[name]
    except KeyError:
        raise KeyError(f"unknown pan target {name!r}") from None


# ---------------------------------------------------------------------------
# High-level, name-aware actions (what the mapping engine calls)
# ---------------------------------------------------------------------------
def mute(name: str, on: bool) -> list[Message]:
    msb, lsb = resolve_mute(name)
    return nrpn_set(msb, lsb, 0x00, 0x01 if on else 0x00)


def mute_toggle(name: str) -> list[Message]:
    msb, lsb = resolve_mute(name)
    return nrpn_increment(msb, lsb)


def level_db(name: str, db: float | None) -> list[Message]:
    msb, lsb = resolve_level(name)
    vc, vf = db_to_vcvf(db)
    return nrpn_set(msb, lsb, vc, vf)


def level_fraction(name: str, frac: float) -> list[Message]:
    msb, lsb = resolve_level(name)
    vc, vf = fraction_to_level_vcvf(frac)
    return nrpn_set(msb, lsb, vc, vf)


def level_step(name: str, up: bool) -> list[Message]:
    msb, lsb = resolve_level(name)
    return nrpn_increment(msb, lsb) if up else nrpn_decrement(msb, lsb)


def pan_position(name: str, position: float) -> list[Message]:
    msb, lsb = resolve_pan(name)
    vc, vf = position_to_pan_vcvf(position)
    return nrpn_set(msb, lsb, vc, vf)


def pan_fraction(name: str, frac: float) -> list[Message]:
    msb, lsb = resolve_pan(name)
    vc, vf = fraction_to_pan_vcvf(frac)
    return nrpn_set(msb, lsb, vc, vf)


def pan_step(name: str, right: bool) -> list[Message]:
    msb, lsb = resolve_pan(name)
    return nrpn_increment(msb, lsb) if right else nrpn_decrement(msb, lsb)


def get_level(name: str) -> list[Message]:
    return nrpn_get(*resolve_level(name))


def get_mute(name: str) -> list[Message]:
    return nrpn_get(*resolve_mute(name))


def get_pan(name: str) -> list[Message]:
    return nrpn_get(*resolve_pan(name))


# ---------------------------------------------------------------------------
# Scenes and Soft Keys
# ---------------------------------------------------------------------------
def scene_recall(scene_number: int) -> list[Message]:
    """Recall a saved scene (1..128).  Bank select 0 + program change (n-1)."""
    n = int(scene_number)
    if not 1 <= n <= 128:
        raise ValueError(f"scene number out of range 1..128: {scene_number}")
    return [
        _cc(P.CC_BANK_MSB, P.SCENE_BANK),       # B0 00 00
        (P.PC_STATUS, _b(n - 1, "program")),    # C0 (n-1)
    ]


def _softkey_note(index: int) -> int:
    i = int(index)
    if i < 1:
        raise ValueError(f"soft key index must be >= 1: {index}")
    return _b(P.SOFTKEY_BASE_NOTE + (i - 1), "soft key note")


def softkey_press(index: int) -> list[Message]:
    return [(P.NOTE_ON_STATUS, _softkey_note(index), 0x7F)]


def softkey_release(index: int) -> list[Message]:
    return [(P.NOTE_OFF_STATUS, _softkey_note(index), 0x00)]


def softkey_tap(index: int) -> list[Message]:
    """A momentary press+release of a soft key."""
    return softkey_press(index) + softkey_release(index)

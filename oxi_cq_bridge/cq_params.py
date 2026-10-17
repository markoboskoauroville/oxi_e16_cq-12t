"""
cq_params.py -- Allen & Heath CQ-12T control reference tables.

Every value here is transcribed directly from the official
"CQ MIDI Protocol, Firmware V1.2.0, Issue 4" (Copyright (c) 2024 Allen &
Heath).  See docs/CQ_MIDI_Protocol_notes.md for the human-readable copy.

These tables are *data only* -- no logic lives here, which keeps them easy
to audit against the PDF and easy to extend when A&H publish a new issue.

Conventions
-----------
* All control messaging is on MIDI Channel 1 (status nibble 0xB0 for CC).
* An NRPN "parameter number" is a (MSB, LSB) pair.  A control is addressed
  by that pair; the action (mute/level/pan) is chosen by the data bytes.
* `MSB`/`LSB` below are raw 7-bit values exactly as the PDF lists them.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# MIDI Control Change numbers used to build NRPN strings (PDF section 2.x)
# ---------------------------------------------------------------------------
MIDI_CHANNEL = 1                 # CQ uses MIDI channel 1 for all control
CC_STATUS = 0xB0                 # Control Change, channel 1
PC_STATUS = 0xC0                 # Program Change, channel 1
NOTE_ON_STATUS = 0x90            # Note On, channel 1
NOTE_OFF_STATUS = 0x80           # Note Off, channel 1

CC_BANK_MSB = 0x00               # Bank Select MSB (scene bank)
CC_NRPN_MSB = 0x63               # 99  -- NRPN parameter MSB
CC_NRPN_LSB = 0x62               # 98  -- NRPN parameter LSB
CC_DATA_MSB = 0x06               # 6   -- Data Entry MSB (coarse value)
CC_DATA_LSB = 0x26               # 38  -- Data Entry LSB (fine value)
CC_DATA_INC = 0x60               # 96  -- Data Increment
CC_DATA_DEC = 0x61               # 97  -- Data Decrement

# Soft Keys: sequential notes starting at C3 == note 0x30 (PDF 2.2)
SOFTKEY_BASE_NOTE = 0x30

# Scenes: bank 0 + program change, with a -1 offset (CQ counts 1..128).
SCENE_BANK = 0x00


# ---------------------------------------------------------------------------
# Mute parameter numbers (PDF p.16)  name -> (MSB, LSB)
# ---------------------------------------------------------------------------
MUTE_PARAMS: dict[str, tuple[int, int]] = {}
for _i in range(16):                       # Ip1..Ip16
    MUTE_PARAMS[f"Ip{_i + 1}"] = (0x00, 0x00 + _i)
MUTE_PARAMS.update({
    "St1": (0x00, 0x18), "St2": (0x00, 0x1A),
    "USB": (0x00, 0x1C), "BT": (0x00, 0x1E),
    "MainLR": (0x00, 0x44),
    "Out1": (0x00, 0x45), "Out2": (0x00, 0x46), "Out3": (0x00, 0x47),
    "Out4": (0x00, 0x48), "Out5": (0x00, 0x49), "Out6": (0x00, 0x4A),
    "Out1/2": (0x00, 0x45), "Out3/4": (0x00, 0x47), "Out5/6": (0x00, 0x49),
    "FX1": (0x00, 0x51), "FX2": (0x00, 0x52),
    "FX3": (0x00, 0x53), "FX4": (0x00, 0x54),
    "DCA1": (0x02, 0x00), "DCA2": (0x02, 0x01),
    "DCA3": (0x02, 0x02), "DCA4": (0x02, 0x03),
    "MGRP1": (0x04, 0x00), "MGRP2": (0x04, 0x01),
    "MGRP3": (0x04, 0x02), "MGRP4": (0x04, 0x03),
})

# ---------------------------------------------------------------------------
# Level parameter numbers
# ---------------------------------------------------------------------------
# Sends: source -> destination.  Key format "SRC>DST", value (MSB, LSB).
# Built from the PDF matrix on p.17.  Regular inputs step by 0x0C between
# inputs; the six output columns (Out1..Out6) are consecutive within a row.
LEVEL_SEND_PARAMS: dict[str, tuple[int, int]] = {}

# --- Inputs / stereo / USB / BT / FX -> Main LR (single fader to the mix) ---
_LR_SRC = {}
for _i in range(16):                       # Ip1..Ip16 -> LR : 0x40 0x00..0x0F
    _LR_SRC[f"Ip{_i + 1}"] = (0x40, 0x00 + _i)
_LR_SRC.update({
    "St1": (0x40, 0x18), "St2": (0x40, 0x1A),
    "USB": (0x40, 0x1C), "BT": (0x40, 0x1E),
    "FX1": (0x40, 0x3C), "FX2": (0x40, 0x3D),
    "FX3": (0x40, 0x3E), "FX4": (0x40, 0x3F),
})
for _src, _pn in _LR_SRC.items():
    LEVEL_SEND_PARAMS[f"{_src}>LR"] = _pn

# NRPN parameter numbers are a pair of 7-bit bytes, so they combine in
# base-128, not base-256: combined == MSB * 128 + LSB.
def _split14(combined: int) -> tuple[int, int]:
    return (combined >> 7) & 0x7F, combined & 0x7F


def _join14(msb: int, lsb: int) -> int:
    return msb * 128 + lsb


# --- Inputs -> Out1..Out6 (full send matrix, p.17) ---
# Ip1 Out1 = (0x40,0x44); +0x0C per input; +1 per output column.
_IP_OUT1_BASE = _join14(0x40, 0x44)
for _i in range(16):
    _base = _IP_OUT1_BASE + _i * 0x0C
    for _o in range(6):                    # Out1..Out6
        LEVEL_SEND_PARAMS[f"Ip{_i + 1}>Out{_o + 1}"] = _split14(_base + _o)

# --- Inputs -> FX1..FX4 (send to FX, p.17) ---
# Ip1 FX1 = (0x4C,0x14); +4 per input; +1 per FX column.
_IP_FX1_BASE = _join14(0x4C, 0x14)
for _i in range(16):
    _base = _IP_FX1_BASE + _i * 0x04
    for _f in range(4):                    # FX1..FX4
        LEVEL_SEND_PARAMS[f"Ip{_i + 1}>FX{_f + 1}"] = _split14(_base + _f)

# --- Stereo / USB / BT sources -> Out1..Out6 and FX1..FX4 (p.17) ---
# (source: Out1 base combined, FX1 base combined)
_STEREO_SENDS = {
    "St1": (_join14(0x42, 0x64), _join14(0x4C, 0x74)),
    "St2": (_join14(0x42, 0x7C), _join14(0x4C, 0x7C)),
    "USB": (_join14(0x43, 0x14), _join14(0x4D, 0x04)),
    "BT":  (_join14(0x43, 0x2C), _join14(0x4D, 0x0C)),
}
for _src, (_ob, _fb) in _STEREO_SENDS.items():
    for _o in range(6):                    # Out1..Out6
        LEVEL_SEND_PARAMS[f"{_src}>Out{_o + 1}"] = _split14(_ob + _o)
    for _f in range(4):                    # FX1..FX4
        LEVEL_SEND_PARAMS[f"{_src}>FX{_f + 1}"] = _split14(_fb + _f)

# --- FX returns -> Out1..Out6 and FX -> FX (p.17) ---
# FX cannot send to itself; those cells are blank in the PDF and omitted.
_FX_OUT_BASE = {"FX1": _join14(0x46, 0x14), "FX2": _join14(0x46, 0x20),
                "FX3": _join14(0x46, 0x2C), "FX4": _join14(0x46, 0x38)}
for _src, _ob in _FX_OUT_BASE.items():
    for _o in range(6):                    # Out1..Out6
        LEVEL_SEND_PARAMS[f"{_src}>Out{_o + 1}"] = _split14(_ob + _o)
# FX -> FX send parameter numbers (explicit; diagonal blanks skipped).
_FX_TO_FX = {
    "FX1>FX2": (0x4E, 0x05), "FX1>FX3": (0x4E, 0x06), "FX1>FX4": (0x4E, 0x07),
    "FX2>FX1": (0x4E, 0x08), "FX2>FX3": (0x4E, 0x0A), "FX2>FX4": (0x4E, 0x0B),
    "FX3>FX1": (0x4E, 0x0C), "FX3>FX2": (0x4E, 0x0D), "FX3>FX4": (0x4E, 0x0F),
    "FX4>FX1": (0x4E, 0x10), "FX4>FX2": (0x4E, 0x11), "FX4>FX3": (0x4E, 0x12),
}
LEVEL_SEND_PARAMS.update(_FX_TO_FX)

# Convenience aliases: a send to a stereo output pair addresses the odd
# output of the pair (e.g. "Ip1>Out3/4" == "Ip1>Out3"), matching how the
# PDF labels stereo destinations.
for _src_key in [k for k in list(LEVEL_SEND_PARAMS) if k.endswith(">Out1")]:
    _src = _src_key[: -len(">Out1")]
    if f"{_src}>Out3" in LEVEL_SEND_PARAMS:
        LEVEL_SEND_PARAMS[f"{_src}>Out1/2"] = LEVEL_SEND_PARAMS[f"{_src}>Out1"]
        LEVEL_SEND_PARAMS[f"{_src}>Out3/4"] = LEVEL_SEND_PARAMS[f"{_src}>Out3"]
        LEVEL_SEND_PARAMS[f"{_src}>Out5/6"] = LEVEL_SEND_PARAMS[f"{_src}>Out5"]

# ---------------------------------------------------------------------------
# Output / FX-input / DCA master level parameters (PDF p.17, 0x4F block)
# ---------------------------------------------------------------------------
LEVEL_MASTER_PARAMS: dict[str, tuple[int, int]] = {
    "MainLR": (0x4F, 0x00),
    "Out1": (0x4F, 0x01), "Out2": (0x4F, 0x02), "Out3": (0x4F, 0x03),
    "Out4": (0x4F, 0x04), "Out5": (0x4F, 0x05), "Out6": (0x4F, 0x06),
    "Out1/2": (0x4F, 0x01), "Out3/4": (0x4F, 0x03), "Out5/6": (0x4F, 0x05),
    "FX1in": (0x4F, 0x0D), "FX2in": (0x4F, 0x0E),
    "FX3in": (0x4F, 0x0F), "FX4in": (0x4F, 0x10),
    "DCA1": (0x4F, 0x20), "DCA2": (0x4F, 0x21),
    "DCA3": (0x4F, 0x22), "DCA4": (0x4F, 0x23),
}

# ---------------------------------------------------------------------------
# Pan / balance parameters -- sources to Main LR (PDF p.18, 0x50 block)
# ---------------------------------------------------------------------------
PAN_LR_PARAMS: dict[str, tuple[int, int]] = {}
for _i in range(16):                       # Ip1..Ip16 -> LR : 0x50 0x00..0x0F
    PAN_LR_PARAMS[f"Ip{_i + 1}"] = (0x50, 0x00 + _i)
PAN_LR_PARAMS.update({
    "St1": (0x50, 0x18), "St2": (0x50, 0x1A),
    "USB": (0x50, 0x1C), "BT": (0x50, 0x1E),
    "FX1": (0x50, 0x3C), "FX2": (0x50, 0x3D),
    "FX3": (0x50, 0x3E), "FX4": (0x50, 0x3F),
})

# ---------------------------------------------------------------------------
# Example Level Values (PDF p.15)  dB -> (coarse VC, fine VF)
# `None` dB represents -inf (fully off).  The device encodes a level as a
# 14-bit code == VC * 128 + VF.
# ---------------------------------------------------------------------------
LEVEL_TAPER: list[tuple[float | None, int, int]] = [
    (None, 0x00, 0x00),                    # -inf
    (-89, 0x01, 0x40), (-85, 0x02, 0x00), (-80, 0x02, 0x40),
    (-75, 0x03, 0x40), (-70, 0x04, 0x00), (-65, 0x05, 0x00),
    (-60, 0x06, 0x00), (-55, 0x07, 0x00), (-50, 0x08, 0x00),
    (-45, 0x0C, 0x00), (-40, 0x0F, 0x40), (-38, 0x12, 0x40),
    (-36, 0x15, 0x40), (-35, 0x17, 0x00), (-34, 0x19, 0x00),
    (-33, 0x1A, 0x40), (-32, 0x1C, 0x00), (-31, 0x1D, 0x40),
    (-30, 0x1F, 0x00), (-29, 0x20, 0x40), (-28, 0x22, 0x00),
    (-27, 0x23, 0x40), (-26, 0x25, 0x00), (-25, 0x26, 0x40),
    (-24, 0x28, 0x40), (-23, 0x2A, 0x00), (-22, 0x2B, 0x40),
    (-21, 0x2D, 0x00), (-20, 0x2E, 0x40), (-19, 0x30, 0x00),
    (-18, 0x31, 0x40), (-17, 0x33, 0x00), (-16, 0x34, 0x40),
    (-15, 0x36, 0x00), (-14, 0x38, 0x00), (-13, 0x39, 0x40),
    (-12, 0x3B, 0x00), (-11, 0x3C, 0x40), (-10, 0x3E, 0x00),
    (-9, 0x41, 0x40), (-8, 0x44, 0x40), (-7, 0x48, 0x00),
    (-6, 0x4B, 0x00), (-5, 0x4E, 0x40), (-4, 0x52, 0x40),
    (-3, 0x56, 0x40), (-2, 0x5A, 0x00), (-1, 0x5E, 0x00),
    (0, 0x62, 0x00), (1, 0x65, 0x40), (2, 0x69, 0x00),
    (3, 0x6C, 0x40), (4, 0x70, 0x00), (5, 0x73, 0x40),
    (6, 0x75, 0x40), (7, 0x78, 0x00), (8, 0x7A, 0x40),
    (9, 0x7D, 0x00), (10, 0x7F, 0x40),
]

# Highest / lowest encodable level codes (code == VC*128 + VF).
LEVEL_CODE_MIN = 0x00 * 128 + 0x00         # 0      (-inf)
LEVEL_CODE_0DB = 0x62 * 128 + 0x00         # 12544  (0 dB)
LEVEL_CODE_MAX = 0x7F * 128 + 0x40         # 16320  (+10 dB)

# ---------------------------------------------------------------------------
# Example Pan/Balance Values (PDF p.15)  position% -> (VC, VF)
# position runs -100 (full left) .. 0 (centre) .. +100 (full right).
# Pan is a 14-bit code: L100% = 0, centre = 0x40*128 = 8192, R100% = 16383.
# ---------------------------------------------------------------------------
PAN_TAPER: list[tuple[int, int, int]] = [
    (-100, 0x00, 0x00), (-90, 0x06, 0x33), (-80, 0x0C, 0x66),
    (-70, 0x13, 0x19), (-60, 0x19, 0x4C), (-50, 0x1F, 0x7F),
    (-40, 0x26, 0x32), (-30, 0x2C, 0x65), (-20, 0x33, 0x18),
    (-15, 0x36, 0x32), (-10, 0x39, 0x4B), (-5, 0x3C, 0x65),
    (0, 0x40, 0x00), (5, 0x43, 0x18), (10, 0x46, 0x32),
    (15, 0x49, 0x4B), (20, 0x4C, 0x65), (30, 0x53, 0x18),
    (40, 0x59, 0x4B), (50, 0x5F, 0x7F), (60, 0x66, 0x32),
    (70, 0x6C, 0x65), (80, 0x73, 0x18), (90, 0x79, 0x4B),
    (100, 0x7F, 0x7F),
]

PAN_CODE_MIN = 0x00 * 128 + 0x00           # 0      (L100%)
PAN_CODE_CTR = 0x40 * 128 + 0x00           # 8192   (centre)
PAN_CODE_MAX = 0x7F * 128 + 0x7F           # 16383  (R100%)

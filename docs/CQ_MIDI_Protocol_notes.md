# CQ-12T MIDI Protocol — working notes

Condensed from **Allen & Heath "CQ MIDI Protocol, Firmware V1.2.0, Issue 4"
(© 2024 Allen & Heath)**. This is the human-readable companion to
`oxi_cq_bridge/cq_params.py` (the data) and `oxi_cq_bridge/cq_protocol.py`
(the encoders). Hex values are shown without the `0x` prefix, matching the
original document. Every example below is also asserted in
`tests/test_1_mechanism.py`.

## Transport & channel

- The CQ speaks MIDI over **USB-B** (appears as a MIDI in/out device) and
  over **Ethernet** as **MIDI-over-TCP/IP** on **port 51325** — only **one**
  TCP/IP MIDI client at a time.
- All control messaging is on **MIDI Channel 1** (CC status byte `B0`).
- The CQ also *transmits* NRPN messages when its own controls move, so its
  output can be recorded and replayed (and, in future, read back for UI sync).

## Message building blocks (CC numbers on channel 1)

| Purpose | CC (hex) | CC (dec) |
|---|---|---|
| Bank Select MSB (scenes) | `00` | 0 |
| NRPN parameter MSB | `63` | 99 |
| NRPN parameter LSB | `62` | 98 |
| Data Entry MSB (coarse) | `06` | 6 |
| Data Entry LSB (fine) | `26` | 38 |
| Data Increment | `60` | 96 |
| Data Decrement | `61` | 97 |

An NRPN "parameter number" is a **(MSB, LSB)** pair of 7-bit bytes. They
combine in **base-128**: `combined = MSB*128 + LSB` (this matters when
walking the send matrices — see `_join14`/`_split14` in `cq_params.py`).

## Actions

### Scene recall (§2.1)
`B0 00 00` (bank 0) then `C0 PG` (program change). `PG = scene − 1`
(MIDI counts from 0). E.g. scene 7 → `B0 00 00 C0 06`.

### Soft keys (§2.2)
Note On `90 SK 7F` = press, Note Off `80 SK 00` = release.
`SK = 0x30 + (softkey − 1)` (soft key 1 = C3 = note `30`).

### Mutes (§2.3)
- On:  `B0 63 MB  B0 62 LB  B0 06 00  B0 26 01`
- Off: `B0 63 MB  B0 62 LB  B0 06 00  B0 26 00`
- Toggle: `B0 63 MB  B0 62 LB  B0 60 00` (data increment toggles mute state)

### Levels (§2.4)
- Absolute: `B0 63 MB  B0 62 LB  B0 06 VC  B0 26 VF` — `VC`/`VF` from the
  taper table (`LEVEL_TAPER`). A level is a 14-bit code `VC*128 + VF`
  (−inf = `00 00`, 0 dB = `62 00`, +10 dB = `7F 40`).
- Relative ±1 dB: `B0 63 MB  B0 62 LB  B0 60 00` (up) / `… B0 61 00` (down).

### Pan / balance (§2.5)
- Absolute: same shape as a level; `00 00` = L100 %, `40 00` = centre,
  `7F 7F` = R100 % (`PAN_TAPER`).
- Relative: increment = right one step, decrement = left one step.

### Get a value (§2.6)
`B0 63 MB  B0 62 LB  B0 60 7F` — increment message with data `7F` asks the
CQ to transmit the current value of that parameter.

## Parameter number tables (see `cq_params.py` for the full set)

**Mutes** (`MUTE_PARAMS`): Ip1–Ip16 = `00 00`…`00 0F`; St1 `00 18`,
St2 `00 1A`, USB `00 1C`, BT `00 1E`; Main LR `00 44`; Out1–Out6
`00 45`…`00 4A`; FX1–FX4 `00 51`…`00 54`; DCA1–4 `02 00`…`02 03`;
MuteGroup1–4 `04 00`…`04 03`.

**Levels — sends** (`LEVEL_SEND_PARAMS`, keyed `"SRC>DST"`): inputs/stereo/
USB/BT/FX to Main LR (`40 xx`), the full input→Out1..Out6 matrix, the
input→FX1..FX4 matrix, and the stereo/USB/BT/FX send rows. Stereo-pair
aliases (`>Out1/2`, `>Out3/4`, `>Out5/6`) map to the odd output of the pair.

**Levels — masters** (`LEVEL_MASTER_PARAMS`): Main LR `4F 00`; Out1–6
`4F 01`…`4F 06`; FX1–4 input `4F 0D`…`4F 10`; DCA1–4 `4F 20`…`4F 23`.

**Pan to LR** (`PAN_LR_PARAMS`): Ip1–16 `50 00`…`50 0F`; St1 `50 18`,
St2 `50 1A`, USB `50 1C`, BT `50 1E`; FX1–4 `50 3C`…`50 3F`.

## Caveats (from A&H docs / community)

- Mute **toggle** cannot be used with DCA mutes or Mute-Group mutes
  (use absolute on/off there).
- Some soft-key functions (transport-style Prev/Stop/Start/Pause/Next) can't
  be driven by note messages.
- Blank (unsaved) scenes can't be recalled.
- Octave labelling differs between tools: if C3 doesn't hit soft key 1, try
  C2 / C4 (the raw note numbers here are authoritative).
- For pan to linked stereo outputs, set "Follow Main LR Pan" to Off on the CQ.

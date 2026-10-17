# OXI-CQ Bridge

**Remote-control an Allen & Heath CQ-12T digital mixer from an OXI
Instruments E16 rotary MIDI controller.**

This is a Mantra Productions application built to replace the vendor
"OCQ‑12T" style control app on macOS. Turn an E16 encoder or press it, and
the bridge translates that movement into the exact MIDI/NRPN message the
CQ‑12T understands — over USB MIDI or MIDI‑over‑TCP/IP — while a local web
UI mirrors the whole control surface live.

```
 ██████  ██   ██ ██       ██████  ██████
██    ██  ██ ██  ██      ██      ██    ██
██    ██   ███   ██ █████ ██     ██    ██
██    ██  ██ ██  ██      ██      ██ ▄▄ ██
 ██████  ██   ██ ██       ██████  ██████
            OXI E16  →  Allen & Heath CQ-12T
```

---

## 1. What it is, in one paragraph

The E16 is a pure MIDI controller (16 push‑and‑turn endless encoders, CC /
NRPN / 14‑bit, up to 12 pages). The CQ‑12T accepts remote control as MIDI on
channel 1: **NRPN** for levels, pans and mutes; **Program Change** for scene
recall; **Note On/Off** for soft keys. The bridge sits in the middle: it
reads the E16, looks up what each control should do in a **mapping** (plain
JSON), and emits the precise bytes to the mixer. A built‑in, dependency‑free
web UI shows every encoder, its value, and a live log of the MIDI being
sent — and doubles as an on‑screen "virtual E16" so you can test the whole
chain with no hardware at all.

## 2. Quick start

```bash
# Dry run — no mixer, no controller needed. Opens a virtual E16 in the browser.
./run.sh
#   → web UI at http://127.0.0.1:8753/   (press Q then Enter to quit)

# Talk to a real CQ-12T over the network (find its IP on the mixer: Setup → Network)
./run.sh --output tcp:192.168.1.60

# Real E16 over USB + real CQ-12T over USB (needs the optional hardware deps, §8)
./run.sh --input usb --output usb

# See available real MIDI ports
./run.sh --list-ports
```

The **core app needs only `python3`** (bundled with macOS Command Line
Tools). No pip install, no framework. Real USB/Bluetooth MIDI is the only
optional extra — see §8.

## 3. How it works (data flow)

```
   OXI E16  ──MIDI(CC/Note)──▶  MidiInput ──▶  MappingEngine ──▶  cq_protocol
 (hardware)                     (midi_io)       (mapping.py)      (exact bytes)
                                                     │                  │
                                                     ▼                  ▼
                                        Bridge records + publishes   MidiOutput
                                           to the web UI (SSE)       (USB / TCP)
                                                                         │
                                                                         ▼
                                                                  Allen & Heath
                                                                     CQ-12T
```

1. **Input** — a `MidiInput` backend emits a normalised `MidiEvent`
   (`type`, `channel`, `data1`, `data2`). Backends: `SimulatedInput`
   (virtual E16 / tests) or `MidoInput` (real USB/BT port).
2. **Mapping** — `MappingEngine.process(event)` does a single dict lookup
   keyed by `(type, channel, data1)` and returns a `Dispatch` describing the
   action and the MIDI messages to send. Pure, no I/O.
3. **Protocol** — `cq_protocol` builds the exact NRPN / PC / Note byte
   sequences from the CQ MIDI Protocol tables in `cq_params`.
4. **Output** — a `MidiOutput` backend writes the bytes: `TcpMidiOutput`
   (MIDI‑over‑TCP to port 51325), `MidoOutput` (real USB port), or
   `CaptureOutput` (dry run — records instead of sending).
5. **Bridge + UI** — `Bridge` forwards the bytes, records each dispatch, and
   publishes it over Server‑Sent Events to the web UI.

## 4. Project layout

```
oxi_e16_cq-12t/
├── run.sh                      Launcher  (python3 -m oxi_cq_bridge)
├── run_tests.sh                Runs the Four Tests (stdlib unittest)
├── requirements.txt            Core deps: NONE (stdlib only)
├── requirements-hardware.txt   Optional: mido + python-rtmidi for real MIDI
├── mappings/
│   └── default_oxi_e16.json    Factory mapping (generated, see §6)
├── docs/
│   └── CQ_MIDI_Protocol_notes.md   Human-readable protocol reference
├── oxi_cq_bridge/
│   ├── __init__.py             Version / app name constants
│   ├── __main__.py             CLI: args, launch, open browser, Q-to-quit
│   ├── banner.py               Block-ASCII startup banner (palette, no red)
│   ├── cq_params.py            CQ-12T reference tables (data only)
│   ├── cq_protocol.py          Encoders: intent → exact MIDI bytes
│   ├── midi_io.py              Input/output backends + MidiEvent
│   ├── mapping.py              MappingEngine: event → action (pure)
│   ├── default_mapping.py      Builder for the factory mapping JSON
│   ├── bridge.py               Runtime: wires in→map→out, records, pub/sub
│   ├── server.py               Stdlib http.server web UI + SSE + inject API
│   └── web/index.html          Single-page UI (inline CSS/JS, no CDN)
└── tests/
    ├── test_1_mechanism.py     Protocol bytes vs the A&H PDF's own examples
    ├── test_2_realthing.py     End-to-end sim→bridge→capture, + HTTP server
    ├── test_3_edges.py         Clamping, bad input, relative, mute semantics
    └── test_4_upgrade.py       Mapping schema/version compatibility contract
```

### Module details (so an agent can take over without reading all the code)

- **`cq_params.py`** — *data only*. Dictionaries of NRPN parameter numbers
  `(MSB, LSB)` for mutes, level sends, level masters and pans, plus the dB
  **level taper** and **pan taper** tables, all transcribed from the A&H
  PDF. NRPN parameter numbers combine in base‑128 (`_join14`/`_split14`);
  the big input→output and input→FX send matrices are generated from their
  documented base + stride so they can't drift by hand. Every value is
  pinned by a test.
- **`cq_protocol.py`** — pure functions. Low‑level `nrpn_set/increment/
  decrement/get`; value conversions `db_to_vcvf`, `fraction_to_level_vcvf`
  (linear‑in‑code audio‑taper fader), `position_to_pan_vcvf`; name‑aware
  actions `mute`, `mute_toggle`, `level_db`, `level_fraction`, `level_step`,
  `pan_*`, `scene_recall`, `softkey_*`. A "message" is a tuple of raw bytes;
  `flatten()` makes the on‑wire stream, `describe()` makes the UI hex.
- **`midi_io.py`** — `MidiEvent` (normalised input) and the backends.
  `mido`/`python-rtmidi` are imported lazily so nothing breaks on a machine
  that only uses the simulator/TCP/capture paths. `TcpMidiOutput` is
  self‑healing (a dropped socket reconnects on the next send). `TeeOutput`
  fans out to several outputs.
- **`mapping.py`** — `MappingEngine` compiles the JSON into a flat
  `(type, channel, number) → Binding` lookup and a `(page, slot, role) →
  Binding` index for the UI. `process()` returns a `Dispatch` (messages +
  display update). Relative‑encoder encodings decoded in `decode_relative`.
  Loud validation: unknown actions/targets and event conflicts raise
  `MappingError` at load time, never silently at runtime.
- **`bridge.py`** — owns engine + input + output. `handle_event` translates,
  sends (tolerating output errors), records to a ring buffer, and publishes
  to SSE subscribers. `make_input` / `make_output` parse the `--input` /
  `--output` spec strings.
- **`server.py`** — stdlib `ThreadingHTTPServer`. Routes: `/` (UI),
  `/api/state`, `/api/ports`, `/api/stream` (SSE), `POST /api/inject`.
- **`web/index.html`** — one file, no external libraries. Renders page tabs
  and a 16‑tile grid; sliders/buttons POST to `/api/inject`; the SSE feed
  updates meters, mute state and the live log.

## 5. The factory mapping (what each E16 control does out of the box)

The E16 has 12 pages of 16 encoders. The default mapping uses **6 pages**,
each transmitting on **its own MIDI channel** (page *N* → channel *N*):

| Page (ch) | Name | Encoder **turn** | Encoder **push** |
|---|---|---|---|
| 1 | Input Faders → Main LR | Ch1–12, ST1, ST2, USB, BT **level to Main LR** | **mute** that input |
| 2 | Input Pan → Main LR | same 16 sources **pan** | **mute** that input |
| 3 | Output / FX / DCA Masters | Main LR, Out1–6, FX1–4 return, DCA1–4 **master level** (slot 16: scene scrub) | **mute** that master (slot 16: soft key 1) |
| 4 | Input Sends → FX1 | Ch1–12/ST/USB/BT **send level to FX1** | **mute** that source |
| 5 | Input Sends → FX2 | same **send level to FX2** | **mute** that source |
| 6 | Scenes & Soft Keys | slot 1: scene scrub | slots 1–8: recall scenes 1–8; slots 9–16: soft keys 1–8 (momentary) |

Everything the CQ exposes over MIDI is reachable: the mapping is just JSON
and the protocol layer already knows the full send matrix (inputs→outputs,
inputs→FX, FX→FX, stereo/USB/BT sends), so adding pages (e.g. sends to
Out 1–6, FX3/FX4 sends, pans to outputs) is editing data, not code.

## 6. Setting up the E16

Configure the E16 so each page transmits what the mapping expects
(all of this is remappable — see §7):

- **Page *N* transmits on MIDI channel *N*.**
- For physical encoder **slot S** (1–16):
  - **turn →** CC **`15 + S`** (so CC16…CC31), absolute mode (0–127);
  - **push →** Note **`35 + S`** (so Note36…Note51).
- Relative encoders are supported too — set `relative_mode` in the mapping
  (`twos_complement` | `binary_offset` | `signed_bit`) to match the E16, and
  set a turn binding's `mode` to `"relative"`.

If your E16 preset emits different CC/note/channel numbers, either change the
preset or edit the mapping to match — the bridge adapts to either.

## 7. Customising the mapping

The mapping is a JSON document (schema_version 1). Minimal shape:

```json
{
  "schema_version": 1,
  "name": "My mapping",
  "input_channel": 1,
  "relative_mode": "twos_complement",
  "scene_count": 128,
  "pages": [
    {
      "index": 0, "channel": 1, "name": "Faders",
      "controls": [
        { "slot": 1, "label": "Ch 1",
          "turn": { "cc": 16, "mode": "absolute", "action": "level", "target": "Ip1>LR" },
          "push": { "note": 36, "action": "mute", "target": "Ip1", "behavior": "toggle" } }
      ]
    }
  ]
}
```

- **actions**: `level`, `pan`, `mute`, `scene`, `softkey`.
- **level/pan targets**: names from the tables in `cq_params.py`, e.g.
  `Ip1>LR`, `Ip3>Out2`, `Ip5>FX1`, `MainLR`, `Out3`, `FX2in`, `DCA1`
  (level); `Ip1`, `St1`, `FX2` (pan to LR).
- **mute targets**: `Ip1`…`Ip16`, `St1`, `St2`, `USB`, `BT`, `MainLR`,
  `Out1`…`Out6`, `FX1`…`FX4`, `DCA1`…`DCA4`, `MGRP1`…`MGRP4`.
- **modes**: level/pan `absolute` (0–127 → full throw) or `relative`
  (±1 dB / 1 step); mute `toggle` (default), `momentary`, `on`, `off`;
  softkey `momentary` (default) or `tap`; scene `absolute`, `relative`, or
  fixed (give a `note` + numeric `target`).
- **options**: `invert` (reverse a turn), per‑page or per‑control `channel`.

Run `python -m oxi_cq_bridge --mapping path/to/your.json`. Regenerate the
factory file after editing the builder with:

```bash
python -m oxi_cq_bridge.default_mapping > mappings/default_oxi_e16.json
```

## 8. Dependencies

- **Core / dry run / TCP / tests:** Python 3.9+ standard library only.
- **Real USB / Bluetooth MIDI** (`--input usb`, `--output usb`): optional —
  `pip install -r requirements-hardware.txt` (`mido`, `python-rtmidi`). On
  macOS these talk to CoreMIDI; the E16 is class‑compliant so it appears as
  a MIDI port automatically. (The CQ also appears as a USB MIDI device.)

## 9. Testing

```bash
./run_tests.sh          # or: python3 -m unittest discover -s tests
```

Built to the Mantra "Four Tests of building":

1. **The mechanism alone** (`test_1_mechanism.py`) — every protocol builder
   is checked byte‑for‑byte against the worked examples printed in the A&H
   CQ MIDI Protocol PDF, plus every taper/pan breakpoint.
2. **The real thing, once** (`test_2_realthing.py`) — the whole pipeline
   (factory mapping → simulated E16 → Bridge → capture, and the live HTTP
   server + inject API) produces exactly the expected MIDI.
3. **The ugly edge cases** (`test_3_edges.py`) — value clamping, out‑of‑range
   rejection, all three relative encodings, mute toggle/momentary semantics,
   partial output failure.
4. **The upgrade path** (`test_4_upgrade.py`) — the mapping schema contract:
   version gating, forward‑compatible unknown fields, defaults, and that the
   shipped factory JSON still matches its builder.

Current status: **47 tests, all passing.**

## 10. What has NOT been tested (unproven work — Mantra rule 24)

Honesty about the edges:

- **No real hardware in the loop.** Development and all tests ran on Linux
  with the simulator and capture sink. The outgoing bytes are verified
  against Allen & Heath's own documented examples, but the bridge has **not**
  yet been run against a physical CQ‑12T or a physical E16. First real‑world
  step: `--output tcp:<mixer-ip>` and watch the mixer respond.
- **Output only / no feedback sync.** The bridge sends to the mixer; it does
  not yet read the CQ's return MIDI, so the UI's mute/level indicators are
  optimistic (they assume the send succeeded) and can drift if the console is
  also touched by hand. The protocol layer already implements the `get`
  command (`cq_protocol.get_*`) and `TcpMidiOutput` is structured for duplex,
  so adding read‑back is the natural next increment.
- **E16 transmit profile assumed.** The factory mapping assumes the E16 is
  configured per §6. A ready‑made E16 preset file is not included (it would
  need the OXI app's format); the mapping adapts to whatever the E16 sends.
- **Relative‑encoder default.** `relative_mode` defaults to
  `twos_complement`; the exact encoding the E16 emits in relative mode was
  not confirmed on hardware. The default mapping uses absolute encoders, so
  this only matters if you switch a control to `"mode": "relative"`.
- **MIDI‑over‑TCP framing** is raw MIDI bytes on the stream, matching the A&H
  "all other clients … port 51325" guidance; it has not been confirmed
  against the mixer's TCP handshake expectations on real hardware.

## 11. References

- Allen & Heath — *CQ MIDI Protocol, Firmware V1.2.0, Issue 4* (2024).
  Condensed in `docs/CQ_MIDI_Protocol_notes.md`.
- OXI Instruments — E16 rotary MIDI controller.

---
*Mantra Productions · palette: white / orange / dark‑yellow.*

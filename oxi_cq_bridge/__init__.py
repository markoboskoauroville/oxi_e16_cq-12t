"""
OXI-CQ Bridge
=============

A remote-control bridge that maps the controls of an OXI Instruments E16
rotary MIDI controller onto an Allen & Heath CQ-12T digital mixer.

This replaces the vendor "OCQ-12T" style control app: turn the E16 encoders
and the bridge translates each movement/press into the exact MIDI/NRPN
messages the CQ-12T understands, sent over USB MIDI or MIDI-over-TCP.

The package is dependency-free for its core, protocol, mapping, simulator,
network output and web UI (Python standard library only).  Real USB MIDI
hardware I/O uses the optional `python-rtmidi`/`mido` packages, imported
lazily only when a real MIDI port is opened.

Built to the Mantra Productions standards (block banner, calming
white/orange/dark-yellow palette, comprehensive handover README).
"""

__version__ = "1.0.0"
__app_name__ = "OXI-CQ Bridge"
__target__ = "Allen & Heath CQ-12T"
__controller__ = "OXI Instruments E16"

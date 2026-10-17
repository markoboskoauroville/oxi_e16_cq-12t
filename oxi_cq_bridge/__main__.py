"""
Command-line entry point:  python -m oxi_cq_bridge [options]

Starts the OXI E16 -> CQ-12T bridge and its local web UI, opens the UI in
the browser, then waits.  Press 'Q' then Enter (or Ctrl-C) to quit.

Examples
--------
  python -m oxi_cq_bridge                      # dry run: on-screen virtual E16
  python -m oxi_cq_bridge --output tcp:192.168.1.60
  python -m oxi_cq_bridge --input usb --output usb   # real E16 + real CQ (USB)
  python -m oxi_cq_bridge --list-ports
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .banner import banner
from .bridge import Bridge, make_input, make_output
from .mapping import MappingEngine
from .midi_io import list_ports
from .server import serve_in_thread

DEFAULT_MAPPING = Path(__file__).parent.parent / "mappings" / "default_oxi_e16.json"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="oxi_cq_bridge",
                                description="OXI E16 -> Allen & Heath CQ-12T remote bridge")
    p.add_argument("--mapping", default=str(DEFAULT_MAPPING),
                   help="path to the mapping JSON (default: factory mapping)")
    p.add_argument("--output", default="dry",
                   help="mixer output: dry | tcp:HOST[:PORT] | usb[:NAME] (default dry)")
    p.add_argument("--input", default="sim",
                   help="controller input: sim | usb[:NAME] (default sim)")
    p.add_argument("--host", default="127.0.0.1", help="web UI host")
    p.add_argument("--port", type=int, default=8753, help="web UI port")
    p.add_argument("--no-browser", action="store_true",
                   help="do not open the web UI automatically")
    p.add_argument("--no-color", action="store_true", help="plain banner")
    p.add_argument("--list-ports", action="store_true",
                   help="list real MIDI ports and exit")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.list_ports:
        ports = list_ports()
        if ports.get("error"):
            print("Real MIDI ports unavailable:", ports["error"])
            print("Install hardware support with:  pip install mido python-rtmidi")
            return 0
        print("MIDI inputs :", ports["inputs"] or "(none)")
        print("MIDI outputs:", ports["outputs"] or "(none)")
        return 0

    print(banner(use_color=not args.no_color, version=__version__,
                 subtitle="Mantra Productions"))
    print()

    try:
        engine = MappingEngine.from_file(args.mapping)
    except Exception as exc:                                # noqa: BLE001
        print(f"[!] Could not load mapping {args.mapping}: {exc}")
        return 2

    try:
        midi_in = make_input(args.input)
        midi_out = make_output(args.output)
    except Exception as exc:                                # noqa: BLE001
        print(f"[!] I/O setup failed: {exc}")
        return 2

    bridge = Bridge(engine, midi_in, midi_out)
    try:
        bridge.start()
    except Exception as exc:                                # noqa: BLE001
        print(f"[!] Could not open MIDI input {midi_in.name!r}: {exc}")
        print("    Tip: run with --list-ports, or use --input sim for a dry run.")
        return 2

    httpd, _thread = serve_in_thread(bridge, args.host, args.port)
    url = f"http://{args.host}:{args.port}/"

    print(f"  mapping : {engine.name}")
    print(f"  input   : {midi_in.name}")
    print(f"  output  : {midi_out.name}")
    print(f"  web UI  : {url}")
    print("\n  Press 'Q' then Enter (or Ctrl-C) to quit.\n")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:                                   # noqa: BLE001
            pass

    exit_code = 0
    try:
        _wait_for_quit()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Shutting down...")
        httpd.shutdown()
        bridge.stop()
    return exit_code


def _wait_for_quit() -> None:
    """Block until the user asks to quit ('Q') or stdin closes."""
    while True:
        try:
            line = input()
        except EOFError:
            # non-interactive (piped/headless): park until Ctrl-C
            import threading
            threading.Event().wait()
            return
        if line.strip().lower() in ("q", "quit", "exit"):
            return


if __name__ == "__main__":
    raise SystemExit(main())

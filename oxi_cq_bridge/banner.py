"""
banner.py -- thick block-style startup banner (Mantra rule 6) in the house
palette (white / orange / dark-yellow, never red -- Mantra rule 40).
"""

from __future__ import annotations

# 256-colour ANSI: 208 = orange, 178 = dark yellow/gold, 231 = white.
_ORANGE = "\033[38;5;208m"
_GOLD = "\033[38;5;178m"
_WHITE = "\033[38;5;231m"
_RESET = "\033[0m"
_DIM = "\033[2m"

_BLOCK = r"""
 ██████  ██   ██ ██       ██████  ██████
██    ██  ██ ██  ██      ██      ██    ██
██    ██   ███   ██ █████ ██     ██    ██
██    ██  ██ ██  ██      ██      ██ ▄▄ ██
 ██████  ██   ██ ██       ██████  ██████
                                     ▀▀
"""


def banner(use_color: bool = True, version: str = "", subtitle: str = "") -> str:
    art = _BLOCK.strip("\n")
    if use_color:
        art = f"{_ORANGE}{art}{_RESET}"
    lines = [art]
    tag = "OXI E16  ->  Allen & Heath CQ-12T  remote bridge"
    if use_color:
        tag = f"{_GOLD}{tag}{_RESET}"
    lines.append("  " + tag)
    if version or subtitle:
        meta = f"  v{version}   {subtitle}".rstrip()
        if use_color:
            meta = f"{_DIM}{_WHITE}{meta}{_RESET}"
        lines.append(meta)
    return "\n".join(lines)


if __name__ == "__main__":
    print(banner(version="1.0.0", subtitle="Mantra Productions"))

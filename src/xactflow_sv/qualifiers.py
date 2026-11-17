"""qualifiers.py: find the clock and reset ports of a module, for their isClock/isReset qualifier.

A port is a clock or a reset either because the metadata file says so ("clocks"/"resets"),
or because its name is a usual clock/reset name. The metadata file is the source of truth: when
it has "clocks", only the ports it lists are clocks and no clock is detected from its name, and
likewise for "resets".
"""

from __future__ import annotations

import re
from typing import Optional

import ipxact

# Any "<prefix>_" before the name, e.g. "sys_clk_i", and a replica suffix after it, an uppercase
# letter or a number, e.g. "clk_iA"/"clk_iB"/"clk_iC" for a TMR design. The suffix is
# case-sensitive so that "rst_n" is not read as "rst" plus a replica "_n".
_PREFIX = r"(?:\w+_)?"
_REPLICA = r"(?:_?(?:[A-Z]|[0-9]+))?"

# clk_i, clk, clock, clock_i.
_CLOCK_NAME = re.compile(rf"{_PREFIX}(?i:clk|clock)(?i:_i)?{_REPLICA}")
# arst_ni, arst_n, rst_n, rst_ni, reset_n, reset_ni. Checked before _RESET_HIGH_NAME.
_RESET_LOW_NAME = re.compile(rf"{_PREFIX}(?i:a?rst|reset)(?i:_ni?){_REPLICA}")
# arst_i, arst, rst, rst_i, reset, reset_i.
_RESET_HIGH_NAME = re.compile(rf"{_PREFIX}(?i:a?rst|reset)(?i:_i)?{_REPLICA}")

_RESET_LEVELS = ("low", "high")


def _detect(name: str) -> Optional[tuple[str, Optional[str]]]:
    """Return ("clock", None) or ("reset", level) when `name` is a usual clock/reset name."""
    if _CLOCK_NAME.fullmatch(name):
        return "clock", None
    if _RESET_LOW_NAME.fullmatch(name):
        return "reset", "low"
    if _RESET_HIGH_NAME.fullmatch(name):
        return "reset", "high"
    return None


def _is_wire(port: dict) -> bool:
    return not port["is_interface"] and not port["is_struct"]


def _metadata_clocks(clocks: object) -> list[str]:
    if not (isinstance(clocks, list) and all(isinstance(name, str) for name in clocks)):
        raise ValueError(f"metadata file 'clocks' must be a list of port names, got: {clocks!r}")
    return clocks


def _metadata_resets(resets: object) -> dict[str, str]:
    if not isinstance(resets, dict):
        raise ValueError(f"metadata file 'resets' must map each reset port name to its level, got: {resets!r}")
    levels = {}
    for name, level in resets.items():
        if not (isinstance(level, str) and level.lower() in _RESET_LEVELS):
            raise ValueError(f"metadata file 'resets': port '{name}' has level {level!r} (expected low/high)")
        levels[name] = level.lower()
    return levels


def find_qualifiers(ports: list[dict], clocks: object = None, resets: object = None) -> dict[str, ipxact.Qualifier]:
    """Map each clock/reset port name to its qualifier.

    `clocks` is the metadata file's "clocks", a list of port names, and `resets` its "resets",
    mapping a port name to its active level, "low" or "high". Either one is None when the
    metadata file does not have it. The ports they list must be wire ports, of any direction.

    Without "clocks", the input wire ports with a usual clock name are clocks, and without
    "resets", those with a usual reset name are resets, see _CLOCK_NAME/_RESET_LOW_NAME/
    _RESET_HIGH_NAME. A port listed in the metadata file is never detected from its name.
    """
    clock_names = _metadata_clocks(clocks) if clocks is not None else None
    reset_levels = _metadata_resets(resets) if resets is not None else None

    ports_by_name = {port["name"]: port for port in ports}
    listed = [*(clock_names or []), *(reset_levels or {})]
    for name in listed:
        if name not in ports_by_name:
            raise ValueError(f"metadata file lists '{name}' as a clock/reset, but the module has no such port")
        if not _is_wire(ports_by_name[name]):
            raise ValueError(f"metadata file lists '{name}' as a clock/reset, but it is not a wire port")
    both = sorted(set(clock_names or []) & set(reset_levels or {}))
    if both:
        raise ValueError(f"metadata file lists port(s) {', '.join(both)} as both a clock and a reset")

    qualifiers = {name: ipxact.Qualifier(is_clock=True) for name in clock_names or []}
    for name, level in (reset_levels or {}).items():
        qualifiers[name] = ipxact.Qualifier(is_reset=ipxact.LevelFlag(True, level=level))

    for port in ports:
        name = port["name"]
        if name in listed or port["direction"] != "in" or not _is_wire(port):
            continue
        detected = _detect(name)
        if detected == ("clock", None) and clock_names is None:
            qualifiers[name] = ipxact.Qualifier(is_clock=True)
        elif detected is not None and detected[0] == "reset" and reset_levels is None:
            qualifiers[name] = ipxact.Qualifier(is_reset=ipxact.LevelFlag(True, level=detected[1]))
    return qualifiers

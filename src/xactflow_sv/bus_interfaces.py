"""bus_interfaces.py: build ipxact.BusInterface objects from the metadata file's busInterfaces.

Everything here is derived from the metadata file's "busInterfaces" object, not from parsing
the SystemVerilog itself (see sv_parser.py for that); it is deliberately the only source of
truth for a bus interface's VLNV and mode, with no guessing from port/modport names.
"""

from __future__ import annotations

from typing import Optional

import ipxact

# Accepted values for a metadata busInterfaces[].mode field, compared case-insensitively.
_MODE_MAP = {
    "initiator": ipxact.InterfaceMode.INITIATOR,
    "master": ipxact.InterfaceMode.INITIATOR,
    "target": ipxact.InterfaceMode.TARGET,
    "slave": ipxact.InterfaceMode.TARGET,
    "system": ipxact.InterfaceMode.SYSTEM,
    "mirroredinitiator": ipxact.InterfaceMode.MIRRORED_INITIATOR,
    "mirroredmaster": ipxact.InterfaceMode.MIRRORED_INITIATOR,
    "mirroredtarget": ipxact.InterfaceMode.MIRRORED_TARGET,
    "mirroredslave": ipxact.InterfaceMode.MIRRORED_TARGET,
    "mirroredsystem": ipxact.InterfaceMode.MIRRORED_SYSTEM,
    "monitor": ipxact.InterfaceMode.MONITOR,
}

# Modes whose IP-XACT element requires a "group" (the bus definition's system group).
_GROUP_MODES = (ipxact.InterfaceMode.SYSTEM, ipxact.InterfaceMode.MIRRORED_SYSTEM)

# Accepted keys of a metadata busInterfaces[] entry.
_ENTRY_KEYS = ("bus", "mode", "ports", "interfacePort", "memoryMap", "group", "monitoredMode")


def _mode(value: object) -> Optional[ipxact.InterfaceMode]:
    return _MODE_MAP.get(value.lower()) if isinstance(value, str) else None


def _parse_vlnv(vlnv: object, what: str) -> ipxact.VLNVRef:
    """Parse a 'vendor:library:name:version' string into a VLNVRef."""
    if isinstance(vlnv, str):
        try:
            parsed = ipxact.VLNV.parse(vlnv)
        except ValueError:
            parsed = None
    else:
        parsed = None
    if parsed is None:
        raise ValueError(f"{what} must be a 'vendor:library:name:version' VLNV string, got: {vlnv!r}")
    return ipxact.VLNVRef(vendor=parsed.vendor, library=parsed.library, name=parsed.name, version=parsed.version)


def build_bus_interfaces(bus_interfaces: dict[str, dict], memory_map_names: list[str]) -> list[ipxact.BusInterface]:
    """Build every ipxact.BusInterface from the metadata file's busInterfaces.

    A target interface references the memory map named by its "memoryMap". When the
    component has exactly one target interface and one memory map, a target interface
    without "memoryMap" references that memory map by default.
    """
    targets = [
        name for name, iface in bus_interfaces.items() if _mode(iface.get("mode")) == ipxact.InterfaceMode.TARGET
    ]
    default_memory_map = memory_map_names[0] if len(targets) == 1 and len(memory_map_names) == 1 else None
    return [
        build_bus_interface(name, iface, iface.get("memoryMap") or (default_memory_map if name in targets else None))
        for name, iface in bus_interfaces.items()
    ]


def build_bus_interface(name: str, iface: dict, memory_map_ref: Optional[str] = None) -> ipxact.BusInterface:
    """Build one ipxact.BusInterface from a metadata-file busInterfaces[name] entry.

    Either a discrete-signal mapping:

        <interface-name>: {
            "bus":   "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":  "<mode>",
            "ports": {"<LOGICAL_NAME>": "<physical_port_name>", ...}
        }

    or a reference to a genuine SV `interface`-typed port, which already groups its own
    signals so no port-level mapping is needed:

        <interface-name>: {
            "bus":           "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":          "<mode>",
            "interfacePort": "<physical_port_name>"
        }

    "mode" is one of the _MODE_MAP keys. A "system" or "mirroredSystem" entry also gives its
    "group", and a "monitor" entry its "monitoredMode" (the mode of the monitored
    interface) and, when that is a system mode, its "group". `memory_map_ref` is the memory
    map a target interface references.

    The abstraction definition is derived by convention as "<bus-def-name>_rtl" at the same
    vendor/library/version as "bus". Physical port existence, mapping completeness and the
    mode-specific keys are not checked here, see validate_bus_interfaces.
    """
    mode = _mode(iface.get("mode"))
    if mode is None:
        raise ValueError(f"busInterface '{name}': unknown mode '{iface.get('mode')}' (expected {_modes_text()})")

    bus_type = _parse_vlnv(iface.get("bus"), f"busInterface '{name}' bus")

    abstraction_types = []
    if not iface.get("interfacePort"):
        abstraction_ref = ipxact.VLNVRef(
            vendor=bus_type.vendor,
            library=bus_type.library,
            name=f"{bus_type.name}_rtl",
            version=bus_type.version,
        )
        port_maps = []
        for logical, physical in (iface.get("ports") or {}).items():
            port_name, *sub_path = physical.split(".")
            port_maps.append(
                ipxact.PortMap(
                    logical_port=logical,
                    physical_port=port_name,
                    sub_port_refs=[ipxact.SubPortReference(sub_port_ref=field) for field in sub_path],
                )
            )
        abstraction_types = [ipxact.AbstractionType(abstraction_ref=abstraction_ref, port_maps=port_maps)]

    Mode = ipxact.InterfaceMode
    group = iface.get("group")
    return ipxact.BusInterface(
        name=name,
        bus_type=bus_type,
        mode=mode,
        abstraction_types=abstraction_types,
        initiator=ipxact.InitiatorInterface() if mode == Mode.INITIATOR else None,
        target=ipxact.TargetInterface(memory_map_ref=memory_map_ref) if mode == Mode.TARGET else None,
        system=ipxact.SystemInterface(group=group) if mode == Mode.SYSTEM else None,
        mirrored_initiator=ipxact.MirroredInitiatorInterface() if mode == Mode.MIRRORED_INITIATOR else None,
        mirrored_target=ipxact.MirroredTargetInterface() if mode == Mode.MIRRORED_TARGET else None,
        mirrored_system=ipxact.SystemInterface(group=group) if mode == Mode.MIRRORED_SYSTEM else None,
        monitor=(
            ipxact.MonitorInterface(interface_mode=_mode(iface.get("monitoredMode")), group=group)
            if mode == Mode.MONITOR
            else None
        ),
    )


def _modes_text() -> str:
    return "/".join(_MODE_MAP)


def validate_bus_interfaces(
    bus_interfaces: dict[str, dict], ports: list[dict], memory_map_names: Optional[list[str]] = None
) -> None:
    """Verify the metadata file's busInterfaces against the parsed module.

    - Every entry only uses known keys, and the keys its mode needs: "group" for a system
      mode, "monitoredMode" for a monitor, and "memoryMap" only on a target, naming one of
      `memory_map_names`.
    - Every physical port mapped in a "ports" entry actually exists, and a dotted sub-field
      reference (e.g. 'apb_req_i.psel') only targets a struct/typedef-typed port (the field
      name itself can't be checked without elaboration).
    - Every "interfacePort" reference exists and is a genuine SV `interface`-typed port, and
      an entry doesn't set both "ports" and "interfacePort" (ambiguous, pick one).
    - Every genuine SV `interface`-typed port in the module is referenced by exactly one
      busInterfaces[...].interfacePort. There is no fallback guess for its bus VLNV/mode, so
      an undescribed one is an error, not a silently-placeholder'd component.

    Raises ValueError with every mismatch found, rather than stopping at the first one.
    """
    if not isinstance(bus_interfaces, dict):
        raise ValueError(f"'busInterfaces' must be a {{name: entry}} object, got: {bus_interfaces!r}")

    by_name = {p["name"]: p for p in ports}
    errors = []
    referenced_iface_ports = set()

    for iface_name, iface in bus_interfaces.items():
        if not isinstance(iface, dict):
            errors.append(f"busInterface '{iface_name}': entry must be an object, got: {iface!r}")
            continue

        errors.extend(_entry_key_errors(iface_name, iface, memory_map_names or []))

        interface_port = iface.get("interfacePort")
        port_maps = iface.get("ports") or {}

        if interface_port:
            if port_maps:
                errors.append(
                    f"busInterface '{iface_name}': has both 'interfacePort' and 'ports'. An "
                    "interfacePort already groups its own signals, use only one"
                )
            port = by_name.get(interface_port)
            if port is None:
                errors.append(
                    f"busInterface '{iface_name}': interfacePort '{interface_port}' is not a "
                    "port of this module"
                )
            elif not port["is_interface"]:
                errors.append(
                    f"busInterface '{iface_name}': interfacePort '{interface_port}' is not a "
                    "SystemVerilog interface-typed port"
                )
            elif interface_port in referenced_iface_ports:
                errors.append(
                    f"busInterface '{iface_name}': interfacePort '{interface_port}' is already "
                    "referenced by another busInterfaces entry, each interface-typed port must "
                    "map to exactly one busInterface"
                )
            else:
                referenced_iface_ports.add(interface_port)
            continue

        if not port_maps:
            errors.append(
                f"busInterface '{iface_name}': has neither 'interfacePort' nor 'ports', "
                "nothing is mapped to it"
            )
            continue

        if not isinstance(port_maps, dict):
            errors.append(
                f"busInterface '{iface_name}': 'ports' must be a {{logical name: physical "
                f"name}} object, got: {port_maps!r}"
            )
            continue

        for logical, physical in port_maps.items():
            if not isinstance(physical, str):
                errors.append(
                    f"busInterface '{iface_name}': logical port '{logical}' maps to "
                    f"{physical!r}, which is not a physical port name string"
                )
                continue
            port_name = physical.split(".")[0]
            port = by_name.get(port_name)
            if port is None:
                errors.append(
                    f"busInterface '{iface_name}': logical port '{logical}' maps to "
                    f"'{physical}', which is not a port of this module"
                )
            elif port["is_interface"]:
                errors.append(
                    f"busInterface '{iface_name}': logical port '{logical}' maps to "
                    f"'{physical}', which is a SystemVerilog interface-typed port; describe it "
                    "with \"interfacePort\", not a \"ports\" mapping"
                )
            elif "." in physical and not port["is_struct"]:
                errors.append(
                    f"busInterface '{iface_name}': logical port '{logical}' maps to "
                    f"'{physical}', but '{port_name}' is not a struct/typedef-typed port, it "
                    "has no sub-fields to map into"
                )

    for port in ports:
        if port["is_interface"] and port["name"] not in referenced_iface_ports:
            errors.append(
                f"interface port '{port['name']}' (modport '{port['modport']}') has no "
                "matching busInterfaces entry, add one with "
                f"\"interfacePort\": \"{port['name']}\" in the metadata file"
            )

    if errors:
        raise ValueError("invalid busInterfaces in metadata file:\n  " + "\n  ".join(errors))


def _entry_key_errors(iface_name: str, iface: dict, memory_map_names: list[str]) -> list[str]:
    """Check an entry's keys, and the mode-specific ones ("group", "monitoredMode", "memoryMap")."""
    prefix = f"busInterface '{iface_name}':"
    errors = []

    unknown = [key for key in iface if key not in _ENTRY_KEYS]
    if unknown:
        errors.append(f"{prefix} unknown key(s) {', '.join(unknown)} (expected: {', '.join(_ENTRY_KEYS)})")

    mode = _mode(iface.get("mode"))
    if mode is None:
        return errors  # reported by build_bus_interface, with the accepted modes

    Mode = ipxact.InterfaceMode
    monitored_mode = _mode(iface.get("monitoredMode"))
    if mode == Mode.MONITOR:
        if monitored_mode is None or monitored_mode == Mode.MONITOR:
            errors.append(
                f"{prefix} a monitor needs a 'monitoredMode', the mode of the monitored interface "
                f"(one of {'/'.join(k for k, m in _MODE_MAP.items() if m != Mode.MONITOR)}), got: "
                f"{iface.get('monitoredMode')!r}"
            )
    elif "monitoredMode" in iface:
        errors.append(f"{prefix} 'monitoredMode' is only for a monitor")

    group = iface.get("group")
    needs_group = mode in _GROUP_MODES or (mode == Mode.MONITOR and monitored_mode in _GROUP_MODES)
    if group is not None and not (isinstance(group, str) and group):
        errors.append(f"{prefix} 'group' must be a non-empty string, got: {group!r}")
    elif needs_group and group is None:
        errors.append(f"{prefix} a system interface needs a 'group', the bus definition's system group name")
    elif group is not None and not (needs_group or mode == Mode.MONITOR):
        errors.append(f"{prefix} 'group' is only for a system, mirroredSystem or monitor interface")

    memory_map = iface.get("memoryMap")
    if memory_map is not None:
        if mode != Mode.TARGET:
            errors.append(f"{prefix} 'memoryMap' is only for a target interface")
        elif memory_map not in memory_map_names:
            found = ", ".join(memory_map_names) if memory_map_names else "none, no registerFile is set"
            errors.append(f"{prefix} memoryMap '{memory_map}' is not a memory map of the component (found: {found})")

    return errors

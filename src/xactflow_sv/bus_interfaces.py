"""bus_interfaces.py: build ipxact.BusInterface objects from the metadata file's busInterfaces.

Everything here is derived from the metadata file's "busInterfaces" object, not from parsing
the SystemVerilog itself (see sv_parser.py for that); it is deliberately the only source of
truth for a bus interface's VLNV and mode, with no guessing from port/modport names.

A struct-typed physical port mapped with a dotted "port.field" reference (addressing one
field of the struct) is not supported yet: ipxact-compiler's PortMap has no equivalent of
ipxact:physicalPort/ipxact:subPort to record which field is being referenced.
"""

from __future__ import annotations

import ipxact

# Accepted values for a metadata busInterfaces[].mode field.
_MODE_MAP = {
    "initiator": ipxact.InterfaceMode.INITIATOR,
    "master": ipxact.InterfaceMode.INITIATOR,
    "target": ipxact.InterfaceMode.TARGET,
    "slave": ipxact.InterfaceMode.TARGET,
}


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


def build_bus_interface(name: str, iface: dict) -> ipxact.BusInterface:
    """Build one ipxact.BusInterface from a metadata-file busInterfaces[name] entry.

    Either a discrete-signal mapping:

        <interface-name>: {
            "bus":   "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":  "initiator" | "target" | "master" | "slave",
            "ports": {"<LOGICAL_NAME>": "<physical_port_name>", ...}
        }

    or a reference to a genuine SV `interface`-typed port, which already groups its own
    signals so no port-level mapping is needed:

        <interface-name>: {
            "bus":           "<vendor>:<library>:<bus-def-name>:<version>",
            "mode":          "initiator" | "target" | "master" | "slave",
            "interfacePort": "<physical_port_name>"
        }

    The abstraction definition is derived by convention as "<bus-def-name>_rtl" at the same
    vendor/library/version as "bus". Physical port existence and mapping completeness are
    not checked here, see validate_bus_interfaces.
    """
    mode_key = str(iface.get("mode", "")).lower()
    mode = _MODE_MAP.get(mode_key)
    if mode is None:
        raise ValueError(
            f"busInterface '{name}': unknown mode '{iface.get('mode')}' (expected "
            "initiator/target or master/slave)"
        )

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
            if "." in physical:
                raise NotImplementedError(
                    f"busInterface '{name}': logical port '{logical}' maps to '{physical}', "
                    "a struct-field reference, which is not yet supported"
                )
            port_maps.append(ipxact.PortMap(logical_port=logical, physical_port=physical))
        abstraction_types = [ipxact.AbstractionType(abstraction_ref=abstraction_ref, port_maps=port_maps)]

    return ipxact.BusInterface(
        name=name,
        bus_type=bus_type,
        mode=mode,
        abstraction_types=abstraction_types,
        initiator=ipxact.InitiatorInterface() if mode == ipxact.InterfaceMode.INITIATOR else None,
        target=ipxact.TargetInterface() if mode == ipxact.InterfaceMode.TARGET else None,
    )


def validate_bus_interfaces(bus_interfaces: dict[str, dict], ports: list[dict]) -> None:
    """Verify the metadata file's busInterfaces against the parsed module.

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

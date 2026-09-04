"""component_builder.py: build an ipxact.Component from parsed SV port/parameter dicts.

Constructs ipxact-compiler dataclasses directly, no XML involved. Bus-interface objects
themselves are built by bus_interfaces.py from the metadata file; register-map conversion
from the metadata file's registerFile is a separate, later phase.
"""

from __future__ import annotations

from typing import Optional

import ipxact


def _build_module_parameters(params: list[dict]) -> list[ipxact.ModuleParameter]:
    return [
        ipxact.ModuleParameter(name=p["name"], value=p["value"] or "0", data_type=p["dataType"])
        for p in params
    ]


def _build_parameters(params: list[dict]) -> list[ipxact.Parameter]:
    # Unlike moduleParameter, ipxact:parameter has no dataType field to carry the SV type in.
    return [ipxact.Parameter(name=p["name"], value=p["value"] or "0", resolve="user") for p in params]


def _build_vectors(packed_dims: list[tuple[str, str]]) -> list[ipxact.Vector]:
    return [ipxact.Vector(left=left, right=right) for left, right in packed_dims]


def _build_port(port: dict) -> ipxact.Port:
    if port["unpacked_dims"]:
        raise NotImplementedError(
            f"port '{port['name']}' has unpacked array dimensions, which ipxact-compiler's "
            "Port/WirePort model does not currently represent"
        )

    if port["is_interface"]:
        # No dedicated field for the interface type name/modport, so they go in description.
        return ipxact.Port(
            name=port["name"],
            structured=ipxact.StructuredPort(struct_type="interface", sub_ports=[]),
            description=f"SystemVerilog interface '{port['iface_type']}' (modport '{port['modport']}')",
        )

    if port["is_struct"]:
        # No elaboration, so field layout is unknown; only the type name goes in description.
        return ipxact.Port(
            name=port["name"],
            structured=ipxact.StructuredPort(
                struct_type="struct",
                vectors=_build_vectors(port["packed_dims"]),
                sub_ports=[],
                direction=ipxact.Direction(port["direction"]),
            ),
            description=f"SystemVerilog type '{port['type_name']}'",
        )

    return ipxact.Port(
        name=port["name"],
        wire=ipxact.WirePort(
            direction=ipxact.Direction(port["direction"]), vectors=_build_vectors(port["packed_dims"])
        ),
    )


def build_component(
    module_name: str,
    vendor: str,
    library: str,
    version: str,
    params: list[dict],
    ports: list[dict],
    bus_interfaces: Optional[list[ipxact.BusInterface]] = None,
) -> ipxact.Component:
    instantiation_name = f"{module_name}_rtl"

    model = ipxact.Model(
        views=[
            ipxact.View(
                name="rtl",
                env_identifiers=["::"],
                component_instantiation_ref=instantiation_name,
            )
        ],
        component_instantiations=[
            ipxact.ComponentInstantiation(
                name=instantiation_name,
                module_name=module_name,
                module_parameters=_build_module_parameters(params),
            )
        ],
        ports=[_build_port(port) for port in ports],
    )

    return ipxact.Component(
        vlnv=ipxact.VLNV(vendor, library, module_name, version),
        bus_interfaces=list(bus_interfaces or []),
        model=model,
        parameters=_build_parameters(params),
    )

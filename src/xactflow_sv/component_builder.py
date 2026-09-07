"""component_builder.py: build an ipxact.Component from parsed SV port/parameter dicts.

Constructs ipxact-compiler dataclasses directly, no XML involved. Bus-interface objects
are built by bus_interfaces.py and memory maps by register_map.py, both from the metadata
file; this module assembles them into the final Component.
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


def _build_vectors(dims: list[tuple[str, str]]) -> list[ipxact.Vector]:
    return [ipxact.Vector(left=left, right=right) for left, right in dims]


def _build_arrays(dims: list[tuple[str, str]]) -> list[ipxact.ArrayBound]:
    return [ipxact.ArrayBound(left=left, right=right) for left, right in dims]


def _build_port(port: dict) -> ipxact.Port:
    arrays = _build_arrays(port["unpacked_dims"])

    if port["is_interface"]:
        return ipxact.Port(
            name=port["name"],
            structured=ipxact.StructuredPort(
                struct_type="interface",
                sub_ports=[],
                struct_port_type_defs=[
                    ipxact.StructPortTypeDef(type_name=port["iface_type"], role=port["modport"] or None)
                ],
            ),
            arrays=arrays,
        )

    if port["is_struct"]:
        return ipxact.Port(
            name=port["name"],
            structured=ipxact.StructuredPort(
                struct_type="struct",
                vectors=_build_vectors(port["packed_dims"]),
                sub_ports=[],
                direction=ipxact.Direction(port["direction"]),
                struct_port_type_defs=[ipxact.StructPortTypeDef(type_name=port["type_name"])],
            ),
            arrays=arrays,
        )

    return ipxact.Port(
        name=port["name"],
        wire=ipxact.WirePort(
            direction=ipxact.Direction(port["direction"]), vectors=_build_vectors(port["packed_dims"])
        ),
        arrays=arrays,
    )


def build_component(
    module_name: str,
    vendor: str,
    library: str,
    version: str,
    params: list[dict],
    ports: list[dict],
    bus_interfaces: Optional[list[ipxact.BusInterface]] = None,
    memory_maps: Optional[list[ipxact.MemoryMap]] = None,
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
        memory_maps=list(memory_maps or []),
        model=model,
        parameters=_build_parameters(params),
    )

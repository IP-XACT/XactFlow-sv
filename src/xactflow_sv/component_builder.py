"""component_builder.py: build an ipxact.Component from parsed SV port/parameter dicts.

Constructs ipxact-compiler dataclasses directly, no XML involved. Bus-interface objects
are built by bus_interfaces.py and memory maps by register_map.py, both from the metadata
file; this module assembles them into the final Component.
"""

from __future__ import annotations

from typing import Optional, Union

import ipxact


def _build_module_parameters(params: list[dict]) -> list[ipxact.ModuleParameter]:
    return [
        ipxact.ModuleParameter(
            name=p["name"],
            value=ipxact.Expression(p["name"]),
            data_type=p["dataType"],
            arrays=_build_arrays(p["arrays"]),
        )
        for p in params
    ]


def _build_parameters(params: list[dict]) -> list[ipxact.Parameter]:
    return [
        ipxact.Parameter(
            name=p["name"],
            value=ipxact.Expression(p["value"]),
            resolve="user",
            parameter_id=p["name"],
            arrays=_build_arrays(p["arrays"]),
        )
        for p in params
    ]


def _build_vectors(dims: list[tuple[str, str]]) -> list[ipxact.Vector]:
    return [ipxact.Vector(left=ipxact.Expression(left), right=ipxact.Expression(right)) for left, right in dims]


def _build_arrays(dims: list[tuple[str, str]]) -> list[ipxact.ArrayBound]:
    return [ipxact.ArrayBound(left=ipxact.Expression(left), right=ipxact.Expression(right)) for left, right in dims]


def _build_port(port: dict, qualifier: Optional[ipxact.Qualifier] = None) -> ipxact.Port:
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
            direction=ipxact.Direction(port["direction"]),
            qualifier=qualifier,
            vectors=_build_vectors(port["packed_dims"]),
        ),
        arrays=arrays,
    )


def _reset_types(memory_maps: list[ipxact.MemoryMap]) -> list[ipxact.ResetType]:
    """The reset types the field resets of `memory_maps` refer to, in order of first use. A
    reset that refers to none is of IP-XACT's default reset type, which is not declared.
    """
    names: dict[str, None] = {}

    def add(items: list[Union[ipxact.Register, ipxact.RegisterFile]]) -> None:
        for item in items:
            if isinstance(item, ipxact.RegisterFile):
                add(item.registers)
                continue
            for field in item.fields:
                names.update((reset.reset_type_ref, None) for reset in field.resets if reset.reset_type_ref)

    for memory_map in memory_maps:
        for block in memory_map.items:
            if isinstance(block, ipxact.AddressBlock):
                add(block.registers)
    return [ipxact.ResetType(name=name) for name in names]


def build_component(
    module_name: str,
    vendor: str,
    library: str,
    version: str,
    params: list[dict],
    ports: list[dict],
    bus_interfaces: Optional[list[ipxact.BusInterface]] = None,
    memory_maps: Optional[list[ipxact.MemoryMap]] = None,
    qualifiers: Optional[dict[str, ipxact.Qualifier]] = None,
) -> ipxact.Component:
    instantiation_name = f"{module_name}_rtl"

    model = ipxact.Model(
        views=[
            ipxact.View(
                name="rtl",
                env_identifiers=[ipxact.TextValue("::")],
                component_instantiation_ref=instantiation_name,
            )
        ],
        component_instantiations=[
            ipxact.ComponentInstantiation(
                name=instantiation_name,
                language="systemverilog",
                module_name=module_name,
                module_parameters=_build_module_parameters(params),
            )
        ],
        ports=[_build_port(port, (qualifiers or {}).get(port["name"])) for port in ports],
    )

    return ipxact.Component(
        vlnv=ipxact.VLNV(vendor, library, module_name, version),
        bus_interfaces=list(bus_interfaces or []),
        memory_maps=list(memory_maps or []),
        model=model,
        reset_types=_reset_types(memory_maps or []),
        parameters=_build_parameters(params),
    )

"""component_builder.py: build an ipxact.Component from parsed SV port/parameter dicts.

Mirrors ipxact-sv2ipxact's ipxact_builder.py, but constructs ipxact-compiler dataclasses
directly instead of XML elements. Bus-interface mapping and register-map conversion are
separate, later phases (see the metadata file's busInterfaces/registerFile in the handoff),
so this module only covers a component's VLNV, plain ports, and parameters.
"""

from __future__ import annotations

import ipxact


def _build_module_parameters(params: list[dict]) -> list[ipxact.ModuleParameter]:
    return [
        ipxact.ModuleParameter(name=p["name"], value=p["value"] or "0", data_type=p["dataType"])
        for p in params
    ]


def _build_parameters(params: list[dict]) -> list[ipxact.Parameter]:
    # ipxact:parameter only allows a coarse "type" (bit/byte/int/.../string) that can't
    # losslessly hold an arbitrary SV type string like "logic [3:0]" or "my_pkg::my_t", so
    # unlike moduleParameter, no dataType is carried over here.
    return [ipxact.Parameter(name=p["name"], value=p["value"] or "0", resolve="user") for p in params]


def _build_port(port: dict) -> ipxact.Port:
    if port["is_interface"] or port["is_struct"]:
        type_name = port.get("iface_type") or port.get("type_name")
        raise NotImplementedError(
            f"port '{port['name']}' is struct/interface-typed ({type_name}); "
            "structured/transactional port support is a later phase"
        )

    if port["unpacked_dims"]:
        raise NotImplementedError(
            f"port '{port['name']}' has unpacked array dimensions, which ipxact-compiler's "
            "Port/WirePort model does not currently represent"
        )

    vectors = [ipxact.Vector(left=left, right=right) for left, right in port["packed_dims"]]
    return ipxact.Port(
        name=port["name"],
        wire=ipxact.WirePort(direction=ipxact.Direction(port["direction"]), vectors=vectors),
    )


def build_component(
    module_name: str,
    vendor: str,
    library: str,
    version: str,
    params: list[dict],
    ports: list[dict],
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
        model=model,
        parameters=_build_parameters(params),
    )

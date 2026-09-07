import json
from pathlib import Path

import ipxact
import pytest

from xactflow_sv import SVImporter

FIXTURES = Path(__file__).parent / "fixtures"


def test_discrete_ports_bus_interface_mapping():
    component = SVImporter().import_(
        FIXTURES / "apb_gpio" / "apb_gpio.sv",
        metadata=str(FIXTURES / "apb_gpio" / "apb_gpio_bus_ipxact.json"),
    )

    assert len(component.bus_interfaces) == 1
    bus_interface = component.bus_interfaces[0]

    assert bus_interface.name == "apb"
    assert bus_interface.bus_type == ipxact.VLNVRef("Vendor", "Library", "apb4", "1.0")
    assert bus_interface.mode == ipxact.InterfaceMode.TARGET
    assert bus_interface.target == ipxact.TargetInterface()
    assert bus_interface.initiator is None

    assert len(bus_interface.abstraction_types) == 1
    abstraction_type = bus_interface.abstraction_types[0]
    assert abstraction_type.abstraction_ref == ipxact.VLNVRef("Vendor", "Library", "apb4_rtl", "1.0")
    assert ipxact.PortMap(logical_port="PSEL", physical_port="psel") in abstraction_type.port_maps
    assert ipxact.PortMap(logical_port="PADDR", physical_port="paddr") in abstraction_type.port_maps

    # The plain physical ports are still built as usual, regardless of the bus mapping.
    assert len(component.model.ports) == 11


def test_struct_field_bus_interface_mapping():
    component = SVImporter().import_(
        FIXTURES / "struct_port" / "struct_port.sv",
        metadata=str(FIXTURES / "struct_port" / "struct_port_bus_ipxact.json"),
    )

    abstraction_type = component.bus_interfaces[0].abstraction_types[0]
    assert abstraction_type.port_maps == [
        ipxact.PortMap(
            logical_port="PSEL",
            physical_port="apb_req_i",
            sub_port_refs=[ipxact.SubPortReference(sub_port_ref="psel")],
        )
    ]


def test_interface_port_bus_interface_mapping():
    component = SVImporter().import_(
        FIXTURES / "apb_target_interface" / "apb_target.sv",
        metadata=str(FIXTURES / "apb_target_interface" / "apb_target_ipxact.json"),
    )

    assert len(component.bus_interfaces) == 1
    bus_interface = component.bus_interfaces[0]
    assert bus_interface.name == "apb"
    assert bus_interface.abstraction_types == []

    ports_by_name = {p.name: p for p in component.model.ports}
    assert ports_by_name["apb"].structured == ipxact.StructuredPort(
        struct_type="interface",
        sub_ports=[],
        struct_port_type_defs=[ipxact.StructPortTypeDef(type_name="apb_if", role="slave")],
    )
    assert ports_by_name["apb"].wire is None
    assert ports_by_name["apb"].transactional is None


def test_null_bus_interfaces_is_treated_as_absent(tmp_path):
    metadata = {"vendor": "Vendor", "library": "Library", "version": "1.0", "busInterfaces": None}
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    component = SVImporter().import_(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))
    assert component.bus_interfaces == []


def test_interface_port_referenced_by_two_bus_interfaces_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {
            "apb_a": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "interfacePort": "apb"},
            "apb_b": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "interfacePort": "apb"},
        },
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="already referenced by another busInterfaces entry"):
        SVImporter().import_(
            FIXTURES / "apb_target_interface" / "apb_target.sv", metadata=str(metadata_path)
        )


def test_null_ports_mapping_is_treated_as_empty(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {"apb": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "ports": None}},
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    # No crash on the null "ports"; the module's interface port is simply left undescribed,
    # which is still an error, just not an AttributeError.
    with pytest.raises(ValueError, match="has no matching busInterfaces entry"):
        SVImporter().import_(
            FIXTURES / "apb_target_interface" / "apb_target.sv", metadata=str(metadata_path)
        )


def test_discrete_ports_mapping_to_interface_port_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {
            "apb_real": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "interfacePort": "apb"},
            "apb_bogus": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "ports": {"PSEL": "apb"}},
        },
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="SystemVerilog interface-typed port"):
        SVImporter().import_(
            FIXTURES / "apb_target_interface" / "apb_target.sv", metadata=str(metadata_path)
        )


def test_bus_interface_with_neither_ports_nor_interface_port_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {"apb": {"bus": "Vendor:Library:apb4:1.0", "mode": "target"}},
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="has neither 'interfacePort' nor 'ports'"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_bus_interface_mapped_to_non_string_physical_port_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {
            "apb": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "ports": {"PSEL": None}}
        },
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="not a physical port name string"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_bus_interface_with_null_bus_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {"apb": {"bus": None, "mode": "target", "ports": {"PSEL": "psel"}}},
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="must be a 'vendor:library:name:version' VLNV string"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_bus_interface_with_ports_as_list_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {
            "apb": {"bus": "Vendor:Library:apb4:1.0", "mode": "target", "ports": ["psel"]}
        },
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="'ports' must be a"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_null_bus_interface_entry_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {"apb": None},
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="entry must be an object"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_bus_interfaces_as_list_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": ["apb"],
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="'busInterfaces' must be a"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_interface_port_without_matching_bus_interface_raises():
    with pytest.raises(ValueError, match="has no matching busInterfaces entry"):
        SVImporter().import_(
            FIXTURES / "apb_target_interface" / "apb_target.sv",
            metadata=str(FIXTURES / "apb_target_interface" / "apb_target_no_bus_ipxact.json"),
        )


def test_bus_interface_mapped_to_unknown_physical_port_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {
            "apb": {
                "bus": "Vendor:Library:apb4:1.0",
                "mode": "target",
                "ports": {"PSEL": "does_not_exist"},
            }
        },
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="does_not_exist"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_bus_interface_unknown_mode_raises(tmp_path):
    metadata = {
        "vendor": "Vendor",
        "library": "Library",
        "version": "1.0",
        "busInterfaces": {
            "apb": {"bus": "Vendor:Library:apb4:1.0", "mode": "bogus", "ports": {"PSEL": "psel"}}
        },
    }
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="unknown mode"):
        SVImporter().import_(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))

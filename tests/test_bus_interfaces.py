import json
from pathlib import Path

import ipxact
import pytest

from xactflow import Library
from xactflow_sv import SVImporter

FIXTURES = Path(__file__).parent / "fixtures"


def _import(source_path, **options):
    """Run SVImporter into an empty library and return the one component it added."""
    library = Library()
    SVImporter().import_(source_path, library, **options)
    (component,) = library.targets()
    return component


def test_discrete_ports_bus_interface_mapping():
    component = _import(
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
    component = _import(
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
    component = _import(
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

    component = _import(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))
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
        _import(
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
        _import(
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
        _import(
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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def test_interface_port_without_matching_bus_interface_raises():
    with pytest.raises(ValueError, match="has no matching busInterfaces entry"):
        _import(
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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


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
        _import(FIXTURES / "apb_gpio" / "apb_gpio.sv", metadata=str(metadata_path))


def _import_with_bus_interfaces(tmp_path, bus_interfaces, register_file=True):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m (input logic psel, input logic penable, output logic pready);\nendmodule\n")
    metadata = {"vendor": "Vendor", "library": "Library", "busInterfaces": bus_interfaces}
    if register_file:
        metadata["registerFile"] = str(FIXTURES / "basic_regs" / "basic_regs.rdl")
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))
    return _import(sv_file, metadata=str(metadata_path))


def _entry(mode, **extra):
    return {"bus": "Vendor:Library:apb4:1.0", "mode": mode, "ports": {"PSEL": "psel"}, **extra}


def test_target_references_the_memory_map_named_by_memory_map(tmp_path):
    component = _import_with_bus_interfaces(
        tmp_path, {"s0": _entry("target", memoryMap="basic_regs_mmap"), "s1": _entry("target")}
    )

    targets = {b.name: b.target for b in component.bus_interfaces}
    assert targets == {
        "s0": ipxact.TargetInterface(memory_map_ref="basic_regs_mmap"),
        "s1": ipxact.TargetInterface(),
    }


def test_single_target_references_the_single_memory_map_by_default(tmp_path):
    component = _import_with_bus_interfaces(tmp_path, {"s": _entry("target"), "m": _entry("initiator")})

    targets = {b.name: b.target for b in component.bus_interfaces}
    assert targets == {"s": ipxact.TargetInterface(memory_map_ref="basic_regs_mmap"), "m": None}


def test_target_has_no_memory_map_without_register_file(tmp_path):
    component = _import_with_bus_interfaces(tmp_path, {"s": _entry("target")}, register_file=False)

    assert component.bus_interfaces[0].target == ipxact.TargetInterface()


@pytest.mark.parametrize(
    "entry, error",
    [
        (
            _entry("target", memoryMap="nope"),
            "memoryMap 'nope' is not a memory map of the component \\(found: basic_regs_mmap\\)",
        ),
        (_entry("initiator", memoryMap="basic_regs_mmap"), "'memoryMap' is only for a target interface"),
        (_entry("target", prots={}), "unknown key\\(s\\) prots"),
        (_entry("system"), "a system interface needs a 'group'"),
        (_entry("mirroredSystem"), "a system interface needs a 'group'"),
        (_entry("monitor"), "a monitor needs a 'monitoredMode'"),
        (_entry("monitor", monitoredMode="monitor"), "a monitor needs a 'monitoredMode'"),
        (_entry("monitor", monitoredMode="system"), "a system interface needs a 'group'"),
        (_entry("target", monitoredMode="target"), "'monitoredMode' is only for a monitor"),
        (_entry("target", group="g"), "'group' is only for a system, mirroredSystem or monitor interface"),
        (_entry("system", group=""), "'group' must be a non-empty string"),
    ],
)
def test_invalid_bus_interface_keys_raise(tmp_path, entry, error):
    with pytest.raises(ValueError, match=error):
        _import_with_bus_interfaces(tmp_path, {"apb": entry})


def test_memory_map_without_register_file_raises(tmp_path):
    with pytest.raises(ValueError, match="memoryMap 'basic_regs_mmap' .* \\(found: none, no registerFile is set\\)"):
        _import_with_bus_interfaces(tmp_path, {"s": _entry("target", memoryMap="basic_regs_mmap")}, register_file=False)


@pytest.mark.parametrize(
    "entry, field, value",
    [
        (_entry("system", group="g"), "system", ipxact.SystemInterface(group="g")),
        (_entry("mirroredInitiator"), "mirrored_initiator", ipxact.MirroredInitiatorInterface()),
        (_entry("mirroredMaster"), "mirrored_initiator", ipxact.MirroredInitiatorInterface()),
        (_entry("mirroredTarget"), "mirrored_target", ipxact.MirroredTargetInterface()),
        (_entry("mirroredSlave"), "mirrored_target", ipxact.MirroredTargetInterface()),
        (_entry("mirroredSystem", group="g"), "mirrored_system", ipxact.SystemInterface(group="g")),
        (
            _entry("monitor", monitoredMode="target"),
            "monitor",
            ipxact.MonitorInterface(interface_mode=ipxact.InterfaceMode.TARGET),
        ),
        (
            _entry("monitor", monitoredMode="mirroredSystem", group="g"),
            "monitor",
            ipxact.MonitorInterface(interface_mode=ipxact.InterfaceMode.MIRRORED_SYSTEM, group="g"),
        ),
    ],
)
def test_other_bus_interface_modes(tmp_path, entry, field, value):
    bus_interface = _import_with_bus_interfaces(tmp_path, {"apb": entry}).bus_interfaces[0]

    assert getattr(bus_interface, field) == value
    assert bus_interface.initiator is None and bus_interface.target is None

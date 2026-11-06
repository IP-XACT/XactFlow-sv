from pathlib import Path

import ipxact
import pytest

from xactflow import evaluate, parameter_resolver
from xactflow_sv import SVImporter
from xactflow_sv.component_builder import _build_port

FIXTURES = Path(__file__).parent / "fixtures"


def test_import_plain_wire_ports_module():
    component = SVImporter().import_(
        FIXTURES / "adder" / "adder.sv",
        metadata=str(FIXTURES / "adder" / "adder_ipxact.json"),
    )

    expected = ipxact.Component(
        vlnv=ipxact.VLNV("Vendor", "Library", "adder", "1.0"),
        model=ipxact.Model(
            views=[
                ipxact.View(
                    name="rtl",
                    env_identifiers=[ipxact.TextValue("::")],
                    component_instantiation_ref="adder_rtl",
                )
            ],
            component_instantiations=[
                ipxact.ComponentInstantiation(name="adder_rtl", language="systemverilog", module_name="adder"),
            ],
            ports=[
                ipxact.Port(
                    name="a",
                    wire=ipxact.WirePort(
                        direction=ipxact.Direction.IN,
                        vectors=[ipxact.Vector(left=ipxact.Expression("7"), right=ipxact.Expression("0"))],
                    ),
                ),
                ipxact.Port(
                    name="b",
                    wire=ipxact.WirePort(
                        direction=ipxact.Direction.IN,
                        vectors=[ipxact.Vector(left=ipxact.Expression("7"), right=ipxact.Expression("0"))],
                    ),
                ),
                ipxact.Port(
                    name="o",
                    wire=ipxact.WirePort(
                        direction=ipxact.Direction.OUT,
                        vectors=[ipxact.Vector(left=ipxact.Expression("8"), right=ipxact.Expression("0"))],
                    ),
                ),
            ],
        ),
    )

    assert component == expected


def test_import_module_with_parameter():
    component = SVImporter().import_(
        FIXTURES / "apb_gpio" / "apb_gpio.sv",
        metadata=str(FIXTURES / "apb_gpio" / "apb_gpio_ipxact.json"),
    )

    assert component.vlnv == ipxact.VLNV("Vendor", "Library", "apb_gpio", "1.0")
    assert component.parameters == [
        ipxact.Parameter(name="DATA_W", value=ipxact.Expression("8"), resolve="user", parameter_id="DATA_W")
    ]

    module_params = component.model.component_instantiations[0].module_parameters
    assert module_params == [
        ipxact.ModuleParameter(name="DATA_W", value=ipxact.Expression("DATA_W"), data_type="int")
    ]

    ports_by_name = {p.name: p for p in component.model.ports}
    assert ports_by_name["pwdata"].wire.vectors == [
        ipxact.Vector(left=ipxact.Expression("DATA_W-1"), right=ipxact.Expression("0"))
    ]
    assert ports_by_name["paddr"].wire.vectors == [
        ipxact.Vector(left=ipxact.Expression("3"), right=ipxact.Expression("0"))
    ]
    assert ports_by_name["pclk"].wire.vectors == []


def test_imported_parameters_are_cross_referenceable_by_name():
    component = SVImporter().import_(
        FIXTURES / "apb_gpio" / "apb_gpio.sv",
        metadata=str(FIXTURES / "apb_gpio" / "apb_gpio_ipxact.json"),
    )

    resolve = parameter_resolver(component.parameters)
    pwdata = next(p for p in component.model.ports if p.name == "pwdata")
    assert evaluate(pwdata.wire.vectors[0].left, resolve_parameter=resolve) == 7


def test_import_struct_port():
    component = SVImporter().import_(
        FIXTURES / "struct_port" / "struct_port.sv",
        metadata=str(FIXTURES / "struct_port" / "ipxact.json"),
    )

    port = component.model.ports[0]
    assert port.name == "apb_req_i"
    assert port.structured == ipxact.StructuredPort(
        struct_type="struct",
        direction=ipxact.Direction.IN,
        sub_ports=[],
        struct_port_type_defs=[ipxact.StructPortTypeDef(type_name="apb_req_t")],
    )
    assert port.wire is None


def test_interface_port_without_modport_has_no_role():
    port = _build_port(
        {
            "name": "apb",
            "direction": "inout",
            "is_interface": True,
            "is_struct": False,
            "iface_type": "interface",
            "modport": "",
            "packed_dims": [],
            "unpacked_dims": [],
        }
    )

    assert port.structured.struct_port_type_defs == [ipxact.StructPortTypeDef(type_name="interface")]


def test_import_interface_array_port():
    component = SVImporter().import_(
        FIXTURES / "unsupported" / "interface_array_port.sv",
        metadata=str(FIXTURES / "unsupported" / "interface_array_port_ipxact.json"),
    )

    port = component.model.ports[0]
    assert port.name == "apb"
    assert port.arrays == [ipxact.ArrayBound(left=ipxact.Expression("3"), right=ipxact.Expression("0"))]


def test_import_unpacked_array_port():
    component = SVImporter().import_(
        FIXTURES / "unsupported" / "unpacked_port.sv",
        metadata=str(FIXTURES / "unsupported" / "ipxact.json"),
    )

    ports_by_name = {p.name: p for p in component.model.ports}
    assert ports_by_name["mem"].arrays == [
        ipxact.ArrayBound(left=ipxact.Expression("0"), right=ipxact.Expression("15"))
    ]
    assert ports_by_name["mem_sized"].arrays == [
        ipxact.ArrayBound(left=ipxact.Expression("(16)-1"), right=ipxact.Expression("0"))
    ]
    assert ports_by_name["ways"].arrays == [
        ipxact.ArrayBound(left=ipxact.Expression("(NumWays)-1"), right=ipxact.Expression("0"))
    ]


def test_import_missing_metadata_field_raises(tmp_path):
    bad_metadata = tmp_path / "missing_library.json"
    bad_metadata.write_text('{"vendor": "Vendor"}')

    with pytest.raises(ValueError, match="library"):
        SVImporter().import_(FIXTURES / "adder" / "adder.sv", metadata=str(bad_metadata))


def _write_ifdef_module(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(
        "module m (\n`ifdef WIDE\n  input logic [31:0] d\n`else\n  input logic [7:0] d\n`endif\n);\nendmodule\n"
    )
    metadata = tmp_path / "m_ipxact.json"
    metadata.write_text('{"vendor": "Vendor", "library": "Library"}')
    return sv_file, str(metadata)


@pytest.mark.parametrize("define", ["OTHER,WIDE", ["OTHER", "WIDE"]])
def test_define_option_as_string_or_list(tmp_path, define):
    sv_file, metadata = _write_ifdef_module(tmp_path)

    component = SVImporter().import_(sv_file, metadata=metadata, define=define)
    assert component.model.ports[0].wire.vectors[0].left == ipxact.Expression("31")


@pytest.mark.parametrize("name, value", [("define", 1), ("define", ["WIDE", 1]), ("include", {"a": "b"})])
def test_list_option_of_wrong_type_raises(tmp_path, name, value):
    sv_file, metadata = _write_ifdef_module(tmp_path)

    with pytest.raises(ValueError, match=f"option '{name}' must be a comma-separated string or a list"):
        SVImporter().import_(sv_file, metadata=metadata, **{name: value})


def test_module_option_selects_the_module(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module helper (input logic x);\nendmodule\nmodule top (input logic clk);\nendmodule\n")
    metadata = tmp_path / "m_ipxact.json"
    metadata.write_text('{"vendor": "Vendor", "library": "Library"}')

    component = SVImporter().import_(sv_file, metadata=str(metadata), module="top")
    assert component.vlnv.name == "top"

    with pytest.raises(ValueError, match="option 'module' must be a module name string"):
        SVImporter().import_(sv_file, metadata=str(metadata), module=["top"])


def test_array_parameter_has_arrays_on_both_parameters(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m #(parameter int ARR [2] = '{1, 2}) (input logic clk);\nendmodule\n")
    metadata = tmp_path / "m_ipxact.json"
    metadata.write_text('{"vendor": "Vendor", "library": "Library"}')

    component = SVImporter().import_(sv_file, metadata=str(metadata))
    arrays = [ipxact.ArrayBound(left=ipxact.Expression("(2)-1"), right=ipxact.Expression("0"))]
    assert component.parameters[0].arrays == arrays
    assert component.model.component_instantiations[0].module_parameters[0].arrays == arrays

from pathlib import Path

import ipxact
import pytest

from xactflow_sv import SVImporter

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
                    env_identifiers=["::"],
                    component_instantiation_ref="adder_rtl",
                )
            ],
            component_instantiations=[
                ipxact.ComponentInstantiation(name="adder_rtl", module_name="adder"),
            ],
            ports=[
                ipxact.Port(
                    name="a",
                    wire=ipxact.WirePort(
                        direction=ipxact.Direction.IN,
                        vectors=[ipxact.Vector(left="7", right="0")],
                    ),
                ),
                ipxact.Port(
                    name="b",
                    wire=ipxact.WirePort(
                        direction=ipxact.Direction.IN,
                        vectors=[ipxact.Vector(left="7", right="0")],
                    ),
                ),
                ipxact.Port(
                    name="o",
                    wire=ipxact.WirePort(
                        direction=ipxact.Direction.OUT,
                        vectors=[ipxact.Vector(left="8", right="0")],
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
    assert component.parameters == [ipxact.Parameter(name="DATA_W", value="8", resolve="user")]

    module_params = component.model.component_instantiations[0].module_parameters
    assert module_params == [ipxact.ModuleParameter(name="DATA_W", value="8", data_type="int")]

    ports_by_name = {p.name: p for p in component.model.ports}
    assert ports_by_name["pwdata"].wire.vectors == [ipxact.Vector(left="DATA_W-1", right="0")]
    assert ports_by_name["paddr"].wire.vectors == [ipxact.Vector(left="3", right="0")]
    assert ports_by_name["pclk"].wire.vectors == []


def test_import_struct_port_not_yet_supported():
    with pytest.raises(NotImplementedError, match="struct/interface-typed"):
        SVImporter().import_(
            FIXTURES / "unsupported" / "struct_port.sv",
            metadata=str(FIXTURES / "unsupported" / "ipxact.json"),
        )


def test_import_unpacked_array_port_not_yet_supported():
    with pytest.raises(NotImplementedError, match="unpacked array"):
        SVImporter().import_(
            FIXTURES / "unsupported" / "unpacked_port.sv",
            metadata=str(FIXTURES / "unsupported" / "ipxact.json"),
        )


def test_import_missing_metadata_field_raises(tmp_path):
    bad_metadata = tmp_path / "missing_library.json"
    bad_metadata.write_text('{"vendor": "Vendor"}')

    with pytest.raises(ValueError, match="library"):
        SVImporter().import_(FIXTURES / "adder" / "adder.sv", metadata=str(bad_metadata))

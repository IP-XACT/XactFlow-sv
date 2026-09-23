from pathlib import Path

import pytest

from xactflow_sv.sv_parser import extract_module_name, extract_parameters, extract_ports, find_module, parse_sv

FIXTURES = Path(__file__).parent / "fixtures"


def _header(sv_file: Path) -> dict:
    tree_json = parse_sv(sv_file, [])
    module_node = find_module(tree_json)
    return module_node.get("header", {})


def test_extract_module_name_and_plain_wire_ports():
    header = _header(FIXTURES / "adder" / "adder.sv")

    assert extract_module_name(header) == "adder"
    assert extract_parameters(header) == []
    assert extract_ports(header) == [
        {
            "name": "a",
            "direction": "in",
            "is_interface": False,
            "is_struct": False,
            "type_name": "",
            "packed_dims": [("7", "0")],
            "unpacked_dims": [],
        },
        {
            "name": "b",
            "direction": "in",
            "is_interface": False,
            "is_struct": False,
            "type_name": "",
            "packed_dims": [("7", "0")],
            "unpacked_dims": [],
        },
        {
            "name": "o",
            "direction": "out",
            "is_interface": False,
            "is_struct": False,
            "type_name": "",
            "packed_dims": [("8", "0")],
            "unpacked_dims": [],
        },
    ]


def test_extract_parameters_with_parametric_vector_width():
    header = _header(FIXTURES / "apb_gpio" / "apb_gpio.sv")

    assert extract_parameters(header) == [{"name": "DATA_W", "dataType": "int", "value": "8"}]

    ports_by_name = {p["name"]: p for p in extract_ports(header)}
    assert ports_by_name["pwdata"]["packed_dims"] == [("DATA_W-1", "0")]
    assert ports_by_name["paddr"]["packed_dims"] == [("3", "0")]


def test_direction_less_first_port_defaults_to_inout(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m ([7:0] a, input logic [7:0] b);\nendmodule\n")

    header = _header(sv_file)
    ports_by_name = {p["name"]: p for p in extract_ports(header)}
    assert ports_by_name["a"]["direction"] == "inout"
    assert ports_by_name["b"]["direction"] == "in"


def test_explicit_ansi_port_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(
        "module m(output .foo(internal_sig), input logic bar);\nlogic internal_sig;\nendmodule\n"
    )

    with pytest.raises(NotImplementedError, match="ExplicitAnsiPort"):
        extract_ports(_header(sv_file))


def test_non_ansi_port_list_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m(a, b);\ninput a;\noutput b;\nendmodule\n")

    with pytest.raises(NotImplementedError, match="ImplicitNonAnsiPort"):
        extract_ports(_header(sv_file))


def test_indexed_part_select_dimension_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m(input logic [BASE+:8] foo);\nparameter BASE = 0;\nendmodule\n")

    with pytest.raises(NotImplementedError, match="AscendingRangeSelect"):
        extract_ports(_header(sv_file))


def test_shorthand_unpacked_array_size_dimension():
    header = _header(FIXTURES / "unsupported" / "unpacked_port.sv")

    ports_by_name = {p["name"]: p for p in extract_ports(header)}
    assert ports_by_name["mem_sized"]["unpacked_dims"] == [("(16)-1", "0")]
    assert ports_by_name["ways"]["unpacked_dims"] == [("(NumWays)-1", "0")]


def test_shorthand_unpacked_array_size_is_parenthesized_to_keep_precedence(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(
        "module m #(\n"
        "    parameter int ADDR_WIDTH = 4\n"
        ") (\n"
        "    output logic [7:0] mem [1 << ADDR_WIDTH]\n"
        ");\nendmodule\n"
    )

    ports_by_name = {p["name"]: p for p in extract_ports(_header(sv_file))}
    # Without the parentheses, "1<<ADDR_WIDTH-1" would be misread as "1 << (ADDR_WIDTH-1)"
    # instead of the intended "(1<<ADDR_WIDTH)-1".
    assert ports_by_name["mem"]["unpacked_dims"] == [("(1<<ADDR_WIDTH)-1", "0")]


def test_array_size_shorthand_on_a_packed_port_dimension_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m(output logic [4] x);\nendmodule\n")

    # [N] is only valid SV grammar for an unpacked dimension; a bare [N] as a packed
    # dimension (meant as [3:0], say) must not be silently reinterpreted as array-size
    # shorthand just because pyslang's syntax-only parse doesn't reject it either.
    with pytest.raises(NotImplementedError, match="BitSelect"):
        extract_ports(_header(sv_file))


def test_array_size_shorthand_on_a_packed_parameter_dimension_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m #(parameter logic [4] BAD = 0) (input logic clk);\nendmodule\n")

    with pytest.raises(NotImplementedError, match="BitSelect"):
        extract_parameters(_header(sv_file))


def test_zero_size_unpacked_array_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m(output logic [7:0] mem [0]);\nendmodule\n")

    with pytest.raises(NotImplementedError, match="zero-size"):
        extract_ports(_header(sv_file))


def test_queue_dimension_raises_not_implemented(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m(output logic [7:0] mem_q [$]);\nendmodule\n")

    # A queue's specifier has no "selector" at all (unlike a range or [N] array size), so
    # this must be rejected by the specifier's own kind, not silently pass through as if
    # there were nothing to reject.
    with pytest.raises(NotImplementedError, match="QueueDimensionSpecifier"):
        extract_ports(_header(sv_file))

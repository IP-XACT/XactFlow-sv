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

    assert extract_parameters(header) == [{"name": "DATA_W", "dataType": "int", "value": "8", "arrays": []}]

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


def test_syntax_error_raises_with_file_and_line(tmp_path):
    sv_file = tmp_path / "m.sv"
    # Missing comma after port 'a': pyslang would otherwise recover by dropping port 'b'.
    sv_file.write_text(
        "module m (\n  input logic a\n  output logic b,\n  input logic c\n);\nendmodule\n"
    )

    with pytest.raises(ValueError, match=r"m\.sv:2:16: error: expected ','"):
        parse_sv(sv_file, [])


def test_missing_include_file_raises(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text('`include "defs.svh"\nmodule m (input logic [`DW-1:0] data);\nendmodule\n')

    with pytest.raises(ValueError, match="'defs.svh': No such file or directory"):
        parse_sv(sv_file, [])


def test_include_dirs_are_searched(tmp_path):
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "defs.svh").write_text("`define DW 16\n")
    sv_file = tmp_path / "m.sv"
    sv_file.write_text('`include "defs.svh"\nmodule m (input logic [`DW-1:0] data);\nendmodule\n')

    tree_json = parse_sv(sv_file, [], [str(tmp_path / "inc")])
    ports = extract_ports(find_module(tree_json).get("header", {}))
    assert ports[0]["packed_dims"] == [("16-1", "0")]


def test_several_modules_without_module_name_raises(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module helper (input logic x);\nendmodule\nmodule top (input logic clk);\nendmodule\n")

    with pytest.raises(ValueError, match=r"several modules \(helper, top\)"):
        find_module(parse_sv(sv_file, []))


def test_module_name_selects_the_module(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module helper (input logic x);\nendmodule\nmodule top (input logic clk);\nendmodule\n")

    module_node = find_module(parse_sv(sv_file, []), "top")
    assert extract_module_name(module_node.get("header", {})) == "top"


def test_unknown_module_name_raises(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module helper (input logic x);\nendmodule\n")

    with pytest.raises(ValueError, match="module 'top' not found in input file, found: helper"):
        find_module(parse_sv(sv_file, []), "top")


def _parameters(tmp_path, source: str) -> list[dict]:
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(source)
    module_node = find_module(parse_sv(sv_file, []))
    return extract_parameters(module_node.get("header", {}), module_node.get("members", []))


def test_parameter_without_keyword_after_localparam_is_local(tmp_path):
    params = _parameters(
        tmp_path,
        "module m #(\n  parameter int P = 1,\n  localparam int L = 4,\n  int A = 5\n) (input logic clk);\nendmodule\n",
    )

    # Per the SV LRM, 'A' takes the 'localparam' keyword of the declaration before it.
    assert [p["name"] for p in params] == ["P"]


def test_first_parameter_without_keyword_is_a_parameter(tmp_path):
    params = _parameters(tmp_path, "module m #(int A = 5, localparam int L = 4) (input logic clk);\nendmodule\n")

    assert [p["name"] for p in params] == ["A"]


def test_body_parameters_without_parameter_port_list(tmp_path):
    params = _parameters(
        tmp_path,
        "module m (input logic clk);\n"
        "  parameter int WIDTH = 8, DEPTH = 4;\n"
        "  localparam int L = 1;\n"
        "  parameter type T = logic;\n"
        "endmodule\n",
    )

    assert [(p["name"], p["dataType"], p["value"]) for p in params] == [
        ("WIDTH", "int", "8"),
        ("DEPTH", "int", "4"),
        ("T", "type", "logic"),
    ]


def test_body_parameters_are_local_with_a_parameter_port_list(tmp_path):
    params = _parameters(
        tmp_path, "module m #(parameter int P = 1) (input logic clk);\n  parameter int B = 2;\nendmodule\n"
    )

    # With a parameter port list, the SV LRM makes a body 'parameter' a localparam.
    assert [p["name"] for p in params] == ["P"]


@pytest.mark.parametrize("declaration", ["parameter int N", "parameter type T"])
def test_parameter_without_default_raises_not_implemented(tmp_path, declaration):
    with pytest.raises(NotImplementedError, match="has no default value"):
        _parameters(tmp_path, f"module m #({declaration}) (input logic clk);\nendmodule\n")


def test_parameter_data_type_keeps_signing_and_implicit_base_type(tmp_path):
    params = _parameters(
        tmp_path,
        "module m #(\n"
        "  parameter bit signed [3:0] SB = -2,\n"
        "  parameter signed [3:0] IMPL = 3,\n"
        "  parameter [7:0] IMPL_RANGE = 3,\n"
        "  parameter IMPL_NONE = 3,\n"
        "  parameter signed IMPL_SIGNED = -3,\n"
        "  parameter int unsigned U = 1\n"
        ") (input logic clk);\nendmodule\n",
    )

    assert {p["name"]: p["dataType"] for p in params} == {
        "SB": "bit signed [3:0]",
        "IMPL": "logic signed [3:0]",
        "IMPL_RANGE": "logic [7:0]",
        "IMPL_NONE": "int",
        "IMPL_SIGNED": "int signed",
        "U": "int unsigned",
    }


def test_parameter_unpacked_dimensions(tmp_path):
    params = _parameters(
        tmp_path,
        "module m #(parameter int ARR [2] = '{1, 2}, parameter int RNG [0:3] = '{0, 1, 2, 3}) "
        "(input logic clk);\nendmodule\n",
    )

    assert {p["name"]: p["arrays"] for p in params} == {"ARR": [("(2)-1", "0")], "RNG": [("0", "3")]}


def test_integer_type_ports_get_their_width(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(
        "module m (\n"
        "  input byte a_byte, input shortint a_shortint, input int a_int, input int unsigned a_uint,\n"
        "  input integer a_integer, input longint a_longint, input time a_time,\n"
        "  output int a_int_arr [2]\n"
        ");\nendmodule\n"
    )

    ports_by_name = {p["name"]: p for p in extract_ports(_header(sv_file))}
    assert {name: p["packed_dims"] for name, p in ports_by_name.items()} == {
        "a_byte": [("7", "0")],
        "a_shortint": [("15", "0")],
        "a_int": [("31", "0")],
        "a_uint": [("31", "0")],
        "a_integer": [("31", "0")],
        "a_longint": [("63", "0")],
        "a_time": [("63", "0")],
        "a_int_arr": [("31", "0")],
    }
    assert ports_by_name["a_int_arr"]["unpacked_dims"] == [("(2)-1", "0")]


@pytest.mark.parametrize(
    "data_type, kind",
    [
        ("real", "RealType"),
        ("shortreal", "ShortRealType"),
        ("realtime", "RealTimeType"),
        ("string", "StringType"),
        ("chandle", "CHandleType"),
        ("event", "EventType"),
    ],
)
def test_port_type_without_bit_width_raises_not_implemented(tmp_path, data_type, kind):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(f"module m (input {data_type} a);\nendmodule\n")

    with pytest.raises(NotImplementedError, match=f"port 'a' has data type '{data_type}' \\({kind}\\)"):
        extract_ports(_header(sv_file))


def test_port_without_direction_and_type_takes_the_previous_port_header(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text(
        "module m (\n"
        "  input logic [7:0] a, b, input int c, d, input apb_req_t r1, r2,\n"
        "  apb_if.slave s0, s1, input wire [3:0] w0, w1,\n"
        "  output logic [3:0] e [2], f, output g\n"
        ");\nendmodule\n"
    )

    ports_by_name = {p["name"]: p for p in extract_ports(_header(sv_file))}
    for first, second in [("a", "b"), ("c", "d"), ("r1", "r2"), ("s0", "s1"), ("w0", "w1")]:
        assert ports_by_name[second] == {**ports_by_name[first], "name": second}
    # The data type is inherited, not the unpacked dimensions of the previous declarator.
    assert ports_by_name["f"]["packed_dims"] == [("3", "0")]
    assert ports_by_name["f"]["unpacked_dims"] == []
    # A port with its own direction does not inherit the previous data type.
    assert ports_by_name["g"]["packed_dims"] == []


def test_duplicate_module_names_raise(tmp_path):
    sv_file = tmp_path / "m.sv"
    sv_file.write_text("module m (input logic x);\nendmodule\nmodule m (input logic y);\nendmodule\n")

    with pytest.raises(ValueError, match="declares module\\(s\\) m more than once"):
        find_module(parse_sv(sv_file, []), "m")

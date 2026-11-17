import ipxact
import pytest

from xactflow_sv.qualifiers import find_qualifiers

from test_importer import FIXTURES, _import

CLOCK = ipxact.Qualifier(is_clock=True)
RESET_LOW = ipxact.Qualifier(is_reset=ipxact.LevelFlag(True, level="low"))
RESET_HIGH = ipxact.Qualifier(is_reset=ipxact.LevelFlag(True, level="high"))


def _port(name, direction="in", is_struct=False, is_interface=False):
    return {"name": name, "direction": direction, "is_struct": is_struct, "is_interface": is_interface}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("clk", CLOCK),
        ("clk_i", CLOCK),
        ("clock", CLOCK),
        ("clock_i", CLOCK),
        ("CLK", CLOCK),
        ("sys_clk_i", CLOCK),
        ("clk_iA", CLOCK),
        ("clk_iC", CLOCK),
        ("clk_0", CLOCK),
        ("arst_ni", RESET_LOW),
        ("arst_n", RESET_LOW),
        ("rst_n", RESET_LOW),
        ("rst_ni", RESET_LOW),
        ("reset_n", RESET_LOW),
        ("reset_ni", RESET_LOW),
        ("RST_N", RESET_LOW),
        ("por_rst_niB", RESET_LOW),
        ("arst_i", RESET_HIGH),
        ("arst", RESET_HIGH),
        ("rst", RESET_HIGH),
        ("rst_i", RESET_HIGH),
        ("reset", RESET_HIGH),
        ("reset_i", RESET_HIGH),
        ("soft_rst_iA", RESET_HIGH),
    ],
)
def test_clock_and_reset_detected_from_name(name, expected):
    assert find_qualifiers([_port(name)]) == {name: expected}


@pytest.mark.parametrize("name", ["clk_en_i", "clk_sel", "rst_cnt", "pclk", "nrst", "clk_ia", "data_i", "clocking_i"])
def test_other_names_are_not_detected(name):
    assert find_qualifiers([_port(name)]) == {}


def test_only_input_wire_ports_are_detected():
    ports = [
        _port("clk_o", direction="out"),
        _port("rst_n", direction="inout"),
        _port("clk", is_struct=True),
        _port("rst", is_interface=True),
    ]
    assert find_qualifiers(ports) == {}


def test_metadata_clocks_replace_clock_detection_only():
    ports = [_port("clk"), _port("core_ck"), _port("rst_n")]

    assert find_qualifiers(ports, clocks=["core_ck"]) == {"core_ck": CLOCK, "rst_n": RESET_LOW}


def test_metadata_resets_replace_reset_detection_only():
    ports = [_port("clk"), _port("rst_n"), _port("por", direction="out")]

    assert find_qualifiers(ports, resets={"por": "HIGH"}) == {"clk": CLOCK, "por": RESET_HIGH}


def test_empty_metadata_lists_disable_detection():
    assert find_qualifiers([_port("clk"), _port("rst_n")], clocks=[], resets={}) == {}


def test_port_listed_in_metadata_is_not_detected_as_the_other_kind():
    # Named like a clock, but the metadata file says it is a reset.
    assert find_qualifiers([_port("clk")], resets={"clk": "low"}) == {"clk": RESET_LOW}


@pytest.mark.parametrize(
    "clocks, resets, match",
    [
        ("clk", None, "'clocks' must be a list of port names"),
        (None, ["rst"], "'resets' must map each reset port name to its level"),
        (None, {"rst": "active"}, "port 'rst' has level 'active' \\(expected low/high\\)"),
        (["missing"], None, "'missing' as a clock/reset, but the module has no such port"),
        (["bus"], None, "'bus' as a clock/reset, but it is not a wire port"),
        (["clk"], {"clk": "low"}, "port\\(s\\) clk as both a clock and a reset"),
    ],
)
def test_invalid_metadata_raises(clocks, resets, match):
    with pytest.raises(ValueError, match=match):
        find_qualifiers([_port("clk"), _port("rst"), _port("bus", is_struct=True)], clocks, resets)


def test_import_sets_qualifiers_on_ports(tmp_path):
    metadata = tmp_path / "m_ipxact.json"
    metadata.write_text('{"vendor": "Vendor", "library": "Library", "resets": {"rst_n": "high"}}')

    ports_by_name = {
        p.name: p for p in _import(FIXTURES / "basic_regs" / "basic_regs.sv", metadata=str(metadata)).model.ports
    }
    assert ports_by_name["clk"].wire.qualifier == CLOCK
    assert ports_by_name["rst_n"].wire.qualifier == RESET_HIGH
    assert all(p.wire.qualifier is None for name, p in ports_by_name.items() if name not in ("clk", "rst_n"))

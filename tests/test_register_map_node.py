import dataclasses
import enum
import json
import os
from pathlib import Path

import ipxact
import pytest
from systemrdl import RDLCompiler, component
from systemrdl.udp import UDPDefinition

from xactflow_sv.register_map import build_memory_maps, build_memory_maps_from_node

RDL = Path(__file__).parent / "fixtures" / "basic_regs"
GOLDEN = RDL / "golden"

# The RDL fixtures that convert, each one with its expected memory map in golden/<name>.json.
CONVERTIBLE = [
    "basic_regs",
    "field_access",
    "enum_regs",
    "accesswidth_regs",
    "nested_regs",
    "hierarchical_regs",
    "mem_regs",
    "mem_and_block",
    "arrays",
    "alias_regs",
    "reset_signals",
    "block_arrays",
]


def _compile(path: Path, udps=()):
    compiler = RDLCompiler()
    for udp in udps:
        compiler.register_udp(udp, soft=False)
    compiler.compile_file(str(path))
    return compiler.elaborate().top


def _jsonable(value):
    """`value` as plain JSON data, leaving out the fields that are None or empty, and giving an
    expression that only has a value as that value."""
    if isinstance(value, ipxact.Expression) and value == ipxact.Expression(value.value):
        return value.value
    if dataclasses.is_dataclass(value):
        fields = {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
        return {name: field for name, field in fields.items() if field not in (None, [], {})}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, enum.Enum):
        return value.value
    return value


@pytest.mark.parametrize("name", CONVERTIBLE)
def test_node_path_matches_golden(name):
    """Set XACTFLOW_SV_UPDATE_GOLDEN=1 to write golden/<name>.json from the current output, then
    review the diff.
    """
    actual = _jsonable(build_memory_maps_from_node(_compile(RDL / f"{name}.rdl")))
    golden = GOLDEN / f"{name}.json"
    if os.environ.get("XACTFLOW_SV_UPDATE_GOLDEN"):
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(json.dumps(actual, indent=2) + "\n")
    assert actual == json.loads(golden.read_text())


def test_field_access_mapping():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "field_access.rdl"))
    fields = {
        field.name: field for register in memory_map.items[0].registers for field in register.fields
    }

    def policy(name):
        (field_access_policy,) = fields[name].field_access_policies
        return field_access_policy

    Access, Write, Read = ipxact.AccessType, ipxact.ModifiedWriteValue, ipxact.ReadAction
    assert {name: policy(f"{name}_f").access for name in ("ro", "rw", "wo", "rw1", "w1")} == {
        "ro": Access.READ_ONLY,
        "rw": Access.READ_WRITE,
        "wo": Access.WRITE_ONLY,
        "rw1": Access.READ_WRITE_ONCE,
        "w1": Access.WRITE_ONCE,
    }
    assert {name: policy(f"{name}_f").read_action for name in ("rclr", "rset", "ruser")} == {
        "rclr": Read.CLEAR,
        "rset": Read.SET,
        "ruser": Read.MODIFY,
    }
    assert {
        name: policy(f"{name}_f").modified_write_value
        for name in ("woclr", "woset", "wot", "wzc", "wzs", "wzt", "wclr", "wset", "wuser")
    } == {
        "woclr": Write.ONE_TO_CLEAR,
        "woset": Write.ONE_TO_SET,
        "wot": Write.ONE_TO_TOGGLE,
        "wzc": Write.ZERO_TO_CLEAR,
        "wzs": Write.ZERO_TO_SET,
        "wzt": Write.ZERO_TO_TOGGLE,
        "wclr": Write.CLEAR,
        "wset": Write.SET,
        "wuser": Write.MODIFY,
    }

    pending = fields["pending"]
    assert (pending.display_name, pending.description) == ("Pending", "set by hardware, cleared by reading it")
    assert pending.resets == [ipxact.Reset(value=ipxact.Expression("'ha"))]
    assert pending.volatile is True
    assert policy("pending").testable is False
    assert (fields["upper"].bit_offset, fields["upper"].bit_width) == (ipxact.Expression("4"), ipxact.Expression("5"))


def test_enum_field_has_enumerated_values():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "enum_regs.rdl"))
    mode, other = memory_map.items[0].registers[0].fields

    assert mode.enumerated_values == [
        ipxact.EnumeratedValue(
            name="OFF", value=ipxact.Expression("'h0"), display_name="Off", description="the block is off"
        ),
        ipxact.EnumeratedValue(name="SLOW", value=ipxact.Expression("'h1"), description="low power"),
        ipxact.EnumeratedValue(name="FAST", value=ipxact.Expression("'h2")),
    ]
    assert other.enumerated_values == []


def test_accesswidth_does_not_change_register_size_or_block_width():
    # IEEE 1685-2022 has no access width: size is the regwidth, the block width the largest one.
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "accesswidth_regs.rdl"))
    block = memory_map.items[0]

    assert block.width == ipxact.Expression("64")
    assert [(register.name, register.size) for register in block.registers] == [
        ("WIDE", ipxact.Expression("64")),
        ("NARROW", ipxact.Expression("32")),
    ]


def test_nested_regfiles_and_addrmap_become_register_files():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "nested_regs.rdl"))
    (block,) = memory_map.items
    top_reg, outer, sub = block.registers

    assert isinstance(top_reg, ipxact.Register)
    assert (outer.name, outer.address_offset, outer.range) == (
        "outer",
        ipxact.Expression("'h100"),
        ipxact.Expression("'h18"),
    )
    assert outer.description == "outer register file"
    outer_reg, inner = outer.registers
    assert (outer_reg.name, inner.name, inner.address_offset) == ("OUTER_REG", "inner", ipxact.Expression("'h10"))
    assert [(register.name, register.address_offset) for register in inner.registers] == [
        ("INNER_REG", ipxact.Expression("'h4"))
    ]
    assert isinstance(sub, ipxact.RegisterFile)
    assert [register.name for register in sub.registers] == ["SUB_REG"]
    # The address block takes the width of its widest register, nested ones included.
    assert block.width == ipxact.Expression("32")


def test_addrmap_of_addrmaps_and_mems_gives_one_address_block_each():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "mem_and_block.rdl"))

    assert memory_map.name == "mem_and_block_mmap"
    assert memory_map.description == "a register block and a RAM"
    regs, ram = memory_map.items
    assert (regs.name, regs.base_address, regs.width, regs.usage) == (
        "regs",
        ipxact.Expression("'h0"),
        ipxact.Expression("32"),
        None,
    )
    assert ram == ipxact.AddressBlock(
        name="RAM",
        display_name="Scratch RAM",
        base_address=ipxact.Expression("'h1000"),
        range=ipxact.Expression("'h200"),
        width=ipxact.Expression("64"),
        usage=ipxact.UsageType.MEMORY,
        access_policies=[ipxact.AccessPolicy(access=ipxact.AccessType.READ_WRITE)],
    )


class _Owner(UDPDefinition):
    """A user-defined property, as e.g. PeakRDL-regblock or an in-house flow registers them."""

    name = "owner"
    valid_components = {component.Reg}
    valid_type = str


def test_node_compiled_with_a_udp_registered(tmp_path):
    rdl = tmp_path / "udp_regs.rdl"
    rdl.write_text(
        'addrmap udp_regs {\n'
        '    reg { owner = "power team"; field { sw = rw; hw = r; } value[8] = 0; } CTRL @ 0x0;\n'
        '};\n'
    )

    # The UDP is not declared in the RDL, so compiling the file on its own fails.
    with pytest.raises(ValueError, match="failed to compile"):
        build_memory_maps(rdl)

    (memory_map,) = build_memory_maps_from_node(_compile(rdl, udps=[_Owner]))
    assert memory_map.name == "udp_regs_mmap"
    assert [register.name for register in memory_map.items[0].registers] == ["CTRL"]

    assert build_memory_maps(rdl, udps=[_Owner]) == [memory_map]


def test_register_file_with_include_dir_and_parameter_override(tmp_path):
    (tmp_path / "inc").mkdir()
    (tmp_path / "inc" / "ctrl_reg.rdl").write_text(
        "reg ctrl_reg #(longint unsigned W = 8) { field { sw = rw; hw = r; } value[W] = 0; };\n"
    )
    rdl = tmp_path / "param_regs.rdl"
    rdl.write_text(
        '`include "ctrl_reg.rdl"\n'
        "addrmap param_regs #(longint unsigned W = 8) { ctrl_reg #(.W(W)) CTRL @ 0x0; };\n"
    )

    with pytest.raises(ValueError, match="failed to compile"):
        build_memory_maps(rdl)

    (memory_map,) = build_memory_maps(rdl, include_dirs=[tmp_path / "inc"], parameters={"W": 12})
    (field,) = memory_map.items[0].registers[0].fields
    assert field.bit_width == ipxact.Expression("12")


def test_register_and_register_file_arrays():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "arrays.rdl"))
    (block,) = memory_map.items
    items = {item.name: item for item in block.registers}

    def array(dims, stride):
        return ipxact.MemoryArray(
            dims=[ipxact.ArrayDim(size=ipxact.Expression(str(dim))) for dim in dims],
            stride=ipxact.Expression(stride),
        )

    assert items["CTRL"].array == array([4], "'h4")
    assert (items["SPARSE"].address_offset, items["SPARSE"].array) == (ipxact.Expression("'h20"), array([4], "'h8"))
    assert items["MATRIX"].array == array([2, 3], "'h4")
    # A register file's range is the size of one element, the stride is in its array.
    assert (items["CH"].range, items["CH"].array) == (ipxact.Expression("'h8"), array([2], "'h10"))
    assert [register.name for register in items["CH"].registers] == ["CFG", "COUNT"]
    assert isinstance(items["LANE"], ipxact.RegisterFile)
    assert (items["LANE"].range, items["LANE"].array) == (ipxact.Expression("'h4"), array([2], "'h4"))
    assert block.range == ipxact.Expression("'h208")


@pytest.mark.parametrize(
    "name, error, match",
    [
        ("ispresent_regs", NotImplementedError, "'ispresent_regs.DISABLED_REG' has ispresent=false"),
        ("nested_mem", NotImplementedError, "mem 'top_with_nested_mem.ROM' is in 'top_with_nested_mem'"),
    ],
)
def test_unsupported_fixture_raises(name, error, match):
    with pytest.raises(error, match=match):
        build_memory_maps_from_node(_compile(RDL / f"{name}.rdl"))


@pytest.mark.parametrize(
    "body, match",
    [
        (
            "reg { field { sw = rw; hw = r; } a[4] = 0; field { sw = rw; hw = r; } b[4]; b->reset = a; } CTRL @ 0x0;",
            "'m.CTRL.b' has a reset value that is a reference \\(m.CTRL.a\\)",
        ),
        (
            "bridge;\n"
            "addrmap { reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0; } a @ 0x0;\n"
            "addrmap { reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0; } b @ 0x100;",
            "addrmap 'm' is a bridge",
        ),
    ],
)
def test_construct_without_ipxact_equivalent_raises(tmp_path, body, match):
    rdl = tmp_path / "m.rdl"
    rdl.write_text(f"addrmap m {{\n{body}\n}};\n")

    with pytest.raises(NotImplementedError, match=match):
        build_memory_maps_from_node(_compile(rdl))


def test_node_that_is_not_an_addrmap_raises():
    root = _compile(RDL / "basic_regs.rdl").parent

    with pytest.raises(TypeError, match="expected a systemrdl AddrmapNode, got: RootNode"):
        build_memory_maps_from_node(root)


def test_alias_field_refers_to_its_primary():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "alias_regs.rdl"))
    (block,) = memory_map.items
    main, main_alias, channel = block.registers

    def reference(register_files, register, field):
        return ipxact.FieldReference(
            memory_map_ref="alias_regs_mmap",
            address_block_ref=ipxact.IndexedRef(name="alias_regs"),
            register_file_refs=[ipxact.IndexedRef(name=name) for name in register_files],
            register_ref=ipxact.IndexedRef(name=register),
            field_ref=ipxact.IndexedRef(name=field),
        )

    assert main.fields[0].alias_of is None
    # Its storage is the primary's: no width, volatile or reset of its own.
    assert main_alias.fields[0] == ipxact.Field(
        name="enable",
        bit_offset=ipxact.Expression("0"),
        alias_of=reference([], "MAIN", "enable"),
        field_access_policies=[ipxact.FieldAccessPolicy(access=ipxact.AccessType.READ_WRITE)],
    )
    assert main_alias.fields[1].alias_of == reference([], "MAIN", "mode")

    ctrl, ctrl_clr = channel.registers
    (enable,) = ctrl_clr.fields
    assert enable.alias_of == reference(["CH"], "CTRL", "enable")
    # The alias's own software access.
    assert enable.field_access_policies == [
        ipxact.FieldAccessPolicy(
            access=ipxact.AccessType.READ_WRITE, modified_write_value=ipxact.ModifiedWriteValue.ONE_TO_CLEAR
        )
    ]


def test_alias_in_an_array_raises(tmp_path):
    rdl = tmp_path / "m.rdl"
    rdl.write_text(
        "addrmap m {\n"
        "    reg r_t { field { sw = rw; hw = r; } v[8] = 0; };\n"
        "    r_t PRIM[2] @ 0x0;\n"
        "    alias PRIM r_t ALI[2] @ 0x10;\n"
        "};\n"
    )

    with pytest.raises(NotImplementedError, match="field 'm.ALI\\[\\].v' is an alias in an array"):
        build_memory_maps_from_node(_compile(rdl))


def test_reset_signal_other_than_the_default_gives_a_reset_type():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "reset_signals.rdl"))
    by_default, by_soft_rst, no_reset = memory_map.items[0].registers[0].fields

    # The field_reset signal is the default reset, IP-XACT's HARD reset type.
    assert by_default.resets == [ipxact.Reset(value=ipxact.Expression("'h0"))]
    assert by_soft_rst.resets == [ipxact.Reset(value=ipxact.Expression("'h5"), reset_type_ref="soft_rst")]
    assert no_reset.resets == []


@pytest.mark.parametrize(
    "body, match",
    [
        (
            "regfile { signal { activehigh; } srst; reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0;"
            " R.v->resetsignal = srst; } A @ 0x0;\n"
            "regfile { signal { activehigh; } srst; reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0;"
            " R.v->resetsignal = srst; } B @ 0x10;",
            "reset signals 'm.A.srst' and 'm.B.srst' have the same name",
        ),
        (
            "signal { activehigh; } HARD; reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0;"
            " R.v->resetsignal = HARD;",
            "reset signal 'm.HARD' of field 'm.R.v' is named HARD",
        ),
    ],
)
def test_reset_signal_names_that_cannot_be_reset_types_raise(tmp_path, body, match):
    rdl = tmp_path / "m.rdl"
    rdl.write_text(f"addrmap m {{\n{body}\n}};\n")

    with pytest.raises(NotImplementedError, match=match):
        build_memory_maps_from_node(_compile(rdl))


def test_top_of_addrmaps_and_mems_with_an_array_gives_an_address_block_array():
    (memory_map,) = build_memory_maps_from_node(_compile(RDL / "block_arrays.rdl"))
    lane, shadow = memory_map.items

    assert (lane.base_address, lane.range) == (ipxact.Expression("'h0"), ipxact.Expression("'h4"))
    assert lane.array == ipxact.MemoryArray(
        dims=[ipxact.ArrayDim(size=ipxact.Expression("4"))], stride=ipxact.Expression("'h100")
    )
    # A mem with sw = na.
    assert shadow.access_policies == [ipxact.AccessPolicy(access=ipxact.AccessType.NO_ACCESS)]


def test_array_node_raises():
    lane = _compile(RDL / "block_arrays.rdl").get_child_by_name("LANE")

    with pytest.raises(ValueError, match="addrmap 'block_arrays.LANE\\[\\]' is an array"):
        build_memory_maps_from_node(lane)


import json
from pathlib import Path

import ipxact
import pytest

from xactflow import Library
from xactflow_sv import SVImporter
from xactflow_sv.register_map import build_memory_maps

FIXTURES = Path(__file__).parent / "fixtures"


def _import(source_path, **options):
    """Run SVImporter into an empty library and return the one component it added."""
    library = Library()
    SVImporter().import_(source_path, library, **options)
    (component,) = library.targets()
    return component


def test_build_memory_maps_from_rdl_file():
    memory_maps = build_memory_maps(FIXTURES / "basic_regs" / "basic_regs.rdl")

    assert len(memory_maps) == 1
    memory_map = memory_maps[0]
    assert memory_map.name == "basic_regs_mmap"
    assert len(memory_map.items) == 1

    address_block = memory_map.items[0]
    assert isinstance(address_block, ipxact.AddressBlock)
    assert address_block.name == "basic_regs"
    assert address_block.base_address.value == "'h0"
    assert address_block.range.value == "'h8"
    assert address_block.width.value == "32"

    registers_by_name = {r.name: r for r in address_block.registers}
    assert set(registers_by_name) == {"CTRL", "STATUS"}

    ctrl = registers_by_name["CTRL"]
    assert ctrl.address_offset.value == "'h0"
    assert ctrl.size.value == "32"
    assert len(ctrl.fields) == 1

    assert ctrl.description == "control register"

    ctrl_field = ctrl.fields[0]
    assert ctrl_field.name == "value"
    assert ctrl_field.bit_offset.value == "0"
    assert ctrl_field.bit_width.value == "8"
    assert ctrl_field.description == "the value"
    assert ctrl_field.resets == [ipxact.Reset(value=ipxact.Expression("'h0"))]
    assert ctrl_field.field_access_policies == [
        ipxact.FieldAccessPolicy(access=ipxact.AccessType.READ_WRITE)
    ]

    status_field = registers_by_name["STATUS"].fields[0]
    assert status_field.volatile is True
    assert status_field.field_access_policies == [
        ipxact.FieldAccessPolicy(access=ipxact.AccessType.READ_ONLY)
    ]


def test_import_module_with_register_file():
    component = _import(
        FIXTURES / "basic_regs" / "basic_regs.sv",
        metadata=str(FIXTURES / "basic_regs" / "basic_regs_ipxact.json"),
    )

    assert len(component.memory_maps) == 1
    assert component.memory_maps[0].name == "basic_regs_mmap"
    # The plain physical ports are still built as usual, regardless of the register map.
    assert len(component.model.ports) == 4


def test_import_module_without_register_file_has_no_memory_maps():
    component = _import(
        FIXTURES / "adder" / "adder.sv", metadata=str(FIXTURES / "adder" / "adder_ipxact.json")
    )

    assert component.memory_maps == []


def test_mem_address_block_carries_access_policy():
    memory_maps = build_memory_maps(FIXTURES / "basic_regs" / "mem_regs.rdl")

    address_block = memory_maps[0].items[0]
    assert address_block.usage == ipxact.UsageType.MEMORY
    assert address_block.access_policies == [ipxact.AccessPolicy(access=ipxact.AccessType.READ_ONLY)]


def test_unrelated_orphaned_addrmap_raises():
    with pytest.raises(ValueError, match="never used under the chosen top-level addrmap"):
        build_memory_maps(FIXTURES / "basic_regs" / "multiple_addrmaps.rdl")


def test_reusable_sub_block_addrmap_is_not_treated_as_ambiguous():
    memory_maps = build_memory_maps(FIXTURES / "basic_regs" / "hierarchical_regs.rdl")

    assert len(memory_maps) == 1
    address_blocks_by_name = {b.name: b for b in memory_maps[0].items}
    assert set(address_blocks_by_name) == {"blk0", "blk1"}
    assert address_blocks_by_name["blk0"].registers[0].name == "LEAF_REG"


def test_register_array_with_oversized_stride_is_supported():
    (memory_map,) = build_memory_maps(FIXTURES / "basic_regs" / "bad_stride.rdl")

    (register,) = memory_map.items[0].registers
    assert register.array.stride == ipxact.Expression("'h8")


def test_register_file_pointing_at_a_directory_raises_value_error(tmp_path):
    directory = tmp_path / "not_a_file.rdl"
    directory.mkdir()

    with pytest.raises(ValueError, match="registerFile could not be read"):
        build_memory_maps(directory)


def test_ispresent_false_register_not_yet_supported():
    with pytest.raises(NotImplementedError, match="ispresent=false"):
        build_memory_maps(FIXTURES / "basic_regs" / "ispresent_regs.rdl")


def test_construct_ipxact_cannot_represent_raises():
    with pytest.raises(NotImplementedError, match="'top_with_nested_mem', which becomes an IP-XACT address block"):
        build_memory_maps(FIXTURES / "basic_regs" / "nested_mem.rdl")


def test_register_array_is_supported():
    (memory_map,) = build_memory_maps(FIXTURES / "basic_regs" / "array_regs.rdl")

    (register,) = memory_map.items[0].registers
    assert register.array == ipxact.MemoryArray(
        dims=[ipxact.ArrayDim(size=ipxact.Expression("4"))], stride=ipxact.Expression("'h4")
    )


def test_addrmap_only_used_with_ispresent_false_raises_ispresent_error(tmp_path):
    rdl = tmp_path / "not_present.rdl"
    rdl.write_text(
        "addrmap sub { reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0; };\n"
        "addrmap t { sub S @ 0x0; S->ispresent = false;"
        " reg { field { sw = rw; hw = r; } v[8] = 0; } X @ 0x100; };\n"
    )

    with pytest.raises(NotImplementedError, match="'t.S' has ispresent=false"):
        build_memory_maps(rdl)


def test_single_include_dir_not_in_a_list_raises():
    with pytest.raises(TypeError, match="include_dirs must be a list of directories"):
        build_memory_maps(FIXTURES / "basic_regs" / "basic_regs.rdl", include_dirs="inc")


def test_compile_error_gives_the_compiler_messages(tmp_path):
    rdl = tmp_path / "overlap.rdl"
    rdl.write_text(
        "addrmap overlap {\n"
        "    reg { field { sw = rw; hw = r; } v[8] = 0; } A @ 0x0;\n"
        "    reg { field { sw = rw; hw = r; } v[8] = 0; } B @ 0x0;\n"
        "};\n"
    )

    with pytest.raises(ValueError, match=f"failed to compile: {rdl}:3: .*overlap"):
        build_memory_maps(rdl)


def test_unused_addrmap_of_an_included_file_is_allowed(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "blocks.rdl").write_text(
        "addrmap blk_a { reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0; };\n"
        "addrmap blk_b { reg { field { sw = rw; hw = r; } v[8] = 0; } R @ 0x0; };\n"
    )
    rdl = tmp_path / "top.rdl"
    rdl.write_text('`include "blocks.rdl"\naddrmap top { blk_a A @ 0x0; blk_a B @ 0x100; };\n')

    (memory_map,) = build_memory_maps(rdl, include_dirs=[tmp_path / "lib"])
    assert [block.name for block in memory_map.items] == ["A", "B"]


def test_single_udp_not_in_a_list_raises():
    from test_register_map_node import _Owner

    with pytest.raises(TypeError, match="udps must be a list of user-defined property classes"):
        build_memory_maps(FIXTURES / "basic_regs" / "basic_regs.rdl", udps=_Owner)


def test_component_declares_the_reset_types_its_fields_refer_to():
    component = _import(
        FIXTURES / "basic_regs" / "basic_regs.sv",
        vlnv_vendor="Vendor",
        vlnv_library="Library",
        registerFile=str(FIXTURES / "basic_regs" / "reset_signals.rdl"),
    )

    assert component.reset_types == [ipxact.ResetType(name="soft_rst")]


def test_component_without_reset_signals_has_no_reset_types():
    component = _import(
        FIXTURES / "basic_regs" / "basic_regs.sv",
        metadata=str(FIXTURES / "basic_regs" / "basic_regs_ipxact.json"),
    )

    assert component.reset_types == []


def test_compiler_warning_raises(tmp_path):
    # Instantiating an addrmap in the root namespace is ignored by the compiler, with a warning.
    rdl = tmp_path / "warn.rdl"
    rdl.write_text("addrmap warn { reg { field { sw = rw; hw = r; } value[4] = 0; } CTRL @ 0x0; } warn_inst;\n")

    with pytest.raises(NotImplementedError, match="could not be fully converted"):
        build_memory_maps(rdl)


def test_missing_register_file_raises_value_error():
    with pytest.raises(ValueError, match="registerFile could not be read"):
        build_memory_maps(FIXTURES / "basic_regs" / "does_not_exist.rdl")


def test_invalid_register_file_syntax_raises_value_error(tmp_path):
    bad_rdl = tmp_path / "bad.rdl"
    bad_rdl.write_text("this is not valid rdl {{{")

    with pytest.raises(ValueError, match="failed to compile"):
        build_memory_maps(bad_rdl)


def test_non_string_register_file_raises(tmp_path):
    metadata = {"vendor": "Vendor", "library": "Library", "version": "1.0", "registerFile": 123}
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="'registerFile' must be a non-empty path string"):
        _import(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))


def test_empty_string_register_file_raises(tmp_path):
    metadata = {"vendor": "Vendor", "library": "Library", "version": "1.0", "registerFile": ""}
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="'registerFile' must be a non-empty path string"):
        _import(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))


def test_register_file_path_is_resolved_relative_to_metadata_file(tmp_path):
    (tmp_path / "regs.rdl").write_text((FIXTURES / "basic_regs" / "basic_regs.rdl").read_text())
    metadata = {"vendor": "Vendor", "library": "Library", "version": "1.0", "registerFile": "regs.rdl"}
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    component = _import(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))
    assert len(component.memory_maps) == 1

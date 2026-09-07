import json
from pathlib import Path

import ipxact
import pytest

from xactflow_sv import SVImporter
from xactflow_sv.register_map import build_memory_maps

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_memory_maps_from_rdl_file():
    memory_maps = build_memory_maps(FIXTURES / "basic_regs" / "basic_regs.rdl")

    assert len(memory_maps) == 1
    memory_map = memory_maps[0]
    assert memory_map.name == "basic_regs_mmap"
    assert len(memory_map.items) == 1

    address_block = memory_map.items[0]
    assert isinstance(address_block, ipxact.AddressBlock)
    assert address_block.name == "basic_regs"
    assert address_block.base_address == "'h0"
    assert address_block.range == "'h8"
    assert address_block.width == "32"

    registers_by_name = {r.name: r for r in address_block.registers}
    assert set(registers_by_name) == {"CTRL", "STATUS"}

    ctrl = registers_by_name["CTRL"]
    assert ctrl.address_offset == "'h0"
    assert ctrl.size == "32"
    assert len(ctrl.fields) == 1

    assert ctrl.description == "control register"

    ctrl_field = ctrl.fields[0]
    assert ctrl_field.name == "value"
    assert ctrl_field.bit_offset == "0"
    assert ctrl_field.bit_width == "8"
    assert ctrl_field.description == "the value"
    assert ctrl_field.resets == [ipxact.Reset(value="'h0")]
    assert ctrl_field.field_access_policies == [
        ipxact.FieldAccessPolicy(access=ipxact.AccessType.READ_WRITE)
    ]

    status_field = registers_by_name["STATUS"].fields[0]
    assert status_field.volatile is True
    assert status_field.field_access_policies == [
        ipxact.FieldAccessPolicy(access=ipxact.AccessType.READ_ONLY)
    ]


def test_import_module_with_register_file():
    component = SVImporter().import_(
        FIXTURES / "basic_regs" / "basic_regs.sv",
        metadata=str(FIXTURES / "basic_regs" / "basic_regs_ipxact.json"),
    )

    assert len(component.memory_maps) == 1
    assert component.memory_maps[0].name == "basic_regs_mmap"
    # The plain physical ports are still built as usual, regardless of the register map.
    assert len(component.model.ports) == 4


def test_import_module_without_register_file_has_no_memory_maps():
    component = SVImporter().import_(
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


def test_register_array_with_oversized_stride_raises_value_error():
    with pytest.raises(ValueError, match="failed to compile"):
        build_memory_maps(FIXTURES / "basic_regs" / "bad_stride.rdl")


def test_register_file_pointing_at_a_directory_raises_value_error(tmp_path):
    directory = tmp_path / "not_a_file.rdl"
    directory.mkdir()

    with pytest.raises(ValueError, match="registerFile could not be read"):
        build_memory_maps(directory)


def test_ispresent_false_register_not_yet_supported():
    with pytest.raises(NotImplementedError, match="ispresent=false"):
        build_memory_maps(FIXTURES / "basic_regs" / "ispresent_regs.rdl")


def test_construct_peakrdl_ipxact_cannot_export_raises():
    with pytest.raises(NotImplementedError, match="could not be fully converted"):
        build_memory_maps(FIXTURES / "basic_regs" / "nested_mem.rdl")


def test_register_array_not_yet_supported():
    with pytest.raises(NotImplementedError, match="is an array"):
        build_memory_maps(FIXTURES / "basic_regs" / "array_regs.rdl")


def test_unexpected_exporter_output_raises(monkeypatch):
    class _FakeExporter:
        def __init__(self, **kwargs):
            pass

        def export(self, root_node, path):
            Path(path).write_text('<?xml version="1.0"?><not-ipxact-component/>')

    monkeypatch.setattr("xactflow_sv.register_map.IPXACTExporter", _FakeExporter)

    with pytest.raises(RuntimeError, match="unexpected root element"):
        build_memory_maps(FIXTURES / "basic_regs" / "basic_regs.rdl")


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
        SVImporter().import_(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))


def test_empty_string_register_file_raises(tmp_path):
    metadata = {"vendor": "Vendor", "library": "Library", "version": "1.0", "registerFile": ""}
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="'registerFile' must be a non-empty path string"):
        SVImporter().import_(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))


def test_register_file_path_is_resolved_relative_to_metadata_file(tmp_path):
    (tmp_path / "regs.rdl").write_text((FIXTURES / "basic_regs" / "basic_regs.rdl").read_text())
    metadata = {"vendor": "Vendor", "library": "Library", "version": "1.0", "registerFile": "regs.rdl"}
    metadata_path = tmp_path / "meta.json"
    metadata_path.write_text(json.dumps(metadata))

    component = SVImporter().import_(FIXTURES / "adder" / "adder.sv", metadata=str(metadata_path))
    assert len(component.memory_maps) == 1

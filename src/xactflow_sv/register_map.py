"""register_map.py: build ipxact.MemoryMap objects from a SystemRDL register file.

Compiles the register file with systemrdl-compiler, exports it to a temporary IEEE
1685-2014 IP-XACT XML file with peakrdl-ipxact, then reads that XML directly to build
ipxact-compiler's 1685-2022 memory-map dataclasses. A dedicated reader is used instead of
ipxact-compiler's own parser. This is a deliberate choice for now, not a permanent one.
Switching to ipxact-compiler's parser needs a 2014-to-2022 XML restructuring step first,
which is easier to do here, has we only do it for the memory-map dataclasses.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import ipxact
from peakrdl_ipxact import IPXACTExporter
from systemrdl import RDLCompiler, component
from systemrdl.messages import MessagePrinter, RDLCompileError, Severity
from systemrdl.node import AddrmapNode

_NS = "http://www.accellera.org/XMLSchema/IPXACT/1685-2014"


class _WarningCollector(MessagePrinter):
    """Captures compiler/exporter warnings instead of printing them.

    Installed on the RDLCompiler's message handler before compiling; peakrdl-ipxact reuses
    that same handler while exporting, so this also catches export-time warnings.
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def print_message(self, severity: Severity, text: str, src_ref: object = None) -> None:
        if severity == Severity.WARNING:
            self.warnings.append(text)


def _tag(local: str) -> str:
    return f"{{{_NS}}}{local}"


def _text(el: ET.Element, local: str) -> Optional[str]:
    child = el.find(_tag(local))
    return child.text if child is not None else None


def _require_no_array(el: ET.Element, what: str) -> None:
    if el.find(_tag("dim")) is not None:
        raise NotImplementedError(f"{what} is an array, which is not yet supported")


def _require_present(el: ET.Element, what: str) -> None:
    # isPresent has no IEEE 1685-2022 equivalent (it was dropped from the schema), so a
    # conditionally-absent register/field/block has no way to be represented as such.
    is_present = _text(el, "isPresent")
    if is_present is not None and is_present == "0":
        raise NotImplementedError(f"{what} has ispresent=false, which is not yet supported")


def _name_group(el: ET.Element) -> dict:
    return {"display_name": _text(el, "displayName"), "description": _text(el, "description")}


def _build_field_access_policy(field_el: ET.Element) -> list[ipxact.FieldAccessPolicy]:
    kwargs = {}

    access = _text(field_el, "access")
    if access is not None:
        kwargs["access"] = ipxact.AccessType(access)

    modified_write_value = _text(field_el, "modifiedWriteValue")
    if modified_write_value is not None:
        kwargs["modified_write_value"] = ipxact.ModifiedWriteValue(modified_write_value)

    read_action = _text(field_el, "readAction")
    if read_action is not None:
        kwargs["read_action"] = ipxact.ReadAction(read_action)

    testable = _text(field_el, "testable")
    if testable is not None:
        kwargs["testable"] = testable == "true"

    return [ipxact.FieldAccessPolicy(**kwargs)] if kwargs else []


def _build_field(field_el: ET.Element) -> ipxact.Field:
    _require_present(field_el, f"field '{_text(field_el, 'name')}'")
    resets = [
        ipxact.Reset(value=_text(reset_el, "value"))
        for reset_el in field_el.findall(f"{_tag('resets')}/{_tag('reset')}")
    ]
    enumerated_values = [
        ipxact.EnumeratedValue(
            name=_text(enum_el, "name"), value=_text(enum_el, "value"), **_name_group(enum_el)
        )
        for enum_el in field_el.findall(f"{_tag('enumeratedValues')}/{_tag('enumeratedValue')}")
    ]
    volatile = _text(field_el, "volatile")

    return ipxact.Field(
        name=_text(field_el, "name"),
        bit_offset=_text(field_el, "bitOffset"),
        bit_width=_text(field_el, "bitWidth"),
        volatile=(volatile == "true") if volatile is not None else None,
        resets=resets,
        field_access_policies=_build_field_access_policy(field_el),
        enumerated_values=enumerated_values,
        **_name_group(field_el),
    )


def _build_registers(parent_el: ET.Element) -> list:
    items = []
    for child in parent_el:
        if child.tag == _tag("register"):
            _require_no_array(child, f"register '{_text(child, 'name')}'")
            _require_present(child, f"register '{_text(child, 'name')}'")
            items.append(
                ipxact.Register(
                    name=_text(child, "name"),
                    address_offset=_text(child, "addressOffset"),
                    size=_text(child, "size"),
                    fields=[_build_field(fe) for fe in child.findall(_tag("field"))],
                    **_name_group(child),
                )
            )
        elif child.tag == _tag("registerFile"):
            _require_no_array(child, f"register file '{_text(child, 'name')}'")
            _require_present(child, f"register file '{_text(child, 'name')}'")
            items.append(
                ipxact.RegisterFile(
                    name=_text(child, "name"),
                    address_offset=_text(child, "addressOffset"),
                    range=_text(child, "range"),
                    registers=_build_registers(child),
                    **_name_group(child),
                )
            )
    return items


def _build_address_block(block_el: ET.Element) -> ipxact.AddressBlock:
    _require_present(block_el, f"address block '{_text(block_el, 'name')}'")
    usage = _text(block_el, "usage")
    access = _text(block_el, "access")
    return ipxact.AddressBlock(
        name=_text(block_el, "name"),
        base_address=_text(block_el, "baseAddress"),
        range=_text(block_el, "range"),
        width=_text(block_el, "width"),
        usage=ipxact.UsageType(usage) if usage is not None else None,
        access_policies=[ipxact.AccessPolicy(access=ipxact.AccessType(access))] if access is not None else [],
        registers=_build_registers(block_el),
        **_name_group(block_el),
    )


def build_memory_maps(register_file: Path) -> list[ipxact.MemoryMap]:
    """Compile `register_file` (a SystemRDL file) into its ipxact.MemoryMap objects."""
    collector = _WarningCollector()
    compiler = RDLCompiler()
    compiler.msg.printer = collector
    try:
        compiler.compile_file(str(register_file))
        root_node = compiler.elaborate()  # picks the last-defined addrmap, matching systemrdl's own default

        # An addrmap defined at file scope but never instantiated anywhere under the chosen
        # top is a second, unrelated design left in the same file, not a reusable sub-block:
        # systemrdl-compiler would otherwise silently ignore it rather than erroring.
        used_type_names = {root_node.top.type_name}
        used_type_names.update(
            d.type_name for d in root_node.top.descendants(unroll=False) if isinstance(d, AddrmapNode)
        )
        orphaned = [
            name
            for name, comp_def in compiler.root.comp_defs.items()
            if isinstance(comp_def, component.Addrmap) and name not in used_type_names
        ]
        if orphaned:
            raise ValueError(
                f"registerFile {register_file} defines addrmap(s) {', '.join(orphaned)}, never used "
                f"under the chosen top-level addrmap '{root_node.top.type_name}'"
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            xml_path = Path(tmp_dir) / "register_map.xml"
            # Only <ipxact:memoryMaps> is ever read back below, so the exporter's own VLNV
            # (which it otherwise defaults to placeholders) is irrelevant here.
            IPXACTExporter().export(root_node, str(xml_path))
            root_el = ET.parse(xml_path).getroot()
    except OSError as exc:
        raise ValueError(f"registerFile could not be read: {register_file} ({exc})") from exc
    except RDLCompileError as exc:
        raise ValueError(f"registerFile {register_file} failed to compile: {exc}") from exc

    if collector.warnings:
        raise NotImplementedError(
            f"registerFile {register_file} uses a construct that could not be fully converted: "
            + "; ".join(collector.warnings)
        )

    if root_el.tag != _tag("component"):
        raise RuntimeError(
            f"peakrdl-ipxact produced an unexpected root element {root_el.tag!r}, expected "
            f"{_tag('component')!r}; its output format may have changed"
        )

    memory_maps_el = root_el.find(_tag("memoryMaps"))
    if memory_maps_el is None:
        return []

    return [
        ipxact.MemoryMap(
            name=_text(mmap_el, "name"),
            items=[_build_address_block(block_el) for block_el in mmap_el.findall(_tag("addressBlock"))],
            **_name_group(mmap_el),
        )
        for mmap_el in memory_maps_el.findall(_tag("memoryMap"))
    ]

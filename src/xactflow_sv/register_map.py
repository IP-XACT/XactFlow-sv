"""register_map.py: build ipxact.MemoryMap objects from SystemRDL.

build_memory_maps_from_node() walks an elaborated systemrdl-compiler node and builds
ipxact-compiler's IEEE 1685-2022 memory-map dataclasses directly. build_memory_maps()
compiles a SystemRDL file first, then calls it.

The SystemRDL 2.0 to IEEE 1685-2022 rules (sw, onread/onwrite, resets, enums, mem, arrays,
aliases, nesting and flattening of addrmaps) are listed in the README's "Register maps".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import ipxact
from systemrdl import RDLCompiler, component, rdltypes
from systemrdl.messages import MessagePrinter, RDLCompileError, Severity
from systemrdl.node import (
    AddressableNode,
    AddrmapNode,
    FieldNode,
    MemNode,
    Node,
    RegfileNode,
    RegNode,
    SignalNode,
)
from systemrdl.source_ref import DetailedFileSourceRef, FileSourceRef, SourceRefBase
from systemrdl.udp import UDPDefinition


class _MessageCollector(MessagePrinter):
    """Captures the compiler's warnings and errors, with their location, instead of printing them."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def print_message(self, severity: Severity, text: str, src_ref: Optional[SourceRefBase] = None) -> None:
        if isinstance(src_ref, DetailedFileSourceRef):
            text = f"{src_ref.path}:{src_ref.line}: {text}"
        elif isinstance(src_ref, FileSourceRef):
            text = f"{src_ref.path}: {text}"
        if severity >= Severity.ERROR:
            self.errors.append(text)
        elif severity == Severity.WARNING:
            self.warnings.append(text)


# sw -> ipxact:access. sw = na is only possible on a mem, systemrdl-compiler rejects a field
# without software access.
_ACCESS = {
    rdltypes.AccessType.r: ipxact.AccessType.READ_ONLY,
    rdltypes.AccessType.rw: ipxact.AccessType.READ_WRITE,
    rdltypes.AccessType.rw1: ipxact.AccessType.READ_WRITE_ONCE,
    rdltypes.AccessType.w: ipxact.AccessType.WRITE_ONLY,
    rdltypes.AccessType.w1: ipxact.AccessType.WRITE_ONCE,
    rdltypes.AccessType.na: ipxact.AccessType.NO_ACCESS,
}

# onwrite -> ipxact:modifiedWriteValue.
_MODIFIED_WRITE_VALUE = {
    rdltypes.OnWriteType.woclr: ipxact.ModifiedWriteValue.ONE_TO_CLEAR,
    rdltypes.OnWriteType.woset: ipxact.ModifiedWriteValue.ONE_TO_SET,
    rdltypes.OnWriteType.wot: ipxact.ModifiedWriteValue.ONE_TO_TOGGLE,
    rdltypes.OnWriteType.wzc: ipxact.ModifiedWriteValue.ZERO_TO_CLEAR,
    rdltypes.OnWriteType.wzs: ipxact.ModifiedWriteValue.ZERO_TO_SET,
    rdltypes.OnWriteType.wzt: ipxact.ModifiedWriteValue.ZERO_TO_TOGGLE,
    rdltypes.OnWriteType.wclr: ipxact.ModifiedWriteValue.CLEAR,
    rdltypes.OnWriteType.wset: ipxact.ModifiedWriteValue.SET,
    rdltypes.OnWriteType.wuser: ipxact.ModifiedWriteValue.MODIFY,
}

# onread -> ipxact:readAction.
_READ_ACTION = {
    rdltypes.OnReadType.rclr: ipxact.ReadAction.CLEAR,
    rdltypes.OnReadType.rset: ipxact.ReadAction.SET,
    rdltypes.OnReadType.ruser: ipxact.ReadAction.MODIFY,
}

# The reset type a reset has when it names none, IEEE 1685-2022's resetTypeRef default.
_DEFAULT_RESET_TYPE = "HARD"


@dataclass(frozen=True)
class _Block:
    """The address block being built, and its memory map, which an alias field refers to."""

    memory_map: str
    node: AddressableNode


def _hex(value: int) -> ipxact.Expression:
    return ipxact.Expression(f"'h{value:x}")


def _dec(value: int) -> ipxact.Expression:
    return ipxact.Expression(str(value))


def _node_name_group(node: Node) -> dict:
    # "name" defaults to the instance name when it is not assigned, it is only a displayName
    # when the RDL gives one.
    return {"display_name": node.get_property("name", default=None), "description": node.get_property("desc")}


def _check_node(node: Node, what: str) -> None:
    # isPresent has no IEEE 1685-2022 equivalent (it was dropped from the schema), so a
    # conditionally-absent register/field/block has no way to be represented as such.
    if not node.get_property("ispresent"):
        raise NotImplementedError(f"{what} '{node.get_path()}' has ispresent=false, which is not yet supported")


def _array(node: AddressableNode) -> Optional[ipxact.MemoryArray]:
    """The dimensions of an array, and the address increment from one element to the next,
    which can be larger than an element.
    """
    dimensions, stride = node.array_dimensions, node.array_stride
    if dimensions is None or stride is None:  # not an array
        return None
    return ipxact.MemoryArray(dims=[ipxact.ArrayDim(size=_dec(size)) for size in dimensions], stride=_hex(stride))


def _reset_signal(field: FieldNode) -> Optional[SignalNode]:
    """The signal resetting `field`, when it is not the design's default reset.

    Without an explicit resetsignal, systemrdl-compiler gives the nearest field_reset signal,
    the default reset of every field, which is IEEE 1685-2022's default reset type.
    """
    signal = field.get_property("resetsignal")
    if signal is None or signal.get_property("field_reset"):
        return None
    if signal.inst_name == _DEFAULT_RESET_TYPE:
        raise NotImplementedError(
            f"reset signal '{signal.get_path()}' of field '{field.get_path()}' is named "
            f"{_DEFAULT_RESET_TYPE}, IP-XACT's default reset type, which is not yet supported"
        )
    return signal


def _alias_of(field: FieldNode, block: _Block) -> ipxact.FieldReference:
    """A reference to the primary field of an alias field, which systemrdl-compiler requires to
    be at the same position, with the same width, in a register of the same scope.
    """
    primary = field.alias_primary
    register = primary.parent
    register_files: list[ipxact.IndexedRef] = []
    node: Optional[Node] = register
    while node is not None:
        if isinstance(node, AddressableNode) and node.is_array:
            # Each alias element refers to the primary element of the same index, which an
            # IP-XACT reference cannot say.
            raise NotImplementedError(
                f"field '{field.get_path()}' is an alias in an array, which is not yet supported"
            )
        if node.get_path() == block.node.get_path():
            break
        if node is not register:
            register_files.insert(0, ipxact.IndexedRef(name=node.inst_name))
        node = node.parent
    return ipxact.FieldReference(
        memory_map_ref=block.memory_map,
        address_block_ref=ipxact.IndexedRef(name=block.node.inst_name),
        register_file_refs=register_files,
        register_ref=ipxact.IndexedRef(name=register.inst_name),
        field_ref=ipxact.IndexedRef(name=primary.inst_name),
    )


def _field_from_node(node: FieldNode, block: _Block) -> ipxact.Field:
    _check_node(node, "field")

    onwrite = node.get_property("onwrite")
    onread = node.get_property("onread")
    policy = ipxact.FieldAccessPolicy(
        access=_ACCESS[node.get_property("sw")],
        modified_write_value=_MODIFIED_WRITE_VALUE[onwrite] if onwrite else None,
        read_action=_READ_ACTION[onread] if onread else None,
        testable=False if node.get_property("donttest") else None,
    )
    encode = node.get_property("encode")
    enumerated_values = [
        ipxact.EnumeratedValue(
            name=member.name, value=_hex(member.value), display_name=member.rdl_name, description=member.rdl_desc
        )
        for member in (encode or [])
    ]

    if node.is_alias:
        # An alias field has the storage of its primary, so its width, volatility and reset are
        # the primary's: IEEE 1685-2022 gives it aliasOf instead of them.
        return ipxact.Field(
            name=node.inst_name,
            bit_offset=_dec(node.low),
            alias_of=_alias_of(node, block),
            field_access_policies=[policy],
            enumerated_values=enumerated_values,
            **_node_name_group(node),
        )

    reset = node.get_property("reset")
    if reset is not None and not isinstance(reset, int):
        reference = reset.get_path() if isinstance(reset, Node) else repr(reset)
        raise NotImplementedError(
            f"field '{node.get_path()}' has a reset value that is a reference ({reference}), "
            "which is not yet supported"
        )
    reset_signal = _reset_signal(node)

    return ipxact.Field(
        name=node.inst_name,
        bit_offset=_dec(node.low),
        bit_width=_dec(node.width),
        volatile=True if node.is_volatile else None,
        resets=(
            [ipxact.Reset(value=_hex(reset), reset_type_ref=reset_signal.inst_name if reset_signal else None)]
            if reset is not None
            else []
        ),
        field_access_policies=[policy],
        enumerated_values=enumerated_values,
        **_node_name_group(node),
    )


def _registers_from_node(
    node: Union[AddrmapNode, RegfileNode, MemNode], block: _Block
) -> list[Union[ipxact.Register, ipxact.RegisterFile]]:
    """The registers and register files under `node`, in order."""
    items: list[Union[ipxact.Register, ipxact.RegisterFile]] = []
    for child in node.children(skip_not_present=False):
        if isinstance(child, RegNode):
            _check_node(child, "register")
            items.append(
                ipxact.Register(
                    name=child.inst_name,
                    address_offset=_hex(child.raw_address_offset),
                    array=_array(child),
                    size=_dec(child.get_property("regwidth")),
                    fields=[_field_from_node(field, block) for field in child.fields(skip_not_present=False)],
                    **_node_name_group(child),
                )
            )
        elif isinstance(child, (AddrmapNode, RegfileNode)):
            _check_node(child, "register file")
            items.append(
                ipxact.RegisterFile(
                    name=child.inst_name,
                    address_offset=_hex(child.raw_address_offset),
                    array=_array(child),
                    # The size of one element, for an array.
                    range=_hex(child.size),
                    registers=_registers_from_node(child, block),
                    **_node_name_group(child),
                )
            )
        elif isinstance(child, MemNode):
            raise NotImplementedError(
                f"mem '{child.get_path()}' is in '{node.get_path()}', which becomes an IP-XACT address "
                "block or register file and cannot hold a memory: a mem is only supported as a child "
                "of a top-level addrmap that holds nothing but addrmaps and mems"
            )
    return items


def _address_block_from_node(node: Union[AddrmapNode, MemNode], memory_map: str) -> ipxact.AddressBlock:
    _check_node(node, "address block")
    is_mem = isinstance(node, MemNode)
    registers = _registers_from_node(node, _Block(memory_map, node))

    # IEEE 1685 gives a width to the address block, not to its registers: the widest register,
    # nested ones included, or the memwidth of a mem without virtual registers.
    regwidths = [
        descendant.get_property("regwidth")
        for descendant in node.descendants(unroll=False, skip_not_present=False)
        if isinstance(descendant, RegNode)
    ]
    width = node.get_property("memwidth") if is_mem and not regwidths else max(regwidths)

    return ipxact.AddressBlock(
        name=node.inst_name,
        # The address of the first element, for an array.
        base_address=_hex(node.raw_absolute_address),
        array=_array(node),
        range=_hex(node.size),
        width=_dec(width),
        usage=ipxact.UsageType.MEMORY if is_mem else None,
        access_policies=[ipxact.AccessPolicy(access=_ACCESS[node.get_property("sw")])] if is_mem else [],
        registers=registers,
        **_node_name_group(node),
    )


def _check_reset_signal_names(node: AddrmapNode) -> None:
    """A reset type is named after its signal, so two signals with the same name would share it."""
    paths: dict[str, str] = {}
    for field in node.descendants(unroll=False, skip_not_present=False):
        # Only a field with a reset value has a reset, and an alias field has none of its own.
        if not isinstance(field, FieldNode) or field.is_alias or field.get_property("reset") is None:
            continue
        signal = _reset_signal(field)
        if signal is not None and paths.setdefault(signal.inst_name, signal.get_path()) != signal.get_path():
            raise NotImplementedError(
                f"reset signals '{paths[signal.inst_name]}' and '{signal.get_path()}' have the same name, "
                "which would give them the same IP-XACT reset type"
            )


def build_memory_maps_from_node(node: AddrmapNode) -> list[ipxact.MemoryMap]:
    """Build the ipxact.MemoryMap of an elaborated SystemRDL addrmap, e.g. `root.top` after
    `RDLCompiler.elaborate()`.

    The memory map is named "<addrmap name>_mmap". When the addrmap only holds addrmaps and
    mems, each one becomes an address block of the memory map, which carries the addrmap's own
    name/desc. Otherwise the addrmap itself is the memory map's only address block, its nested
    addrmaps and regfiles becoming register files.

    Base addresses are absolute in the elaborated design: they start at 0 for `root.top`, but
    at the node's own address for a node nested in it.

    A field reset by a signal other than the default field_reset one refers to a reset type
    named after that signal, which build_component() adds to the component.

    The RDL is not compiled here, so the node can come from a compiler with user-defined
    properties registered, include directories or parameter overrides; user-defined properties
    are not part of the memory map. Raises NotImplementedError for what IEEE 1685-2022 cannot
    represent, instead of leaving it out, see the README's "Known limitations".
    """
    if not isinstance(node, AddrmapNode):
        raise TypeError(f"expected a systemrdl AddrmapNode, got: {type(node).__name__}")
    if node.is_array:
        raise ValueError(f"addrmap '{node.get_path()}' is an array, convert the addrmap holding it instead")
    if node.get_property("bridge"):
        raise NotImplementedError(f"addrmap '{node.get_path()}' is a bridge, which is not yet supported")
    _check_reset_signal_names(node)

    memory_map = f"{node.inst_name}_mmap"
    children = [child for child in node.children(skip_not_present=False) if isinstance(child, AddressableNode)]
    blocks = [child for child in children if isinstance(child, (AddrmapNode, MemNode))]
    if children and len(blocks) == len(children):
        return [
            ipxact.MemoryMap(
                name=memory_map,
                items=[_address_block_from_node(block, memory_map) for block in blocks],
                **_node_name_group(node),
            )
        ]
    return [ipxact.MemoryMap(name=memory_map, items=[_address_block_from_node(node, memory_map)])]


def build_memory_maps(
    register_file: Union[str, Path],
    *,
    udps: Iterable[type[UDPDefinition]] = (),
    include_dirs: Iterable[Union[str, Path]] = (),
    parameters: Optional[dict[str, Any]] = None,
) -> list[ipxact.MemoryMap]:
    """Compile `register_file` (a SystemRDL file) into its ipxact.MemoryMap objects, see
    build_memory_maps_from_node().

    `udps` are user-defined property classes to register, which the RDL can then use without
    declaring them. `include_dirs` are searched for `include files, after the file's own
    directory. `parameters` overrides the top-level addrmap's parameters, by name.

    The top-level addrmap is the last one defined. An addrmap defined in the file itself, not
    in an included one, but used nowhere under it raises ValueError, as does a compile error.
    A compiler warning raises NotImplementedError.
    """
    if isinstance(udps, type):
        raise TypeError(f"udps must be a list of user-defined property classes, got a single one: {udps!r}")
    if isinstance(include_dirs, (str, Path)):
        raise TypeError(f"include_dirs must be a list of directories, got a single one: {include_dirs!r}")
    messages = _MessageCollector()
    compiler = RDLCompiler()
    compiler.msg.printer = messages
    for udp in udps:
        compiler.register_udp(udp, soft=False)
    try:
        compiler.compile_file(str(register_file), incl_search_paths=[str(path) for path in include_dirs])
        root_node = compiler.elaborate(parameters=parameters)  # picks the last-defined addrmap
    except OSError as exc:
        raise ValueError(f"registerFile could not be read: {register_file} ({exc})") from exc
    except RDLCompileError as exc:
        raise ValueError(
            f"registerFile {register_file} failed to compile: " + "; ".join(messages.errors or [str(exc)])
        ) from exc

    # An addrmap defined in the file but never instantiated anywhere under the chosen top is a
    # second, unrelated design left in the same file, not a reusable sub-block: systemrdl-compiler
    # would otherwise silently ignore it rather than erroring. One from an included file is a
    # library the file only uses part of. orig_type_name, as type_name is mangled when the
    # addrmap's parameters are overridden; an addrmap only used with ispresent=false is still
    # used, build_memory_maps_from_node reports it.
    used_type_names = {root_node.top.orig_type_name}
    used_type_names.update(
        d.orig_type_name
        for d in root_node.top.descendants(unroll=False, skip_not_present=False)
        if isinstance(d, AddrmapNode)
    )
    source = Path(register_file).resolve()
    orphaned = [
        name
        for name, comp_def in compiler.root.comp_defs.items()
        if isinstance(comp_def, component.Addrmap)
        and name not in used_type_names
        and isinstance(comp_def.def_src_ref, FileSourceRef)
        and Path(comp_def.def_src_ref.path).resolve() == source
    ]
    if orphaned:
        raise ValueError(
            f"registerFile {register_file} defines addrmap(s) {', '.join(orphaned)}, never used "
            f"under the chosen top-level addrmap '{root_node.top.orig_type_name}'"
        )

    if messages.warnings:
        raise NotImplementedError(
            f"registerFile {register_file} uses a construct that could not be fully converted: "
            + "; ".join(messages.warnings)
        )

    return build_memory_maps_from_node(root_node.top)

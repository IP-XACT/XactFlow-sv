"""sv_parser.py: parse a SystemVerilog module header into plain dicts.

Uses pyslang to parse the file into a SyntaxTree, then reads the tree as JSON. Only the
module header is inspected (parameters and ports), with no elaboration, no symbol
resolution, no package loading. Types, values, and dimension expressions are taken
verbatim from the source text, matching how IP-XACT's own expression model treats them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pyslang


def parse_sv(sv_file: Path, defines: list[str], include_dirs: Optional[list[str]] = None) -> dict:
    """Parse `sv_file` with pyslang and return its SyntaxTree as a dict."""
    source_manager = pyslang.SourceManager()
    options = pyslang.parsing.PreprocessorOptions()
    options.predefines = list(defines)
    options.additionalIncludePaths = list(include_dirs or [])
    bag = pyslang.Bag()
    bag.preprocessorOptions = options
    tree = pyslang.syntax.SyntaxTree.fromFile(str(sv_file), source_manager, bag)

    errors = [d for d in tree.diagnostics if d.isError()]
    if errors:
        raise ValueError(
            f"{sv_file} has SystemVerilog errors:\n" + pyslang.DiagnosticEngine.reportAll(source_manager, errors)
        )

    return json.loads(tree.to_json())


def find_module(tree_json: dict, module_name: Optional[str] = None) -> dict:
    """Return the ModuleDeclaration node named `module_name`.

    Without `module_name`, the file must declare exactly one module: picking the first one
    would silently ignore the others.
    """
    modules = [m for m in tree_json.get("root", {}).get("members", []) if m.get("kind") == "ModuleDeclaration"]
    if not modules:
        raise ValueError("no module declaration found in input file")

    by_name = {extract_module_name(m.get("header", {})): m for m in modules}
    if module_name is not None:
        if module_name not in by_name:
            raise ValueError(f"module '{module_name}' not found in input file, found: {', '.join(by_name)}")
        return by_name[module_name]
    if len(modules) > 1:
        raise ValueError(
            f"input file declares several modules ({', '.join(by_name)}), choose one with the 'module' option"
        )
    return modules[0]


def _text(node: dict | list | str | None) -> str:
    """Recursively collect all 'text' leaf values from a JSON node, joined without spaces.

    This reassembles any expression exactly as written in the source (e.g. 'DATA_W-1',
    'pkg::CONST', '2**N'). Trivia (whitespace, comments) is intentionally skipped.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return ""  # bare strings are key names, not text
    if isinstance(node, list):
        return "".join(_text(n) for n in node)
    if isinstance(node, dict):
        kind = node.get("kind", "")
        if kind in ("Whitespace", "EndOfLine", "BlockComment", "LineComment"):
            return ""
        if "text" in node and not any(isinstance(v, dict) for v in node.values()):
            return node["text"]
        return "".join(_text(v) for k, v in node.items() if k != "kind")
    return ""


def _range_bounds(dim: dict, allow_size_shorthand: bool = False) -> tuple[str, str]:
    """Extract (left, right) from a VariableDimension node, as raw source text.

    A plain `[left:right]` selector (SimpleRangeSelect) gives the bounds directly. A lone
    `[N]` selector (BitSelect) is only valid SV grammar as an actual unpacked dimension (SV's
    shorthand for "array of N elements", implicitly bounded 0 to N-1, converted here to
    (N-1, 0)); pyslang's syntax-only parsing doesn't reject it appearing as a packed
    dimension too (e.g. a typo'd `logic [4] x` meaning `[3:0]`), so callers must only pass
    allow_size_shorthand=True when `dim` is actually an unpacked dimension. An indexed
    part-select (`[base+:width]`/`[base-:width]`, Ascending/DescendingRangeSelect) encodes a
    base and a width, not a left/right bound, and would need arithmetic to convert, so it is
    not yet supported.
    """
    spec = dim.get("specifier", {})
    sel = spec.get("selector", {})
    selector_kind = sel.get("kind", "")
    if allow_size_shorthand and selector_kind == "BitSelect":
        size = _text(sel.get("expr"))
        if size == "0":
            raise NotImplementedError("a zero-size array dimension ('[0]') is not supported")
        return f"({size})-1", "0"

    # A queue ([$]), dynamic array ([]), or wildcard ([*]) dimension has a different
    # specifier kind and no "selector", so it must be checked, not just the selector kind.
    is_range = spec.get("kind") == "RangeDimensionSpecifier"
    if not is_range or selector_kind != "SimpleRangeSelect":
        kind = selector_kind if is_range else spec.get("kind")
        raise NotImplementedError(
            f"dimension of kind '{kind}' is not yet supported, only a plain [left:right] range"
            + (" or a [N] array size is" if allow_size_shorthand else " is")
        )
    return _text(sel.get("left")), _text(sel.get("right"))


def _type_text(type_node: dict) -> str:
    """Return a parameter's data type as a string, built from the source text.

    The string is the type keyword, then the signing, then the packed dimensions, e.g.
    'int', 'int unsigned', 'bit signed [3:0]', 'logic [3:0]', 'my_pkg::my_t'.
    A parameter with no type keyword:
    - gets 'logic' when it has a signing or a range (`parameter signed [3:0] P` gives
      'logic signed [3:0]'),
    - gets 'int' otherwise (`parameter P = 3`).
    """
    if not type_node:
        return "int"  # implicit type, SV default is int for parameter

    kind = type_node.get("kind", "")

    if "keyword" in type_node and kind not in ("LogicType", "BitType", "RegType"):
        kw = _text(type_node["keyword"])
        signing = _text(type_node.get("signing")) if "signing" in type_node else ""
        return (kw + " " + signing).strip()

    if kind in ("LogicType", "BitType", "RegType", "ImplicitType"):
        kw = _text(type_node.get("keyword", {})) if kind != "ImplicitType" else "logic"
        signing = _text(type_node.get("signing"))
        dims = "".join(f"[{left}:{right}]" for left, right in _extract_dims(type_node))
        if kind == "ImplicitType" and not signing and not dims:
            return "int"
        return " ".join(part for part in (kw, signing, dims) if part)

    if kind in ("NamedType", "ScopedType"):
        return _text(type_node)

    return _text(type_node).strip() or "int"


def _value_text(declarator: dict) -> str:
    """Return the default value expression text for a parameter declarator."""
    init = declarator.get("initializer") or declarator.get("assignment")
    if not init:
        return ""
    expr = init.get("expr") or init.get("type")
    return _text(expr).strip()


def extract_module_name(header: dict) -> str:
    return _text(header.get("name", {}))


def extract_parameters(header: dict, members: Optional[list[dict]] = None) -> list[dict]:
    """Return the module's overridable parameters, as dicts: name, dataType, value, arrays.

    Following the SV LRM:
    - If the module has a parameter port list (`#(...)`), only that list is read. A
      declaration without a keyword takes the keyword of the one before it, and the first
      one defaults to 'parameter'. Body parameters are then local, so they are ignored.
    - Otherwise, the 'parameter' declarations of the module body (`members`) are read.

    'localparam' declarations are always skipped.
    """
    params_node = header.get("parameters")
    if params_node:
        declarations = params_node.get("declarations", [])
    else:
        declarations = [
            m.get("parameter", {}) for m in members or [] if m.get("kind") == "ParameterDeclarationStatement"
        ]

    result = []
    keyword = "ParameterKeyword"  # a first declaration without a keyword is a parameter
    for decl in declarations:
        kind = decl.get("kind")
        if kind not in ("ParameterDeclaration", "TypeParameterDeclaration"):
            continue

        keyword = decl.get("keyword", {}).get("kind") or keyword
        if keyword == "LocalParamKeyword":
            continue

        if kind == "TypeParameterDeclaration":
            for ta in decl.get("declarators", []):
                if ta.get("kind") != "TypeAssignment":
                    continue
                result.append(_parameter(ta, "type", arrays=[]))
        else:
            dtype = _type_text(decl.get("type"))
            for d in decl.get("declarators", []):
                if d.get("kind") != "Declarator":
                    continue
                result.append(_parameter(d, dtype, arrays=_extract_dims(d, allow_size_shorthand=True)))

    return result


def _parameter(declarator: dict, data_type: str, arrays: list[tuple[str, str]]) -> dict:
    name = _text(declarator.get("name"))
    value = _value_text(declarator)
    # A parameter without a default must be overridden by every instance, there is no value
    # to give the component parameter, and making one up would be silently wrong.
    if not value:
        raise NotImplementedError(f"parameter '{name}' has no default value, which is not yet supported")
    return {"name": name, "dataType": data_type, "value": value, "arrays": arrays}


# Map SV direction keyword kinds to IP-XACT direction strings.
_DIRECTION_MAP = {
    "InputKeyword": "in",
    "OutputKeyword": "out",
    "InOutKeyword": "inout",
    "RefKeyword": "inout",  # best approximation in IP-XACT
}


def _extract_dims(node: dict, allow_size_shorthand: bool = False) -> list[tuple[str, str]]:
    """Return the (left, right) dimensions listed in `node`'s 'dimensions' field.
    """
    return [
        _range_bounds(d, allow_size_shorthand)
        for d in node.get("dimensions", [])
        if d.get("kind") == "VariableDimension"
    ]


def extract_ports(header: dict) -> list[dict]:
    """Return a list of port dicts with keys:
    name, direction, is_interface, is_struct, type_name, packed_dims, unpacked_dims
    (plus iface_type, modport for interface ports).
    """
    ports_node = header.get("ports", {})
    result = []

    # Carry the last seen direction across ports that omit it (ANSI implicit). Per the SV
    # LRM, a direction-less port with no preceding port defaults to inout, not in.
    last_direction = "inout"

    for port in ports_node.get("ports", []):
        kind = port.get("kind")
        if kind == "Comma":
            continue
        if kind != "ImplicitAnsiPort":
            raise NotImplementedError(
                f"port list entry of kind '{kind}' is not yet supported, only implicit "
                "ANSI-style ports are (explicit ANSI ports and non-ANSI port lists need "
                "dedicated handling)"
            )

        port_header = port.get("header", {})
        declarator = port.get("declarator", {})
        name = _text(declarator.get("name", {}))

        dir_node = port_header.get("direction", {})
        dir_kind = dir_node.get("kind", "")
        direction = _DIRECTION_MAP.get(dir_kind, last_direction)
        if dir_kind:
            last_direction = direction

        if port_header.get("kind") == "InterfacePortHeader":
            iface_name = _text(port_header.get("nameOrKeyword", {}))
            modport = _text(port_header.get("modport", {}).get("member", {}))
            result.append(
                {
                    "name": name,
                    "direction": direction,
                    "is_interface": True,
                    "is_struct": False,
                    "iface_type": iface_name,
                    "modport": modport,
                    "packed_dims": [],
                    "unpacked_dims": _extract_dims(declarator, allow_size_shorthand=True),
                }
            )
            continue

        data_type = port_header.get("dataType", {})
        packed_dims = _extract_dims(data_type)
        unpacked_dims = _extract_dims(declarator, allow_size_shorthand=True)

        # A NamedType/ScopedType base (e.g. 'apb_req_t', 'my_pkg::my_req_t', or a
        # 'parameter type' default) is a struct/union/typedef reference, not a builtin
        # vector type: its width and field layout are unknown without elaboration.
        is_struct = data_type.get("kind") in ("NamedType", "ScopedType")
        type_name = _text(data_type) if is_struct else ""

        result.append(
            {
                "name": name,
                "direction": direction,
                "is_interface": False,
                "is_struct": is_struct,
                "type_name": type_name,
                "packed_dims": packed_dims,
                "unpacked_dims": unpacked_dims,
            }
        )

    return result

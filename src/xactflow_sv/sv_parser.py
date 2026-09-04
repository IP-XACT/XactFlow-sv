"""sv_parser.py: parse a SystemVerilog module header into plain dicts.

Uses pyslang to parse the file into a SyntaxTree, then reads the tree as JSON. Only the
module header is inspected (parameters and ports), with no elaboration, no symbol
resolution, no package loading. Types, values, and dimension expressions are taken
verbatim from the source text, matching how IP-XACT's own expression model treats them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyslang


def parse_sv(sv_file: Path, defines: list[str]) -> dict:
    """Parse `sv_file` with pyslang and return its SyntaxTree as a dict."""
    if defines:
        source_manager = pyslang.SourceManager()
        bag = pyslang.Bag()
        options = pyslang.parsing.PreprocessorOptions()
        options.predefines = list(defines)
        bag.preprocessorOptions = options
        tree = pyslang.syntax.SyntaxTree.fromFile(str(sv_file), source_manager, bag)
    else:
        tree = pyslang.syntax.SyntaxTree.fromFile(str(sv_file))

    return json.loads(tree.to_json())


def find_module(tree_json: dict) -> dict:
    """Return the first ModuleDeclaration node in the tree."""
    for member in tree_json.get("root", {}).get("members", []):
        if member.get("kind") == "ModuleDeclaration":
            return member
    raise ValueError("no module declaration found in input file")


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


def _range_bounds(dim: dict) -> tuple[str, str]:
    """Extract (left, right) from a VariableDimension node, as raw source text.

    Only a plain `[left:right]` selector (SimpleRangeSelect) is supported: an indexed
    part-select (`[base+:width]`/`[base-:width]`, Ascending/DescendingRangeSelect) encodes
    a base and a width, not a left/right bound, and would need arithmetic to convert.
    """
    spec = dim.get("specifier", {})
    sel = spec.get("selector", {})
    selector_kind = sel.get("kind", "")
    if selector_kind and selector_kind != "SimpleRangeSelect":
        raise NotImplementedError(
            f"dimension selector of kind '{selector_kind}' is not yet supported, only a plain "
            "[left:right] range is"
        )
    return _text(sel.get("left")), _text(sel.get("right"))


def _type_text(type_node: dict) -> str:
    """Return a parameter's data type string as written in the source.

    Walks the type node and collects the keyword plus any dimensions, e.g. 'int',
    'bit', 'int unsigned', 'logic [3:0]', 'my_pkg::my_t'.
    """
    if not type_node:
        return "int"  # implicit type, SV default is int for parameter

    kind = type_node.get("kind", "")

    if "keyword" in type_node and kind not in ("LogicType", "BitType", "RegType"):
        kw = _text(type_node["keyword"])
        signing = _text(type_node.get("signing")) if "signing" in type_node else ""
        return (kw + " " + signing).strip()

    if kind in ("LogicType", "BitType", "RegType"):
        kw = _text(type_node.get("keyword", {}))
        dims = type_node.get("dimensions", [])
        if dims:
            dim_str = "".join(
                f"[{_range_bounds(d)[0]}:{_range_bounds(d)[1]}]"
                for d in dims
                if d.get("kind") == "VariableDimension"
            )
            return f"{kw} {dim_str}".strip()
        return kw

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


def extract_parameters(header: dict) -> list[dict]:
    """Return a list of parameter dicts with keys: name, dataType, value.

    Only 'parameter' keywords are included; 'localparam' nodes are skipped.
    """
    params_node = header.get("parameters")
    if not params_node:
        return []

    result = []
    for decl in params_node.get("declarations", []):
        kind = decl.get("kind")
        if kind not in ("ParameterDeclaration", "TypeParameterDeclaration"):
            continue

        kw_kind = decl.get("keyword", {}).get("kind", "")
        if kw_kind == "LocalParamKeyword":
            continue

        if kind == "TypeParameterDeclaration":
            for ta in decl.get("declarators", []):
                if ta.get("kind") != "TypeAssignment":
                    continue
                name = _text(ta.get("name"))
                value = _value_text(ta)
                result.append({"name": name, "dataType": "type", "value": value})
        else:
            dtype = _type_text(decl.get("type"))
            for d in decl.get("declarators", []):
                if d.get("kind") != "Declarator":
                    continue
                name = _text(d.get("name"))
                value = _value_text(d)
                result.append({"name": name, "dataType": dtype, "value": value})

    return result


# Map SV direction keyword kinds to IP-XACT direction strings.
_DIRECTION_MAP = {
    "InputKeyword": "in",
    "OutputKeyword": "out",
    "InOutKeyword": "inout",
    "RefKeyword": "inout",  # best approximation in IP-XACT
}


def _extract_dims(node: dict) -> list[tuple[str, str]]:
    """Return the (left, right) dimensions listed in `node`'s 'dimensions' field.

    Used both for a dataType node's packed dimensions and a declarator node's unpacked
    dimensions: both hold a plain list of VariableDimension nodes in that field.
    """
    return [
        _range_bounds(d) for d in node.get("dimensions", []) if d.get("kind") == "VariableDimension"
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
                    "unpacked_dims": _extract_dims(declarator),
                }
            )
            continue

        data_type = port_header.get("dataType", {})
        packed_dims = _extract_dims(data_type)
        unpacked_dims = _extract_dims(declarator)

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

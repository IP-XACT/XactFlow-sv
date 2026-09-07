# xactflow-sv

A [XactFlow](https://github.com/IP-XACT/XactFlow) importer plugin that reads a SystemVerilog
module plus a JSON metadata file (and, optionally, a SystemRDL register map) and produces an
`ipxact.Component` object, built on
[`ipxact-compiler`](https://github.com/IP-XACT/ipxact-compiler)'s
[IEEE 1685-2022](https://standards.ieee.org/ieee/1685/10307/) object model.

`ipxact-compiler` parses IP-XACT and hands back Python objects; XactFlow's `Importer` interface
is for the other direction, reading some non-IP-XACT source and producing that object model.
This package is one such importer: it does not write XML itself, turning the resulting
`Component` into a file is a separate exporter's job (see
[`xactflow-component`](https://github.com/IP-XACT/XactFlow-component)).

The SystemVerilog parsing and metadata-driven design was informed by
[`ipxact-sv2ipxact`](https://github.com/mtravaillard/ipxact-sv2ipxact), an earlier, experimental
standalone tool that solves a closely related problem by writing IP-XACT XML directly. This
package is a from-scratch implementation built against `ipxact-compiler`'s object model rather
than a fork of it, and its scope, dependencies, and error handling are its own.

## Installation

```bash
pip install xactflow-sv
```

For local development, `requirements-dev.txt` overrides `ipxact-compiler` and `xactflow` to
local installations. It can be modified to adapt the paths or to keep either version on PyPI.

```bash
pip install -r requirements-dev.txt -e .
```

Requires Python >= 3.9. Runtime dependencies beyond `ipxact-compiler`/`xactflow`:
[`pyslang`](https://pypi.org/project/pyslang/) for SystemVerilog parsing, and
[`systemrdl-compiler`](https://pypi.org/project/systemrdl-compiler/)/
[`peakrdl-ipxact`](https://pypi.org/project/peakrdl-ipxact/) for register map conversion.

## Usage

An importer's job stops at producing an `ipxact.Component` object; it does not write anything
out on its own. As a library:

```python
from pathlib import Path

from xactflow_sv import SVImporter

component = SVImporter().import_(Path("my_module.sv"), metadata="my_module_ipxact.json")
component.vlnv            # VLNV
component.model.ports     # list[Port]
component.bus_interfaces  # list[BusInterface]
component.memory_maps     # list[MemoryMap]
```

To get an actual IP-XACT XML file, hand `component` to an exporter, e.g.
[`xactflow-component`](https://github.com/IP-XACT/XactFlow-component):

```python
from xactflow_component import ComponentExporter

ComponentExporter().export(component, Path("out"))  # writes out/<name>.xml
```

Once installed, the importer also registers itself under the `xactflow.importers` entry point
group as `sv`, so it becomes available as a XactFlow CLI subcommand:

```bash
xactflow sv my_module.sv --option metadata=my_module_ipxact.json
```

This is only useful as a quick smoke test of the SV/metadata parsing itself, not to get an
actual `Component` out: XactFlow's CLI currently has no way to chain an importer straight into
an exporter, so this just prints a one-line summary (`imported '...' via 'sv': Component`) and
discards the result. Getting real output today means using this package as a library, as above.
An issue as been open on [XactFlow](https://github.com/IP-XACT/XactFlow/issues/2), in order to
find a solution for this.

Only the SystemVerilog module header (parameters and ports) is inspected, with no elaboration,
no package loading, no symbol resolution: types, values, and dimension expressions are taken
verbatim from the source text, matching how IP-XACT's own expression model treats them.

### Metadata file

A small JSON file supplies what can't be inferred from the SV source alone:

```json
{
    "vendor": "Vendor",
    "library": "Library",
    "version": "1.0",
    "busInterfaces": {
        "apb": {
            "bus": "Vendor:Library:apb4:1.0",
            "mode": "target",
            "ports": {"PSEL": "psel", "PADDR": "bus_req_i.paddr"}
        }
    },
    "registerFile": "register_map.rdl"
}
```

- `vendor`/`library` are required, `version` defaults to `"1.0"`. The component's `name` always
  comes from the parsed module name, not from this file.
- `busInterfaces` maps a bus interface name to its bus VLNV, mode (`initiator`/`target`, or
  `master`/`slave`), and either a `ports` mapping of logical bus signal names onto physical SV
  port names, or an `interfacePort` naming a genuine SV `interface`-typed port that already
  groups its own signals. A `ports` value can be a plain port name or a dotted `port.field`
  reference into a struct-typed port (e.g. `bus_req_i.paddr`). The abstraction definition is
  derived by convention as `<bus-def-name>_rtl` at the same vendor/library/version as `bus`.
  Every `interface`-typed port in the module must be described this way; there is no fallback
  guess for its bus VLNV or mode.
- `registerFile` is a path, relative to the metadata file, to a SystemRDL file describing the
  component's register map. It is compiled with `systemrdl-compiler`, converted with
  `peakrdl-ipxact`, and merged in as the component's `memory_maps`.

`--option define=SYM1,SYM2` (or `defines=[...]` when calling `import_` directly) predefines
preprocessor symbols before parsing.

## Design notes

- **Three independent stages**, each targeting `ipxact-compiler` dataclasses directly rather
  than XML: `sv_parser.py` parses the module header into plain dicts, `component_builder.py`
  turns those into `ipxact.Port`/`ipxact.Parameter`/the component's `Model`,
  `bus_interfaces.py` builds `ipxact.BusInterface` from the metadata's `busInterfaces`, and
  `register_map.py` builds `ipxact.MemoryMap` from a SystemRDL file.
- **`register_map.py` reads `peakrdl-ipxact`'s own XML output, not through
  `ipxact-compiler`'s parser.** `peakrdl-ipxact` only supports IEEE 1685-2014, while
  `ipxact-compiler` only supports IEEE 1685-2022. A namespace swap alone would silently lose
  that data (a field's access policy, or a register's array dimensions, would just come back
  empty rather than erroring), so a dedicated reader is used instead. This is a keep-it-simple
  choice for now, maybe not permanent, see [Ideas for later](#ideas-for-later).
- **Fails loudly instead of silently dropping data.** Constructs this package does not yet
  support (see below) raise `NotImplementedError`/`ValueError` rather than producing a
  `Component` that quietly leaves something out.

## Known limitations

- **Explicit ANSI ports** (`.name(expr)` syntax) **and non-ANSI port lists** (`module m(a, b);
  input a; ...`) **are not supported.** Only implicit ANSI-style ports are.
  A direction-less first port defaults to `inout`, per the SystemVerilog LRM.
- **Indexed part-select dimensions are not supported**, e.g. `[base+:width]`/`[base-:width]`.
  Only a plain `[left:right]` range is.
- **Register/register file arrays are not supported.**
- **A `registerFile` defining an `addrmap` that is never used under the chosen top-level
  `addrmap` is rejected**, rather than silently ignored as SystemRDL itself would. A reusable
  sub-block `addrmap` instantiated under a real top is unaffected.
- **A register/field/register file/address block with `ispresent = false` is not supported.**
  IEEE 1685-2022 has no equivalent of `isPresent`, so there is no way to represent one as
  conditionally absent rather than silently promoting it to always-present.
- **A SystemRDL construct `peakrdl-ipxact` cannot export at all raises an error**, e.g. a `mem`
  node nested inside another node's hierarchy, rather than silently producing an incomplete
  memory map.
- **`fileSets` are not generated.** The component never records which `.sv` file the module
  actually came from.

## Ideas for later

- **Have `register_map.py` build on `ipxact-compiler`'s own parser instead of its own
  ElementTree reader.** Not a quick swap: `peakrdl-ipxact` only emits IEEE 1685-2014 XML, and
  `ipxact-compiler`'s parser expects 2022's structure for field access and array dimensions
  (see [Design notes](#design-notes)). Feeding it 2014 XML after only a namespace fix-up
  parses without error but silently comes back missing that data, confirmed by testing it
  directly. Doing this properly needs a 2014-to-2022 XML restructuring step first (in this
  package, not in `ipxact-compiler`, which is a 1685-2022 parser by design), or for
  `peakrdl-ipxact` to gain 2022 output support upstream.

## License

LGPL-3.0. See [LICENSE](LICENSE).

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
out on its own, so getting an actual IP-XACT XML file also needs an exporter, like
[`xactflow-component`](https://github.com/IP-XACT/XactFlow-component). Once both are installed,
the importer registers itself under the `xactflow.importers` entry point group as `sv`, so
it can run as one XactFlow CLI command, `--then` chaining straight into the exporter
in the same process:

```bash
xactflow sv my_module.sv --option metadata=my_module_ipxact.json --then component --output out/
# writes out/<vendor>_<library>_<name>_<version>.xml
```

Or as a library, e.g. to inspect or modify the `Component` before exporting it:

```python
from pathlib import Path

from xactflow_sv import SVImporter
from xactflow_component import ComponentExporter

component = SVImporter().import_(Path("my_module.sv"), metadata="my_module_ipxact.json")
component.vlnv            # VLNV
component.model.ports     # list[Port]
component.bus_interfaces  # list[BusInterface]
component.memory_maps     # list[MemoryMap]

ComponentExporter().export(component, Path("out"))  # writes out/<vendor>_<library>_<name>_<version>.xml
```

Only the module's parameters and ports are read (from its header, and its body parameters when
it has no parameter port list), with no elaboration, no package loading, no symbol resolution:
types, values, and dimension expressions are taken verbatim from the source text, matching how
IP-XACT's own expression model treats them.

### Generated component

- **VLNV**: vendor, library and version from the metadata file, name from the module.
- **Model**: one view `rtl` referencing one component instantiation `<module>_rtl`, with
  `language` `systemverilog` and `moduleName` the module name.
- **Parameters**: each overridable SV parameter gives a component `parameter` (with
  `parameterId` and `resolve="user"`, holding the default value) and a `moduleParameter`
  whose value references that `parameterId`, so a design override reaches the HDL module.
  The `moduleParameter`'s `dataType` is the SV type as written (`int`, `logic [7:0]`,
  `type` for a type parameter, ...), and unpacked dimensions become `arrays` on both.
- **Ports**:
  - a vector or scalar port is a `wire` port, its packed dimensions giving the `vectors`.
    Built-in integer types get their width, e.g. `int` gives `[31:0]`;
  - a struct/typedef-typed port is a `structured` port of type `struct`, with the type name
    in `structPortTypeDefs` (its width is not known without elaboration);
  - an SV `interface` port is a `structured` port of type `interface`, with the interface
    name and its modport as role;
  - unpacked dimensions become `arrays`, `[N]` giving `[(N)-1:0]`.
- **Bus interfaces** and **memory maps**: from the metadata file, see below.

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
            "ports": {"PSEL": "psel", "PADDR": "bus_req_i.paddr"},
            "memoryMap": "register_map_mmap"
        }
    },
    "registerFile": "register_map.rdl"
}
```

- `vendor`/`library` are required, `version` defaults to `"1.0"`. The component's `name` always
  comes from the parsed module name, not from this file.
- `busInterfaces` maps a bus interface name to its bus VLNV, mode, and either a `ports`
  mapping of logical bus signal names onto physical SV port names, or an `interfacePort`
  naming a genuine SV `interface`-typed port that already groups its own signals. A `ports`
  value can be a plain port name or a dotted `port.field` reference into a struct-typed port
  (e.g. `bus_req_i.paddr`). For a `ports` mapping, the abstraction definition is derived by
  convention as `<bus-def-name>_rtl` at the same vendor/library/version as `bus`. Every
  `interface`-typed port in the module must be described this way; there is no fallback guess
  for its bus VLNV or mode.
- `mode` is one of (case-insensitive):

  | mode | also accepted | extra keys |
  |---|---|---|
  | `initiator` | `master` | |
  | `target` | `slave` | `memoryMap` (optional) |
  | `system` | | `group` (required) |
  | `mirroredInitiator` | `mirroredMaster` | |
  | `mirroredTarget` | `mirroredSlave` | |
  | `mirroredSystem` | | `group` (required) |
  | `monitor` | | `monitoredMode` (required), `group` (required if `monitoredMode` is a system mode) |

  `group` is the system group name from the bus definition. `monitoredMode` is the mode of
  the interface the monitor watches, any mode but `monitor`.
- `memoryMap` names the memory map a target interface gives access to. When the component has
  exactly one target interface and one memory map, that target references it by default.
- `registerFile` is a path (absolute, or relative to the metadata file) to a SystemRDL file
  describing the component's register map. It is compiled with `systemrdl-compiler`,
  converted with `peakrdl-ipxact`, and merged in as the component's `memory_maps`. Its memory
  map is named `<addrmap name>_mmap`.

Any other key, at the top level or in a `busInterfaces` entry, raises a `ValueError`, so a
misspelled key is not silently ignored.

### Options

Given as `--option name=value` on the CLI, or as keyword arguments to `import_`:

- `metadata` (required): path to the metadata file.
- `define`: preprocessor symbols to predefine before parsing, `--option define=SYM1,SYM2=4`
  on the CLI or `define=["SYM1", "SYM2=4"]` in Python.
- `include`: directories to search for `` `include `` files, in addition to the `.sv` file's
  own directory, `--option include=rtl/include,rtl/pkg` or `include=["rtl/include", ...]`.
- `module`: name of the module to import. Required when the file declares more than one
  module.

Paths given in options are relative to the current directory. Any SystemVerilog error
reported while parsing (syntax error, include file not found, unknown macro) raises a
`ValueError` with its file and line, instead of importing a partially parsed module.

## Design notes

- **Four independent stages**, each targeting `ipxact-compiler` dataclasses directly rather
  than XML: `sv_parser.py` reads the module's parameters and ports into plain dicts,
  `component_builder.py` turns those into `ipxact.Port`/`ipxact.Parameter`/the component's `Model`,
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
- **Only `[left:right]` dimensions are supported**, plus the `[N]` size shorthand on an unpacked
  dimension. Indexed part-selects (`[base+:width]`/`[base-:width]`), queues (`[$]`), dynamic
  arrays (`[]`) and a zero size (`[0]`) raise an error.
- **Ports with a data type that has no bit width are not supported**: `real`, `shortreal`,
  `realtime`, `string`, `chandle`, `event`. Built-in integer types get their width as a
  vector, e.g. `int` gives `[31:0]` and `byte` gives `[7:0]`.
- **A parameter without a default value is not supported** (`parameter int N` or
  `parameter type T` with no `= ...`), as there is no value to give the component parameter.
  Parameters are read from the parameter port list (`#(...)`), or from the module body when
  the module has none, `localparam`s being skipped, per the SystemVerilog LRM.
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

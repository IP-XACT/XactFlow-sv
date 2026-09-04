__version__ = "0.1.0"

import json
from pathlib import Path

from xactflow import Importer

from .bus_interfaces import build_bus_interface, validate_bus_interfaces
from .component_builder import build_component
from .sv_parser import extract_module_name, extract_parameters, extract_ports, find_module, parse_sv


class SVImporter(Importer):
    """Reads a SystemVerilog module plus a JSON metadata file into an ipxact.Component.

    The metadata file's "vendor" and "library" are required, "version" defaults to "1.0".
    "busInterfaces" is optional and maps a bus interface name to its bus/mode/port mapping,
    see bus_interfaces.py. "registerFile" is not read yet; register-map conversion is a
    later phase.
    """

    name = "sv"

    def import_(self, source_path: Path, **options: object) -> object:
        if "metadata" not in options:
            raise ValueError("missing required option 'metadata' (path to the IP-XACT metadata JSON file)")
        metadata_path = Path(options["metadata"])  # resolved relative to the CWD
        metadata = json.loads(metadata_path.read_text())

        try:
            vendor = metadata["vendor"]
            library = metadata["library"]
        except KeyError as exc:
            raise ValueError(f"metadata file {metadata_path} is missing required field {exc}") from exc
        version = metadata.get("version", "1.0")

        defines_option = options.get("define", "")
        defines = [d for d in str(defines_option).split(",") if d]

        tree_json = parse_sv(source_path, defines)
        module_node = find_module(tree_json)
        header = module_node.get("header", {})
        module_name = extract_module_name(header)
        params = extract_parameters(header)
        ports = extract_ports(header)

        bus_interfaces_meta = metadata.get("busInterfaces") or {}
        validate_bus_interfaces(bus_interfaces_meta, ports)
        bus_interfaces = [
            build_bus_interface(name, iface) for name, iface in bus_interfaces_meta.items()
        ]

        return build_component(module_name, vendor, library, version, params, ports, bus_interfaces)


__all__ = ["__version__", "SVImporter"]

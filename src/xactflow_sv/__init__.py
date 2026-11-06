__version__ = "0.2.0"

import json
from pathlib import Path

from xactflow import Importer

from .bus_interfaces import build_bus_interfaces, validate_bus_interfaces
from .component_builder import build_component
from .register_map import build_memory_maps
from .sv_parser import extract_module_name, extract_parameters, extract_ports, find_module, parse_sv

# Accepted top-level keys of the metadata file.
_METADATA_KEYS = ("vendor", "library", "version", "busInterfaces", "registerFile")


def _list_option(options: dict, name: str) -> list[str]:
    """Read option `name` as a list of strings.

    Accepts a comma-separated string (how the CLI's --option passes it) or a list of strings
    (when calling import_ directly).
    """
    value = options.get(name)
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return [v.strip() for v in value if v.strip()]
    raise ValueError(f"option '{name}' must be a comma-separated string or a list of strings, got: {value!r}")


class SVImporter(Importer):
    """Reads a SystemVerilog module plus a JSON metadata file into an ipxact.Component.

    The metadata file's "vendor" and "library" are required, "version" defaults to "1.0".
    "busInterfaces" is optional and maps a bus interface name to its bus/mode/port mapping,
    see bus_interfaces.py. "registerFile" is optional and is a path to a SystemRDL register
    map, resolved relative to the metadata file unless it is itself absolute, see
    register_map.py.

    Options: "metadata" (required), "define" (preprocessor symbols), "include" (`include
    search directories, resolved relative to the CWD), and "module" (the module to import,
    required when the file declares more than one).
    """

    name = "sv"

    def import_(self, source_path: Path, **options: object) -> object:
        if "metadata" not in options:
            raise ValueError("missing required option 'metadata' (path to the IP-XACT metadata JSON file)")
        metadata_path = Path(options["metadata"])  # resolved relative to the CWD
        metadata = json.loads(metadata_path.read_text())
        if not isinstance(metadata, dict):
            raise ValueError(
                f"metadata file {metadata_path} must contain a JSON object, got: {type(metadata).__name__}"
            )

        # A misspelled key would otherwise be silently ignored, dropping what it describes.
        unknown = [key for key in metadata if key not in _METADATA_KEYS]
        if unknown:
            raise ValueError(
                f"metadata file {metadata_path} has unknown key(s) {', '.join(unknown)} "
                f"(expected: {', '.join(_METADATA_KEYS)})"
            )

        try:
            vendor = metadata["vendor"]
            library = metadata["library"]
        except KeyError as exc:
            raise ValueError(f"metadata file {metadata_path} is missing required field {exc}") from exc
        version = metadata.get("version", "1.0")

        defines = _list_option(options, "define")
        include_dirs = _list_option(options, "include")
        module_option = options.get("module")
        if module_option is not None and not isinstance(module_option, str):
            raise ValueError(f"option 'module' must be a module name string, got: {module_option!r}")

        tree_json = parse_sv(source_path, defines, include_dirs)
        module_node = find_module(tree_json, module_option or None)
        header = module_node.get("header", {})
        module_name = extract_module_name(header)
        params = extract_parameters(header, module_node.get("members", []))
        ports = extract_ports(header)

        register_file = metadata.get("registerFile")
        if register_file is not None and not (isinstance(register_file, str) and register_file):
            raise ValueError(f"metadata file 'registerFile' must be a non-empty path string, got: {register_file!r}")
        memory_maps = (
            build_memory_maps(metadata_path.parent / register_file)
            if register_file
            else []
        )
        memory_map_names = [memory_map.name for memory_map in memory_maps]

        bus_interfaces_meta = metadata.get("busInterfaces") or {}
        validate_bus_interfaces(bus_interfaces_meta, ports, memory_map_names)
        bus_interfaces = build_bus_interfaces(bus_interfaces_meta, memory_map_names)

        return build_component(
            module_name, vendor, library, version, params, ports, bus_interfaces, memory_maps
        )


__all__ = ["__version__", "SVImporter"]

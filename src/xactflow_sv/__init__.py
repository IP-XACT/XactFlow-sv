__version__ = "0.3.0"

import json
from pathlib import Path

from xactflow import Importer, Library

from .bus_interfaces import build_bus_interfaces, validate_bus_interfaces
from .component_builder import build_component
from .qualifiers import find_qualifiers
from .register_map import build_memory_maps
from .sv_parser import extract_module_name, extract_parameters, extract_ports, find_module, parse_sv

# Accepted top-level keys of the metadata file.
_METADATA_KEYS = ("vendor", "library", "version", "busInterfaces", "registerFile", "clocks", "resets")

# Options that can be given instead of a metadata file, and the metadata key each one stands
# for. "vlnv_" keeps "vlnv_library" apart from import_()'s own `library` argument.
_METADATA_OPTIONS = {
    "vlnv_vendor": "vendor",
    "vlnv_library": "library",
    "vlnv_version": "version",
    "registerFile": "registerFile",
}


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


def _read_metadata(options: dict) -> tuple[dict, Path, str]:
    """Return the metadata, the directory its "registerFile" is relative to, and where it comes
    from for error messages.

    It is read from the "metadata" file, or else built from the "vlnv_vendor", "vlnv_library",
    "vlnv_version" and "registerFile" options, never from both, so that nothing is given twice with
    different values.
    """
    given = [name for name in _METADATA_OPTIONS if name in options]
    if "metadata" in options:
        if given:
            raise ValueError(
                f"option(s) {', '.join(given)} cannot be used with option 'metadata', "
                "give them in the metadata file instead"
            )
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
        return metadata, metadata_path.parent, f"metadata file {metadata_path}"

    for name in given:
        if not isinstance(options[name], str):
            raise ValueError(f"option '{name}' must be a string, got: {options[name]!r}")
    missing = [name for name in ("vlnv_vendor", "vlnv_library") if name not in options]
    if missing:
        raise ValueError(
            f"missing required option(s) {', '.join(missing)}, or option 'metadata' "
            "(path to the IP-XACT metadata JSON file)"
        )
    return {_METADATA_OPTIONS[name]: options[name] for name in given}, Path("."), "options"  # resolved relative to the CWD


class SVImporter(Importer):
    """Reads a SystemVerilog module plus a JSON metadata file into an ipxact.Component, added to
    the library as a target.

    The component is regenerated from its source, so it replaces any document of the same VLNV
    already in the library, e.g. its previous output scanned from a --lib folder.

    The metadata file's "vendor" and "library" are required, "version" defaults to "1.0".
    "busInterfaces" is optional and maps a bus interface name to its bus/mode/port mapping,
    see bus_interfaces.py. "registerFile" is optional and is a path to a SystemRDL register
    map, resolved relative to the metadata file unless it is itself absolute, see
    register_map.py. "clocks" and "resets" are optional and list the clock/reset ports, given
    their isClock/isReset qualifier; without them, they are detected from the port names, see
    qualifiers.py.

    Options: "metadata" (path to the metadata file), or instead "vlnv_vendor",
    "vlnv_library", "vlnv_version" and "registerFile" (resolved relative to the CWD), which
    then have the same meaning as the metadata file's "vendor", "library", "version" and
    "registerFile". "define" (preprocessor symbols), "include" (`include
    search directories, resolved relative to the CWD), and "module" (the module to import,
    required when the file declares more than one).
    """

    name = "sv"

    def import_(self, source_path: Path, library: Library, **options: object) -> None:
        metadata, base_dir, where = _read_metadata(options)

        try:
            vendor = metadata["vendor"]
            library_name = metadata["library"]
        except KeyError as exc:
            raise ValueError(f"{where} is missing required field {exc}") from exc
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
            raise ValueError(f"{where}: 'registerFile' must be a non-empty path string, got: {register_file!r}")
        memory_maps = (
            build_memory_maps(base_dir / register_file)
            if register_file
            else []
        )
        memory_map_names = [memory_map.name for memory_map in memory_maps]

        bus_interfaces_meta = metadata.get("busInterfaces") or {}
        validate_bus_interfaces(bus_interfaces_meta, ports, memory_map_names)
        bus_interfaces = build_bus_interfaces(bus_interfaces_meta, memory_map_names)

        qualifiers = find_qualifiers(ports, metadata.get("clocks"), metadata.get("resets"))

        component = build_component(
            module_name, vendor, library_name, version, params, ports, bus_interfaces, memory_maps, qualifiers
        )
        library.add(component, source_path, replace=True, target=True)


__all__ = ["__version__", "SVImporter"]

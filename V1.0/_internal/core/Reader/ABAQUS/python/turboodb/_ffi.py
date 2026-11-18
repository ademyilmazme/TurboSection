"""ctypes binding of turboodb.dll (native/include/turboodb/turboodb.h):
loading, the mirrored structures and status -> exception mapping.  No result
logic: every value and every name comes from the native library.

The DLL is found at ``TURBOODB_NATIVE_DLL``, in ``native/build/<config>/``
(development) or in ``runtime/bin/`` (distribution: packaging/build_release.py).
Its Abaqus runtime (the ODB API and the SIMULIA / CATIA frameworks it loads)
is resolved in this order (:func:`runtime_source`), never through PATH:

1. ``runtime/bin`` — the project's runtime bundle, when every file of
   ``runtime/manifest.json`` is present and the bundle is of the Abaqus
   release turboodb.dll was built against (runtime/bundle_runtime.py);
2. the Windows system — the Visual C++ redistributable (MFC included) and
   system DLLs (System32; msvcp140 checked to be recent enough);
3. the Abaqus installation of the build (``turboodb_build_info.json`` next to
   the DLL: ``win_b64/code/bin``) — a development fall back when the bundle
   is absent or incomplete.  ``TURBOODB_ABAQUS_ROOT`` selects an
   installation explicitly (it then replaces the bundle).

The loading itself is core/Reader/common/native_loader.py, shared with the
ANSYS reader.
"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

ABI_VERSION = 3
READER_ROOT = Path(__file__).resolve().parents[2]          # core/Reader/ABAQUS
BUILD_CONFIGS = ("Release", "RelWithDebInfo", "MinSizeRel", "Debug")
RUNTIME_DIR = READER_ROOT / "runtime" / "bin"
RUNTIME_MANIFEST = READER_ROOT / "runtime" / "manifest.json"


def _common_loader():
    """core/Reader/common/native_loader.py (loaded by path, once per process)."""
    name = "turbosection_reader_native_loader"
    module = sys.modules.get(name)
    if module is None:
        path = READER_ROOT.parent / "common" / "native_loader.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return module


_L = _common_loader()
module_version, file_version = _L.module_version, _L.file_version

OK, E_ARGUMENT, E_HANDLE, E_OPEN, E_FORMAT, E_RANGE, E_BUFFER, E_STATE, E_NATIVE, E_MEMORY, \
    E_INTERNAL, E_MISSING, E_VERSION = 0, -1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -11, -12
STATUS_NAMES = {OK: "OK", E_ARGUMENT: "ARGUMENT", E_HANDLE: "HANDLE", E_OPEN: "OPEN",
                E_FORMAT: "FORMAT", E_RANGE: "RANGE", E_BUFFER: "BUFFER", E_STATE: "STATE",
                E_NATIVE: "NATIVE", E_MEMORY: "MEMORY", E_INTERNAL: "INTERNAL",
                E_MISSING: "MISSING", E_VERSION: "VERSION"}

NAME_SIZE = 256
TEXT_SIZE = 1024
MAX_LOCATIONS = 16
MAX_INVARIANTS = 16
ASSEMBLY = -1
ALL_INSTANCES = -2
NODE_SET, ELEMENT_SET, SURFACE = 0, 1, 2
BLOCK_INSTANCE, BLOCK_POSITION, BLOCK_TYPE, BLOCK_PRECISION, BLOCK_WIDTH, \
    BLOCK_ORIENTATION_WIDTH, BLOCK_HAS_CONJUGATE, BLOCK_SECTION_POINT, BLOCK_INTS = range(9)
BLOCK_ELEMENT_TYPE, BLOCK_SECTION_POINT_NAME, BLOCK_COMPONENTS, BLOCK_NAMES = range(4)

i32, i64, f64 = ctypes.c_int32, ctypes.c_int64, ctypes.c_double
P_i32, P_i64, P_f64 = ctypes.POINTER(i32), ctypes.POINTER(i64), ctypes.POINTER(f64)
P_char = ctypes.POINTER(ctypes.c_char)
vp = ctypes.c_void_p


# ------------------------------------------------------------------ errors
class TurboODBError(Exception):
    """The native library could not do what was asked (base class)."""


class UnavailableError(TurboODBError):
    """The native library or its Abaqus runtime cannot be loaded."""


class OpenError(TurboODBError):
    """The file cannot be opened as an ODB (missing, not an ODB, corrupt)."""


class VersionError(OpenError):
    """The ODB was written by another Abaqus release (needs upgrading)."""


class FormatError(TurboODBError):
    """Inconsistent data in an opened ODB."""


class RangeError(TurboODBError, IndexError):
    """An instance, step, frame or field index outside the file's."""


class ClosedError(TurboODBError):
    """The file was used after close()."""


class NativeError(TurboODBError):
    """The ODB API raised an error."""


class MissingError(TurboODBError, LookupError):
    """No such data in this frame or file (a valid state)."""


class ArgumentError(TurboODBError, ValueError):
    """An argument the native library rejects."""


# ------------------------------------------------------------ structures
class Info(ctypes.Structure):
    _fields_ = [("struct_size", i32), ("n_instances", i32), ("n_steps", i32),
                ("analysis_code", i32), ("precision", i32), ("has_sector_definition", i32),
                ("file_size", i64), ("n_truncated", i32), ("reserved", i32),
                ("name", ctypes.c_char * NAME_SIZE), ("title", ctypes.c_char * TEXT_SIZE),
                ("description", ctypes.c_char * TEXT_SIZE), ("version", ctypes.c_char * NAME_SIZE),
                ("creation_time", ctypes.c_char * NAME_SIZE),
                ("modification_time", ctypes.c_char * NAME_SIZE),
                ("machine_name", ctypes.c_char * NAME_SIZE)]


class InstanceInfo(ctypes.Structure):
    _fields_ = [("index", i32), ("embedded_space", i32), ("part_type", i32),
                ("n_node_sets", i32), ("n_element_sets", i32), ("n_surfaces", i32),
                ("n_nodes", i64), ("n_elements", i64), ("name", ctypes.c_char * NAME_SIZE)]


class MeshSizes(ctypes.Structure):
    _fields_ = [("n_nodes", i64), ("n_elements", i64), ("n_connectivity", i64),
                ("n_element_types", i32), ("reserved", i32)]


class MeshBuffers(ctypes.Structure):
    _fields_ = [("n_nodes", i64), ("node_labels", P_i32), ("node_coordinates", P_f64),
                ("n_elements", i64), ("element_labels", P_i32), ("element_types", P_i32),
                ("element_integration_points", P_i32), ("element_offsets", P_i64),
                ("n_connectivity", i64), ("connectivity", P_i32), ("connectivity_instances", P_i32),
                ("n_element_types", i32), ("element_type_names", P_char)]


class SetsSizes(ctypes.Structure):
    _fields_ = [("n_sets", i64), ("n_members", i64)]


class SetsBuffers(ctypes.Structure):
    _fields_ = [("n_sets", i64), ("names", P_char), ("member_kinds", P_i32), ("offsets", P_i64),
                ("n_members", i64), ("member_instances", P_i32), ("member_labels", P_i32),
                ("member_faces", P_i32)]


class StepInfo(ctypes.Structure):
    _fields_ = [("index", i32), ("number", i32), ("domain", i32), ("n_frames", i32),
                ("nlgeom", i32), ("is_perturbation", i32), ("time_period", f64),
                ("total_time", f64), ("name", ctypes.c_char * NAME_SIZE),
                ("description", ctypes.c_char * TEXT_SIZE),
                ("procedure", ctypes.c_char * NAME_SIZE),
                ("previous_step", ctypes.c_char * NAME_SIZE)]


class FrameInfo(ctypes.Structure):
    _fields_ = [("index", i32), ("increment", i32), ("mode", i32), ("cyclic_mode", i32),
                ("frame_value", f64), ("frequency", f64), ("domain", i32), ("is_imaginary", i32),
                ("has_associated_frame", i32), ("n_fields", i32),
                ("description", ctypes.c_char * TEXT_SIZE),
                ("load_case", ctypes.c_char * NAME_SIZE)]


class FieldInfo(ctypes.Structure):
    _fields_ = [("index", i32), ("type", i32), ("is_complex", i32), ("n_components", i32),
                ("n_locations", i32), ("positions", i32 * MAX_LOCATIONS),
                ("n_section_points", i32 * MAX_LOCATIONS), ("n_invariants", i32),
                ("invariants", i32 * MAX_INVARIANTS), ("name", ctypes.c_char * NAME_SIZE),
                ("description", ctypes.c_char * TEXT_SIZE),
                ("component_labels", ctypes.c_char * TEXT_SIZE)]


class FieldSizes(ctypes.Structure):
    _fields_ = [("n_blocks", i64), ("n_rows", i64), ("n_values", i64), ("n_conjugate", i64),
                ("n_orientation", i64)]


class FieldBuffers(ctypes.Structure):
    _fields_ = [("n_blocks", i64), ("block_ints", P_i32), ("block_rows", P_i64),
                ("block_values", P_i64), ("block_orientation", P_i64), ("block_names", P_char),
                ("n_rows", i64), ("element_labels", P_i32), ("node_labels", P_i32),
                ("integration_points", P_i32), ("faces", P_i32), ("n_values", i64),
                ("values", P_f64), ("n_conjugate", i64), ("conjugate", P_f64),
                ("n_orientation", i64), ("orientation", P_f64)]


_SIGNATURES = (
    ("turboodb_abi_version", i32, []),
    ("turboodb_last_error", ctypes.c_char_p, []),
    ("turboodb_position_name", ctypes.c_char_p, [i32]),
    ("turboodb_data_type_name", ctypes.c_char_p, [i32]),
    ("turboodb_domain_name", ctypes.c_char_p, [i32]),
    ("turboodb_dimension_name", ctypes.c_char_p, [i32]),
    ("turboodb_part_type_name", ctypes.c_char_p, [i32]),
    ("turboodb_face_name", ctypes.c_char_p, [i32]),
    ("turboodb_invariant_name", ctypes.c_char_p, [i32]),
    ("turboodb_analysis_code_name", ctypes.c_char_p, [i32]),
    ("turboodb_open", i32, [ctypes.c_char_p, ctypes.POINTER(vp)]),
    ("turboodb_close", i32, [vp]),
    ("turboodb_info_get", i32, [vp, ctypes.POINTER(Info)]),
    ("turboodb_instance_info_get", i32, [vp, i32, ctypes.POINTER(InstanceInfo)]),
    ("turboodb_mesh_load", i32, [vp, i32, ctypes.POINTER(MeshSizes)]),
    ("turboodb_mesh_copy", i32, [vp, ctypes.POINTER(MeshBuffers)]),
    ("turboodb_mesh_release", i32, [vp]),
    ("turboodb_sets_load", i32, [vp, i32, i32, ctypes.POINTER(SetsSizes)]),
    ("turboodb_sets_copy", i32, [vp, ctypes.POINTER(SetsBuffers)]),
    ("turboodb_sets_release", i32, [vp]),
    ("turboodb_step_info_get", i32, [vp, i32, ctypes.POINTER(StepInfo)]),
    ("turboodb_frames_read", i32, [vp, i32, i32, i32, ctypes.POINTER(FrameInfo)]),
    ("turboodb_field_info_get", i32, [vp, i32, i32, i32, ctypes.POINTER(FieldInfo)]),
    ("turboodb_field_load", i32, [vp, i32, i32, ctypes.c_char_p, ctypes.POINTER(FieldSizes)]),
    ("turboodb_field_load_at", i32,
     [vp, i32, i32, ctypes.c_char_p, i32, ctypes.POINTER(FieldSizes)]),
    ("turboodb_field_load_region", i32,
     [vp, i32, i32, ctypes.c_char_p, i32, i32, ctypes.POINTER(FieldSizes)]),
    ("turboodb_field_copy", i32, [vp, ctypes.POINTER(FieldBuffers)]),
    ("turboodb_field_release", i32, [vp]),
)

_lock = threading.Lock()
_library: Optional[ctypes.CDLL] = None
_load_error: Optional[str] = None
_loaded_dll: Optional[Path] = None


def dll_candidates() -> List[Path]:
    override = os.environ.get("TURBOODB_NATIVE_DLL")
    if override:
        return [Path(override)]
    built = [READER_ROOT / "native" / "build" / c / "turboodb.dll" for c in BUILD_CONFIGS]
    return built + [RUNTIME_DIR / "turboodb.dll"]


def loaded_dll() -> Optional[Path]:
    """The turboodb.dll this process loaded (None: not loaded)."""
    return _loaded_dll


def build_info(dll: Path) -> dict:
    """turboodb_build_info.json next to the DLL ({} when absent)."""
    info_file = dll.with_name("turboodb_build_info.json")
    if info_file.is_file():
        return json.loads(info_file.read_text(encoding="utf-8"))
    return {}


def built_release(dll: Path) -> str:
    """The Abaqus release of the build: the root's folder name (``2024``)."""
    return Path(build_info(dll).get("abaqus_root", "")).name


def bundle_status(dll: Path) -> Tuple[bool, str]:
    """(usable, why not) of the runtime bundle (runtime/bin) for *dll*'s build."""
    return _L.bundle_status(RUNTIME_MANIFEST, RUNTIME_DIR, built_release(dll))


def runtime_source(dll: Path) -> dict:
    """Where the Abaqus runtime of *dll* is loaded from (module doc):
    ``{"source": "bundle" | "installation", "dirs": [...], "note": ...}``."""
    info = build_info(dll)
    root = os.environ.get("TURBOODB_ABAQUS_ROOT")
    if root:
        built = info.get("abaqus_root", "")
        dirs = [d.replace(built, root.replace("\\", "/")) if built else d
                for d in info.get("runtime_dirs", [])] or \
            [os.path.join(root, "win_b64", "code", "bin")]
        return {"source": "installation", "dirs": dirs, "note": "TURBOODB_ABAQUS_ROOT"}
    usable, why = bundle_status(dll)
    if usable:
        return {"source": "bundle", "dirs": [str(RUNTIME_DIR)], "note": ""}
    return {"source": "installation", "dirs": list(info.get("runtime_dirs", [])), "note": why}


def runtime_info() -> dict:
    """The loaded C++ runtime and the ODB API's main modules (diagnostics)."""
    return {name: module_version(name)
            for name in ("msvcp140.dll", "vcruntime140.dll", "mfc140u.dll", "ABQSMAOdbApi.dll",
                         "ABQSMAOdbCore.dll", "ABQSMABasCoreUtils.dll", "turboodb.dll")}


def library() -> ctypes.CDLL:
    """The loaded turboodb.dll (loaded once per process).

    Raises UnavailableError with every searched location when it, or one of
    its Abaqus dependencies, cannot be loaded."""
    global _library, _load_error, _loaded_dll
    with _lock:
        if _library is not None:
            return _library
        if _load_error is not None:
            raise UnavailableError(_load_error)
        dll = next((p for p in dll_candidates() if p.is_file()), None)
        if dll is None:
            _load_error = ("the native ODB reader is not built: no turboodb.dll at "
                           + ", ".join(str(p) for p in dll_candidates())
                           + " (cmake -S native -B native/build -A x64; "
                             "cmake --build native/build --config Release)")
            raise UnavailableError(_load_error)
        info = build_info(dll)
        try:
            _L.ensure_cpp_runtime(_L.required_cpp_runtime(info), UnavailableError, "turboodb.dll")
        except UnavailableError as exc:
            _load_error = str(exc)
            raise
        source = runtime_source(dll)
        dirs = source["dirs"]
        missing = [d for d in dirs if not os.path.isdir(d)]
        try:
            lib = _L.load_dll(dll, dirs)       # the DLL's folder, these dirs, System32 — never PATH
        except OSError as exc:
            _load_error = (f"cannot load {dll}: {exc}; Abaqus runtime from the {source['source']} "
                           f"{dirs} (missing: {missing or 'none'})"
                           + (f"; {source['note']}" if source["note"] else ""))
            raise UnavailableError(_load_error) from None
        for name, restype, argtypes in _SIGNATURES:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        version = lib.turboodb_abi_version()
        if version != ABI_VERSION:
            _load_error = f"{dll} has ABI version {version}, this binding expects {ABI_VERSION}"
            raise UnavailableError(_load_error)
        _library, _loaded_dll = lib, dll
        return lib


_ERRORS = {E_OPEN: OpenError, E_VERSION: VersionError, E_FORMAT: FormatError,
           E_RANGE: RangeError, E_HANDLE: ClosedError, E_NATIVE: NativeError,
           E_MEMORY: MemoryError, E_MISSING: MissingError, E_ARGUMENT: ArgumentError}


def check(lib: ctypes.CDLL, status: int) -> None:
    """Raise the exception of a failing status (message: the native one)."""
    if status == OK:
        return
    message = (lib.turboodb_last_error() or b"").decode("utf-8", "replace") or \
        f"native status {STATUS_NAMES.get(status, status)}"
    raise _ERRORS.get(status, TurboODBError)(f"[{STATUS_NAMES.get(status, status)}] {message}")


def text(raw) -> str:
    """A fixed char field (or char pointer result) as str."""
    if raw is None:
        return ""
    return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace")


def ptr(array, ctype):
    """A ctypes pointer to a NumPy array's data (None for an empty array)."""
    return array.ctypes.data_as(ctypes.POINTER(ctype)) if array.size else None

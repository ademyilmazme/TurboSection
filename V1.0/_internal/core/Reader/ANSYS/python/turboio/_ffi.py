"""ctypes binding of turboio.dll (native/include/turboio/turboio.h): loading,
the mirrored structures and status -> exception mapping.  No result logic:
every value and every name comes from the native library.

The DLL is found at ``TURBOIO_NATIVE_DLL``, in ``native/build/<config>/``
(development) or in ``runtime/bin/`` (distribution: packaging/build_release.py).
Its ANSYS runtime (CReaders.dll, CKernel.dll and their Intel runtimes) is
resolved in this order (:func:`runtime_source`), never through PATH:

1. ``runtime/bin`` — the project's runtime bundle, when every file of
   ``runtime/manifest.json`` is present and the bundle is of the ANSYS
   release turboio.dll was built against (runtime/bundle_runtime.py);
2. the Windows system — the Visual C++ redistributable and system DLLs
   (System32; msvcp140 checked to be recent enough, :func:`_ensure_cpp_runtime`);
3. the ANSYS installation of the build (``turboio_build_info.json`` next to
   the DLL) — a development fall back when the bundle is absent or
   incomplete.  ``TURBOIO_ANSYS_ROOT`` selects an installation explicitly
   (it then replaces the bundle).
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

ABI_VERSION = 8
READER_ROOT = Path(__file__).resolve().parents[2]          # core/Reader/ANSYS
BUILD_CONFIGS = ("Release", "RelWithDebInfo", "MinSizeRel", "Debug")
RUNTIME_DIR = READER_ROOT / "runtime" / "bin"
RUNTIME_MANIFEST = READER_ROOT / "runtime" / "manifest.json"


def _common_loader():
    """core/Reader/common/native_loader.py, shared with the other readers
    (loaded by path, once per process)."""
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
    E_INTERNAL, E_MISSING = 0, -1, -2, -3, -4, -5, -6, -7, -8, -9, -10, -11
STATUS_NAMES = {OK: "OK", E_ARGUMENT: "ARGUMENT", E_HANDLE: "HANDLE", E_OPEN: "OPEN",
                E_FORMAT: "FORMAT", E_RANGE: "RANGE", E_BUFFER: "BUFFER", E_STATE: "STATE",
                E_NATIVE: "NATIVE", E_MEMORY: "MEMORY", E_INTERNAL: "INTERNAL",
                E_MISSING: "MISSING"}

HEADER_WORDS = 80
MAX_DOF = 128
NAME_SIZE = 260

i32, i64, u64, f64 = ctypes.c_int32, ctypes.c_int64, ctypes.c_uint64, ctypes.c_double
P_i32, P_i64, P_f64 = ctypes.POINTER(i32), ctypes.POINTER(i64), ctypes.POINTER(f64)
vp = ctypes.c_void_p


# ------------------------------------------------------------------ errors
class TurboIOError(Exception):
    """The native library could not do what was asked (base class)."""


class UnavailableError(TurboIOError):
    """The native library or its ANSYS runtime cannot be loaded."""


class OpenError(TurboIOError):
    """The file cannot be opened as a result file."""


class FormatError(TurboIOError):
    """A record is missing or inconsistent."""


class SetIndexError(TurboIOError, IndexError):
    """A result set index outside the file's sets."""


class ClosedError(TurboIOError):
    """The file was used after close()."""


class NativeError(TurboIOError):
    """The solver's reader library raised an error."""


class MissingError(TurboIOError, LookupError):
    """The set holds no such data (a valid state), never an empty or
    zero-filled result."""


class ArgumentError(TurboIOError, ValueError):
    """An argument the native library rejects (unknown record ...)."""


# ------------------------------------------------------------ structures
class Metadata(ctypes.Structure):
    _fields_ = [("struct_size", i32), ("ansys_release", f64), ("file_size", i64),
                ("n_nodes", i32), ("max_node_id", i32), ("n_elements", i32),
                ("max_element_id", i32), ("n_sets", i32), ("analysis_kan", i32),
                ("n_dof", i32), ("units", i32), ("n_sectors", i32), ("cyclic_csys", i32),
                ("cyclic_n_elements", i32), ("cyclic_n_nodes", i32), ("precision_key", i32),
                ("complex_flag", i32), ("rstsprs", i32), ("avail_bits", u64),
                ("rst_header", i32 * HEADER_WORDS), ("geo_header", i32 * HEADER_WORDS),
                ("n_dof_ids", i32), ("dof_ids", i32 * MAX_DOF)]


class ElementTypeInfo(ctypes.Structure):
    _fields_ = [("ref", i32), ("routine", i32), ("nodelm", i32), ("nodfor", i32),
                ("nodstr", i32), ("legacy_nodstr", i32), ("keyopts", i32 * 18),
                ("record_words", i32)]


class MeshSizes(ctypes.Structure):
    _fields_ = [("n_nodes", i64), ("n_elements", i64), ("n_element_nodes", i64),
                ("n_element_types", i32), ("ety_words", i32)]


class MeshBuffers(ctypes.Structure):
    _fields_ = [("n_nodes", i64), ("node_ids", P_i32), ("node_xyz", P_f64),
                ("node_angles", P_f64), ("n_elements", i64), ("element_header", P_i32),
                ("element_offsets", P_i64), ("n_element_nodes", i64), ("element_nodes", P_i32),
                ("n_element_types", i32), ("ety_words", i32), ("element_type_records", P_i32),
                ("element_types", ctypes.POINTER(ElementTypeInfo))]


class SetInfo(ctypes.Structure):
    _fields_ = [("index", i32), ("load_step", i32), ("substep", i32), ("cumulative", i32),
                ("time_freq", f64), ("header_itime", i32), ("header_iter", i32),
                ("header_ncumit", i32), ("mode", i32), ("isym", i32), ("kcmplx", i32),
                ("n_dof", i32), ("dof_ids", i32 * 32), ("avail_bits", u64),
                ("has_cyclic_index", i32), ("cyclic_index", i32), ("legacy_cyclic_index", i32),
                ("has_nodal_solution", i32), ("has_element_solution", i32)]


class TableSizes(ctypes.Structure):
    _fields_ = [("n_entities", i64), ("n_rows", i64), ("n_nodes", i64), ("n_columns", i32),
                ("layout", i32), ("partial", i32), ("reserved", i32), ("n_invalid", i64)]


class TableBuffers(ctypes.Structure):
    _fields_ = [("n_entities", i64), ("n_rows", i64), ("n_nodes", i64), ("n_columns", i32),
                ("layout", i32), ("entity_ids", P_i32), ("offsets", P_i64), ("status", P_i32),
                ("row_nodes", P_i32), ("node_offsets", P_i64), ("keys", P_i32),
                ("names", ctypes.POINTER(ctypes.c_char)), ("values", P_f64)]


_SIGNATURES = (
    ("turboio_abi_version", i32, []),
    ("turboio_last_error", ctypes.c_char_p, []),
    ("turboio_element_record_name", ctypes.c_char_p, [i32]),
    ("turboio_avail_item_name", ctypes.c_char_p, [i32]),
    ("turboio_dof_label", ctypes.c_char_p, [i32]),
    ("turboio_mp_label", ctypes.c_char_p, [i32]),
    ("turboio_real_constants_load", i32, [vp, ctypes.POINTER(TableSizes)]),
    ("turboio_coordinate_systems_load", i32, [vp, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_components_load", i32, [vp, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_materials_load", i32, [vp, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_avail_mask", u64, []),
    ("turboio_open", i32, [ctypes.c_char_p, ctypes.POINTER(vp)]),
    ("turboio_close", i32, [vp]),
    ("turboio_metadata_get", i32, [vp, ctypes.POINTER(Metadata)]),
    ("turboio_mesh_load", i32, [vp, ctypes.POINTER(MeshSizes)]),
    ("turboio_mesh_copy", i32, [vp, ctypes.POINTER(MeshBuffers)]),
    ("turboio_mesh_release", i32, [vp]),
    ("turboio_sets_count", i32, [vp, P_i32]),
    ("turboio_sets_read", i32, [vp, i32, i32, ctypes.POINTER(SetInfo), P_i32]),
    ("turboio_set_fields", i32, [vp, i32, i32, P_i32, P_i32]),
    ("turboio_raw_nodal_load", i32, [vp, i32, P_i32, i64, ctypes.POINTER(TableSizes)]),
    ("turboio_raw_element_load", i32,
     [vp, i32, ctypes.c_char_p, P_i32, i64, ctypes.POINTER(TableSizes)]),
    ("turboio_raw_reactions_load", i32, [vp, i32, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_nodal_solution_load", i32, [vp, i32, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_element_stress_load", i32, [vp, i32, i32, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_nodal_temperature_load", i32, [vp, i32, ctypes.POINTER(TableSizes)]),
    ("turboio_legacy_element_data_load", i32,
     [vp, i32, ctypes.c_char_p, P_i32, i64, ctypes.POINTER(TableSizes)]),
    ("turboio_table_copy", i32, [vp, ctypes.POINTER(TableBuffers)]),
    ("turboio_table_release", i32, [vp]),
)

_lock = threading.Lock()
_library: Optional[ctypes.CDLL] = None
_load_error: Optional[str] = None
_loaded_dll: Optional[Path] = None


def dll_candidates() -> List[Path]:
    override = os.environ.get("TURBOIO_NATIVE_DLL")
    if override:
        return [Path(override)]
    built = [READER_ROOT / "native" / "build" / c / "turboio.dll" for c in BUILD_CONFIGS]
    return built + [RUNTIME_DIR / "turboio.dll"]


def loaded_dll() -> Optional[Path]:
    """The turboio.dll this process loaded (None: not loaded)."""
    return _loaded_dll


def build_info(dll: Path) -> dict:
    """turboio_build_info.json next to the DLL ({} when absent)."""
    info_file = dll.with_name("turboio_build_info.json")
    if info_file.is_file():
        return json.loads(info_file.read_text(encoding="utf-8"))
    return {}


def bundle_status(dll: Path) -> Tuple[bool, str]:
    """(usable, why not) of the runtime bundle (runtime/bin) for *dll*'s build."""
    return _L.bundle_status(RUNTIME_MANIFEST, RUNTIME_DIR,
                            Path(build_info(dll).get("ansys_root", "")).name)


def runtime_source(dll: Path) -> dict:
    """Where the ANSYS runtime of *dll* is loaded from (module doc):
    ``{"source": "bundle" | "installation", "dirs": [...], "note": ...}``."""
    info = build_info(dll)
    root = os.environ.get("TURBOIO_ANSYS_ROOT")
    if root:
        built = info.get("ansys_root", "")
        dirs = [d.replace(built, root.replace("\\", "/")) if built else d
                for d in info.get("runtime_dirs", [])] or [os.path.join(root, "ansys", "bin", "winx64")]
        return {"source": "installation", "dirs": dirs, "note": "TURBOIO_ANSYS_ROOT"}
    usable, why = bundle_status(dll)
    if usable:
        return {"source": "bundle", "dirs": [str(RUNTIME_DIR)], "note": ""}
    return {"source": "installation", "dirs": list(info.get("runtime_dirs", [])), "note": why}


def runtime_dirs(dll: Path) -> List[str]:
    """The ANSYS runtime directories searched for *dll* (:func:`runtime_source`)."""
    return runtime_source(dll)["dirs"]


def _ensure_cpp_runtime(required: Tuple[int, int]) -> None:
    """Make the process's MSVCP140.dll at least *required* before CReaders
    loads: one module of that name serves the whole process, ANSYS ships
    14.20 in ansys/bin/winx64 (searched before System32 once added as a DLL
    directory) and code built by a newer compiler crashes against it
    (docs/NATIVE_RST_READER_AUDIT.md §3; core/Reader/common/native_loader.py)."""
    _L.ensure_cpp_runtime(required, UnavailableError, "turboio.dll")


def runtime_info() -> dict:
    """The loaded C++ runtime and ANSYS modules (diagnostics, tests)."""
    out = {}
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "CReaders.dll",
                 "CKernel.dll", "libmmd.dll", "mkl_rt.2.dll", "turboio.dll"):
        out[name] = module_version(name)
    return out


def library() -> ctypes.CDLL:
    """The loaded turboio.dll (loaded once per process).

    Raises UnavailableError with every searched location when it, or one of
    its ANSYS dependencies, cannot be loaded."""
    global _library, _load_error, _loaded_dll
    with _lock:
        if _library is not None:
            return _library
        if _load_error is not None:
            raise UnavailableError(_load_error)
        dll = next((p for p in dll_candidates() if p.is_file()), None)
        if dll is None:
            _load_error = ("the native result reader is not built: no turboio.dll at "
                           + ", ".join(str(p) for p in dll_candidates())
                           + " (cmake -S native -B native/build -A x64; "
                             "cmake --build native/build --config Release)")
            raise UnavailableError(_load_error)
        info = build_info(dll)
        try:
            _ensure_cpp_runtime(_L.required_cpp_runtime(info))
        except UnavailableError as exc:
            _load_error = str(exc)
            raise
        source = runtime_source(dll)
        dirs = source["dirs"]
        missing = [d for d in dirs if not os.path.isdir(d)]
        try:
            lib = _L.load_dll(dll, dirs)       # the DLL's folder, these dirs, System32 — never PATH
        except OSError as exc:
            _load_error = (f"cannot load {dll}: {exc}; ANSYS runtime from the {source['source']} "
                           f"{dirs} (missing: {missing or 'none'})"
                           + (f"; {source['note']}" if source["note"] else ""))
            raise UnavailableError(_load_error) from None
        for name, restype, argtypes in _SIGNATURES:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        version = lib.turboio_abi_version()
        if version != ABI_VERSION:
            _load_error = f"{dll} has ABI version {version}, this binding expects {ABI_VERSION}"
            raise UnavailableError(_load_error)
        _library, _loaded_dll = lib, dll
        return lib


_ERRORS = {E_OPEN: OpenError, E_FORMAT: FormatError, E_RANGE: SetIndexError,
           E_HANDLE: ClosedError, E_NATIVE: NativeError, E_MEMORY: MemoryError,
           E_MISSING: MissingError, E_ARGUMENT: ArgumentError}


def check(lib: ctypes.CDLL, status: int) -> None:
    """Raise the exception of a failing status (message: the native one)."""
    if status == OK:
        return
    message = (lib.turboio_last_error() or b"").decode("utf-8", "replace") or \
        f"native status {STATUS_NAMES.get(status, status)}"
    raise _ERRORS.get(status, TurboIOError)(f"[{STATUS_NAMES.get(status, status)}] {message}")


def name(raw: Optional[bytes]) -> Optional[str]:
    return None if raw is None else raw.decode("ascii")


def ptr(array, ctype):
    """A ctypes pointer to a contiguous NumPy array (None for an empty one)."""
    if array is None or array.size == 0:
        return None
    assert array.flags["C_CONTIGUOUS"]
    return array.ctypes.data_as(ctypes.POINTER(ctype))

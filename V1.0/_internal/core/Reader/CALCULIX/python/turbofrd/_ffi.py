"""ctypes binding of turbofrd.dll (native/include/turbofrd/turbofrd.h):
structures, signatures, loading and errors — no result logic.

The DLL is found at ``TURBOFRD_NATIVE_DLL``, in ``native/build/<config>/``
(development) or in ``runtime/bin/`` (distribution: packaging/build_release.py),
and loaded by core/Reader/common/native_loader.py (its own folder and
System32 — never PATH).  It is built with the static C++ runtime: no
runtime bundle is needed.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional

ABI_VERSION = 1
READER_ROOT = Path(__file__).resolve().parents[2]          # core/Reader/CALCULIX
BUILD_CONFIGS = ("Release", "RelWithDebInfo", "MinSizeRel", "Debug")
NAME_SIZE = 64
TEXT_SIZE = 512


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

OK, E_ARGUMENT, E_HANDLE, E_OPEN, E_FORMAT, E_RANGE, E_BUFFER, E_UNSUPPORTED, E_STATE, \
    E_NATIVE = 0, -1, -2, -3, -4, -5, -6, -7, -8, -9
STATUS_NAMES = {OK: "OK", E_ARGUMENT: "ARGUMENT", E_HANDLE: "HANDLE", E_OPEN: "OPEN",
                E_FORMAT: "FORMAT", E_RANGE: "RANGE", E_BUFFER: "BUFFER",
                E_UNSUPPORTED: "UNSUPPORTED", E_STATE: "STATE", E_NATIVE: "NATIVE"}
NODAL, NODAL_AVERAGED, NODAL_UNKNOWN = 1, 2, 3
LOCATIONS = {NODAL: "NODAL", NODAL_AVERAGED: "NODAL_AVERAGED", NODAL_UNKNOWN: "NODAL_UNKNOWN"}
REAL, IMAGINARY = 0, 1
WARNING, UNSUPPORTED_ELEMENTS, UNSUPPORTED_FIELDS = 0, 1, 2


class TurboFRDError(Exception):
    """A turbofrd failure (the native message)."""


class UnavailableError(TurboFRDError):
    """turbofrd.dll is not built or cannot be loaded."""


class OpenError(TurboFRDError):
    """The file cannot be opened (missing, a directory, empty)."""


class FormatError(TurboFRDError):
    """Malformed / corrupt content: truncated, a bad number, inconsistent counts."""


class UnsupportedError(TurboFRDError):
    """Valid FRD content this reader does not read."""


class RangeError(TurboFRDError, IndexError):
    """A set / field / component index out of range."""


class ClosedError(TurboFRDError):
    """The file is closed."""


class NativeError(TurboFRDError):
    """An unexpected native failure."""


class ArgumentError(TurboFRDError, ValueError):
    """An invalid argument."""


_ERRORS = {E_OPEN: OpenError, E_FORMAT: FormatError, E_UNSUPPORTED: UnsupportedError,
           E_RANGE: RangeError, E_HANDLE: ClosedError, E_NATIVE: NativeError,
           E_ARGUMENT: ArgumentError}

i32, i64, f64, vp = ctypes.c_int32, ctypes.c_int64, ctypes.c_double, ctypes.c_void_p
P_char = ctypes.POINTER(ctypes.c_char)


class Info(ctypes.Structure):
    _fields_ = [("file_size", i64), ("n_nodes", i64), ("n_elements", i64),
                ("node_format", i32), ("element_format", i32), ("n_sets", i32),
                ("n_blocks", i32), ("n_user_records", i32), ("n_materials", i32),
                ("n_messages", i32 * 3), ("n_element_types", i32), ("scan_seconds", f64),
                ("model", ctypes.c_char * TEXT_SIZE), ("program", ctypes.c_char * NAME_SIZE),
                ("version", ctypes.c_char * NAME_SIZE)]


class SetInfo(ctypes.Structure):
    _fields_ = [("index", i32), ("numstp", i32), ("step", i32), ("increment", i32),
                ("analysis_code", i32), ("mode", i32), ("nodal_diameter", i32),
                ("subcase", i32), ("n_fields", i32), ("has_imaginary", i32), ("value", f64),
                ("eigenvalue", f64), ("generalized_mass", f64),
                ("setname", ctypes.c_char * NAME_SIZE), ("text", ctypes.c_char * NAME_SIZE),
                ("analysis", ctypes.c_char * NAME_SIZE), ("domain", ctypes.c_char * NAME_SIZE)]


class FieldInfo(ctypes.Structure):
    _fields_ = [("block", i32), ("n_components", i32), ("n_stored", i32), ("irtype", i32),
                ("format", i32), ("location", i32), ("part", i32), ("supported", i32),
                ("readable", i32), ("n_rows", i64), ("name", ctypes.c_char * NAME_SIZE),
                ("semantic", ctypes.c_char * NAME_SIZE), ("kind", ctypes.c_char * NAME_SIZE),
                ("note", ctypes.c_char * TEXT_SIZE)]


class ComponentInfo(ctypes.Structure):
    _fields_ = [("ictype", i32), ("index1", i32), ("index2", i32), ("exists", i32),
                ("column", i32), ("name", ctypes.c_char * NAME_SIZE),
                ("semantic", ctypes.c_char * NAME_SIZE), ("icname", ctypes.c_char * NAME_SIZE)]


class NodeSizes(ctypes.Structure):
    _fields_ = [("n_nodes", i64)]


class NodeBuffers(ctypes.Structure):
    _fields_ = [("n_nodes", i64), ("ids", ctypes.POINTER(i64)),
                ("coordinates", ctypes.POINTER(f64))]


class ElementSizes(ctypes.Structure):
    _fields_ = [("n_elements", i64), ("n_connectivity", i64)]


class ElementBuffers(ctypes.Structure):
    _fields_ = [("n_elements", i64), ("n_connectivity", i64), ("ids", ctypes.POINTER(i64)),
                ("types", ctypes.POINTER(i32)), ("groups", ctypes.POINTER(i32)),
                ("materials", ctypes.POINTER(i32)), ("offsets", ctypes.POINTER(i64)),
                ("connectivity", ctypes.POINTER(i64))]


class FieldSizes(ctypes.Structure):
    _fields_ = [("n_rows", i64), ("n_columns", i32)]


class FieldBuffers(ctypes.Structure):
    _fields_ = [("n_rows", i64), ("n_columns", i32), ("ids", ctypes.POINTER(i64)),
                ("values", ctypes.POINTER(f64))]


class ElementType(ctypes.Structure):
    _fields_ = [("code", i32), ("n_nodes", i32), ("supported", i32),
                ("name", ctypes.c_char * NAME_SIZE), ("topology", ctypes.c_char * NAME_SIZE)]


_SIGNATURES = (
    ("turbofrd_abi_version", i32, []),
    ("turbofrd_last_error", i32, [P_char, i32]),
    ("turbofrd_open", i32, [ctypes.c_char_p, ctypes.POINTER(vp)]),
    ("turbofrd_close", i32, [vp]),
    ("turbofrd_info_get", i32, [vp, ctypes.POINTER(Info)]),
    ("turbofrd_user_record", i32, [vp, i32, P_char, P_char]),
    ("turbofrd_material", i32, [vp, i32, ctypes.POINTER(i32), P_char]),
    ("turbofrd_message", i32, [vp, i32, i32, P_char]),
    ("turbofrd_element_type_info", i32, [i32, ctypes.POINTER(ElementType)]),
    ("turbofrd_type_count", i32, [vp, i32, ctypes.POINTER(i32), ctypes.POINTER(i64)]),
    ("turbofrd_nodes_load", i32, [vp, ctypes.POINTER(NodeSizes)]),
    ("turbofrd_nodes_copy", i32, [vp, ctypes.POINTER(NodeBuffers)]),
    ("turbofrd_elements_load", i32, [vp, ctypes.POINTER(ElementSizes)]),
    ("turbofrd_elements_copy", i32, [vp, ctypes.POINTER(ElementBuffers)]),
    ("turbofrd_set_info_get", i32, [vp, i32, ctypes.POINTER(SetInfo)]),
    ("turbofrd_field_info_get", i32, [vp, i32, i32, ctypes.POINTER(FieldInfo)]),
    ("turbofrd_component_info_get", i32, [vp, i32, i32, i32, ctypes.POINTER(ComponentInfo)]),
    ("turbofrd_field_load", i32, [vp, i32, i32, ctypes.POINTER(FieldSizes)]),
    ("turbofrd_field_copy", i32, [vp, ctypes.POINTER(FieldBuffers)]),
    ("turbofrd_release", i32, [vp]),
)

_lock = threading.Lock()
_library: Optional[ctypes.CDLL] = None
_load_error: Optional[str] = None
_loaded_dll: Optional[Path] = None


def dll_candidates() -> List[Path]:
    override = os.environ.get("TURBOFRD_NATIVE_DLL")
    if override:
        return [Path(override)]
    built = [READER_ROOT / "native" / "build" / c / "turbofrd.dll" for c in BUILD_CONFIGS]
    return built + [READER_ROOT / "runtime" / "bin" / "turbofrd.dll"]


def loaded_dll() -> Optional[Path]:
    """The turbofrd.dll this process loaded (None: not loaded)."""
    return _loaded_dll


def library() -> ctypes.CDLL:
    """The loaded turbofrd.dll (once per process); UnavailableError with
    every searched location when it cannot be loaded."""
    global _library, _load_error, _loaded_dll
    with _lock:
        if _library is not None:
            return _library
        if _load_error is not None:
            raise UnavailableError(_load_error)
        dll = next((p for p in dll_candidates() if p.is_file()), None)
        if dll is None:
            _load_error = ("the native FRD reader is not built: no turbofrd.dll at "
                           + ", ".join(str(p) for p in dll_candidates())
                           + " (cmake -S native -B native/build -A x64; "
                             "cmake --build native/build --config Release)")
            raise UnavailableError(_load_error)
        try:
            lib = _L.load_dll(dll, [])          # its folder and System32 — never PATH
        except OSError as exc:
            _load_error = f"cannot load {dll}: {exc}"
            raise UnavailableError(_load_error) from None
        for name, restype, argtypes in _SIGNATURES:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        version = lib.turbofrd_abi_version()
        if version != ABI_VERSION:
            _load_error = f"{dll} has ABI version {version}, this binding expects {ABI_VERSION}"
            raise UnavailableError(_load_error)
        _library, _loaded_dll = lib, dll
        return lib


def check(lib: ctypes.CDLL, status: int) -> None:
    """Raise the exception of a failing status (message: the native one)."""
    if status == OK:
        return
    buf = ctypes.create_string_buffer(2048)
    lib.turbofrd_last_error(buf, len(buf))
    message = buf.value.decode("utf-8", "replace") or \
        f"native status {STATUS_NAMES.get(status, status)}"
    raise _ERRORS.get(status, TurboFRDError)(f"[{STATUS_NAMES.get(status, status)}] {message}")


def text(raw) -> str:
    """A fixed char field as str."""
    if raw is None:
        return ""
    return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace")


def ptr(array, ctype):
    """A ctypes pointer to a NumPy array's data (None for an empty array)."""
    return array.ctypes.data_as(ctypes.POINTER(ctype)) if array.size else None

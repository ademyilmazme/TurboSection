"""Loading a native reader DLL and its vendor runtime — shared by the solver
bindings (ANSYS/python/turboio/_ffi.py, ABAQUS/python/turboodb/_ffi.py),
which load this file by path (it imports nothing of theirs).

* :func:`ensure_cpp_runtime` — one msvcp140.dll serves a whole process: make
  sure the one in use is at least as new as the reader was built for (a
  vendor's older copy, found first in a DLL directory, crashes newer code);
* :func:`bundle_status` — whether a solver's runtime bundle (``runtime/bin``
  + ``runtime/manifest.json``) is complete and of the reader's release;
* :func:`load_dll` — LoadLibraryEx with the default search directories: the
  DLL's own folder, the given runtime directories, System32 — never PATH.
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Iterable, Optional, Tuple, Type


def module_version(name: str) -> Tuple[Optional[str], Optional[Tuple[int, ...]]]:
    """(path, file version) of the module *name* loaded in this process
    ((None, None): not loaded)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    handle = k32.GetModuleHandleW(name)
    if not handle:
        return None, None
    buf = ctypes.create_unicode_buffer(32768)
    k32.GetModuleFileNameW(handle, buf, len(buf))
    return buf.value, file_version(buf.value)


def file_version(path: str) -> Optional[Tuple[int, ...]]:
    """The fixed file version (a, b, c, d) of a PE file (None: none)."""
    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p]
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, data):
        return None
    block, length = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(data, "\\", ctypes.byref(block), ctypes.byref(length)):
        return None
    fixed = ctypes.cast(block, ctypes.POINTER(wintypes.DWORD * 13)).contents
    ms, ls = fixed[2], fixed[3]
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def ensure_cpp_runtime(required: Tuple[int, int], error: Type[Exception], reader: str) -> None:
    """Make the process's MSVCP140.dll at least *required* before a vendor
    runtime loads (module doc).  Never patched over: an older one already
    loaded raises *error* instead of crashing later."""
    path, version = module_version("msvcp140.dll")
    if path is None:
        system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                              "msvcp140.dll")
        if not os.path.isfile(system):
            raise error(
                "the Microsoft Visual C++ 2015-2022 redistributable (msvcp140.dll) is not "
                f"installed; {reader} needs version >= {required[0]}.{required[1]}")
        ctypes.WinDLL(system)
        path, version = module_version("msvcp140.dll")
    if version is None or version[:2] < tuple(required):
        raise error(
            f"the C++ runtime loaded in this process is {path} "
            f"{'.'.join(map(str, version or ()))}; {reader} needs msvcp140.dll >= "
            f"{required[0]}.{required[1]} (load {reader} before modules that bring an older one, "
            "or install a newer Visual C++ redistributable)")


def required_cpp_runtime(info: dict) -> Tuple[int, int]:
    """The minimum msvcp140 version recorded in a build-info file."""
    return tuple(int(v) for v in str(info.get("min_msvcp140", "14.0")).split(".")[:2])


def bundle_status(manifest: Path, runtime_dir: Path, built_release: str) -> Tuple[bool, str]:
    """(usable, why not) of a runtime bundle: every manifest file present in
    *runtime_dir* and the bundle of *built_release* (when that is known)."""
    if not manifest.is_file():
        return False, f"no runtime bundle ({manifest} absent)"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if built_release and data.get("release") != built_release:
        return False, (f"the runtime bundle is {data.get('release')}, the reader was built "
                       f"against {built_release}")
    missing = [f["name"] for f in data.get("files", []) if not (runtime_dir / f["name"]).is_file()]
    if missing or not data.get("files"):
        return False, f"the runtime bundle is incomplete: {missing or 'no files'} not in {runtime_dir}"
    return True, ""


def load_dll(dll: Path, dirs: Iterable[str]) -> ctypes.CDLL:
    """*dll* loaded with *dirs* as its runtime directories (module doc);
    OSError when it or a dependency cannot be loaded."""
    handles = [os.add_dll_directory(d) for d in dirs if os.path.isdir(d)]
    try:
        return ctypes.CDLL(str(dll))
    finally:
        # the DLLs are mapped now; later loads must not search these dirs
        for h in handles:
            h.close()


__all__ = ["bundle_status", "ensure_cpp_runtime", "file_version", "load_dll", "module_version",
           "required_cpp_runtime"]

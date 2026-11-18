"""How turbonastran reaches pyNastran (worker/nastran_adapter.py).

* **worker** — the adapter runs in the isolated runtime (``runtime/venv``:
  pyNastran and the NumPy it requires, set up by runtime/setup_runtime.py)
  as a child process speaking worker/serve.py's protocol; the application's
  interpreter never imports pyNastran.  pyNastran 1.4.1 requires NumPy < 2
  (it uses ``np.in1d``, removed in NumPy 2.4), the application runs NumPy
  2.x: this is the default.
* **inprocess** — the adapter in this interpreter, when pyNastran is
  importable here (a release supporting the application's NumPy).

``TURBONASTRAN_MODE`` = ``auto`` (default: in-process when pyNastran is
importable here, else the worker) | ``worker`` | ``inprocess``;
``TURBONASTRAN_PYTHON`` names another runtime interpreter.
"""

from __future__ import annotations

import collections
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np

READER_ROOT = Path(__file__).resolve().parents[2]          # core/Reader/NASTRAN
WORKER = READER_ROOT / "worker" / "serve.py"
ADAPTER = READER_ROOT / "worker" / "nastran_adapter.py"
RUNTIME = READER_ROOT / "runtime" / "venv"
MODE_ENV = "TURBONASTRAN_MODE"


class NastranReaderError(Exception):
    """The Nastran reader could not do what was asked (base class)."""


class UnavailableError(NastranReaderError):
    """Neither the isolated runtime nor an importable pyNastran is available."""


class OpenError(NastranReaderError):
    """The BDF or OP2 cannot be read."""


class FormatError(NastranReaderError):
    """Inconsistent data in a read file."""


class MissingError(NastranReaderError, LookupError):
    """No such result in this result set (a valid state)."""


class ArgumentError(NastranReaderError, ValueError):
    """A request the reader refuses (unknown result, set out of range)."""


class NativeError(NastranReaderError):
    """pyNastran raised an error."""


class WorkerError(NastranReaderError):
    """The worker process failed or exited."""


_ERRORS = {"open": OpenError, "format": FormatError, "missing": MissingError,
           "argument": ArgumentError, "native": NativeError}


def runtime_python() -> Path:
    """The isolated runtime's interpreter: ``TURBONASTRAN_PYTHON``, the
    development venv (``runtime/venv``) or, where there is none, a
    distribution's standalone runtime (``runtime/python``: CPython with the
    venv's packages, packaging/build_release.py)."""
    override = os.environ.get("TURBONASTRAN_PYTHON")
    if override:
        return Path(override)
    venv = RUNTIME / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    standalone = READER_ROOT / "runtime" / "python" / ("python.exe" if os.name == "nt"
                                                       else "bin/python")
    return standalone if not venv.is_file() and standalone.is_file() else venv


def _raise(meta: dict) -> None:
    if "error" in meta:
        raise _ERRORS.get(meta["error"], NastranReaderError)(meta.get("message", meta["error"]))


class WorkerBackend:
    """The adapter in a child process of the isolated runtime."""

    mode = "worker"

    def __init__(self) -> None:
        python = runtime_python()
        if not python.is_file():
            raise UnavailableError(f"the Nastran runtime is not set up: no {python} "
                                   "(python core/Reader/NASTRAN/runtime/setup_runtime.py)")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen([str(python), "-I", str(WORKER)], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                      creationflags=flags)
        self._log = collections.deque(maxlen=200)          # the worker's stderr (pyNastran's log)
        threading.Thread(target=self._drain, daemon=True).start()
        self._lock = threading.Lock()

    def _drain(self) -> None:
        for line in iter(self._proc.stderr.readline, b""):
            self._log.append(line.decode("utf-8", "replace").rstrip())

    def log(self) -> Tuple[str, ...]:
        return tuple(self._log)

    def _read(self, n: int) -> bytes:
        chunks, got = [], 0
        while got < n:
            chunk = self._proc.stdout.read(n - got)
            if not chunk:
                raise WorkerError("the Nastran worker exited (" + str(self._proc.poll()) + "): "
                                  + " | ".join(list(self._log)[-5:]))
            chunks.append(chunk)
            got += len(chunk)
        return b"".join(chunks)

    def call(self, op: str, **args) -> Tuple[dict, Dict[str, np.ndarray]]:
        with self._lock:
            if self._proc.poll() is not None:
                raise WorkerError(f"the Nastran worker is not running (exit {self._proc.returncode})")
            try:
                self._proc.stdin.write((json.dumps({"op": op, **args}) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
            except OSError as exc:
                raise WorkerError(f"the Nastran worker does not accept requests: {exc}") from None
            size = struct.unpack("<Q", self._read(8))[0]
            payload = self._read(size)
        with np.load(io.BytesIO(payload), allow_pickle=False) as z:
            meta = json.loads(z["__meta__"].tobytes().decode("utf-8"))
            arrays = {k: z[k] for k in z.files if k != "__meta__"}
        _raise(meta)
        return meta, arrays

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self.call("exit")
            except NastranReaderError:
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        for stream in (self._proc.stdin, self._proc.stdout):
            try:
                stream.close()
            except OSError:
                pass


class InProcessBackend:
    """The adapter in this interpreter (pyNastran importable here)."""

    mode = "inprocess"

    def __init__(self) -> None:
        name = "turbonastran_adapter"
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(name, ADAPTER)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                raise UnavailableError(f"pyNastran cannot be used in this interpreter: "
                                       f"{type(exc).__name__}: {exc}") from None
            sys.modules[name] = module
        self._A = module
        self._session = module.Session()
        self._lock = threading.Lock()

    def call(self, op: str, **args) -> Tuple[dict, Dict[str, np.ndarray]]:
        with self._lock:
            try:
                return self._session.handle({"op": op, **args})
            except self._A.AdapterError as exc:
                _raise({"error": exc.kind, "message": str(exc)})
            except Exception as exc:
                raise NativeError(f"{type(exc).__name__}: {exc}") from None

    def close(self) -> None:
        self._session = None


def pynastran_importable() -> Optional[str]:
    """None when pyNastran imports in this interpreter, else why not."""
    if importlib.util.find_spec("pyNastran") is None:
        return "pyNastran is not installed in this interpreter"
    try:
        import pyNastran.bdf.bdf  # noqa: F401
        import pyNastran.op2.op2  # noqa: F401
    except Exception as exc:                                 # e.g. a NumPy it does not support
        for key in [k for k in sys.modules if k == "pyNastran" or k.startswith("pyNastran.")]:
            del sys.modules[key]
        return f"{type(exc).__name__}: {exc}"
    return None


def make_backend(mode: Optional[str] = None):
    mode = (mode or os.environ.get(MODE_ENV) or "auto").strip().lower()
    if mode == "worker":
        return WorkerBackend()
    if mode == "inprocess":
        return InProcessBackend()
    if mode != "auto":
        raise ArgumentError(f"unknown {MODE_ENV} {mode!r} (auto, worker, inprocess)")
    return InProcessBackend() if pynastran_importable() is None else WorkerBackend()

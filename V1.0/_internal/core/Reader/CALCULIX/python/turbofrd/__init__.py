"""turbofrd — TurboSection's native CalculiX .frd reader (a thin binding of
turbofrd.dll, native/include/turbofrd/turbofrd.h).

    import turbofrd
    with turbofrd.open("job.frd") as frd:
        frd.info()                          # header records, counts, the scan's time
        nodes = frd.nodes()                 # ids, coordinates
        elements = frd.elements()           # ids, FRD types, connectivity (FRD node order)
        for s in frd.result_sets():         # step, increment, time / frequency / mode …
            s.fields                        # DISP, STRESS … — location, components, no values
        stress = frd.field(s.index, "STRESS")   # node ids, (N, 6) values as written

The FRD file alone — never the .inp, .dat, .sta.  Opening scans the file
once (an index of its blocks; no value decoded); geometry and one field of
one set are decoded on request.  Data only: values as CalculiX wrote them
(ASCII %12.5E, binary float32 widened exactly; nothing averaged, rotated or
derived).  Every field states its location: NODAL (a nodal solution value)
or NODAL_AVERAGED (an element quantity CalculiX extrapolated to the nodes
and averaged over the elements sharing them — docs/FRD_READER.md §5).
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from . import _ffi as N
from ._ffi import (ArgumentError, ClosedError, FormatError, NativeError, OpenError, RangeError,
                   TurboFRDError, UnavailableError, UnsupportedError)

NODAL, NODAL_AVERAGED, NODAL_UNKNOWN = "NODAL", "NODAL_AVERAGED", "NODAL_UNKNOWN"
REAL, IMAGINARY = "real", "imaginary"
#: Domains of a result set (from the 100C analysis type and the set's parts).
STATIC, TRANSIENT, MODAL, HARMONIC, BUCKLING, OTHER = (
    "static", "transient", "modal", "harmonic", "buckling", "other")


def library() -> ctypes.CDLL:
    return N.library()


@dataclass(frozen=True)
class Info:
    path: str
    file_size: int
    model: str                         # the 1C record
    program: str                       # 1UPGM
    version: str                       # 1UVERSION
    user: Tuple[Tuple[str, str], ...]  # every 1U record (key, text), file order
    materials: Dict[int, str]          # 1UMAT: number -> name
    n_nodes: int
    n_elements: int
    node_format: int                   # 0 / 1 ASCII short / long, 2 / 3 binary float32 / 64
    element_format: int
    n_sets: int
    element_types: Dict[int, int]      # FRD element type -> count
    scan_seconds: float


@dataclass(frozen=True)
class ElementTypeInfo:
    code: int
    name: str                          # he8, pe6, te4, he20, pe15, te10, tr3 …
    n_nodes: int                       # -1: an unknown type
    supported: bool                    # a solid the reader's tests cover
    topology: str                      # HEX8 … (supported)


@dataclass(frozen=True)
class Nodes:
    ids: np.ndarray                    # int64 (N,), as written
    coordinates: np.ndarray            # float64 (N, 3)


@dataclass(frozen=True)
class Elements:
    ids: np.ndarray                    # int64 (E,)
    types: np.ndarray                  # int32 (E,): FRD element type codes
    groups: np.ndarray                 # int32 (E,)
    materials: np.ndarray              # int32 (E,): the 1UMAT numbers
    offsets: np.ndarray                # int64 (E + 1,) into connectivity
    connectivity: np.ndarray           # int64: node ids in FRD order

    def nodes_of(self, i: int) -> np.ndarray:
        return self.connectivity[self.offsets[i]:self.offsets[i + 1]]


@dataclass(frozen=True)
class Component:
    name: str                          # SXX, EXY, D1, T …
    semantic: str                      # xx yy zz xy yz zx / x y z / ""
    ictype: int                        # 1 scalar, 2 vector, 4 matrix, 12 / 14 polar
    index1: int
    index2: int
    exists: int                        # 0 / 2 values written, 1 computed by the viewer (none)
    column: int                        # its column in Field.values (-1: not stored)


@dataclass(frozen=True)
class FieldInfo:
    set_index: int
    name: str                          # the FRD dataset (DISP, STRESS, TOSTRAIN …)
    semantic: str                      # displacement, stress, total_strain … ("" unknown)
    part: str                          # real / imaginary
    location: str                      # NODAL / NODAL_AVERAGED / NODAL_UNKNOWN
    kind: str                          # scalar, vector, tensor, vector_polar …
    n_rows: int
    n_components: int                  # declared
    n_stored: int                      # with values in the rows
    irtype: int
    format: int
    supported: bool                    # a known, validated base field
    readable: bool                     # its values can be loaded
    note: str                          # why it is not supported
    components: Tuple[Component, ...]

    @property
    def stored_components(self) -> Tuple[Component, ...]:
        return tuple(c for c in self.components if c.column >= 0)


@dataclass(frozen=True)
class ResultSet:
    index: int
    numstp: int                        # the 100C NUMSTP (CalculiX: its increment counter)
    step: Optional[int]                # 1PSTEP
    increment: Optional[int]
    analysis_code: int                 # the 100C ICTYPE
    analysis: str                      # the 100C ANALYS text (MODAL …)
    domain: str                        # static, transient, modal, harmonic, buckling, other
    value: float                       # the 100C VALUE, as written
    time: Optional[float]              # static / transient: value
    frequency: Optional[float]         # modal / harmonic: value (cycles per time)
    load_factor: Optional[float]       # buckling: value
    mode: Optional[int]                # 1PMODE
    eigenvalue: Optional[float]        # 1PGK
    generalized_mass: Optional[float]  # 1PGM
    nodal_diameter: Optional[int]      # 1PHID (None: CalculiX's -1, no cyclic symmetry)
    subcase: Optional[int]             # 1PSUBC
    has_imaginary: bool
    setname: str
    text: str
    fields: Tuple[FieldInfo, ...]

    def field_info(self, name: str) -> FieldInfo:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"set {self.index + 1} has no dataset {name!r} "
                       f"({', '.join(f.name for f in self.fields)})")

    @property
    def available(self) -> Tuple[str, ...]:
        """The supported base fields of the set (semantic, part)."""
        return tuple(dict.fromkeys(f.semantic + ("" if f.part == REAL else " (imaginary)")
                                   for f in self.fields if f.supported))


@dataclass(frozen=True)
class Field:
    info: FieldInfo
    node_ids: np.ndarray               # int64 (N,)
    values: np.ndarray                 # float64 (N, n_stored), the FRD's column order

    @property
    def components(self) -> Tuple[str, ...]:
        """The semantic names of the value columns (xx … / x y z / the FRD names)."""
        return tuple(c.semantic or c.name for c in self.info.stored_components)

    @property
    def location(self) -> str:
        return self.info.location


@dataclass(frozen=True)
class Unsupported:
    elements: Tuple[Tuple[int, str, int, str], ...]     # (FRD type, name, count, why)
    fields: Tuple[Tuple[int, str, str], ...]            # (set index, dataset, why)
    messages: Tuple[str, ...]                           # the native summaries


def element_type(code: int) -> ElementTypeInfo:
    lib = N.library()
    t = N.ElementType()
    N.check(lib, lib.turbofrd_element_type_info(int(code), ctypes.byref(t)))
    return ElementTypeInfo(t.code, N.text(t.name), t.n_nodes, bool(t.supported),
                           N.text(t.topology))


def open(path) -> "File":  # noqa: A001 - the module's entry point
    return File(path)


class File:
    """An open FRD file (read-only, indexed).  Use as a context manager or close()."""

    def __init__(self, path) -> None:
        self._lib = N.library()
        self.path = os.fspath(path)
        handle = ctypes.c_void_p()
        t0 = time.perf_counter()
        N.check(self._lib, self._lib.turbofrd_open(self.path.encode("utf-8"),
                                                   ctypes.byref(handle)))
        self._h = handle
        self._staging = threading.Lock()
        self._sets: Optional[Tuple[ResultSet, ...]] = None
        self._timings: Dict[str, float] = {"open": time.perf_counter() - t0}

    def close(self) -> None:
        if self._h is not None:
            h, self._h = self._h, None
            N.check(self._lib, self._lib.turbofrd_close(h))

    @property
    def closed(self) -> bool:
        return self._h is None

    def __enter__(self) -> "File":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _handle(self):
        if self._h is None:
            raise ClosedError("the FRD file is closed")
        return self._h

    def _call(self, fn: str, *args) -> None:
        N.check(self._lib, getattr(self._lib, fn)(self._handle(), *args))

    def timings(self) -> Dict[str, float]:
        """Seconds of the open (and its native scan) and of the last loads."""
        out = dict(self._timings)
        out["scan"] = self._info().scan_seconds
        return out

    # ---------------------------------------------------------------- file
    def _info(self) -> N.Info:
        i = N.Info()
        self._call("turbofrd_info_get", ctypes.byref(i))
        return i

    def info(self) -> Info:
        i = self._info()
        user = []
        key, txt = ctypes.create_string_buffer(N.NAME_SIZE), ctypes.create_string_buffer(N.TEXT_SIZE)
        for k in range(i.n_user_records):
            self._call("turbofrd_user_record", k, key, txt)
            user.append((N.text(key.raw), N.text(txt.raw)))
        materials = {}
        number = ctypes.c_int32()
        for k in range(i.n_materials):
            self._call("turbofrd_material", k, ctypes.byref(number), key)
            materials[number.value] = N.text(key.raw)
        types = {}
        code, count = ctypes.c_int32(), ctypes.c_int64()
        for k in range(i.n_element_types):
            self._call("turbofrd_type_count", k, ctypes.byref(code), ctypes.byref(count))
            types[code.value] = count.value
        return Info(self.path, i.file_size, N.text(i.model), N.text(i.program),
                    N.text(i.version), tuple(user), materials, i.n_nodes, i.n_elements,
                    i.node_format, i.element_format, i.n_sets, types, i.scan_seconds)

    def _messages(self, kind: int) -> Tuple[str, ...]:
        i = self._info()
        buf = ctypes.create_string_buffer(N.TEXT_SIZE)
        out = []
        for k in range(i.n_messages[kind]):
            self._call("turbofrd_message", kind, k, buf)
            out.append(N.text(buf.raw))
        return tuple(out)

    def warnings(self) -> Tuple[str, ...]:
        """Valid content with a remark (e.g. CalculiX's own count mismatches)."""
        return self._messages(N.WARNING)

    def unsupported_elements(self) -> Tuple[Tuple[int, str, int, str], ...]:
        """(FRD type, name, count, why) of every element type the reader
        lists but does not support (2D / line types, unknown codes)."""
        out = []
        for code, count in self.info().element_types.items():
            t = element_type(code)
            if not t.supported:
                why = next((m.split(" — ", 1)[1] for m in self._messages(N.UNSUPPORTED_ELEMENTS)
                            if f"(FRD type {code})" in m), "")
                out.append((code, t.name, count, why))
        return tuple(out)

    def unsupported_fields(self) -> Tuple[Tuple[int, str, str], ...]:
        """(set index, dataset, why) of every dataset that is not a supported
        base field."""
        return tuple((f.set_index, f.name, f.note) for s in self.result_sets()
                     for f in s.fields if not f.supported)

    def unsupported(self) -> Unsupported:
        return Unsupported(self.unsupported_elements(), self.unsupported_fields(),
                           self._messages(N.UNSUPPORTED_ELEMENTS)
                           + self._messages(N.UNSUPPORTED_FIELDS))

    # ------------------------------------------------------------ geometry
    def nodes(self) -> Nodes:
        with self._staging:
            t0 = time.perf_counter()
            s = N.NodeSizes()
            self._call("turbofrd_nodes_load", ctypes.byref(s))
            try:
                ids = np.empty(s.n_nodes, np.int64)
                xyz = np.empty((s.n_nodes, 3), np.float64)
                b = N.NodeBuffers(s.n_nodes, N.ptr(ids, N.i64), N.ptr(xyz, N.f64))
                self._call("turbofrd_nodes_copy", ctypes.byref(b))
            finally:
                self._call("turbofrd_release")
            self._timings["nodes"] = time.perf_counter() - t0
            return Nodes(ids, xyz)

    def elements(self) -> Elements:
        with self._staging:
            t0 = time.perf_counter()
            s = N.ElementSizes()
            self._call("turbofrd_elements_load", ctypes.byref(s))
            try:
                ids = np.empty(s.n_elements, np.int64)
                types = np.empty(s.n_elements, np.int32)
                groups = np.empty(s.n_elements, np.int32)
                mats = np.empty(s.n_elements, np.int32)
                offsets = np.empty(s.n_elements + 1, np.int64)
                conn = np.empty(s.n_connectivity, np.int64)
                b = N.ElementBuffers(s.n_elements, s.n_connectivity, N.ptr(ids, N.i64),
                                     N.ptr(types, N.i32), N.ptr(groups, N.i32),
                                     N.ptr(mats, N.i32), N.ptr(offsets, N.i64),
                                     N.ptr(conn, N.i64))
                self._call("turbofrd_elements_copy", ctypes.byref(b))
            finally:
                self._call("turbofrd_release")
            self._timings["elements"] = time.perf_counter() - t0
            return Elements(ids, types, groups, mats, offsets, conn)

    # ------------------------------------------------------------- results
    def result_sets(self) -> Tuple[ResultSet, ...]:
        """Every result set with its fields' metadata (no value read)."""
        if self._sets is None:
            self._sets = tuple(self._set(k) for k in range(self._info().n_sets))
        return self._sets

    def _set(self, k: int) -> ResultSet:
        s = N.SetInfo()
        self._call("turbofrd_set_info_get", k, ctypes.byref(s))
        fields = tuple(self._field_info(k, j) for j in range(s.n_fields))
        domain = N.text(s.domain)

        def opt(v, none):
            return None if v == none else v
        return ResultSet(
            k, s.numstp, opt(s.step, -1), opt(s.increment, -1), s.analysis_code,
            N.text(s.analysis), domain, s.value,
            s.value if domain in (STATIC, TRANSIENT) else None,
            s.value if domain in (MODAL, HARMONIC) else None,
            s.value if domain == BUCKLING else None, opt(s.mode, -1),
            None if np.isnan(s.eigenvalue) else s.eigenvalue,
            None if np.isnan(s.generalized_mass) else s.generalized_mass,
            opt(s.nodal_diameter, -1), opt(s.subcase, -1), bool(s.has_imaginary),
            N.text(s.setname), N.text(s.text), fields)

    def _field_info(self, k: int, j: int) -> FieldInfo:
        f = N.FieldInfo()
        self._call("turbofrd_field_info_get", k, j, ctypes.byref(f))
        comps = []
        c = N.ComponentInfo()
        for m in range(f.n_components):
            self._call("turbofrd_component_info_get", k, j, m, ctypes.byref(c))
            comps.append(Component(N.text(c.name), N.text(c.semantic), c.ictype, c.index1,
                                   c.index2, c.exists, c.column))
        return FieldInfo(k, N.text(f.name), N.text(f.semantic),
                         IMAGINARY if f.part == N.IMAGINARY else REAL,
                         N.LOCATIONS.get(f.location, str(f.location)), N.text(f.kind), f.n_rows,
                         f.n_components, f.n_stored, f.irtype, f.format, bool(f.supported),
                         bool(f.readable), N.text(f.note), tuple(comps))

    def field(self, set_index: int, name: Union[str, int]) -> Field:
        """The values of dataset *name* (or its position) of result set
        *set_index* — that block only; UnsupportedError when it is not
        readable, FormatError when it is malformed."""
        sets = self.result_sets()
        if not 0 <= int(set_index) < len(sets):
            raise RangeError(f"result set {set_index} of {len(sets)}")
        s = sets[int(set_index)]
        j = name if isinstance(name, int) else next(
            (k for k, f in enumerate(s.fields) if f.name == name), None)
        if j is None:
            raise KeyError(f"set {s.index + 1} has no dataset {name!r} "
                           f"({', '.join(f.name for f in s.fields)})")
        with self._staging:
            t0 = time.perf_counter()
            z = N.FieldSizes()
            self._call("turbofrd_field_load", int(set_index), int(j), ctypes.byref(z))
            try:
                ids = np.empty(z.n_rows, np.int64)
                values = np.empty((z.n_rows, z.n_columns), np.float64)
                b = N.FieldBuffers(z.n_rows, z.n_columns, N.ptr(ids, N.i64),
                                   N.ptr(values, N.f64))
                self._call("turbofrd_field_copy", ctypes.byref(b))
            finally:
                self._call("turbofrd_release")
            self._timings[f"field {s.fields[j].name}"] = time.perf_counter() - t0
            return Field(s.fields[j], ids, values)

    def fields_of(self, set_index: int, semantic: str) -> Tuple[Field, ...]:
        """The fields of *semantic* (stress, displacement …) at a set: the
        real part, then the imaginary one where the set has it."""
        s = self.result_sets()[int(set_index)]
        return tuple(self.field(set_index, f.name) for f in s.fields
                     if f.semantic == semantic and f.readable)


__all__ = ["ArgumentError", "BUCKLING", "ClosedError", "Component", "Elements",
           "ElementTypeInfo", "Field", "FieldInfo", "File", "FormatError", "HARMONIC",
           "IMAGINARY", "Info", "MODAL", "NODAL", "NODAL_AVERAGED", "NODAL_UNKNOWN",
           "NativeError", "Nodes", "OTHER", "OpenError", "REAL", "RangeError", "ResultSet",
           "STATIC", "TRANSIENT", "TurboFRDError", "UnavailableError", "Unsupported",
           "UnsupportedError", "element_type", "library", "open"]

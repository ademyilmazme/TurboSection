"""turboio — thin Python binding of the native result reader (turboio.dll).

The native library reads the file (metadata, mesh, result sets, raw nodal
and element solutions) and presents raw fields the way the legacy reader
hands them to TurboSection; this module only moves its tables into NumPy
arrays (one copy, no per-value Python work) and maps statuses to
exceptions.  Derived post-processing (nodal rotation, averaging, principal
and equivalent stresses ...) is TurboSection's own Python, unchanged:

    RST -> turboio.dll -> contiguous arrays -> NumPy -> TurboSection core.*

    with turboio.open(path) as f:
        sets = f.result_sets()
        stress = f.legacy_element_stress(len(sets) - 1)
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

import numpy as np

from . import _ffi as N
from ._ffi import (ArgumentError, ClosedError, FormatError, MissingError, NativeError, OpenError,
                   SetIndexError, TurboIOError, UnavailableError)

#: Per-element state of a raw element record (turboio.h TURBOIO_RECORD_*).
RECORD_NO_SOLUTION, RECORD_ABSENT, RECORD_ZERO, RECORD_STORED = 0, 1, 2, 3
#: Table layouts (TURBOIO_LAYOUT_*).
LAYOUT_NODAL, LAYOUT_RECORDS, LAYOUT_ELEMENT_NODAL, LAYOUT_ELEMENT_DATA, LAYOUT_NODE_DOF, \
    LAYOUT_COMPONENTS, LAYOUT_PROPERTIES = 0, 1, 2, 3, 4, 5, 6
#: Component kinds (LAYOUT_COMPONENTS entity ids).
NODE_COMPONENT, ELEMENT_COMPONENT = 1, 2
#: Property shapes (LAYOUT_PROPERTIES status): a scalar, a (temperatures,
#: values) table, a material without properties.
SCALAR, TABLE, NO_PROPERTY = 0, 1, -1
#: turboio_set_fields code of the DOF solution.
NODAL_SOLUTION_CODE = -1


def library() -> ctypes.CDLL:
    """The loaded native library (UnavailableError: why it cannot load)."""
    return N.library()


# ------------------------------------------------------------ name tables
def element_record_name(index: int) -> Optional[str]:
    return N.name(library().turboio_element_record_name(index))


def element_record_names() -> Tuple[str, ...]:
    """The element index table's records, in its order."""
    out = []
    while (n := element_record_name(len(out))) is not None:
        out.append(n)
    return tuple(out)


def avail_item_name(bit: int) -> Optional[str]:
    return N.name(library().turboio_avail_item_name(bit))


def avail_names(bits: int) -> Tuple[str, ...]:
    """Item names of an AvailData word, in bit order."""
    return tuple(n for b in range(64) if bits >> b & 1 and (n := avail_item_name(b)))


def dof_label(dof: int) -> Optional[str]:
    return N.name(library().turboio_dof_label(dof))


def mp_label(slot: int) -> Optional[str]:
    """The MP property label of a material-record slot (mpcom.inc)."""
    return N.name(library().turboio_mp_label(slot))


def legacy_avail_mask() -> int:
    """The AvailData bits the legacy reader names."""
    return int(library().turboio_legacy_avail_mask())


# ------------------------------------------------------------ value types
@dataclass(frozen=True)
class Metadata:
    """turboio_metadata, field for field."""

    path: str
    ansys_release: float
    file_size: int
    n_nodes: int
    max_node_id: int
    n_elements: int
    max_element_id: int
    n_sets: int
    analysis_kan: int
    n_dof: int
    units: int
    n_sectors: int
    cyclic_csys: int
    cyclic_n_elements: int
    cyclic_n_nodes: int
    precision_key: int
    complex_flag: int
    rstsprs: int
    avail_bits: int
    rst_header: Tuple[int, ...]
    geo_header: Tuple[int, ...]
    dof_ids: Tuple[int, ...]


@dataclass(frozen=True)
class ElementType:
    """turboio_element_type and the raw ETY record."""

    ref: int
    routine: int
    nodelm: int
    nodfor: int
    nodstr: int
    legacy_nodstr: int
    keyopts: Tuple[int, ...]
    record: Tuple[int, ...]


@dataclass(frozen=True, eq=False)
class Mesh:
    """Nodes and elements in file order (turboio_mesh_buffers)."""

    node_ids: np.ndarray              # int32 (N,)
    coordinates: np.ndarray           # float64 (N, 3)
    node_angles: np.ndarray           # float64 (N, 3): THXY, THYZ, THZX [deg]
    element_header: np.ndarray        # int32 (E, 10): mat type real secnum esys death solidm shape elnum baseeid
    element_offsets: np.ndarray       # int64 (E + 1,) into element_nodes
    element_nodes: np.ndarray         # int32, positional, 0 = empty slot
    element_types: Dict[int, ElementType]

    @property
    def element_ids(self) -> np.ndarray:
        return self.element_header[:, 8]

    def element_record(self, i: int) -> np.ndarray:
        """The i-th raw element record (10 header words, then nodes)."""
        a, b = self.element_offsets[i], self.element_offsets[i + 1]
        return np.concatenate([self.element_header[i], self.element_nodes[a:b]])


@dataclass(frozen=True)
class ResultSet:
    """turboio_set_info, field for field."""

    index: int                        # 0-based
    load_step: int
    substep: int
    cumulative: int
    time_freq: float
    header_itime: int
    header_iter: int
    header_ncumit: int
    mode: int
    isym: int
    kcmplx: int
    dof_ids: Tuple[int, ...]
    avail_bits: int
    cyclic_index: Optional[int]       # raw RST::CYC value (None: not cyclic)
    legacy_cyclic_index: Optional[int]
    has_nodal_solution: bool
    has_element_solution: bool

    @property
    def number(self) -> int:
        """The 1-based set number (MAPDL's SET,LIST)."""
        return self.index + 1

    @property
    def dof_labels(self) -> Tuple[str, ...]:
        return tuple(dof_label(d) or f"DOF{d}" for d in self.dof_ids)


@dataclass(frozen=True, eq=False)
class Table:
    """A native result table (turboio_table_buffers).

    LAYOUT_NODAL: ``values[k]`` is the row of ``entity_ids[k]``.
    LAYOUT_RECORDS: per element ``status`` and ``values[offsets[i]:offsets[i+1]]``.
    LAYOUT_ELEMENT_NODAL: per element rows ``offsets[i]:offsets[i+1]`` of
    ``values`` / ``row_nodes``.
    LAYOUT_ELEMENT_DATA: per element ``status``, its values
    ``offsets[i]:offsets[i+1]`` and its nodes
    ``row_nodes[node_offsets[i]:node_offsets[i+1]]``.
    LAYOUT_NODE_DOF: row k is node ``entity_ids[k]``, DOF ``keys[k]``.
    LAYOUT_COMPONENTS: component k of kind ``entity_ids[k]``, ``names[k]``,
    ids ``row_nodes[node_offsets[k]:node_offsets[k+1]]``.
    LAYOUT_PROPERTIES: material ``entity_ids[k]``, MP slot ``keys[k]``, shape
    ``status[k]``, values ``offsets[k]:offsets[k+1]``."""

    layout: int
    entity_ids: np.ndarray
    values: np.ndarray                # (rows, n_columns); RECORDS: flat
    offsets: Optional[np.ndarray] = None
    status: Optional[np.ndarray] = None
    row_nodes: Optional[np.ndarray] = None
    column_names: Tuple[str, ...] = ()
    partial: bool = False
    n_invalid: int = 0
    node_offsets: Optional[np.ndarray] = None
    keys: Optional[np.ndarray] = None
    names: Tuple[str, ...] = ()

    @property
    def dofs(self) -> Optional[np.ndarray]:
        """LAYOUT_NODE_DOF: the DOF positions (``keys``)."""
        return self.keys

    def entity_values(self, i: int) -> np.ndarray:
        return self.values[self.offsets[i]:self.offsets[i + 1]]

    def entity_nodes(self, i: int) -> np.ndarray:
        at = self.node_offsets if self.layout in (LAYOUT_ELEMENT_DATA, LAYOUT_COMPONENTS) \
            else self.offsets
        return self.row_nodes[at[i]:at[i + 1]]


# ------------------------------------------------------------------ file
def open(path) -> "File":  # noqa: A001 - the module's entry point
    return File(path)


class File:
    """One result file opened by the native reader."""

    def __init__(self, path) -> None:
        self._lib = library()
        self.path = os.fspath(path)
        self._handle: Optional[ctypes.c_void_p] = None
        self._metadata: Optional[Metadata] = None
        handle = ctypes.c_void_p()
        N.check(self._lib, self._lib.turboio_open(self.path.encode("utf-8"), ctypes.byref(handle)))
        self._handle = handle

    # -------------------------------------------------------- lifetime
    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            N.check(self._lib, self._lib.turboio_close(handle))

    @property
    def closed(self) -> bool:
        return self._handle is None

    def __enter__(self) -> "File":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter shutdown
            pass

    def _call(self, fn, *args) -> None:
        if self._handle is None:
            raise ClosedError(f"{self.path}: the file is closed")
        N.check(self._lib, fn(self._handle, *args))

    # -------------------------------------------------------- headers
    def metadata(self) -> Metadata:
        if self._handle is None:
            raise ClosedError(f"{self.path}: the file is closed")
        if self._metadata is None:
            md = N.Metadata()
            md.struct_size = ctypes.sizeof(N.Metadata)
            self._call(self._lib.turboio_metadata_get, ctypes.byref(md))
            scalars = {f: getattr(md, f) for f, _ in N.Metadata._fields_
                       if f not in ("struct_size", "rst_header", "geo_header", "n_dof_ids",
                                    "dof_ids")}
            self._metadata = Metadata(
                path=self.path, rst_header=tuple(md.rst_header), geo_header=tuple(md.geo_header),
                dof_ids=tuple(md.dof_ids[:md.n_dof_ids]), **scalars)
        return self._metadata

    def mesh(self) -> Mesh:
        sizes = N.MeshSizes()
        self._call(self._lib.turboio_mesh_load, ctypes.byref(sizes))
        n, ne, nn = sizes.n_nodes, sizes.n_elements, sizes.n_element_nodes
        nt, words = sizes.n_element_types, sizes.ety_words
        node_ids = np.empty(n, np.int32)
        xyz = np.empty((n, 3), np.float64)
        angles = np.empty((n, 3), np.float64)
        header = np.empty((ne, 10), np.int32)
        offsets = np.empty(ne + 1, np.int64)
        nodes = np.empty(nn, np.int32)
        records = np.zeros((nt, max(words, 1)), np.int32)
        types = (N.ElementTypeInfo * max(nt, 1))()
        buffers = N.MeshBuffers(
            n, N.ptr(node_ids, N.i32), N.ptr(xyz, N.f64), N.ptr(angles, N.f64), ne,
            N.ptr(header, N.i32), N.ptr(offsets, N.i64), nn, N.ptr(nodes, N.i32), nt, words,
            N.ptr(records, N.i32), types)
        try:
            self._call(self._lib.turboio_mesh_copy, ctypes.byref(buffers))
        except Exception:
            self._lib.turboio_mesh_release(self._handle)
            raise
        element_types = {}
        for k in range(nt):
            t = types[k]
            element_types[t.ref] = ElementType(
                t.ref, t.routine, t.nodelm, t.nodfor, t.nodstr, t.legacy_nodstr,
                tuple(t.keyopts), tuple(int(v) for v in records[k, :t.record_words]))
        return Mesh(node_ids, xyz, angles, header, offsets, nodes, element_types)

    def release_mesh(self) -> None:
        """Drop the library's copy of the mesh (turboio_mesh_release): a
        presentation that needs it again reads it again; the nodal solution
        needs only the node ids, which the library keeps."""
        self._call(self._lib.turboio_mesh_release)

    def result_sets(self) -> Tuple[ResultSet, ...]:
        count = N.i32()
        self._call(self._lib.turboio_sets_count, ctypes.byref(count))
        if count.value <= 0:
            return ()
        infos = (N.SetInfo * count.value)()
        written = N.i32()
        self._call(self._lib.turboio_sets_read, 0, count.value, infos, ctypes.byref(written))
        return tuple(_result_set(infos[i]) for i in range(written.value))

    def set_fields(self, set_index: int) -> Tuple[str, ...]:
        """The raw fields a set provides: "NSL" and element record names."""
        codes = (N.i32 * 64)()
        written = N.i32()
        self._call(self._lib.turboio_set_fields, set_index, 64, codes, ctypes.byref(written))
        return tuple("NSL" if c == NODAL_SOLUTION_CODE else element_record_name(c)
                     for c in codes[:written.value])

    # ------------------------------------------------- geometry tables
    def real_constants(self) -> Table:
        """The real-constant sets: per set number its values R(1..n)."""
        return self._table(self._lib.turboio_real_constants_load)

    def coordinate_systems(self) -> Table:
        """The local coordinate systems: per reference number the record's
        items 1..22 (turboio.h, turboio_coordinate_systems_load)."""
        return self._table(self._lib.turboio_coordinate_systems_load)

    def legacy_components(self) -> Table:
        """``node_components`` / ``element_components``: kind, name, ids."""
        return self._table(self._lib.turboio_legacy_components_load)

    def legacy_materials(self) -> Table:
        """``materials``: per material and MP slot a scalar or a
        (temperatures, values) table."""
        return self._table(self._lib.turboio_legacy_materials_load)

    # -------------------------------------------------------- results
    def raw_nodal(self, set_index: int, scope: Optional[Iterable[int]] = None) -> Table:
        """The DOF solution as stored: nodal systems, file order; NaN where
        the file marks "no value"."""
        t = self._table(self._lib.turboio_raw_nodal_load, set_index, *_scope(scope))
        dofs = self.result_sets()[set_index].dof_labels
        return _named(t, dofs)

    def raw_element(self, set_index: int, record: str,
                    scope: Optional[Iterable[int]] = None) -> Table:
        """An element record as stored, element-equivalence order; a *scope*
        reads only its elements' records."""
        return self._table(self._lib.turboio_raw_element_load, set_index,
                           record.encode("ascii"), *_scope(scope))

    def raw_reactions(self, set_index: int) -> Table:
        """The reaction forces (RF), file order: node, DOF position in the
        set's DOF list (1-based), value — nodal coordinate systems."""
        return self._table(self._lib.turboio_raw_reactions_load, set_index)

    # ------------------------------------------- the legacy presentation
    # What ansys-mapdl-reader hands TurboSection (native legacy_compat.hpp).
    def legacy_nodal_solution(self, set_index: int) -> Table:
        """``nodal_solution(set, in_nodal_coord_sys=True)``: sorted by node,
        "no value" 0, a partial solution every node with NaN rows."""
        t = self._table(self._lib.turboio_legacy_nodal_solution_load, set_index)
        return _named(t, self.result_sets()[set_index].dof_labels)

    def legacy_element_stress(self, set_index: int, global_axes: bool = True) -> Table:
        """``element_stress(set, in_element_coord_sys=not global_axes)``:
        per element (sorted) its rows of SX SY SZ SXY SYZ SXZ and row nodes."""
        return self._table(self._lib.turboio_legacy_element_stress_load, set_index,
                           int(bool(global_axes)))

    def legacy_nodal_temperature(self, set_index: int) -> Table:
        """``nodal_temperature(set)`` of a structural file."""
        return self._table(self._lib.turboio_legacy_nodal_temperature_load, set_index)

    def legacy_element_data(self, set_index: int, record: str,
                            scope: Optional[Iterable[int]] = None) -> Table:
        """``element_solution_data(set, record)``: per element (sorted) the
        stored record (none for any other state) and its nodes; a *scope*
        (element ids) reads only those elements' records."""
        return self._table(self._lib.turboio_legacy_element_data_load, set_index,
                           record.encode("ascii"), *_scope(scope))

    def _table(self, load, *args) -> Table:
        s = N.TableSizes()
        self._call(load, *args, ctypes.byref(s))
        ne, nr, nn, nc, layout = s.n_entities, s.n_rows, s.n_nodes, s.n_columns, s.layout
        flat = layout in (LAYOUT_RECORDS, LAYOUT_ELEMENT_DATA, LAYOUT_PROPERTIES)
        noded = layout in (LAYOUT_ELEMENT_DATA, LAYOUT_COMPONENTS)
        ids = np.empty(ne, np.int32)
        offsets = np.empty(ne + 1, np.int64) if layout in (
            LAYOUT_RECORDS, LAYOUT_ELEMENT_NODAL, LAYOUT_ELEMENT_DATA, LAYOUT_PROPERTIES) else None
        status = np.empty(ne, np.int32) if flat else None
        rows = np.empty(nr, np.int32) if layout == LAYOUT_ELEMENT_NODAL else \
            np.empty(nn, np.int32) if noded else None
        node_offsets = np.empty(ne + 1, np.int64) if noded else None
        keys = np.empty(ne, np.int32) if layout in (LAYOUT_NODE_DOF, LAYOUT_PROPERTIES) else None
        names = ctypes.create_string_buffer(ne * N.NAME_SIZE + 1) \
            if layout == LAYOUT_COMPONENTS else None
        values = np.empty(nr if flat or layout == LAYOUT_COMPONENTS else (nr, nc), np.float64)
        buffers = N.TableBuffers(ne, nr, nn, nc, layout, N.ptr(ids, N.i32),
                                 N.ptr(offsets, N.i64), N.ptr(status, N.i32), N.ptr(rows, N.i32),
                                 N.ptr(node_offsets, N.i64), N.ptr(keys, N.i32),
                                 ctypes.cast(names, ctypes.POINTER(ctypes.c_char))
                                 if names is not None else None,
                                 N.ptr(values, N.f64))
        try:
            self._call(self._lib.turboio_table_copy, ctypes.byref(buffers))
        except Exception:
            self._lib.turboio_table_release(self._handle)
            raise
        text = () if names is None else tuple(
            names.raw[k * N.NAME_SIZE:(k + 1) * N.NAME_SIZE].split(b"\0")[0].decode("latin-1")
            for k in range(ne))
        return Table(layout, ids, values, offsets, status, rows, partial=bool(s.partial),
                     n_invalid=int(s.n_invalid), node_offsets=node_offsets, keys=keys, names=text)


def _scope(scope):
    if scope is None:
        return None, -1
    ids = np.fromiter((int(v) for v in scope), np.int64)
    info = np.iinfo(np.int32)
    if ids.size and (ids.min() < info.min or ids.max() > info.max):
        raise ArgumentError("scope ids must be 32-bit integers (the file's id width)")
    ids = np.ascontiguousarray(ids.astype(np.int32))
    return N.ptr(ids, N.i32), ids.size


def _named(t: Table, names) -> Table:
    return Table(t.layout, t.entity_ids, t.values, t.offsets, t.status, t.row_nodes,
                 tuple(names), t.partial, t.n_invalid, t.node_offsets, t.keys, t.names)


def _result_set(s: "N.SetInfo") -> ResultSet:
    cyclic = bool(s.has_cyclic_index)
    return ResultSet(
        index=s.index, load_step=s.load_step, substep=s.substep, cumulative=s.cumulative,
        time_freq=s.time_freq, header_itime=s.header_itime, header_iter=s.header_iter,
        header_ncumit=s.header_ncumit, mode=s.mode, isym=s.isym, kcmplx=s.kcmplx,
        dof_ids=tuple(s.dof_ids[:s.n_dof]), avail_bits=int(s.avail_bits),
        cyclic_index=s.cyclic_index if cyclic else None,
        legacy_cyclic_index=s.legacy_cyclic_index if cyclic else None,
        has_nodal_solution=bool(s.has_nodal_solution),
        has_element_solution=bool(s.has_element_solution))


__all__ = [
    "ArgumentError", "ClosedError", "ElementType", "File", "FormatError",
    "ELEMENT_COMPONENT", "LAYOUT_COMPONENTS", "LAYOUT_ELEMENT_DATA", "LAYOUT_ELEMENT_NODAL",
    "LAYOUT_NODAL", "LAYOUT_NODE_DOF", "LAYOUT_PROPERTIES", "LAYOUT_RECORDS", "Mesh",
    "Metadata", "NODE_COMPONENT", "NO_PROPERTY", "SCALAR", "TABLE", "mp_label",
    "MissingError", "NODAL_SOLUTION_CODE", "NativeError", "OpenError", "RECORD_ABSENT",
    "RECORD_NO_SOLUTION", "RECORD_STORED", "RECORD_ZERO", "ResultSet",
    "SetIndexError", "Table", "TurboIOError", "UnavailableError",
    "avail_item_name", "avail_names", "dof_label", "element_record_name",
    "element_record_names", "legacy_avail_mask", "library", "open"]

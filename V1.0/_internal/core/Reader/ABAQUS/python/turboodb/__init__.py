"""turboodb — TurboSection's native Abaqus ODB reader (a thin binding of
turboodb.dll, native/include/turboodb/turboodb.h).

    import turboodb
    with turboodb.open("model.odb") as odb:
        steps = odb.steps()
        frames = odb.frames(0)
        u = odb.field(0, len(frames) - 1, "U")     # blocks as stored
        for block in u.blocks:
            block.instance, block.position, block.node_labels, block.values

Data only: every value is what the ODB stores (float32 widened exactly to
float64), every name comes from the native library; nothing is extrapolated,
averaged, rotated or derived.  Labels are the ODB's user labels, 0 meaning
"not applicable"; instances are indexed in the file's order, -1 is the root
assembly (``ASSEMBLY``).
"""

from __future__ import annotations

import ctypes
import os
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from . import _ffi as N
from ._ffi import (ArgumentError, ClosedError, FormatError, MissingError, NativeError, OpenError,
                   RangeError, TurboODBError, UnavailableError, VersionError)

ASSEMBLY = N.ASSEMBLY
ALL_INSTANCES = N.ALL_INSTANCES
NODE_SET, ELEMENT_SET, SURFACE = N.NODE_SET, N.ELEMENT_SET, N.SURFACE


def library() -> ctypes.CDLL:
    """The loaded native library (UnavailableError with the reason)."""
    return N.library()


def _name(fn: str, value: int) -> Optional[str]:
    raw = getattr(N.library(), fn)(int(value))
    return raw.decode("utf-8") if raw else None


def position_name(v: int) -> Optional[str]:
    return _name("turboodb_position_name", v)


def data_type_name(v: int) -> Optional[str]:
    return _name("turboodb_data_type_name", v)


def domain_name(v: int) -> Optional[str]:
    return _name("turboodb_domain_name", v)


def dimension_name(v: int) -> Optional[str]:
    return _name("turboodb_dimension_name", v)


def part_type_name(v: int) -> Optional[str]:
    return _name("turboodb_part_type_name", v)


def face_name(v: int) -> Optional[str]:
    return _name("turboodb_face_name", v)


def invariant_name(v: int) -> Optional[str]:
    return _name("turboodb_invariant_name", v)


def _position_code(name: str) -> int:
    """The odb_ResultPositionEnum value of a position name (the native table's)."""
    v = 0
    while True:
        known = position_name(v)
        if known is None:
            raise ArgumentError(f"unknown position {name!r}")
        if known == name:
            return v
        v += 1


def analysis_code_name(v: int) -> Optional[str]:
    return _name("turboodb_analysis_code_name", v)


@dataclass(frozen=True)
class Info:
    name: str
    title: str
    description: str
    version: str
    creation_time: str
    modification_time: str
    machine_name: str
    analysis_code: str
    precision: int                     # 0 single, 1 double
    n_instances: int
    n_steps: int
    has_sector_definition: bool
    file_size: int
    n_truncated: int


@dataclass(frozen=True)
class Instance:
    index: int                         # -1: the root assembly
    name: str
    embedded_space: str
    part_type: str
    n_nodes: int
    n_elements: int
    n_node_sets: int
    n_element_sets: int
    n_surfaces: int


@dataclass(frozen=True)
class Mesh:
    """Nodes and elements of one instance (or of the root assembly), in the
    ODB's order."""
    node_labels: np.ndarray            # int32 (N,)
    coordinates: np.ndarray            # float64 (N, 3)
    element_labels: np.ndarray         # int32 (E,)
    element_types: np.ndarray          # int32 (E,): index into element_type_names
    element_type_names: Tuple[str, ...]
    element_integration_points: np.ndarray  # int32 (E,)
    element_offsets: np.ndarray        # int64 (E + 1,) into connectivity
    connectivity: np.ndarray           # int32 node labels
    connectivity_instances: np.ndarray  # int32 instance of each connectivity node

    def element_nodes(self, i: int) -> np.ndarray:
        return self.connectivity[self.element_offsets[i]:self.element_offsets[i + 1]]

    def element_type(self, i: int) -> str:
        return self.element_type_names[self.element_types[i]]


@dataclass(frozen=True)
class SetGroup:
    """The node sets, element sets or surfaces of one instance (or of the root
    assembly): names and members (instance, label, face)."""
    names: Tuple[str, ...]
    member_kinds: np.ndarray           # int32 per set: 0 nodes, 1 elements
    offsets: np.ndarray                # int64 (S + 1,)
    member_instances: np.ndarray
    member_labels: np.ndarray
    member_faces: np.ndarray           # odb_ElementFaceEnum of a surface's faces, else 0

    def members(self, i: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        a, b = self.offsets[i], self.offsets[i + 1]
        return self.member_instances[a:b], self.member_labels[a:b], self.member_faces[a:b]


@dataclass(frozen=True)
class Step:
    index: int
    name: str
    description: str
    procedure: str
    previous_step: str
    number: int
    domain: str
    time_period: float
    total_time: float
    n_frames: int
    nlgeom: bool
    is_perturbation: bool


@dataclass(frozen=True)
class Frame:
    index: int
    increment: int
    mode: int
    cyclic_mode: int
    frame_value: float
    frequency: float
    domain: str
    is_imaginary: bool
    has_associated_frame: bool
    n_fields: int
    description: str
    load_case: str


@dataclass(frozen=True)
class FieldInfo:
    index: int
    name: str
    description: str
    type: str
    is_complex: bool
    component_labels: Tuple[str, ...]
    locations: Tuple[Tuple[str, int], ...]   # (position, number of section points)
    invariants: Tuple[str, ...]


@dataclass(frozen=True)
class Block:
    """One bulk data block: one instance, element type, position and section
    point; one row per stored value."""
    instance: int
    position: str
    type: str
    precision: int                     # 0 single, 1 double (as stored)
    width: int
    element_type: str
    section_point: int                 # 0: none
    section_point_name: str
    components: Tuple[str, ...]
    element_labels: np.ndarray         # int32 per row, 0 for nodal data
    node_labels: np.ndarray            # int32 per row, 0 where none
    integration_points: np.ndarray     # int32 per row, 0 where none
    faces: np.ndarray                  # int32 per row, 0 where none
    values: np.ndarray                 # float64 (rows, width)
    conjugate: Optional[np.ndarray]    # float64 (rows, width) of a complex field
    orientation: Optional[np.ndarray]  # float64 (rows, orientation width): the local systems


@dataclass(frozen=True)
class FieldData:
    name: str
    blocks: Tuple[Block, ...]


def open(path) -> "File":  # noqa: A001 - the module's entry point
    return File(path)


class File:
    """An open ODB (read-only).  Use as a context manager or close()."""

    def __init__(self, path) -> None:
        self._lib = N.library()
        self.path = os.fspath(path)
        handle = ctypes.c_void_p()
        N.check(self._lib, self._lib.turboodb_open(self.path.encode("utf-8"), ctypes.byref(handle)))
        self._h = handle
        # staged data belongs to the handle: one load-copy-release at a time
        self._staging = threading.Lock()

    def close(self) -> None:
        if self._h is not None:
            h, self._h = self._h, None
            N.check(self._lib, self._lib.turboodb_close(h))

    @property
    def closed(self) -> bool:
        return self._h is None

    def __enter__(self) -> "File":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _handle(self):
        if self._h is None:
            raise ClosedError("the ODB is closed")
        return self._h

    def _call(self, fn: str, *args) -> None:
        N.check(self._lib, getattr(self._lib, fn)(self._handle(), *args))

    # ---------------------------------------------------------------- file
    def info(self) -> Info:
        i = N.Info()
        i.struct_size = ctypes.sizeof(N.Info)
        self._call("turboodb_info_get", ctypes.byref(i))
        return Info(N.text(i.name), N.text(i.title), N.text(i.description), N.text(i.version),
                    N.text(i.creation_time), N.text(i.modification_time), N.text(i.machine_name),
                    analysis_code_name(i.analysis_code), i.precision, i.n_instances, i.n_steps,
                    bool(i.has_sector_definition), i.file_size, i.n_truncated)

    # --------------------------------------------------------------- model
    def instance(self, index: int) -> Instance:
        i = N.InstanceInfo()
        self._call("turboodb_instance_info_get", int(index), ctypes.byref(i))
        return Instance(i.index, N.text(i.name), dimension_name(i.embedded_space),
                        part_type_name(i.part_type), i.n_nodes, i.n_elements, i.n_node_sets,
                        i.n_element_sets, i.n_surfaces)

    def instances(self) -> Tuple[Instance, ...]:
        return tuple(self.instance(i) for i in range(self.info().n_instances))

    def mesh(self, instance: int) -> Mesh:
        with self._staging:
            return self._mesh(instance)

    def _mesh(self, instance: int) -> Mesh:
        s = N.MeshSizes()
        self._call("turboodb_mesh_load", int(instance), ctypes.byref(s))
        try:
            nl = np.empty(s.n_nodes, np.int32)
            xyz = np.empty((s.n_nodes, 3), np.float64)
            el = np.empty(s.n_elements, np.int32)
            et = np.empty(s.n_elements, np.int32)
            ip = np.empty(s.n_elements, np.int32)
            off = np.empty(s.n_elements + 1, np.int64)
            conn = np.empty(s.n_connectivity, np.int32)
            ci = np.empty(s.n_connectivity, np.int32)
            names = ctypes.create_string_buffer(max(1, s.n_element_types * N.NAME_SIZE))
            b = N.MeshBuffers(s.n_nodes, N.ptr(nl, N.i32), N.ptr(xyz, N.f64), s.n_elements,
                              N.ptr(el, N.i32), N.ptr(et, N.i32), N.ptr(ip, N.i32),
                              N.ptr(off, N.i64), s.n_connectivity, N.ptr(conn, N.i32),
                              N.ptr(ci, N.i32), s.n_element_types,
                              ctypes.cast(names, N.P_char))
            self._call("turboodb_mesh_copy", ctypes.byref(b))
        finally:
            self._call("turboodb_mesh_release")
        types = tuple(N.text(names.raw[k * N.NAME_SIZE:(k + 1) * N.NAME_SIZE])
                      for k in range(s.n_element_types))
        return Mesh(nl, xyz, el, et, types, ip, off, conn, ci)

    def sets(self, instance: int, kind: int) -> SetGroup:
        with self._staging:
            return self._sets(instance, kind)

    def _sets(self, instance: int, kind: int) -> SetGroup:
        s = N.SetsSizes()
        self._call("turboodb_sets_load", int(instance), int(kind), ctypes.byref(s))
        try:
            names = ctypes.create_string_buffer(max(1, s.n_sets * N.NAME_SIZE))
            kinds = np.empty(s.n_sets, np.int32)
            off = np.empty(s.n_sets + 1, np.int64)
            inst = np.empty(s.n_members, np.int32)
            labels = np.empty(s.n_members, np.int32)
            faces = np.empty(s.n_members, np.int32)
            b = N.SetsBuffers(s.n_sets, ctypes.cast(names, N.P_char), N.ptr(kinds, N.i32),
                              N.ptr(off, N.i64), s.n_members, N.ptr(inst, N.i32),
                              N.ptr(labels, N.i32), N.ptr(faces, N.i32))
            self._call("turboodb_sets_copy", ctypes.byref(b))
        finally:
            self._call("turboodb_sets_release")
        set_names = tuple(N.text(names.raw[k * N.NAME_SIZE:(k + 1) * N.NAME_SIZE])
                          for k in range(s.n_sets))
        return SetGroup(set_names, kinds, off, inst, labels, faces)

    def node_sets(self, instance: int) -> SetGroup:
        return self.sets(instance, NODE_SET)

    def element_sets(self, instance: int) -> SetGroup:
        return self.sets(instance, ELEMENT_SET)

    def surfaces(self, instance: int) -> SetGroup:
        return self.sets(instance, SURFACE)

    # ------------------------------------------------------- steps, frames
    def step(self, index: int) -> Step:
        s = N.StepInfo()
        self._call("turboodb_step_info_get", int(index), ctypes.byref(s))
        return Step(s.index, N.text(s.name), N.text(s.description), N.text(s.procedure),
                    N.text(s.previous_step), s.number, domain_name(s.domain), s.time_period,
                    s.total_time, s.n_frames, bool(s.nlgeom), bool(s.is_perturbation))

    def steps(self) -> Tuple[Step, ...]:
        return tuple(self.step(i) for i in range(self.info().n_steps))

    def frames(self, step: int) -> Tuple[Frame, ...]:
        n = self.step(step).n_frames
        out = (N.FrameInfo * max(1, n))()
        self._call("turboodb_frames_read", int(step), 0, n, out)
        return tuple(Frame(f.index, f.increment, f.mode, f.cyclic_mode, f.frame_value, f.frequency,
                           domain_name(f.domain), bool(f.is_imaginary),
                           bool(f.has_associated_frame), f.n_fields, N.text(f.description),
                           N.text(f.load_case)) for f in out[:n])

    # --------------------------------------------------------------- fields
    def field_info(self, step: int, frame: int, index: int) -> FieldInfo:
        f = N.FieldInfo()
        self._call("turboodb_field_info_get", int(step), int(frame), int(index), ctypes.byref(f))
        labels = N.text(f.component_labels)
        return FieldInfo(f.index, N.text(f.name), N.text(f.description), data_type_name(f.type),
                         bool(f.is_complex), tuple(labels.split("\n")) if labels else (),
                         tuple((position_name(f.positions[k]), f.n_section_points[k])
                               for k in range(f.n_locations)),
                         tuple(invariant_name(f.invariants[k]) for k in range(f.n_invariants)))

    def fields(self, step: int, frame: int) -> Tuple[FieldInfo, ...]:
        n = self.frames(step)[frame].n_fields
        return tuple(self.field_info(step, frame, k) for k in range(n))

    def field(self, step: int, frame: int, name: str, position: Optional[str] = None,
              instance: Optional[int] = None) -> FieldData:
        """The values of field output *name*, block by block: as stored, or
        at *position* (e.g. ``"ELEMENT_NODAL"``) as the ODB API's subset
        gives it (turboodb.h, turboodb_field_load_at); *instance* (an
        instance index): that instance's values only — the API's instance
        subset, read before the position (turboodb_field_load_region)."""
        code = -1 if position is None else _position_code(position)
        region = N.ALL_INSTANCES if instance is None else int(instance)
        with self._staging:
            return self._field(step, frame, name, code, region)

    def _field(self, step: int, frame: int, name: str, position: int,
               instance: int = N.ALL_INSTANCES) -> FieldData:
        s = N.FieldSizes()
        self._call("turboodb_field_load_region", int(step), int(frame), name.encode("utf-8"),
                   int(position), int(instance), ctypes.byref(s))
        try:
            nb = s.n_blocks
            ints = np.empty((nb, N.BLOCK_INTS), np.int32)
            rows = np.empty(nb + 1, np.int64)
            vals = np.empty(nb + 1, np.int64)
            orient = np.empty(nb + 1, np.int64)
            names = ctypes.create_string_buffer(max(1, nb * N.BLOCK_NAMES * N.NAME_SIZE))
            el = np.empty(s.n_rows, np.int32)
            nl = np.empty(s.n_rows, np.int32)
            ip = np.empty(s.n_rows, np.int32)
            fc = np.empty(s.n_rows, np.int32)
            values = np.empty(s.n_values, np.float64)
            conj = np.empty(s.n_conjugate, np.float64)
            lcs = np.empty(s.n_orientation, np.float64)
            b = N.FieldBuffers(nb, N.ptr(ints, N.i32), N.ptr(rows, N.i64), N.ptr(vals, N.i64),
                               N.ptr(orient, N.i64), ctypes.cast(names, N.P_char), s.n_rows,
                               N.ptr(el, N.i32), N.ptr(nl, N.i32), N.ptr(ip, N.i32),
                               N.ptr(fc, N.i32), s.n_values, N.ptr(values, N.f64),
                               s.n_conjugate, N.ptr(conj, N.f64), s.n_orientation,
                               N.ptr(lcs, N.f64))
            self._call("turboodb_field_copy", ctypes.byref(b))
        finally:
            self._call("turboodb_field_release")
        blocks = []
        stride = N.BLOCK_NAMES * N.NAME_SIZE
        for k in range(nb):
            r0, r1 = rows[k], rows[k + 1]
            v0, v1 = vals[k], vals[k + 1]
            o0, o1 = orient[k], orient[k + 1]
            width, ow = int(ints[k, N.BLOCK_WIDTH]), int(ints[k, N.BLOCK_ORIENTATION_WIDTH])
            raw = names.raw[k * stride:(k + 1) * stride]
            part = [N.text(raw[j * N.NAME_SIZE:(j + 1) * N.NAME_SIZE]) for j in range(N.BLOCK_NAMES)]
            comps = part[N.BLOCK_COMPONENTS]
            blocks.append(Block(
                int(ints[k, N.BLOCK_INSTANCE]), position_name(ints[k, N.BLOCK_POSITION]),
                data_type_name(ints[k, N.BLOCK_TYPE]), int(ints[k, N.BLOCK_PRECISION]), width,
                part[N.BLOCK_ELEMENT_TYPE], int(ints[k, N.BLOCK_SECTION_POINT]),
                part[N.BLOCK_SECTION_POINT_NAME], tuple(comps.split("\n")) if comps else (),
                el[r0:r1], nl[r0:r1], ip[r0:r1], fc[r0:r1],
                values[v0:v1].reshape(r1 - r0, width),
                conj[v0:v1].reshape(r1 - r0, width) if ints[k, N.BLOCK_HAS_CONJUGATE] else None,
                lcs[o0:o1].reshape(r1 - r0, ow) if ow else None))
        return FieldData(name, tuple(blocks))


__all__ = ["ASSEMBLY", "ArgumentError", "Block", "ClosedError", "ELEMENT_SET", "FieldData",
           "FieldInfo", "File", "FormatError", "Frame", "Info", "Instance", "Mesh",
           "MissingError", "NODE_SET", "NativeError", "OpenError", "RangeError", "SURFACE",
           "SetGroup", "Step", "TurboODBError", "UnavailableError", "VersionError",
           "analysis_code_name", "data_type_name", "dimension_name", "domain_name", "face_name",
           "invariant_name", "library", "open", "part_type_name", "position_name"]

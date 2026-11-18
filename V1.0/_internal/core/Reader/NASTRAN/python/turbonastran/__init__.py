"""turbonastran — TurboSection's Nastran reader (BDF / OP2 through pyNastran).

    import turbonastran
    with turbonastran.open("model.bdf", "model.op2") as src:
        mesh = src.mesh()
        sets = src.result_sets()
        u = src.read_displacement(0)        # NodalResult: node ids, values (N, 6)
        s = src.read_stress(0)              # ElementNodalResult: (element, node) rows

The only door to Nastran files for the rest of TurboSection: NumPy arrays
and plain data classes, never pyNastran objects (pyNastran runs in its own
runtime, _client.py).  Scope (phase N1): GRID and the solid elements
CTETRA, CHEXA, CPENTA; displacement (modal eigenvectors included), SPC
force, temperature, solid stress and strain; static, modal, frequency
response and transient subcases.  What a file holds beyond that is listed by
:meth:`NastranResultSource.unsupported`.

Data only, as the OP2 holds it: float32 widened exactly to float64, complex
values kept complex (complex128, real + imaginary); displacements and SPC
forces in each grid's output system (``Mesh.node_cd``), solid stresses and
strains in the system each element reports (``ElementNodalResult.element_cs``);
nothing averaged, rotated or derived.  Real solid tables also carry the
solver's own principal / von Mises columns (``solver_values``) — the file's,
for checks; TurboSection derives its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from ._client import (ArgumentError, FormatError, MissingError, NastranReaderError, NativeError,
                      OpenError, UnavailableError, WorkerError, make_backend, pynastran_importable)

NODAL_RESULTS = ("displacement", "spc_force", "temperature")
ELEMENT_RESULTS = ("stress", "strain")


@dataclass(frozen=True)
class Info:
    bdf_path: str
    op2_path: Optional[str]
    sol: Optional[int]
    solver: str                        # "MSC" / "NX": the OP2's flavour
    op2_date: Tuple[int, ...]          # (month, day, year) of the solver run
    n_result_sets: int
    mode: str                          # "worker" / "inprocess"
    pynastran_version: str
    numpy_version: str                 # pyNastran's NumPy


@dataclass(frozen=True)
class CoordinateSystems:
    ids: np.ndarray                    # int64 (K,)
    kinds: Tuple[str, ...]             # "R" rectangular, "C" cylindrical, "S" spherical
    cards: Tuple[str, ...]             # CORD2R, CORD1C, ...
    origins: np.ndarray                # float64 (K, 3), basic
    axes: np.ndarray                   # float64 (K, 3, 3): rows = local axes in basic


@dataclass(frozen=True)
class Mesh:
    """GRIDs and solid elements (CTETRA, CHEXA, CPENTA), by ascending id."""
    node_ids: np.ndarray               # int64 (N,)
    coordinates: np.ndarray            # float64 (N, 3), basic system
    node_cp: np.ndarray                # int32 (N,): the system the GRID was given in
    node_cd: np.ndarray                # int32 (N,): the grid's output system (displacements, SPC forces)
    element_ids: np.ndarray            # int64 (E,)
    element_types: np.ndarray          # int32 (E,): index into element_type_names
    element_type_names: Tuple[str, ...]
    element_offsets: np.ndarray        # int64 (E + 1,) into connectivity
    connectivity: np.ndarray           # int64 node ids, Nastran order; 0 = an absent mid-side node
    element_property: np.ndarray       # int64 (E,): PSOLID id
    element_material_cs: np.ndarray    # int32 (E,): PSOLID CORDM
    coordinate_systems: CoordinateSystems

    def element_nodes(self, i: int) -> np.ndarray:
        return self.connectivity[self.element_offsets[i]:self.element_offsets[i + 1]]

    def element_type(self, i: int) -> str:
        return self.element_type_names[self.element_types[i]]


@dataclass(frozen=True)
class ResultSet:
    index: int
    subcase: int
    domain: str                        # static / modal / frequency / transient
    analysis_code: int                 # the OP2's (10: nonlinear static, e.g. SOL 153)
    step: int                          # position in the subcase
    value: float                       # load set, load factor, mode, frequency or time
    mode: Optional[int]
    eigenvalue: Optional[float]
    frequency: Optional[float]         # Hz: a mode's cycles, or the excitation frequency
    time: Optional[float]
    load_factor: Optional[float]
    load_set: Optional[int]
    is_complex: bool
    available: Tuple[str, ...]
    title: str
    subtitle: str
    label: str


@dataclass(frozen=True)
class NodalResult:
    name: str
    set_index: int
    node_ids: np.ndarray               # int64 (N,)
    grid_types: np.ndarray             # int32 (N,): OP2 point types (1 grid, 2 scalar, ...)
    components: Tuple[str, ...]        # T1 T2 T3 R1 R2 R3, or TEMP
    values: np.ndarray                 # float64 or complex128 (N, C), each grid's CD system
    is_complex: bool


@dataclass(frozen=True)
class ElementNodalResult:
    """Solid stress or strain rows: one per (element, node), node 0 the
    element's centre, then its corner nodes in connectivity order."""
    name: str
    set_index: int
    element_ids: np.ndarray            # int64 (R,)
    node_ids: np.ndarray               # int64 (R,), 0 = centre
    element_types: np.ndarray          # int32 (R,): index into element_type_names
    element_type_names: Tuple[str, ...]
    components: Tuple[str, ...]        # OXX OYY OZZ TXY TYZ TXZ, or EXX EYY EZZ EXY EYZ EXZ
    values: np.ndarray                 # float64 or complex128 (R, 6)
    is_complex: bool
    element_cs_ids: np.ndarray         # int64 (E,): the elements of ...
    element_cs: np.ndarray             # int64 (E,): ... the system their values are in
    solver_values: Optional[np.ndarray]  # float64 (R, k): the solver's own derived columns
    solver_names: Tuple[str, ...]      # e.g. omax omid omin von_mises


@dataclass(frozen=True)
class Unsupported:
    """What the files hold beyond phase N1's scope — reported, never read."""
    elements: Dict[str, int]           # card -> count (shells, bars, springs, rigid elements …)
    result_tables: Tuple[str, ...]     # pyNastran table names present but not read
    domains: Dict[str, str]            # tables of unsupported analyses -> the analysis
    ambiguous_tables: Tuple[str, ...]  # tables refused (a step twice with different values)
    notes: Tuple[str, ...]             # e.g. repeated identical steps merged


def open(bdf_path, op2_path=None, mode: Optional[str] = None) -> "NastranResultSource":  # noqa: A001
    return NastranResultSource(bdf_path, op2_path, mode)


class NastranResultSource:
    """A BDF model and (optionally) its OP2 results; close() or ``with``."""

    def __init__(self, bdf_path, op2_path=None, mode: Optional[str] = None) -> None:
        self.bdf_path = os.fspath(bdf_path)
        self.op2_path = None if op2_path is None else os.fspath(op2_path)
        for path in (self.bdf_path, self.op2_path):
            if path is not None and not os.path.isfile(path):
                raise OpenError(f"no such file: {path}")
        self._backend = make_backend(mode)
        try:
            meta, _ = self._backend.call("open", bdf=os.path.abspath(self.bdf_path),
                                         op2=None if self.op2_path is None
                                         else os.path.abspath(self.op2_path))
        except Exception:
            self._backend.close()
            raise
        self._info, self._timings = meta["info"], dict(meta["timings"])
        self._mesh: Optional[Mesh] = None
        self._sets: Optional[Tuple[ResultSet, ...]] = None

    # ---------------------------------------------------------- lifetime
    def close(self) -> None:
        if self._backend is not None:
            backend, self._backend = self._backend, None
            backend.close()

    @property
    def closed(self) -> bool:
        return self._backend is None

    def __enter__(self) -> "NastranResultSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _call(self, op: str, **args):
        if self._backend is None:
            raise NastranReaderError("the Nastran model is closed")
        return self._backend.call(op, **args)

    # -------------------------------------------------------------- model
    def info(self) -> Info:
        i = self._info
        return Info(i["bdf_path"], i["op2_path"], i["sol"], i["solver"], tuple(i["op2_date"]),
                    i["n_result_sets"], self._backend.mode if self._backend else "",
                    i["pynastran_version"], i["numpy_version"])

    def mesh(self) -> Mesh:
        if self._mesh is None:
            meta, a = self._call("mesh")
            cs = CoordinateSystems(a["coord_ids"], tuple(meta["coord_kinds"]),
                                   tuple(meta["coord_cards"]), a["coord_origins"], a["coord_axes"])
            self._mesh = Mesh(a["node_ids"], a["coordinates"], a["node_cp"], a["node_cd"],
                              a["element_ids"], a["element_types"],
                              tuple(meta["element_type_names"]), a["element_offsets"],
                              a["connectivity"], a["element_property"], a["element_material_cs"], cs)
        return self._mesh

    def result_sets(self) -> Tuple[ResultSet, ...]:
        if self._sets is None:
            meta, _ = self._call("result_sets")
            self._sets = tuple(ResultSet(**{**s, "available": tuple(s["available"])})
                               for s in meta["sets"])
        return self._sets

    # ------------------------------------------------------------ results
    def _nodal(self, name: str, set_index: int) -> NodalResult:
        meta, a = self._call("nodal", result=name, set=int(set_index))
        return NodalResult(name, int(set_index), a["node_ids"], a["grid_types"],
                           tuple(meta["components"]), a["values"], bool(meta["is_complex"]))

    def _element_nodal(self, name: str, set_index: int) -> ElementNodalResult:
        meta, a = self._call("element_nodal", result=name, set=int(set_index))
        return ElementNodalResult(name, int(set_index), a["element_ids"], a["node_ids"],
                                  a["element_types"], tuple(meta["element_type_names"]),
                                  tuple(meta["components"]), a["values"], bool(meta["is_complex"]),
                                  a["element_cs_ids"], a["element_cs"], a.get("solver_values"),
                                  tuple(meta["solver_names"]))

    def read_displacement(self, set_index: int) -> NodalResult:
        return self._nodal("displacement", set_index)

    def read_spc_forces(self, set_index: int) -> NodalResult:
        return self._nodal("spc_force", set_index)

    def read_temperature(self, set_index: int) -> NodalResult:
        return self._nodal("temperature", set_index)

    def read_stress(self, set_index: int) -> ElementNodalResult:
        return self._element_nodal("stress", set_index)

    def read_strain(self, set_index: int) -> ElementNodalResult:
        return self._element_nodal("strain", set_index)

    # ------------------------------------------------------- diagnostics
    def unsupported(self) -> Unsupported:
        meta, _ = self._call("unsupported")
        return Unsupported(dict(meta["elements"]), tuple(meta["result_tables"]),
                           dict(meta["domains"]), tuple(meta["ambiguous_tables"]),
                           tuple(meta["notes"]))

    def timings(self) -> Dict[str, float]:
        """Seconds spent by pyNastran and the adapter (bdf_load, op2_load,
        mesh_extract, <result>_extract — the latest of each)."""
        meta, _ = self._call("timings")
        return dict(meta["timings"])


__all__ = ["ArgumentError", "CoordinateSystems", "ELEMENT_RESULTS", "ElementNodalResult",
           "FormatError", "Info", "Mesh", "MissingError", "NODAL_RESULTS", "NastranReaderError",
           "NastranResultSource", "NativeError", "NodalResult", "OpenError", "ResultSet",
           "UnavailableError", "Unsupported", "WorkerError", "open", "pynastran_importable"]

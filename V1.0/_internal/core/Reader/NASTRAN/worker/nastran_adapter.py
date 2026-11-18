"""pyNastran -> plain NumPy arrays and JSON-able metadata.

The only module of TurboSection that touches pyNastran's object model.  It
runs wherever pyNastran is importable: in the isolated runtime
(``runtime/venv``, through ``serve.py``) or in-process
(``turbonastran``, when the application's interpreter can import it).

Scope (phase N1): GRID and the solid elements CTETRA, CHEXA, CPENTA; the
results displacement (eigenvectors of modal subcases included), SPC force,
temperature, and the solid elements' stress and strain; the domains static,
modal, frequency response and transient.  Everything else the files hold is
reported by :meth:`Model.unsupported`, never dropped silently.

Data only: values are what the OP2 holds — float32 widened exactly to
float64, complex64 to complex128 (real / imaginary, as pyNastran stores
them) — in the systems the OP2 holds them in (displacements in each grid's
output system CD, solid stresses in the system the element reports).
Nothing is averaged, rotated or derived: the solver's own principal / von
Mises columns are passed through, labelled as such, never computed here.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import numpy as np

import pyNastran
from pyNastran.bdf.bdf import read_bdf
from pyNastran.op2.op2 import read_op2

SOLID_TYPES = ("CTETRA", "CHEXA", "CPENTA")
#: TurboSection result name -> the OP2 tables (pyNastran attributes) that hold it
NODAL_TABLES = {"displacement": ("displacements", "eigenvectors"),
                "spc_force": ("spc_forces",),
                "temperature": ("temperatures",)}
ELEMENT_RESULTS = ("stress", "strain")
#: the tensor components, in pyNastran's header names, by result
TENSOR_HEADERS = {"stress": ("oxx", "oyy", "ozz", "txy", "tyz", "txz"),
                  "strain": ("exx", "eyy", "ezz", "exy", "eyz", "exz")}
#: the solver's own derived columns of real solid tables (passed through, never used)
SOLVER_HEADERS = {"stress": ("omax", "omid", "omin"), "strain": ("emax", "emid", "emin")}
#: OP2 analysis code -> domain
DOMAINS = {1: "static", 2: "modal", 5: "frequency", 6: "transient", 10: "static"}
ANALYSIS_CODES = {1: "static", 2: "real eigenvalues", 3: "differential stiffness 0",
                  4: "differential stiffness 1", 5: "frequency", 6: "transient",
                  7: "pre-buckling", 8: "post-buckling", 9: "complex eigenvalues",
                  10: "nonlinear statics", 11: "old geometric nonlinear statics",
                  12: "contran"}
#: grid types of nodal tables (OP2 approach codes)
GRID_TYPES = {1: "G", 2: "S", 3: "E", 7: "L"}


class AdapterError(Exception):
    """A request this adapter refuses, with the reason (kind: open, missing,
    argument, format)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _tensor_columns(table, result: str) -> Tuple[List[int], List[int], List[str]]:
    headers = [h.lower() for h in table.get_headers()]
    try:
        tensor = [headers.index(h) for h in TENSOR_HEADERS[result]]
    except ValueError:
        raise AdapterError("format", f"{table.table_name} {type(table).__name__}: columns {headers}, "
                                     f"expected {TENSOR_HEADERS[result]}") from None
    solver, names = [], []
    if not table.is_complex:
        for h in SOLVER_HEADERS[result] + ("von_mises", "max_shear"):
            if h in headers:
                solver.append(headers.index(h))
                names.append(h)
    return tensor, solver, names


class Model:
    """One BDF (and optionally its OP2), read with pyNastran."""

    def __init__(self, bdf_path: str, op2_path: Optional[str] = None) -> None:
        self.bdf_path, self.op2_path = bdf_path, op2_path
        self.timings: Dict[str, float] = {}
        t = time.perf_counter()
        try:
            self.bdf = read_bdf(bdf_path, debug=None)
        except Exception as exc:
            raise AdapterError("open", f"cannot read the BDF {bdf_path}: {type(exc).__name__}: {exc}") \
                from None
        self.timings["bdf_load"] = time.perf_counter() - t
        self.op2 = None
        if op2_path:
            t = time.perf_counter()
            try:
                self.op2 = read_op2(op2_path, debug=None, build_dataframe=False)
            except Exception as exc:
                raise AdapterError("open", f"cannot read the OP2 {op2_path}: {type(exc).__name__}: "
                                           f"{exc}") from None
            self.timings["op2_load"] = time.perf_counter() - t
        self.notes: List[str] = []
        self.unsupported_domains: Dict[str, str] = {}
        self.ambiguous_tables: List[str] = []
        self._sets, self._entries = self._result_sets()

    # ------------------------------------------------------------ the file
    def info(self) -> dict:
        op2 = self.op2
        return {"bdf_path": self.bdf_path, "op2_path": self.op2_path,
                "sol": self.bdf.sol, "title": "",
                "solver": (getattr(op2, "_nastran_format", "") or "").upper() if op2 else "",
                "op2_date": list(getattr(op2, "date", ()) or ()) if op2 else [],
                "pynastran_version": pyNastran.__version__, "numpy_version": np.__version__,
                "n_result_sets": len(self._sets)}

    # ------------------------------------------------------------ the mesh
    def mesh(self) -> Tuple[dict, Dict[str, np.ndarray]]:
        bdf = self.bdf
        t = time.perf_counter()
        if bdf.nodes:
            nid_cp_cd, xyz_basic, _, _, _ = bdf.get_xyz_in_coord_array(cid=0, fdtype="float64",
                                                                       idtype="int64")
            order = np.argsort(nid_cp_cd[:, 0], kind="stable")
            nid_cp_cd, xyz_basic = nid_cp_cd[order], xyz_basic[order]
        else:
            nid_cp_cd, xyz_basic = np.zeros((0, 3), np.int64), np.zeros((0, 3))
        eids, types, offsets, conn, pids, cordm = [], [], [0], [], [], []
        type_names = list(SOLID_TYPES)
        for eid in sorted(bdf.elements):
            e = bdf.elements[eid]
            if e.type not in SOLID_TYPES:
                continue
            eids.append(eid)
            types.append(type_names.index(e.type))
            conn.extend(0 if n is None else int(n) for n in e.node_ids)
            offsets.append(len(conn))
            pids.append(int(e.pid))
            prop = bdf.properties.get(e.pid)
            cordm.append(int(getattr(prop, "cordm", 0) or 0))
        cids = sorted(bdf.coords)
        coords = [bdf.coords[c] for c in cids]
        arrays = {
            "node_ids": nid_cp_cd[:, 0].astype(np.int64),
            "coordinates": np.asarray(xyz_basic, dtype=np.float64).reshape(-1, 3),
            "node_cp": nid_cp_cd[:, 1].astype(np.int32),
            "node_cd": nid_cp_cd[:, 2].astype(np.int32),
            "element_ids": np.asarray(eids, dtype=np.int64),
            "element_types": np.asarray(types, dtype=np.int32),
            "element_offsets": np.asarray(offsets, dtype=np.int64),
            "connectivity": np.asarray(conn, dtype=np.int64),
            "element_property": np.asarray(pids, dtype=np.int64),
            "element_material_cs": np.asarray(cordm, dtype=np.int32),
            "coord_ids": np.asarray(cids, dtype=np.int64),
            "coord_origins": np.array([c.origin for c in coords], dtype=np.float64).reshape(-1, 3),
            "coord_axes": np.array([c.beta() for c in coords], dtype=np.float64).reshape(-1, 3, 3),
        }
        meta = {"element_type_names": type_names,
                "coord_kinds": [c.Type for c in coords], "coord_cards": [c.type for c in coords]}
        self.timings["mesh_extract"] = time.perf_counter() - t
        return meta, arrays

    # ------------------------------------------------------- result sets
    def _tables(self):
        """(result, element type or None, subcase, table) of every supported table."""
        op2 = self.op2
        if op2 is None:
            return
        for result, attrs in NODAL_TABLES.items():
            for attr in attrs:
                for isub, table in sorted(getattr(op2, attr, {}).items(), key=lambda kv: str(kv[0])):
                    yield result, None, isub, table
        for result in ELEMENT_RESULTS:
            group = getattr(op2.op2_results, result)
            for etype in SOLID_TYPES:
                for isub, table in sorted(getattr(group, f"{etype.lower()}_{result}", {}).items(),
                                          key=lambda kv: str(kv[0])):
                    yield result, etype, isub, table

    @staticmethod
    def _step_values(table, code: int) -> np.ndarray:
        if code == 2:
            return np.asarray(table.modes, dtype=np.float64)
        times = np.asarray(table._times, dtype=np.float64)
        if code == 1:                                        # static: the load set, else position
            lsdvmns = getattr(table, "lsdvmns", None)
            if lsdvmns is not None and len(lsdvmns) == len(times):
                return np.asarray(lsdvmns, dtype=np.float64)
            return np.arange(1, len(times) + 1, dtype=np.float64)
        return times

    def _result_sets(self):
        per_subcase: Dict[object, dict] = {}
        entries: Dict[Tuple[object, float], Dict[str, list]] = {}
        for result, etype, isub, table in self._tables():
            code = int(table.analysis_code)
            label = f"{result}{'' if etype is None else ' ' + etype} subcase {isub}"
            if code not in DOMAINS:
                self.unsupported_domains[label] = ANALYSIS_CODES.get(code, f"analysis code {code}")
                continue
            sub = per_subcase.setdefault(isub, {"code": code, "complex": False, "modal": {}, "table": table})
            if sub["code"] != code:
                raise AdapterError("format", f"subcase {isub}: analysis codes {sub['code']} and {code}")
            sub["complex"] = sub["complex"] or bool(table.is_complex)
            values = self._step_values(table, code)
            seen: Dict[float, int] = {}
            for i, v in enumerate(values):
                if v in seen:
                    j = seen[v]
                    if np.array_equal(table.data[i], table.data[j], equal_nan=True):
                        continue
                    self.ambiguous_tables.append(f"{label}: step {v} twice with different values")
                    break
                seen[v] = i
            else:
                if len(seen) < len(values):
                    self.notes.append(f"{label}: {len(values) - len(seen)} repeated step(s) with identical "
                                      f"values merged")
                for v, i in seen.items():
                    entries.setdefault((isub, v), {}).setdefault(result, []).append((etype, table, i))
                    eigns, cycles = getattr(table, "eigns", None), getattr(table, "mode_cycles", None)
                    if code == 2 and v not in sub["modal"] and eigns is not None and cycles is not None:
                        sub["modal"][v] = (float(eigns[i]), float(cycles[i]))
        sets, by_index = [], {}
        for isub in sorted(per_subcase, key=str):
            sub = per_subcase[isub]
            domain = DOMAINS[sub["code"]]
            for step, value in enumerate(sorted(v for (s, v) in entries if s == isub)):
                avail = entries[(isub, value)]
                by_index[len(sets)] = avail
                table = sub["table"]
                entry = {"index": len(sets), "subcase": int(isub) if isinstance(isub, (int, np.integer)) else str(isub),
                         "domain": domain, "analysis_code": sub["code"], "step": step, "value": value,
                         "mode": None, "eigenvalue": None, "frequency": None, "time": None,
                         "load_factor": None, "load_set": None,
                         "is_complex": any(t.is_complex for rows in avail.values() for _, t, _ in rows),
                         "available": sorted(avail),
                         "title": str(getattr(table, "title", "")).strip(),
                         "subtitle": str(getattr(table, "subtitle", "")).strip(),
                         "label": str(getattr(table, "label", "")).strip()}
                if sub["code"] == 2:
                    entry["mode"] = int(value)
                    entry["eigenvalue"], entry["frequency"] = sub["modal"].get(value, (None, None))
                elif sub["code"] == 5:
                    entry["frequency"] = value
                elif sub["code"] == 6:
                    entry["time"] = value
                elif sub["code"] == 10:
                    entry["load_factor"] = value
                else:
                    entry["load_set"] = int(value)
                sets.append(entry)
        return sets, by_index

    def result_sets(self) -> List[dict]:
        return self._sets

    def _rows(self, result: str, set_index: int):
        if not 0 <= set_index < len(self._sets):
            raise AdapterError("argument", f"result set {set_index} of {len(self._sets)}")
        rows = self._entries[set_index].get(result)
        if not rows:
            raise AdapterError("missing", f"no {result} in result set {set_index} "
                                          f"(available: {', '.join(self._sets[set_index]['available'])})")
        return rows

    # ------------------------------------------------------------ results
    def nodal(self, result: str, set_index: int) -> Tuple[dict, Dict[str, np.ndarray]]:
        if result not in NODAL_TABLES:
            raise AdapterError("argument", f"not a nodal result: {result}")
        t = time.perf_counter()
        (_, table, i), = self._rows(result, set_index)
        values = table.data[i]
        values = values.astype(np.complex128 if table.is_complex else np.float64)
        arrays = {"node_ids": table.node_gridtype[:, 0].astype(np.int64),
                  "grid_types": table.node_gridtype[:, 1].astype(np.int32),
                  "values": values}
        components = ("TEMP",) if result == "temperature" else ("T1", "T2", "T3", "R1", "R2", "R3")
        meta = {"result": result, "set_index": set_index, "components": list(components)[:values.shape[1]],
                "is_complex": bool(table.is_complex), "table": table.table_name,
                "grid_type_names": {str(k): v for k, v in GRID_TYPES.items()}}
        self.timings[f"{result}_extract"] = time.perf_counter() - t
        return meta, arrays

    def element_nodal(self, result: str, set_index: int) -> Tuple[dict, Dict[str, np.ndarray]]:
        if result not in ELEMENT_RESULTS:
            raise AdapterError("argument", f"not an element result: {result}")
        t = time.perf_counter()
        parts, solver_names, complex_ = [], None, None
        for etype, table, i in self._rows(result, set_index):
            tensor, solver, names = _tensor_columns(table, result)
            data = table.data[i]
            dtype = np.complex128 if table.is_complex else np.float64
            parts.append((SOLID_TYPES.index(etype), table.element_node.astype(np.int64),
                          data[:, tensor].astype(dtype),
                          data[:, solver].astype(np.float64) if solver else None,
                          table.element_cid.astype(np.int64)))
            solver_names = solver_names if solver_names is not None else names
            if names != solver_names:
                solver_names = []                           # tables disagree: pass none through
            complex_ = bool(table.is_complex) if complex_ is None else complex_ or bool(table.is_complex)
        element_node = np.concatenate([p[1] for p in parts])
        arrays = {"element_ids": element_node[:, 0], "node_ids": element_node[:, 1],
                  "element_types": np.concatenate([np.full(len(p[1]), p[0], np.int32) for p in parts]),
                  "values": np.concatenate([p[2] for p in parts]),
                  "element_cs_ids": np.concatenate([p[4][:, 0] for p in parts]),
                  "element_cs": np.concatenate([p[4][:, 1] for p in parts]).astype(np.int64)}
        if solver_names and all(p[3] is not None for p in parts):
            arrays["solver_values"] = np.concatenate([p[3] for p in parts])
        meta = {"result": result, "set_index": set_index,
                "components": [h.upper() for h in TENSOR_HEADERS[result]],
                "element_type_names": list(SOLID_TYPES), "is_complex": complex_,
                "solver_names": solver_names if "solver_values" in arrays else []}
        self.timings[f"{result}_extract"] = time.perf_counter() - t
        return meta, arrays

    # -------------------------------------------------------- unsupported
    def unsupported(self) -> dict:
        bdf = self.bdf
        elements: Dict[str, int] = {}
        for group in (bdf.elements, bdf.rigid_elements, bdf.masses, bdf.plotels):
            for e in group.values():
                if e.type not in SOLID_TYPES:
                    elements[e.type] = elements.get(e.type, 0) + 1
        tables = []
        if self.op2 is not None:
            used = {attr for attrs in NODAL_TABLES.values() for attr in attrs}
            used |= {f"{r}.{t.lower()}_{r}" for r in ELEMENT_RESULTS for t in SOLID_TYPES}
            for name in self.op2.get_table_types():
                if name in used or name == "params":
                    continue
                # "stress.cquad4_stress" lives in op2.op2_results.stress, "spc_forces" on op2
                if "." in name:
                    head, tail = name.split(".", 1)
                    obj = getattr(getattr(self.op2.op2_results, head, None), tail, None)
                else:
                    obj = getattr(self.op2, name, None)
                if isinstance(obj, dict) and obj:
                    tables.append(name)
        return {"elements": elements, "result_tables": sorted(tables),
                "domains": self.unsupported_domains, "ambiguous_tables": self.ambiguous_tables,
                "notes": self.notes}


class Session:
    """The requests of one client (serve.py's pipe, or turbonastran in-process):
    ``handle(request) -> (metadata, arrays)``, AdapterError on refusal."""

    def __init__(self) -> None:
        self.model: Optional[Model] = None

    def handle(self, req: dict):
        op = req.get("op")
        if op == "open":
            self.model = Model(req["bdf"], req.get("op2"))
            return {"ok": True, "info": self.model.info(), "timings": self.model.timings}, {}
        if self.model is None:
            raise AdapterError("argument", "no model open")
        m = self.model
        if op == "info":
            return {"info": m.info()}, {}
        if op == "mesh":
            return m.mesh()
        if op == "result_sets":
            return {"sets": m.result_sets()}, {}
        if op == "nodal":
            return m.nodal(req["result"], int(req["set"]))
        if op == "element_nodal":
            return m.element_nodal(req["result"], int(req["set"]))
        if op == "unsupported":
            return m.unsupported(), {}
        if op == "timings":
            return {"timings": m.timings}, {}
        raise AdapterError("argument", f"unknown request {op!r}")

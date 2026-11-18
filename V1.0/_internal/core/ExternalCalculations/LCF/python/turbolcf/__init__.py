"""turbolcf — TurboSection's low-cycle-fatigue engine (NASALIFE method and
Strain-Life).

The Python face of turbolcf.dll: loading, the library's identity, options,
contexts, material, runs and detail.  Every formula, rule and default value is
inside the DLL; this package only moves data across the C ABI
(native/include/turbolcf/turbolcf.h), and tests/test_binding_is_thin.py keeps
it that way.

Units are the ABI's: stress MPa, temperature K, time s, life in cycles or
missions; the Larson-Miller block in its native deck units.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from . import _ffi
from ._ffi import (ArgumentError, BufferSizeError, CancelledError, ClosedError, MaterialError,
                   NativeError, OptionsError, RangeError, StateError, TurboLCFError,
                   UnavailableError, UnsupportedError, VersionError, build_info, library,
                   loaded_dll)

__all__ = ["ArgumentError", "BufferSizeError", "CancelledError", "ClosedError", "Context",
           "CYCLE_DTYPE", "DIAGNOSTIC_DTYPE", "Detail", "INCREMENT_DTYPE", "MaterialError",
           "NativeError", "OptionsError", "RangeError", "SUMMARY_DTYPE", "StateError",
           "TurboLCFError", "UnavailableError", "UnsupportedError", "VersionError",
           "VersionInfo", "build_info", "default_options", "library", "loaded_dll", "version",
           "SL_CYCLE_DTYPE", "SL_ROW_DTYPE", "StrainLife", "StrainLifeDetail",
           "sl_check_options", "sl_default_options", "sl_material",
           "SS_CYCLE_DTYPE", "SS_ROW_DTYPE", "StressLife", "StressLifeDetail",
           "ss_check_options", "ss_default_options"]

#: Structured dtypes of the result rows, laid out as the ABI structs.
SUMMARY_DTYPE = _ffi.numpy_dtype(_ffi.Summary)
CYCLE_DTYPE = _ffi.numpy_dtype(_ffi.CycleRecord)
INCREMENT_DTYPE = _ffi.numpy_dtype(_ffi.IncrementRecord)
DIAGNOSTIC_DTYPE = _ffi.numpy_dtype(_ffi.DiagnosticRecord)


@dataclass(frozen=True)
class VersionInfo:
    """The loaded library's identity; model_revision changes with every
    change of numeric behaviour (it belongs in a result's signature)."""

    abi_version: int
    major: int
    minor: int
    patch: int
    model_revision: int
    build: str


@dataclass(frozen=True)
class Detail:
    """Everything the library computed for one location."""

    summary: np.void                    # one SUMMARY_DTYPE row
    cycles: np.ndarray                  # CYCLE_DTYPE: the counter's order (rejected: rank -1)
    increments: np.ndarray              # INCREMENT_DTYPE: the creep integration
    effective_stress: np.ndarray        # per mission point
    projected_stress: np.ndarray        # per mission point (NaN without damage rainflow)
    turning_points: np.ndarray
    closed_sequence: np.ndarray
    diagnostics: np.ndarray             # DIAGNOSTIC_DTYPE: one row per flag raised
    critical_plane_direction: np.ndarray
    critical_plane_scale: float


def version() -> VersionInfo:
    lib = library()
    info = _ffi.VersionInfo(struct_size=ctypes.sizeof(_ffi.VersionInfo))
    _ffi.check(lib, lib.turbolcf_version(ctypes.byref(info)))
    return VersionInfo(info.abi_version, info.major, info.minor, info.patch,
                       info.model_revision, _ffi.text(info.build))


def default_options() -> _ffi.Options:
    """TurboSection's options as the library defines them (a ctypes
    structure the caller may change field by field)."""
    lib = library()
    options = _ffi.Options(struct_size=ctypes.sizeof(_ffi.Options))
    _ffi.check(lib, lib.turbolcf_default_options(ctypes.byref(options)))
    return options


def _f64(values) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64)


def _i64(values) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.int64)


class Context:
    """One turbolcf context: options fixed at creation, material set into
    it, runs against it.  Used by one thread at a time."""

    def __init__(self, options: _ffi.Options):
        self._lib = library()
        self._handle = ctypes.c_void_p()
        _ffi.check(self._lib, self._lib.turbolcf_create(ctypes.byref(options),
                                                        ctypes.byref(self._handle)))

    @property
    def handle(self) -> ctypes.c_void_p:
        if not self._handle:
            raise ClosedError("the context is closed")
        return self._handle

    @property
    def closed(self) -> bool:
        return not self._handle

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, ctypes.c_void_p()
            _ffi.check(self._lib, self._lib.turbolcf_destroy(handle))

    def __enter__(self) -> "Context":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ----------------------------------------------------------- material
    def _call(self, name: str, *args) -> None:
        _ffi.check(self._lib, getattr(self._lib, name)(self.handle, *args))

    def set_lcf_table(self, a_ratio, curve_temperature, curve_offset, life, stress,
                      flags=None) -> None:
        t, o, n, s = _f64(curve_temperature), _i64(curve_offset), _f64(life), _f64(stress)
        f = None if flags is None else np.ascontiguousarray(flags, dtype=np.int32)
        table = _ffi.LcfTable(struct_size=ctypes.sizeof(_ffi.LcfTable), a_ratio=a_ratio,
                              n_curves=len(t), curve_temperature=_ffi.ptr(t, _ffi.f64),
                              curve_offset=_ffi.ptr(o, _ffi.i64), life=_ffi.ptr(n, _ffi.f64),
                              stress=_ffi.ptr(s, _ffi.f64),
                              flags=None if f is None else _ffi.ptr(f, _ffi.i32))
        self._call("turbolcf_set_lcf_table", ctypes.byref(table))

    def set_walker(self, temperature, exponent) -> None:
        t, m = _f64(temperature), _f64(exponent)
        curve = _ffi.Curve(struct_size=ctypes.sizeof(_ffi.Curve), n=len(t),
                           temperature=_ffi.ptr(t, _ffi.f64), value=_ffi.ptr(m, _ffi.f64))
        self._call("turbolcf_set_walker", ctypes.byref(curve))

    def set_ramberg_osgood(self, temperature, strength, exponent, yield_offset_strain,
                           clamp_below_lowest_temperature=False) -> None:
        t, k, n = _f64(temperature), _f64(strength), _f64(exponent)
        curve = _ffi.RambergOsgood(
            struct_size=ctypes.sizeof(_ffi.RambergOsgood), n=len(t),
            temperature=_ffi.ptr(t, _ffi.f64), strength=_ffi.ptr(k, _ffi.f64),
            exponent=_ffi.ptr(n, _ffi.f64), yield_offset_strain=yield_offset_strain,
            clamp_below_lowest_temperature=int(bool(clamp_below_lowest_temperature)))
        self._call("turbolcf_set_ramberg_osgood", ctypes.byref(curve))

    def set_rupture_lmp(self, coefficients, *, constant, stress_offset, temperature_adder,
                        temperature_multiplier, rupture_multiplier, stress_low, stress_high,
                        temperature_low, temperature_high, stress_type) -> None:
        c = _f64(coefficients)
        lmp = _ffi.RuptureLmp(
            struct_size=ctypes.sizeof(_ffi.RuptureLmp), n_coefficients=len(c),
            coefficients=_ffi.ptr(c, _ffi.f64), constant=constant, stress_offset=stress_offset,
            temperature_adder=temperature_adder, temperature_multiplier=temperature_multiplier,
            rupture_multiplier=rupture_multiplier, stress_low=stress_low,
            stress_high=stress_high, temperature_low=temperature_low,
            temperature_high=temperature_high, stress_type=stress_type)
        self._call("turbolcf_set_rupture_lmp", ctypes.byref(lmp))

    def set_rupture_table(self, curve_temperature, curve_offset, stress, life,
                          flags=None) -> None:
        t, o, s, n = _f64(curve_temperature), _i64(curve_offset), _f64(stress), _f64(life)
        f = None if flags is None else np.ascontiguousarray(flags, dtype=np.int32)
        table = _ffi.RuptureTable(struct_size=ctypes.sizeof(_ffi.RuptureTable),
                                  n_curves=len(t), curve_temperature=_ffi.ptr(t, _ffi.f64),
                                  curve_offset=_ffi.ptr(o, _ffi.i64),
                                  stress=_ffi.ptr(s, _ffi.f64), life=_ffi.ptr(n, _ffi.f64),
                                  flags=None if f is None else _ffi.ptr(f, _ffi.i32))
        self._call("turbolcf_set_rupture_table", ctypes.byref(table))

    def validate(self) -> None:
        self._call("turbolcf_validate")

    # ---------------------------------------------------------------- runs
    @staticmethod
    def _history(time, temperature, stress, location_ids):
        t = _f64(time)
        temps = _f64(temperature)
        temps = temps.reshape(1, -1) if temps.ndim == 1 else temps
        s = _f64(stress)
        s = s.reshape(1, *s.shape) if s.ndim == 2 else s
        n_locations, n_points = temps.shape
        if t.shape != (n_points,) or s.shape != (n_locations, n_points, 6):
            raise ArgumentError(f"history shapes: time {t.shape}, temperature {temps.shape}, "
                                f"stress {s.shape}; expected (N,), (L, N), (L, N, 6)")
        ids = None if location_ids is None else _i64(location_ids)
        if ids is not None and ids.shape != (n_locations,):
            raise ArgumentError(f"location_ids {ids.shape}, expected ({n_locations},)")
        h = _ffi.History(struct_size=ctypes.sizeof(_ffi.History), n_locations=n_locations,
                         n_points=n_points, time=_ffi.ptr(t, _ffi.f64),
                         temperature=_ffi.ptr(temps, _ffi.f64), stress=_ffi.ptr(s, _ffi.f64),
                         location_ids=None if ids is None else _ffi.ptr(ids, _ffi.i64))
        return h, (t, temps, s, ids)

    def run(self, time, temperature, stress, location_ids=None,
            progress: Optional[Callable[[int, int], bool]] = None) -> np.ndarray:
        """One SUMMARY_DTYPE row per location.  time (N,), temperature (L, N),
        stress (L, N, 6) xx yy zz xy yz zx.  progress(done, total) returning
        True cancels: CancelledError, whose .rows holds what was evaluated."""
        h, keep = self._history(time, temperature, stress, location_ids)
        rows = np.zeros(h.n_locations, dtype=SUMMARY_DTYPE)
        callback = None if progress is None else _ffi.PROGRESS_FN(
            lambda done, total, user: 1 if progress(done, total) else 0)
        status = self._lib.turbolcf_run(
            self.handle, ctypes.byref(h), rows.ctypes.data_as(ctypes.c_void_p), h.n_locations,
            SUMMARY_DTYPE.itemsize, None if callback is None else ctypes.cast(callback,
                                                                            ctypes.c_void_p),
            None)
        if status == _ffi.E_CANCELLED:
            error = CancelledError(f"[CANCELLED] {_ffi.last_error(self._lib)}")
            error.rows = rows
            raise error
        _ffi.check(self._lib, status)
        return rows

    def detail(self, time, temperature, stress, location_id=None) -> Detail:
        """Everything computed for one location: temperature (N,), stress (N, 6)."""
        h, keep = self._history(time, temperature, stress,
                                None if location_id is None else [location_id])
        if h.n_locations != 1:
            raise ArgumentError("detail() takes one location")
        info = _ffi.DetailInfo(struct_size=ctypes.sizeof(_ffi.DetailInfo))
        self._call("turbolcf_detail_load", ctypes.byref(h), ctypes.byref(info))
        try:
            cycles = np.zeros(info.n_cycles, dtype=CYCLE_DTYPE)
            increments = np.zeros(info.n_increments, dtype=INCREMENT_DTYPE)
            effective = np.zeros(info.n_points)
            projected = np.zeros(info.n_points)
            turning = np.zeros(info.n_turning_points, dtype=np.int64)
            closed = np.zeros(info.n_closed_sequence, dtype=np.int64)
            diagnostics = np.zeros(info.n_diagnostics, dtype=DIAGNOSTIC_DTYPE)
            buffers = _ffi.DetailBuffers(
                struct_size=ctypes.sizeof(_ffi.DetailBuffers),
                n_cycles=info.n_cycles, cycles=_ffi.ptr(cycles, _ffi.CycleRecord),
                n_increments=info.n_increments,
                increments=_ffi.ptr(increments, _ffi.IncrementRecord),
                n_points=info.n_points, effective_stress=_ffi.ptr(effective, _ffi.f64),
                projected_stress=_ffi.ptr(projected, _ffi.f64),
                n_turning_points=info.n_turning_points,
                turning_points=_ffi.ptr(turning, _ffi.i64),
                n_closed_sequence=info.n_closed_sequence,
                closed_sequence=_ffi.ptr(closed, _ffi.i64),
                n_diagnostics=info.n_diagnostics,
                diagnostics=_ffi.ptr(diagnostics, _ffi.DiagnosticRecord))
            self._call("turbolcf_detail_copy", ctypes.byref(buffers))
        finally:
            self._lib.turbolcf_detail_release(self.handle)
        summary = np.frombuffer(bytes(info.summary), dtype=SUMMARY_DTYPE)[0]
        return Detail(summary, cycles, increments, effective, projected, turning, closed,
                      diagnostics, np.array(info.critical_plane_direction[:]),
                      info.critical_plane_scale)


# ================================================================ Strain-Life
#: The Strain-Life result rows and counted cycles, laid out as the ABI structs.
SL_ROW_DTYPE = _ffi.numpy_dtype(_ffi.SlRow)
SL_CYCLE_DTYPE = _ffi.numpy_dtype(_ffi.SlCycle)
_SL_MATERIAL_FIELDS = tuple(name for name, _ in _ffi.SlMaterial._fields_[1:])
_SL_PER_POINT = ("signal", "sigma", "eps_n", "gamma", "sig_n", "tau")


def sl_default_options(analysis: int, criterion: int = 0, wb_method: int = 0) -> _ffi.SlOptions:
    """The defaults of a Strain-Life analysis (and critical-plane criterion,
    Wang-Brown method — 0: the default one) as the library defines them."""
    lib = library()
    options = _ffi.SlOptions(struct_size=ctypes.sizeof(_ffi.SlOptions))
    _ffi.check(lib, lib.turbolcf_sl_default_options(analysis, criterion, wb_method,
                                                    ctypes.byref(options)))
    return options


def sl_check_options(options: _ffi.SlOptions) -> None:
    """OptionsError with the library's reason unless the options are defined."""
    lib = library()
    _ffi.check(lib, lib.turbolcf_sl_check_options(ctypes.byref(options)))


def sl_material(values: dict) -> _ffi.SlMaterial:
    """A turbolcf_sl_material: the given constants {field: value}, every
    other one absent (NaN)."""
    given = dict(values)
    unknown = sorted(set(given) - set(_SL_MATERIAL_FIELDS))
    if unknown:
        raise ArgumentError(f"unknown Strain-Life material fields {unknown}")
    out = _ffi.SlMaterial(struct_size=ctypes.sizeof(_ffi.SlMaterial))
    for name in _SL_MATERIAL_FIELDS:
        setattr(out, name, float(given.get(name, float("nan"))))
    return out


@dataclass(frozen=True)
class StrainLifeDetail:
    """Everything the library computed for one node."""

    row: np.void                        # one SL_ROW_DTYPE row
    mode: int                           # SL_INPUT_* of the analysed path
    direction: np.ndarray               # uniaxial: the principal direction
    angles: np.ndarray                  # critical plane: theta, phi, psi [deg]
    normal: np.ndarray
    shear_direction: np.ndarray
    dang_van_line: tuple                # Dang Van: (a, b) of tau + a p = b
    signal: np.ndarray                  # per mission point (NaN: not of this analysis)
    sigma: np.ndarray
    eps_n: np.ndarray
    gamma: np.ndarray
    sig_n: np.ndarray
    tau: np.ndarray
    reversal_index: np.ndarray
    reversal_sigma: np.ndarray
    reversal_eps: np.ndarray
    reversal_origin: np.ndarray
    via_offset: np.ndarray              # CSR into vias, (n_reversals + 1)
    vias: np.ndarray
    cycles: np.ndarray                  # SL_CYCLE_DTYPE
    branch_offset: np.ndarray           # CSR into branch_eps / branch_sigma
    branch_eps: np.ndarray
    branch_sigma: np.ndarray


class StrainLife:
    """One Strain-Life context: options and material fixed at creation, runs
    against it.  Used by one thread at a time.

    *material*: one turbolcf_sl_material, or one per temperature of
    *temperatures* (ascending; the material over temperature); *mean_table*:
    the user mean stress table as (temperatures, fractions, factors (T, R)),
    or None."""

    def __init__(self, options: _ffi.SlOptions, material, temperatures=None, mean_table=None):
        self._lib = library()
        self._handle = ctypes.c_void_p()
        rows = list(material) if isinstance(material, (list, tuple)) else [material]
        if len(rows) == 1 and temperatures is None and mean_table is None:
            _ffi.check(self._lib, self._lib.turbolcf_sl_create(
                ctypes.byref(options), ctypes.byref(rows[0]), ctypes.byref(self._handle)))
            return
        temps = None if temperatures is None else _f64(temperatures)
        if len(rows) > 1 and (temps is None or temps.shape != (len(rows),)):
            raise ArgumentError(f"{len(rows)} materials need as many temperatures")
        array = (_ffi.SlMaterial * len(rows))(*rows)
        keep = [array, temps]
        table = None
        if mean_table is not None:
            t, f, v = (_f64(x) for x in mean_table)
            if v.shape != (t.shape[0], f.shape[0]):
                raise ArgumentError(f"mean table factors {v.shape}, expected "
                                    f"({t.shape[0]}, {f.shape[0]})")
            table = _ffi.SlMeanTable(struct_size=ctypes.sizeof(_ffi.SlMeanTable),
                                     n_temperatures=t.shape[0], n_rows=f.shape[0],
                                     temperature=_ffi.ptr(t, _ffi.f64),
                                     fraction=_ffi.ptr(f, _ffi.f64), factor=_ffi.ptr(v, _ffi.f64))
            keep += [t, f, v, table]
        spec = _ffi.SlMaterialTable(
            struct_size=ctypes.sizeof(_ffi.SlMaterialTable), n_temperatures=len(rows),
            temperature=None if temps is None else _ffi.ptr(temps, _ffi.f64),
            rows=ctypes.cast(array, ctypes.POINTER(_ffi.SlMaterial)),
            mean_table=None if table is None else ctypes.pointer(table))
        _ffi.check(self._lib, self._lib.turbolcf_sl_create_table(
            ctypes.byref(options), ctypes.byref(spec), ctypes.byref(self._handle)))
        del keep

    @property
    def handle(self) -> ctypes.c_void_p:
        if not self._handle:
            raise ClosedError("the context is closed")
        return self._handle

    @property
    def closed(self) -> bool:
        return not self._handle

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, ctypes.c_void_p()
            _ffi.check(self._lib, self._lib.turbolcf_sl_destroy(handle))

    def __enter__(self) -> "StrainLife":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _history(stress, strain, node_ids, temperature=None):
        s = _f64(stress)
        s = s.reshape(1, *s.shape) if s.ndim == 2 else s
        e = None if strain is None else _f64(strain)
        if e is not None and e.ndim == 2:
            e = e.reshape(1, *e.shape)
        if s.ndim != 3 or s.shape[2] != 6 or (e is not None and e.shape != s.shape):
            raise ArgumentError(f"history shapes: stress {s.shape}, strain "
                                f"{None if e is None else e.shape}; expected (N, M, 6) each")
        ids = None if node_ids is None else _i64(node_ids)
        if ids is not None and ids.shape != (s.shape[0],):
            raise ArgumentError(f"node_ids {ids.shape}, expected ({s.shape[0]},)")
        t = None if temperature is None else _f64(temperature)
        if t is not None and t.ndim == 1:
            t = t.reshape(1, *t.shape)
        if t is not None and t.shape != s.shape[:2]:
            raise ArgumentError(f"temperature {t.shape}, expected {s.shape[:2]}")
        h = _ffi.SlHistory(struct_size=ctypes.sizeof(_ffi.SlHistory), n_nodes=s.shape[0],
                           n_points=s.shape[1], stress=_ffi.ptr(s, _ffi.f64),
                           strain=None if e is None else _ffi.ptr(e, _ffi.f64),
                           node_ids=None if ids is None else _ffi.ptr(ids, _ffi.i64),
                           temperature=None if t is None else _ffi.ptr(t, _ffi.f64))
        return h, (s, e, ids, t)

    def run(self, stress, strain=None, node_ids=None,
            progress: Optional[Callable[[int, int], bool]] = None, stats: bool = False,
            temperature=None):
        """One SL_ROW_DTYPE row per node: stress (N, M, 6) xx yy zz xy yz xz,
        strain the same (direct input) or None, temperature (N, M) or None.
        progress(done, total) returning True cancels: CancelledError, whose
        .rows holds what was evaluated.  stats: also return the search
        counters as a dict."""
        h, keep = self._history(stress, strain, node_ids, temperature)
        rows = np.zeros(h.n_nodes, dtype=SL_ROW_DTYPE)
        counters = _ffi.SlStats(struct_size=ctypes.sizeof(_ffi.SlStats))
        callback = None if progress is None else _ffi.PROGRESS_FN(
            lambda done, total, user: 1 if progress(done, total) else 0)
        status = self._lib.turbolcf_sl_run(
            self.handle, ctypes.byref(h), rows.ctypes.data_as(ctypes.c_void_p), h.n_nodes,
            SL_ROW_DTYPE.itemsize, ctypes.byref(counters),
            None if callback is None else ctypes.cast(callback, ctypes.c_void_p), None)
        if status == _ffi.E_CANCELLED:
            error = CancelledError(f"[CANCELLED] {_ffi.last_error(self._lib)}")
            error.rows = rows
            raise error
        _ffi.check(self._lib, status)
        if stats:
            return rows, {name: getattr(counters, name)
                          for name, _ in _ffi.SlStats._fields_[1:]}
        return rows

    def detail(self, stress, strain=None, node_id=None, angles=None,
               temperature=None) -> StrainLifeDetail:
        """Everything computed for one node: stress (M, 6), strain (M, 6) or
        None, temperature (M,) or None; angles (theta, phi, psi) of a
        critical plane, or None to search."""
        h, keep = self._history(stress, strain, None if node_id is None else [node_id],
                                temperature)
        if h.n_nodes != 1:
            raise ArgumentError("detail() takes one node")
        a = None if angles is None else _f64(angles)
        if a is not None and a.shape != (3,):
            raise ArgumentError(f"angles {a.shape}, expected (3,)")
        info = _ffi.SlDetailInfo(struct_size=ctypes.sizeof(_ffi.SlDetailInfo))
        _ffi.check(self._lib, self._lib.turbolcf_sl_detail_load(
            self.handle, ctypes.byref(h), None if a is None else _ffi.ptr(a, _ffi.f64),
            ctypes.byref(info)))
        try:
            nr = info.n_reversals
            per_point = {name: np.zeros(info.n_points) for name in _SL_PER_POINT}
            index, origin = np.zeros(nr, dtype=np.int64), np.zeros(nr, dtype=np.int64)
            rsig, reps = np.zeros(nr), np.zeros(nr)
            via_offset = np.zeros(nr + 1, dtype=np.int64)
            vias = np.zeros(info.n_vias, dtype=np.int64)
            cycles = np.zeros(info.n_cycles, dtype=SL_CYCLE_DTYPE)
            branch_offset = np.zeros(info.n_branches + 1, dtype=np.int64)
            beps, bsig = np.zeros(info.n_branch_points), np.zeros(info.n_branch_points)
            buffers = _ffi.SlDetailBuffers(
                struct_size=ctypes.sizeof(_ffi.SlDetailBuffers), n_points=info.n_points,
                n_reversals=nr, reversal_index=_ffi.ptr(index, _ffi.i64),
                reversal_sigma=_ffi.ptr(rsig, _ffi.f64), reversal_eps=_ffi.ptr(reps, _ffi.f64),
                reversal_origin=_ffi.ptr(origin, _ffi.i64),
                via_offset=_ffi.ptr(via_offset, _ffi.i64), n_vias=info.n_vias,
                vias=_ffi.ptr(vias, _ffi.i64), n_cycles=info.n_cycles,
                cycles=_ffi.ptr(cycles, _ffi.SlCycle), n_branches=info.n_branches,
                branch_offset=_ffi.ptr(branch_offset, _ffi.i64),
                n_branch_points=info.n_branch_points, branch_eps=_ffi.ptr(beps, _ffi.f64),
                branch_sigma=_ffi.ptr(bsig, _ffi.f64))
            for name, values in per_point.items():
                setattr(buffers, name, _ffi.ptr(values, _ffi.f64))
            _ffi.check(self._lib, self._lib.turbolcf_sl_detail_copy(self.handle,
                                                                    ctypes.byref(buffers)))
        finally:
            self._lib.turbolcf_sl_detail_release(self.handle)
        row = np.frombuffer(bytes(info.row), dtype=SL_ROW_DTYPE)[0]
        return StrainLifeDetail(row, info.mode, np.array(info.direction[:]),
                                np.array(info.angles[:]), np.array(info.normal[:]),
                                np.array(info.shear_direction[:]),
                                (info.dang_van_a, info.dang_van_b),
                                *(per_point[name] for name in _SL_PER_POINT), index, rsig,
                                reps, origin, via_offset, vias, cycles, branch_offset, beps,
                                bsig)


# ================================================================ Stress-Life
#: The Stress-Life result rows and counted cycles, laid out as the ABI structs.
SS_ROW_DTYPE = _ffi.numpy_dtype(_ffi.SsRow)
SS_CYCLE_DTYPE = _ffi.numpy_dtype(_ffi.SsCycle)


def ss_default_options(analysis: int = _ffi.SS_UNIAXIAL) -> _ffi.SsOptions:
    """The defaults of a Stress-Life analysis as the library defines them."""
    lib = library()
    options = _ffi.SsOptions(struct_size=ctypes.sizeof(_ffi.SsOptions))
    _ffi.check(lib, lib.turbolcf_ss_default_options(analysis, ctypes.byref(options)))
    return options


def ss_check_options(options: _ffi.SsOptions) -> None:
    """OptionsError with the library's reason unless the options are defined."""
    lib = library()
    _ffi.check(lib, lib.turbolcf_ss_check_options(ctypes.byref(options)))


@dataclass(frozen=True)
class StressLifeDetail:
    """Everything the library computed for one node."""

    row: np.void                        # one SS_ROW_DTYPE row
    parameter: int                      # SS_PARAM_* analysed
    direction: np.ndarray               # the principal direction (NaN unless that parameter)
    signal: np.ndarray                  # the fatigue stress per history point
    temperature: np.ndarray             # K per point (NaN: isothermal)
    reversal_index: np.ndarray          # history points of the counted sequence
    cycles: np.ndarray                  # SS_CYCLE_DTYPE


class StressLife:
    """One Stress-Life context: options fixed at creation, the material
    blocks set into it, runs against it.  Used by one thread at a time."""

    def __init__(self, options: _ffi.SsOptions):
        self._lib = library()
        self._handle = ctypes.c_void_p()
        _ffi.check(self._lib, self._lib.turbolcf_ss_create(ctypes.byref(options),
                                                           ctypes.byref(self._handle)))

    @property
    def handle(self) -> ctypes.c_void_p:
        if not self._handle:
            raise ClosedError("the context is closed")
        return self._handle

    @property
    def closed(self) -> bool:
        return not self._handle

    def close(self) -> None:
        if self._handle:
            handle, self._handle = self._handle, ctypes.c_void_p()
            _ffi.check(self._lib, self._lib.turbolcf_ss_destroy(handle))

    def __enter__(self) -> "StressLife":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _call(self, name: str, *args) -> None:
        _ffi.check(self._lib, getattr(self._lib, name)(self.handle, *args))

    # ------------------------------------------------------------- material
    def set_sn_table(self, curve_temperature, curve_offset, life, stress,
                     curve_kind: int = _ffi.SS_INTERP_LOG_LOG,
                     stress_measure: int = _ffi.SS_STRESS_AMPLITUDE,
                     life_measure: int = _ffi.SS_LIFE_CYCLES,
                     ratio_kind: int = _ffi.SS_RATIO_NONE, curve_ratio=None) -> None:
        """The S-N table: one curve per temperature (K) — or a family, each
        curve at a temperature and a ratio coordinate SS_RATIO_* —, CSR
        offsets into the life and stress points, the segments' coordinates
        SS_INTERP_* (the library copies the arrays)."""
        t, o, n, s = _f64(curve_temperature), _i64(curve_offset), _f64(life), _f64(stress)
        r = None if curve_ratio is None else _f64(curve_ratio)
        table = _ffi.SsSnTable(struct_size=ctypes.sizeof(_ffi.SsSnTable), n_curves=len(t),
                               ratio_kind=int(ratio_kind), curve_kind=int(curve_kind),
                               stress_measure=int(stress_measure),
                               life_measure=int(life_measure),
                               curve_temperature=_ffi.ptr(t, _ffi.f64),
                               curve_ratio=None if r is None else _ffi.ptr(r, _ffi.f64),
                               curve_offset=_ffi.ptr(o, _ffi.i64), life=_ffi.ptr(n, _ffi.f64),
                               stress=_ffi.ptr(s, _ffi.f64))
        self._call("turbolcf_ss_set_sn_table", ctypes.byref(table))

    def set_haigh_table(self, temperature, fraction, factor) -> None:
        """The user Haigh table: the amplitude factor (n_temperatures, n_rows)
        at mean stress fractions, per temperature (K)."""
        t, f = _f64(temperature), _f64(fraction)
        v = _f64(factor).reshape(len(t), len(f))
        table = _ffi.SsHaighTable(struct_size=ctypes.sizeof(_ffi.SsHaighTable),
                                  n_temperatures=len(t), n_rows=len(f),
                                  temperature=_ffi.ptr(t, _ffi.f64), fraction=_ffi.ptr(f, _ffi.f64),
                                  factor=_ffi.ptr(v, _ffi.f64))
        self._call("turbolcf_ss_set_haigh_table", ctypes.byref(table))

    def set_basquin(self, temperature, strength_coefficient, strength_exponent) -> None:
        """The Basquin constants per temperature (K)."""
        t, sf, b = _f64(temperature), _f64(strength_coefficient), _f64(strength_exponent)
        block = _ffi.SsBasquin(struct_size=ctypes.sizeof(_ffi.SsBasquin), n=len(t),
                               temperature=_ffi.ptr(t, _ffi.f64),
                               strength_coefficient=_ffi.ptr(sf, _ffi.f64),
                               strength_exponent=_ffi.ptr(b, _ffi.f64))
        self._call("turbolcf_ss_set_basquin", ctypes.byref(block))

    def set_property(self, which: int, temperature, value) -> None:
        """A value per temperature (K): SS_PROPERTY_*."""
        t, v = _f64(temperature), _f64(value)
        self._call("turbolcf_ss_set_property", int(which), len(t), _ffi.ptr(t, _ffi.f64),
                   _ffi.ptr(v, _ffi.f64))

    def validate(self) -> None:
        """MaterialError with the library's reason unless the blocks suffice."""
        self._call("turbolcf_ss_validate")

    def curve(self, query: int, x: float, temperature: float = float("nan")):
        """The life curve at a temperature: (value, flags) of SS_QUERY_*;
        RangeError when the extrapolation policy refuses."""
        out, flags = ctypes.c_double(), ctypes.c_uint32()
        self._call("turbolcf_ss_curve_query", int(query), float(x), float(temperature),
                   ctypes.byref(out), ctypes.byref(flags))
        return out.value, int(flags.value)

    def allowable(self, life: float, mean, temperature: float = float("nan")):
        """The Haigh envelope: (the allowable amplitude at each mean stress
        and the life, flags) under the context's mean stress correction."""
        m = _f64(mean)
        out = np.zeros(len(m))
        flags = ctypes.c_uint32()
        self._call("turbolcf_ss_allowable", float(life), float(temperature), len(m),
                   _ffi.ptr(m, _ffi.f64), _ffi.ptr(out, _ffi.f64), ctypes.byref(flags))
        return out, int(flags.value)

    # ------------------------------------------------------------------ run
    @staticmethod
    def _history(stress, temperature, node_ids):
        s = _f64(stress)
        s = s.reshape(1, *s.shape) if s.ndim == 2 else s
        t = None if temperature is None else _f64(temperature)
        if t is not None and t.ndim == 1:
            t = t.reshape(1, *t.shape)
        if s.ndim != 3 or s.shape[2] != 6 or (t is not None and t.shape != s.shape[:2]):
            raise ArgumentError(f"history shapes: stress {s.shape}, temperature "
                                f"{None if t is None else t.shape}; expected (N, M, 6) and (N, M)")
        ids = None if node_ids is None else _i64(node_ids)
        if ids is not None and ids.shape != (s.shape[0],):
            raise ArgumentError(f"node_ids {ids.shape}, expected ({s.shape[0]},)")
        h = _ffi.SsHistory(struct_size=ctypes.sizeof(_ffi.SsHistory), n_nodes=s.shape[0],
                           n_points=s.shape[1], stress=_ffi.ptr(s, _ffi.f64),
                           temperature=None if t is None else _ffi.ptr(t, _ffi.f64),
                           node_ids=None if ids is None else _ffi.ptr(ids, _ffi.i64))
        return h, (s, t, ids)

    def run(self, stress, temperature=None, node_ids=None,
            progress: Optional[Callable[[int, int], bool]] = None, stats: bool = False):
        """One SS_ROW_DTYPE row per node: stress (N, M, 6) xx yy zz xy yz xz,
        temperature (N, M) K or None.  progress(done, total) returning True
        cancels: CancelledError, whose .rows holds what was evaluated.
        stats: also return the counters as a dict."""
        h, keep = self._history(stress, temperature, node_ids)
        rows = np.zeros(h.n_nodes, dtype=SS_ROW_DTYPE)
        counters = _ffi.SsStats(struct_size=ctypes.sizeof(_ffi.SsStats))
        callback = None if progress is None else _ffi.PROGRESS_FN(
            lambda done, total, user: 1 if progress(done, total) else 0)
        status = self._lib.turbolcf_ss_run(
            self.handle, ctypes.byref(h), rows.ctypes.data_as(ctypes.c_void_p), h.n_nodes,
            SS_ROW_DTYPE.itemsize, ctypes.byref(counters),
            None if callback is None else ctypes.cast(callback, ctypes.c_void_p), None)
        if status == _ffi.E_CANCELLED:
            error = CancelledError(f"[CANCELLED] {_ffi.last_error(self._lib)}")
            error.rows = rows
            raise error
        _ffi.check(self._lib, status)
        if stats:
            return rows, {name: getattr(counters, name)
                          for name, _ in _ffi.SsStats._fields_[1:]}
        return rows

    def detail(self, stress, temperature=None, node_id=None) -> StressLifeDetail:
        """Everything computed for one node: stress (M, 6), temperature (M)
        or None."""
        h, keep = self._history(stress, temperature, None if node_id is None else [node_id])
        if h.n_nodes != 1:
            raise ArgumentError("detail() takes one node")
        info = _ffi.SsDetailInfo(struct_size=ctypes.sizeof(_ffi.SsDetailInfo))
        _ffi.check(self._lib, self._lib.turbolcf_ss_detail_load(self.handle, ctypes.byref(h),
                                                                ctypes.byref(info)))
        try:
            signal, temp = np.zeros(info.n_points), np.zeros(info.n_points)
            index = np.zeros(info.n_reversals, dtype=np.int64)
            cycles = np.zeros(info.n_cycles, dtype=SS_CYCLE_DTYPE)
            buffers = _ffi.SsDetailBuffers(
                struct_size=ctypes.sizeof(_ffi.SsDetailBuffers), n_points=info.n_points,
                signal=_ffi.ptr(signal, _ffi.f64), temperature=_ffi.ptr(temp, _ffi.f64),
                n_reversals=info.n_reversals, reversal_index=_ffi.ptr(index, _ffi.i64),
                n_cycles=info.n_cycles, cycles=_ffi.ptr(cycles, _ffi.SsCycle))
            _ffi.check(self._lib, self._lib.turbolcf_ss_detail_copy(self.handle,
                                                                    ctypes.byref(buffers)))
        finally:
            self._lib.turbolcf_ss_detail_release(self.handle)
        row = np.frombuffer(bytes(info.row), dtype=SS_ROW_DTYPE)[0]
        return StressLifeDetail(row, info.parameter, np.array(info.direction[:]), signal, temp,
                                index, cycles)

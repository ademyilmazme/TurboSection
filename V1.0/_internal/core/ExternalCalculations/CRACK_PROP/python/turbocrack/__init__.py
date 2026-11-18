"""turbocrack — TurboSection's crack stress-intensity-factor engine (Python binding).

Semi-elliptical surface crack in a plate (docs/SURFACE_CRACK_SIF.md), K at the
deepest point (a-tip) and at the surface point (c-tip), for

* a uniform membrane stress and the plate-bending load (Newman–Raju, NASA TM-85793
  eq. 14) — :func:`surface_sif_uniform`;
* a stress field varying through the thickness only, given as polynomial
  coefficients (:func:`surface_sif_polynomial`) or as samples joined by straight
  lines (:func:`surface_sif_sampled`) — three-term weight functions calibrated on
  the Newman–Raju tension and bending solutions.

Quarter-elliptical corner crack in a plate (docs/CORNER_CRACK_SIF.md):

* production — ONE solver, :func:`solve_corner` (crack, :class:`CornerStressField`):
  Malik's weight-function formulation (1992) for every field — constant, through
  the thickness, across the width or 2-D; polynomial or sampled — returning the
  effective K_a and K_c with the field class, the evidence class and the
  integration status.  There is no method choice;
* reference only (validation, benchmarks, diagnostics; never a production
  result) — the Newman–Raju corner equation: :func:`evaluate_corner_newman_reference`,
  :func:`corner_newman_reference_front`, :func:`corner_newman_reference_factors`,
  :func:`check_corner_newman_reference`.

Crack growth (docs/CRACK_GROWTH.md): a :class:`Mission` (time history at the node)
-> :func:`rainflow` full cycles (closed loops) as (sigma_low, sigma_high) pairs in
propagation order (the major cycle, then by increasing range) -> :func:`grow`: K at the
low and the high state of every cycle by the production solver, per-direction K_min/K_max, the mean-stress model, Paris
or Sigmoidal, the RK2 midpoint update of a and c, the 0.96 K_mat stop.

Every formula lives in turbocrack.dll; this package only converts arguments and
results.  Units: any consistent set; K comes back in stress × length^(1/2).  A
geometry outside the published range of the formulation raises
:class:`ValidityError`; nothing is extrapolated.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from . import _ffi
from ._ffi import (ANALYSIS_GRADIENT, ANALYSIS_UNIFORM, FLAG_DEEP_CRACK, FLAG_INFINITE_WIDTH,
                   FLAG_LOW_ASPECT, FLAG_REFERENCE_EXTENDED, FLAG_SURFACE_POINT,
                   FLAG_WIDTH_ESTIMATED, ArgumentError, TurboCrackError, UnavailableError,
                   UnsupportedError, ValidityError)

INFINITE = float("inf")

__all__ = ["SurfaceCrack", "SurfaceSIF", "TipResult", "TurboCrackError", "UnavailableError",
           "ArgumentError", "ValidityError", "INFINITE", "library", "version", "check_surface",
           "newman_raju_factors", "surface_sif_uniform", "surface_sif_polynomial",
           "surface_sif_sampled", "evaluate_stress", "FLAG_DEEP_CRACK", "FLAG_LOW_ASPECT",
           "FLAG_INFINITE_WIDTH", "FLAG_SURFACE_POINT", "FLAG_WIDTH_ESTIMATED",
           "FLAG_REFERENCE_EXTENDED", "UnsupportedError", "CornerCrack", "CornerStressField",
           "CornerTip", "CornerSIF", "check_corner", "solve_corner", "evaluate_corner_stress",
           "CornerNewmanTip", "CornerNewmanReference", "check_corner_newman_reference",
           "evaluate_corner_newman_reference", "corner_newman_reference_front",
           "corner_newman_reference_factors", "SurfaceStressField", "Mission", "MissionInfo",
           "mission_check", "RainflowCycle", "rainflow", "GrowthMaterial", "GrowthSummary",
           "GrowthRecords", "GrowthResult", "growth_capacity", "growth_flag_names", "grow"]


@dataclass(frozen=True)
class SurfaceCrack:
    """A semi-elliptical surface crack: depth ``a``, half surface length ``c``,
    plate thickness ``t`` and full plate width ``width`` (Newman–Raju b = width/2;
    ``INFINITE`` for an infinite plate)."""
    a: float
    c: float
    t: float
    width: float = INFINITE

    def _native(self) -> _ffi.SurfaceGeometry:
        return _ffi.SurfaceGeometry(ctypes.sizeof(_ffi.SurfaceGeometry), self.a, self.c, self.t,
                                    self.width)


@dataclass(frozen=True)
class TipResult:
    """K at one tip and the factors behind it.  ``Y0``, ``Y1`` and ``M1``–``M3`` are
    the weight-function references and coefficients (None for Newman–Raju)."""
    K: float
    phi: float
    F: float
    H: float
    flags: Tuple[str, ...]
    Y0: Optional[float] = None
    Y1: Optional[float] = None
    M1: Optional[float] = None
    M2: Optional[float] = None
    M3: Optional[float] = None


@dataclass(frozen=True)
class SurfaceSIF:
    """A surface-crack evaluation: ``a_tip`` (deepest point) and ``c_tip`` (surface point)."""
    method: str
    a_tip: TipResult
    c_tip: TipResult
    a_over_c: float
    a_over_t: float
    c_over_b: float
    Q: float
    flags: Tuple[str, ...]
    quadrature_points: int

    @property
    def K_a(self) -> float:
        return self.a_tip.K

    @property
    def K_c(self) -> float:
        return self.c_tip.K


def library() -> ctypes.CDLL:
    """The loaded turbocrack.dll (:class:`UnavailableError` when not built)."""
    return _ffi.library()


def version() -> dict:
    """{"abi_version", "model_revision", "version", "config"} of the loaded library."""
    return _ffi.version_info(_ffi.library())


def _flag_names(bits: int) -> Tuple[str, ...]:
    return tuple(name for bit, name in _ffi.FLAG_NAMES.items() if bits & bit)


def check_surface(crack: SurfaceCrack, analysis: str = "uniform") -> Tuple[str, ...]:
    """The flags of *crack* for a ``"uniform"`` (Newman–Raju) or ``"gradient"``
    (weight-function) analysis; :class:`ValidityError` with the published range when
    the geometry is outside it."""
    kinds = {"uniform": ANALYSIS_UNIFORM, "gradient": ANALYSIS_GRADIENT}
    if analysis not in kinds:
        raise ArgumentError(f"analysis must be one of {sorted(kinds)}, not {analysis!r}")
    lib = _ffi.library()
    flags = _ffi.i32(0)
    _ffi.check(lib, lib.turbocrack_surface_check(ctypes.byref(crack._native()), kinds[analysis],
                                                 ctypes.byref(flags)))
    return _flag_names(flags.value)


def newman_raju_factors(crack: SurfaceCrack, phi: float) -> Dict[str, float]:
    """The Newman–Raju factors at the parametric angle *phi* (rad, 0 = c-tip,
    pi/2 = deepest point): Q, M1–M3, g, f_phi, f_w, F, p, H1, H2, H, H_slope."""
    lib = _ffi.library()
    out = _ffi.NrFactors()
    out.struct_size = ctypes.sizeof(out)
    _ffi.check(lib, lib.turbocrack_surface_nr_factors(ctypes.byref(crack._native()), phi,
                                                      ctypes.byref(out)))
    return {name: getattr(out, name) for name, _ in _ffi.NrFactors._fields_ if name != "struct_size"}


def _stress(kind, coordinate=_ffi.DEPTH_Z_OVER_T, membrane=0.0, bending=0.0, depth=None,
            value=None):
    keep = []
    s = _ffi.Stress1D()
    s.struct_size = ctypes.sizeof(s)
    s.kind = kind
    s.coordinate = coordinate
    s.membrane = membrane
    s.bending = bending
    s.reserved = 0
    if value is not None:
        v = _ffi.doubles(value)
        keep.append(v)
        s.value = ctypes.cast(v, ctypes.POINTER(_ffi.f64))
        s.n = len(v)
    if depth is not None:
        d = _ffi.doubles(depth)
        keep.append(d)
        s.depth = ctypes.cast(d, ctypes.POINTER(_ffi.f64))
        if value is not None and len(d) != len(value):
            raise ArgumentError(f"{len(d)} depths but {len(value)} stresses")
    return s, keep


def _coordinate(name: str) -> int:
    if name not in _ffi.COORDINATES:
        raise ArgumentError(f"coordinate must be one of {sorted(_ffi.COORDINATES)}, not {name!r}")
    return _ffi.COORDINATES[name]


def _evaluate(crack: SurfaceCrack, stress) -> SurfaceSIF:
    lib = _ffi.library()
    out = _ffi.SurfaceResult()
    out.struct_size = ctypes.sizeof(out)
    _ffi.check(lib, lib.turbocrack_surface_sif(ctypes.byref(crack._native()),
                                               ctypes.byref(stress), ctypes.byref(out)))
    weight_function = out.method == _ffi.METHOD_WEIGHT_FUNCTION
    tips = []
    for i in (0, 1):
        wf = weight_function
        tips.append(TipResult(K=out.K[i], phi=out.phi[i], F=out.F[i], H=out.H[i],
                              flags=_flag_names(out.tip_flags[i]),
                              Y0=out.Y0[i] if wf else None, Y1=out.Y1[i] if wf else None,
                              M1=out.M1[i] if wf else None, M2=out.M2[i] if wf else None,
                              M3=out.M3[i] if wf else None))
    return SurfaceSIF(method=_ffi.METHOD_NAMES[out.method], a_tip=tips[0], c_tip=tips[1],
                      a_over_c=out.a_over_c, a_over_t=out.a_over_t, c_over_b=out.c_over_b,
                      Q=out.Q, flags=_flag_names(out.flags),
                      quadrature_points=out.quadrature_points)


def surface_sif_uniform(crack: SurfaceCrack, membrane: float, bending: float = 0.0) -> SurfaceSIF:
    """Newman–Raju eq. 14: membrane stress S_t and plate bending S_b (the outer-fibre
    stress of S_b (1 - 2 z/t), z from the cracked surface)."""
    s, _keep = _stress(_ffi.STRESS_UNIFORM, membrane=membrane, bending=bending)
    return _evaluate(crack, s)


def surface_sif_polynomial(crack: SurfaceCrack, coefficients: Sequence[float],
                           coordinate: str = "z/t") -> SurfaceSIF:
    """Weight functions for sigma = sum_k coefficients[k] X^k with X = z/t (default),
    z/a or z (length units); z is the depth from the cracked surface."""
    s, keep = _stress(_ffi.STRESS_POLYNOMIAL, coordinate=_coordinate(coordinate),
                      value=list(coefficients))
    result = _evaluate(crack, s)
    del keep
    return result


def surface_sif_sampled(crack: SurfaceCrack, depth: Sequence[float], stress: Sequence[float],
                        coordinate: str = "z") -> SurfaceSIF:
    """Weight functions for samples (depth[i], stress[i]) joined by straight lines.
    depth must start at 0, increase strictly and reach the crack depth (no
    extrapolation); its coordinate is z (default), z/t or z/a."""
    s, keep = _stress(_ffi.STRESS_SAMPLED_LINEAR, coordinate=_coordinate(coordinate),
                      depth=list(depth), value=list(stress))
    result = _evaluate(crack, s)
    del keep
    return result


def evaluate_stress(crack: SurfaceCrack, z: Sequence[float], *, coefficients=None, depth=None,
                    stress=None, coordinate: Optional[str] = None,
                    membrane: Optional[float] = None, bending: float = 0.0):
    """The engine's stress representation at the depths *z* (length units): a
    polynomial (``coefficients``; coordinate default ``"z/t"``), samples (``depth``,
    ``stress``; coordinate default ``"z"``) or the uniform membrane + bending field
    (``membrane``, ``bending``) — the defaults of the SIF functions."""
    if membrane is not None:
        s, keep = _stress(_ffi.STRESS_UNIFORM, membrane=membrane, bending=bending)
    elif coefficients is not None:
        s, keep = _stress(_ffi.STRESS_POLYNOMIAL, coordinate=_coordinate(coordinate or "z/t"),
                          value=list(coefficients))
    elif depth is not None and stress is not None:
        s, keep = _stress(_ffi.STRESS_SAMPLED_LINEAR, coordinate=_coordinate(coordinate or "z"),
                          depth=list(depth), value=list(stress))
    else:
        raise ArgumentError("give membrane, coefficients, or depth and stress")
    lib = _ffi.library()
    zs = _ffi.doubles(z)
    out = (_ffi.f64 * len(zs))()
    _ffi.check(lib, lib.turbocrack_stress_evaluate(ctypes.byref(crack._native()),
                                                   ctypes.byref(s),
                                                   ctypes.cast(zs, ctypes.POINTER(_ffi.f64)),
                                                   len(zs),
                                                   ctypes.cast(out, ctypes.POINTER(_ffi.f64))))
    del keep
    return list(out)


# ---------------------------------------------------------------- corner crack
@dataclass(frozen=True)
class CornerCrack:
    """A quarter-elliptical corner crack in a plate (TM-85793 Fig. 2(c)): depth ``a``
    along the edge face (through the thickness, y), length ``c`` along the plate face
    (across the width, x), thickness ``t`` and width ``width`` = b measured from the
    cracked edge (``INFINITE``: no width correction).  ``width_correction`` of the
    Newman–Raju reference inside Malik's construction: ``"malik"`` (default, decision O2:
    crack-area fraction), ``"newman_raju"`` (TM-85793 eq. 44 as published) or ``"none"``
    (TM-83200, c/b <= 0.2 only)."""
    a: float
    c: float
    t: float
    width: float = INFINITE
    width_correction: str = "malik"

    def _native(self) -> _ffi.CornerGeometry:
        if self.width_correction not in _ffi.WIDTH_CORRECTIONS:
            raise ArgumentError(f"width_correction must be one of {sorted(_ffi.WIDTH_CORRECTIONS)}, "
                                f"not {self.width_correction!r}")
        return _ffi.CornerGeometry(ctypes.sizeof(_ffi.CornerGeometry),
                                   _ffi.WIDTH_CORRECTIONS[self.width_correction], 0, self.a, self.c,
                                   self.t, self.width)


def _plane_coordinate(name: str) -> int:
    if name not in _ffi.PLANE_COORDINATES:
        raise ArgumentError(f"coordinate must be one of {sorted(_ffi.PLANE_COORDINATES)}, "
                            f"not {name!r}")
    return _ffi.PLANE_COORDINATES[name]


def _matrix(rows, what: str):
    """[[m[i][j]]] as (nx, ny, flat list at j * nx + i); every row the same length."""
    rows = [list(r) for r in rows]
    if not rows or not rows[0]:
        raise ArgumentError(f"{what} is empty")
    ny = len(rows[0])
    if any(len(r) != ny for r in rows):
        raise ArgumentError(f"{what} must be rectangular: rows of {sorted({len(r) for r in rows})}")
    return len(rows), ny, [rows[i][j] for j in range(ny) for i in range(len(rows))]


@dataclass(frozen=True)
class CornerStressField:
    """A crack-face field sigma(x, y) for :func:`solve_corner`: x across the width from
    the cracked edge face, y through the thickness from the cracked plate face, origin
    at the corner.  Build it with the class methods.  Every form is one representation
    of the solver's plane field — a constant is a field like any other, and a 1-D field
    is that representation with one axis absent — so all go through the same Malik
    integration."""
    kind: int
    coordinate: int = _ffi.PLANE_LENGTH
    nx: int = 0
    ny: int = 0
    membrane: float = 0.0
    bending: float = 0.0
    x: Tuple[float, ...] = ()
    y: Tuple[float, ...] = ()
    value: Tuple[float, ...] = ()

    @classmethod
    def constant(cls, sigma0: float) -> "CornerStressField":
        """sigma = sigma0."""
        return cls(_ffi.STRESS_UNIFORM, membrane=float(sigma0))

    @classmethod
    def membrane_bending(cls, membrane: float, bending: float = 0.0) -> "CornerStressField":
        """sigma = S_t + S_b (1 - 2 y/t): remote tension plus out-of-plane bending."""
        return cls(_ffi.STRESS_UNIFORM, membrane=float(membrane), bending=float(bending))

    @classmethod
    def polynomial(cls, coefficients, coordinate: str = "section") -> "CornerStressField":
        """sigma = sum coefficients[i][j] X^i Y^j with X, Y in ``"section"`` (x/t, y/t,
        default), ``"crack"`` (x/c, y/a) or ``"length"`` (x, y) units."""
        nx, ny, flat = _matrix(coefficients, "coefficients")
        return cls(_ffi.STRESS_POLYNOMIAL, _plane_coordinate(coordinate), nx, ny, value=tuple(flat))

    @classmethod
    def depth_polynomial(cls, coefficients: Sequence[float],
                         coordinate: str = "section") -> "CornerStressField":
        """sigma = sum_k coefficients[k] Y^k, constant across the width."""
        return cls.polynomial([list(coefficients)], coordinate)

    @classmethod
    def width_polynomial(cls, coefficients: Sequence[float],
                         coordinate: str = "section") -> "CornerStressField":
        """sigma = sum_k coefficients[k] X^k, constant through the thickness."""
        return cls.polynomial([[v] for v in coefficients], coordinate)

    @classmethod
    def grid(cls, x: Sequence[float], y: Sequence[float], values,
             coordinate: str = "length") -> "CornerStressField":
        """Samples values[i][j] = sigma(x[i], y[j]) joined bilinearly; x and y start at 0,
        increase strictly and reach c and a (no extrapolation).  A single x (or y) means
        the field does not vary along it."""
        nx, ny, flat = _matrix(values, "values")
        if nx != len(x) or ny != len(y):
            raise ArgumentError(f"values is {nx} x {ny} but there are {len(x)} x and {len(y)} y")
        return cls(_ffi.STRESS_SAMPLED_BILINEAR, _plane_coordinate(coordinate), nx, ny,
                   x=tuple(x), y=tuple(y), value=tuple(flat))

    @classmethod
    def depth_samples(cls, y: Sequence[float], stress: Sequence[float],
                      coordinate: str = "length") -> "CornerStressField":
        """Samples (y[k], stress[k]) through the thickness, constant across the width."""
        return cls.grid([0.0], y, [list(stress)], coordinate)

    @classmethod
    def width_samples(cls, x: Sequence[float], stress: Sequence[float],
                      coordinate: str = "length") -> "CornerStressField":
        """Samples (x[k], stress[k]) across the width, constant through the thickness."""
        return cls.grid(x, [0.0], [[v] for v in stress], coordinate)

    def _native(self):
        """The ABI struct and the arrays it points into (keep both alive)."""
        keep = []
        s = _ffi.Stress2D()
        s.struct_size = ctypes.sizeof(s)
        s.kind = self.kind
        s.coordinate = self.coordinate
        s.nx = self.nx
        s.ny = self.ny
        s.membrane = self.membrane
        s.bending = self.bending
        for name, data in (("x", self.x), ("y", self.y), ("value", self.value)):
            if data:
                arr = _ffi.doubles(data)
                keep.append(arr)
                setattr(s, name, ctypes.cast(arr, ctypes.POINTER(_ffi.f64)))
        return s, keep


@dataclass(frozen=True)
class CornerTip:
    """One tip of a production result: Malik's effective ``K`` at the parametric angle
    ``phi`` (pi/2: the a-tip on the edge face; 0: the c-tip on the plate face), the
    method's reference ``K_reference`` (eqs 5-6 under unit tension) and its
    ``self_consistency`` there (the reference load through eqs 11-12, / K_reference - 1:
    0 at the a-tip by eq. 8; the c-tip residual, docs/CORNER_CRACK_MALIK_AUDIT.md)."""
    K: float
    phi: float
    K_reference: float
    self_consistency: float
    flags: Tuple[str, ...]


@dataclass(frozen=True)
class CornerSIF:
    """A production corner-crack result (Malik): ``a_tip`` and ``c_tip``;
    ``method`` "MALIK", ``representation`` "EFFECTIVE", ``field_class`` "CONSTANT" /
    "DEPTH" / "WIDTH" / "2D", ``evidence`` "BENCHMARKED" / "EXPERIMENTAL"
    (docs/CORNER_CRACK_SIF.md §8), ``integration`` "CLOSED_FORM" / "PANELS" with the
    orders used."""
    method: str
    representation: str
    field_class: str
    evidence: str
    a_tip: CornerTip
    c_tip: CornerTip
    a_over_c: float
    a_over_t: float
    c_over_b: float
    width_parameter: float
    f_w: float
    flags: Tuple[str, ...]
    integration: str
    quadrature_points: int
    phi_points: int
    depth_points: int
    angle_points: int
    U0: float
    dU0_da: float
    dU0_dc: float

    @property
    def K_a(self) -> float:
        return self.a_tip.K

    @property
    def K_c(self) -> float:
        return self.c_tip.K


def check_corner(crack: CornerCrack) -> Tuple[str, ...]:
    """The flags of *crack* in the production range (Malik on the Newman–Raju reference);
    :class:`ValidityError` with the range when outside it."""
    lib = _ffi.library()
    flags = _ffi.i32(0)
    _ffi.check(lib, lib.turbocrack_corner_check(ctypes.byref(crack._native()), ctypes.byref(flags)))
    return _flag_names(flags.value)


def solve_corner(crack: CornerCrack, field: CornerStressField, *, phi_points: int = 0,
                 depth_points: int = 0, angle_points: int = 0) -> CornerSIF:
    """THE corner-crack solver: Malik's formulation for *field* on *crack* -> K_a, K_c.
    Optional quadrature orders (0 = default): eqs 5-6 (``phi_points``, 64), eq. 8
    (``depth_points``, 48), sampled grids (``angle_points``, 12)."""
    if not isinstance(field, CornerStressField):
        raise ArgumentError("field must be a CornerStressField (build it with its class methods)")
    lib = _ffi.library()
    s, keep = field._native()
    options = _ffi.CornerOptions(ctypes.sizeof(_ffi.CornerOptions), phi_points, depth_points,
                                 angle_points, 0)
    out = _ffi.CornerResult()
    out.struct_size = ctypes.sizeof(out)
    _ffi.check(lib, lib.turbocrack_corner_solve(ctypes.byref(crack._native()), ctypes.byref(s),
                                                ctypes.byref(options), ctypes.byref(out)))
    del keep
    tips = [CornerTip(K=out.K[i], phi=out.phi[i], K_reference=out.K_reference[i],
                      self_consistency=out.self_consistency[i],
                      flags=_flag_names(out.tip_flags[i])) for i in (0, 1)]
    return CornerSIF(method=_ffi.METHOD_NAMES[out.method],
                     representation=_ffi.REPRESENTATION_NAMES[out.representation],
                     field_class=_ffi.FIELD_NAMES[out.field_class],
                     evidence=_ffi.EVIDENCE_NAMES[out.evidence], a_tip=tips[0], c_tip=tips[1],
                     a_over_c=out.a_over_c, a_over_t=out.a_over_t, c_over_b=out.c_over_b,
                     width_parameter=getattr(out, "lambda"), f_w=out.f_w,
                     flags=_flag_names(out.flags),
                     integration=_ffi.INTEGRATION_NAMES[out.integration],
                     quadrature_points=out.quadrature_points, phi_points=out.phi_points,
                     depth_points=out.depth_points, angle_points=out.angle_points, U0=out.U0,
                     dU0_da=out.dU0_da, dU0_dc=out.dU0_dc)


def evaluate_corner_stress(crack: CornerCrack, field: CornerStressField, x: Sequence[float],
                           y: Sequence[float]):
    """*field* as the solver sees it, at the points (x[k], y[k]) in length units."""
    if len(x) != len(y):
        raise ArgumentError(f"{len(x)} x but {len(y)} y")
    lib = _ffi.library()
    s, keep = field._native()
    xs, ys = _ffi.doubles(x), _ffi.doubles(y)
    out = (_ffi.f64 * len(xs))()
    _ffi.check(lib, lib.turbocrack_corner_stress_evaluate(
        ctypes.byref(crack._native()), ctypes.byref(s), ctypes.cast(xs, ctypes.POINTER(_ffi.f64)),
        ctypes.cast(ys, ctypes.POINTER(_ffi.f64)), len(xs),
        ctypes.cast(out, ctypes.POINTER(_ffi.f64))))
    del keep
    return list(out)


# ------------------------------------------ corner crack: Newman–Raju reference only
@dataclass(frozen=True)
class CornerNewmanTip:
    """REFERENCE ONLY: the Newman–Raju equation at one tip — ``K_local`` (eq. 37 at
    ``phi``), ``K_effective`` (the front projected with Malik's eq. 5-6 weights), the
    factors ``F`` and ``H``."""
    K_local: float
    K_effective: float
    phi: float
    F: float
    H: float
    flags: Tuple[str, ...]


@dataclass(frozen=True)
class CornerNewmanReference:
    """REFERENCE ONLY — the Newman–Raju corner equation (TM-85793 eq. 37) for uniform
    tension and out-of-plane bending: for validation, published benchmarks, engineering
    comparison and diagnostics.  Not a production result (:func:`solve_corner` is)."""
    a_tip: CornerNewmanTip
    c_tip: CornerNewmanTip
    a_over_c: float
    a_over_t: float
    c_over_b: float
    Q: float
    width_parameter: float
    f_w: float
    flags: Tuple[str, ...]


def check_corner_newman_reference(crack: CornerCrack) -> Tuple[str, ...]:
    """REFERENCE ONLY: the published range of the Newman–Raju corner equation."""
    lib = _ffi.library()
    flags = _ffi.i32(0)
    _ffi.check(lib, lib.turbocrack_corner_nr_check(ctypes.byref(crack._native()),
                                                   ctypes.byref(flags)))
    return _flag_names(flags.value)


def corner_newman_reference_factors(crack: CornerCrack, phi: float) -> Dict[str, float]:
    """REFERENCE ONLY: the Newman–Raju corner factors at *phi* in [0, pi/2] (rad; 0 = c-tip,
    pi/2 = a-tip): Q, M1–M3, g1, g2, f_phi, lambda, f_w, F, p, H1, H2, H."""
    lib = _ffi.library()
    out = _ffi.CornerFactors()
    out.struct_size = ctypes.sizeof(out)
    _ffi.check(lib, lib.turbocrack_corner_nr_factors(ctypes.byref(crack._native()), phi,
                                                     ctypes.byref(out)))
    return {name: getattr(out, name) for name, _ in _ffi.CornerFactors._fields_
            if name != "struct_size"}


def corner_newman_reference_front(crack: CornerCrack, phi: Sequence[float], membrane: float = 1.0,
                                  bending: float = 0.0):
    """REFERENCE ONLY: local Newman–Raju K at the angles *phi* (rad, each in [0, pi/2])."""
    lib = _ffi.library()
    ph = _ffi.doubles(phi)
    out = (_ffi.f64 * len(ph))()
    _ffi.check(lib, lib.turbocrack_corner_nr_front(ctypes.byref(crack._native()), membrane, bending,
                                                   ctypes.cast(ph, ctypes.POINTER(_ffi.f64)),
                                                   len(ph),
                                                   ctypes.cast(out, ctypes.POINTER(_ffi.f64))))
    return list(out)


def evaluate_corner_newman_reference(crack: CornerCrack, membrane: float, bending: float = 0.0, *,
                                     phi_points: int = 0) -> CornerNewmanReference:
    """REFERENCE ONLY: the Newman–Raju corner equation at the tips under remote tension S_t
    (*membrane*) and out-of-plane bending S_b (*bending*, the plate-face stress of
    S_b (1 - 2 y/t)); the effective projection uses *phi_points* (0 = 64)."""
    lib = _ffi.library()
    out = _ffi.CornerNrResult()
    out.struct_size = ctypes.sizeof(out)
    _ffi.check(lib, lib.turbocrack_corner_nr_reference(ctypes.byref(crack._native()), membrane,
                                                       bending, phi_points, ctypes.byref(out)))
    tips = [CornerNewmanTip(K_local=out.K_local[i], K_effective=out.K_effective[i], phi=out.phi[i],
                            F=out.F[i], H=out.H[i], flags=_flag_names(out.tip_flags[i]))
            for i in (0, 1)]
    return CornerNewmanReference(a_tip=tips[0], c_tip=tips[1], a_over_c=out.a_over_c,
                                 a_over_t=out.a_over_t, c_over_b=out.c_over_b, Q=out.Q,
                                 width_parameter=getattr(out, "lambda"), f_w=out.f_w,
                                 flags=_flag_names(out.flags))


# ---------------------------------------------------------------- crack growth
@dataclass(frozen=True)
class SurfaceStressField:
    """A through-thickness field sigma(z) of a surface crack, for a mission state.  Build it
    with the class methods; a mission field is given in body coordinates (z/t or z)."""
    kind: int
    coordinate: int = _ffi.DEPTH_Z_OVER_T
    membrane: float = 0.0
    bending: float = 0.0
    depth: Tuple[float, ...] = ()
    value: Tuple[float, ...] = ()

    @classmethod
    def uniform(cls, membrane: float, bending: float = 0.0) -> "SurfaceStressField":
        """S_t + S_b (1 - 2 z/t): solved by Newman–Raju eq. 14."""
        return cls(_ffi.STRESS_UNIFORM, membrane=float(membrane), bending=float(bending))

    @classmethod
    def polynomial(cls, coefficients: Sequence[float], coordinate: str = "z/t") -> "SurfaceStressField":
        """sigma = sum_k coefficients[k] X^k, X = z/t (default) or z: weight functions."""
        return cls(_ffi.STRESS_POLYNOMIAL, _coordinate(coordinate), value=tuple(coefficients))

    @classmethod
    def samples(cls, depth: Sequence[float], stress: Sequence[float],
                coordinate: str = "z") -> "SurfaceStressField":
        """Samples (depth[k], stress[k]) joined by straight lines: weight functions."""
        if len(depth) != len(stress):
            raise ArgumentError(f"{len(depth)} depths but {len(stress)} stresses")
        return cls(_ffi.STRESS_SAMPLED_LINEAR, _coordinate(coordinate), depth=tuple(depth),
                   value=tuple(stress))

    def _native(self):
        return _stress(self.kind, self.coordinate, self.membrane, self.bending,
                       self.depth or None, self.value or None)


def _choice(table: Dict[str, int], value: str, what: str) -> int:
    if value not in table:
        raise ArgumentError(f"{what} must be one of {sorted(table)}, not {value!r}")
    return table[value]


def _crack_type(crack) -> int:
    if isinstance(crack, SurfaceCrack):
        return _ffi.CRACK_SURFACE
    if isinstance(crack, CornerCrack):
        return _ffi.CRACK_CORNER
    raise ArgumentError("crack must be a SurfaceCrack or a CornerCrack")


@dataclass(frozen=True)
class Mission:
    """The time history at the analysed node: states in time order (never reordered) with
    the scalar ``stress`` that is rainflow counted, the ``temperature`` and the crack-face
    field of each state by ``field_mode``: ``"uniform_stress"`` (sigma = stress[i] over the
    face), ``"per_state"`` (``fields[field_index[i]]`` as given) or ``"scaled"``
    (stress[i] x ``fields[field_index[i]]``).  Fields: :class:`SurfaceStressField` for a
    surface crack, :class:`CornerStressField` for a corner crack."""
    time: Tuple[float, ...]
    stress: Tuple[float, ...]
    temperature: Tuple[float, ...]
    field_mode: str = "uniform_stress"
    fields: Tuple[object, ...] = ()
    field_index: Tuple[int, ...] = ()
    name: str = ""

    def _native(self, crack_type: Optional[int] = None):
        """The ABI struct and the arrays it points into; crack_type None: the history only."""
        n = len(self.time)
        if len(self.stress) != n or len(self.temperature) != n:
            raise ArgumentError(f"{n} times, {len(self.stress)} stresses and "
                                f"{len(self.temperature)} temperatures: one per state")
        keep = []
        m = _ffi.Mission()
        m.struct_size = ctypes.sizeof(m)
        m.n = n
        m.field_mode = _choice(_ffi.FIELD_MODES, self.field_mode, "field_mode")
        m.reserved = 0
        for name in ("time", "stress", "temperature"):
            arr = _ffi.doubles(getattr(self, name))
            keep.append(arr)
            setattr(m, name, ctypes.cast(arr, ctypes.POINTER(_ffi.f64)))
        if crack_type is None or self.field_mode == "uniform_stress":
            return m, keep
        if len(self.field_index) != n:
            raise ArgumentError(f"{len(self.field_index)} field indices for {n} states")
        index = (_ffi.i32 * n)(*[int(k) for k in self.field_index])
        keep.append(index)
        m.field_index = ctypes.cast(index, ctypes.POINTER(_ffi.i32))
        m.n_fields = len(self.fields)
        if not self.fields:
            return m, keep
        surface = crack_type == _ffi.CRACK_SURFACE
        kind = SurfaceStressField if surface else CornerStressField
        native = _ffi.Stress1D if surface else _ffi.Stress2D
        arr = (native * len(self.fields))()
        for k, field in enumerate(self.fields):
            if not isinstance(field, kind):
                raise ArgumentError(f"fields[{k}] must be a {kind.__name__} for this crack")
            s, more = field._native()
            arr[k] = s
            keep.extend(more)
        keep.append(arr)
        if surface:
            m.fields_1d = ctypes.cast(arr, ctypes.POINTER(_ffi.Stress1D))
        else:
            m.fields_2d = ctypes.cast(arr, ctypes.POINTER(_ffi.Stress2D))
        return m, keep


@dataclass(frozen=True)
class MissionInfo:
    """The validated mission's counts: ``n_full`` full rainflow cycles grow the crack; the
    ``n_half`` residual half cycles (largest range ``largest_half_range``) do not."""
    n_states: int
    n_turning_points: int
    n_full: int
    n_half: int
    duration: float
    stress_min: float
    stress_max: float
    temperature_min: float
    temperature_max: float
    largest_half_range: float


def mission_check(crack, mission: Mission) -> MissionInfo:
    """Validates *mission* for *crack*'s type (:class:`ArgumentError` with the reason)."""
    lib = _ffi.library()
    m, keep = mission._native(_crack_type(crack))
    out = _ffi.MissionInfo()
    out.struct_size = ctypes.sizeof(out)
    _ffi.check(lib, lib.turbocrack_mission_check(_crack_type(crack), ctypes.byref(m),
                                                 ctypes.byref(out)))
    del keep
    return MissionInfo(*[getattr(out, name) for name, _ in out._fields_[1:]])


@dataclass(frozen=True)
class RainflowCycle:
    """One rainflow range as its stress pair: the low and the high mission state with their
    times, stresses and temperatures, ``stress_range`` = |sigma_high - sigma_low|, the
    ``detection_order`` of the count and the ``propagation_order`` of the growth run,
    ``major`` for the mission's major cycle, ``closing_index`` the state that closed it —
    of the next repetition when ``closed_on_repeat`` (the residue's count)."""
    detection_order: int
    propagation_order: int
    major: int
    low_index: int
    high_index: int
    closing_index: int
    closed_on_repeat: int
    low_time: float
    high_time: float
    sigma_low: float
    sigma_high: float
    stress_range: float
    mean_stress: float
    T_low: float
    T_high: float


def rainflow(mission: Mission) -> Tuple[Tuple[RainflowCycle, ...], Tuple[RainflowCycle, ...]]:
    """Rainflow of the mission's scalar stress as a repeating history: (full cycles — the
    four-point count, then its residue counted with the next repetition — as pairs in the
    growth run's propagation order: the major cycle, then by increasing range; half
    cycles: none remain for a repeating mission).  Every full cycle grows the crack once."""
    lib = _ffi.library()
    m, keep = mission._native()
    n_full, n_half = _ffi.i32(0), _ffi.i32(0)
    null = ctypes.POINTER(_ffi.RainflowCycle)()
    _ffi.check(lib, lib.turbocrack_rainflow(ctypes.byref(m), null, 0, null, 0,
                                            ctypes.byref(n_full), ctypes.byref(n_half)))
    full = (_ffi.RainflowCycle * max(n_full.value, 1))()
    half = (_ffi.RainflowCycle * max(n_half.value, 1))()
    full[0].struct_size = half[0].struct_size = ctypes.sizeof(_ffi.RainflowCycle)
    _ffi.check(lib, lib.turbocrack_rainflow(ctypes.byref(m), full, len(full), half, len(half),
                                            ctypes.byref(n_full), ctypes.byref(n_half)))
    del keep
    names = [name for name, _ in _ffi.RainflowCycle._fields_[1:] if name != "reserved"]
    return (tuple(RainflowCycle(*[getattr(c, k) for k in names]) for c in full[:n_full.value]),
            tuple(RainflowCycle(*[getattr(c, k) for k in names]) for c in half[:n_half.value]))


@dataclass(frozen=True)
class GrowthMaterial:
    """Crack-growth data, one entry per table temperature (strictly increasing), in the
    mission's units (K in stress x length^(1/2), rates in length per cycle).  Columns the
    chosen law and mean-stress model do not use may be None.  ``sigmoidal_Kc`` is the
    Sigmoidal law's asymptote; ``toughness`` is K_mat (K_IC), the fracture check's."""
    temperature: Tuple[float, ...]
    toughness: Tuple[float, ...]
    paris_C: Optional[Tuple[float, ...]] = None
    paris_n: Optional[Tuple[float, ...]] = None
    paris_threshold: Optional[Tuple[float, ...]] = None
    sigmoidal_B: Optional[Tuple[float, ...]] = None
    sigmoidal_P: Optional[Tuple[float, ...]] = None
    sigmoidal_Q: Optional[Tuple[float, ...]] = None
    sigmoidal_D: Optional[Tuple[float, ...]] = None
    sigmoidal_threshold: Optional[Tuple[float, ...]] = None
    sigmoidal_Kc: Optional[Tuple[float, ...]] = None
    walker_m_positive: Optional[Tuple[float, ...]] = None
    walker_m_negative: Optional[Tuple[float, ...]] = None

    def _native(self):
        keep = []
        m = _ffi.GrowthMaterial()
        m.struct_size = ctypes.sizeof(m)
        m.n = len(self.temperature)
        m.reserved = 0
        for name in _ffi.GROWTH_MATERIAL_COLUMNS:
            data = getattr(self, name)
            if data is None:
                continue
            if len(data) != m.n:
                raise ArgumentError(f"{name} has {len(data)} values for {m.n} temperatures")
            arr = _ffi.doubles(data)
            keep.append(arr)
            setattr(m, name, ctypes.cast(arr, ctypes.POINTER(_ffi.f64)))
        return m, keep


def growth_flag_names(bits: int) -> Tuple[str, ...]:
    """The TURBOCRACK_GROWTH_FLAG_* names set in *bits*."""
    return tuple(name for bit, name in _ffi.GROWTH_FLAG_NAMES.items() if bits & bit)


@dataclass(frozen=True)
class GrowthSummary:
    """The run: ``status`` ("COMPLETED", "FRACTURE_TOUGHNESS_96_PERCENT",
    "LAW_DOMAIN_EXCEEDED", "GEOMETRY_OUT_OF_RANGE", "CANCELLED", "CRACK_DEPTH_CUTOFF",
    "CRACK_LENGTH_CUTOFF", "CYCLE_CUTOFF"), the stop's direction
    ("A" / "C"), mission, cycle, state, K, K_mat and K/K_mat, and the crack before and
    after."""
    status: str
    stop_direction: str
    missions_started: int
    cycles_per_mission: int
    half_cycles_per_mission: int
    records_written: int
    records_truncated: bool
    stop_mission: int
    stop_cycle: int
    stop_state: int
    total_cycles: int
    a_initial: float
    c_initial: float
    a_final: float
    c_final: float
    stop_K: float
    stop_Kmat: float
    stop_ratio: float
    stop_time: float
    mission_duration: float
    equivalent_cycles: float
    n_missions: int
    stop_mix_index: int
    message: str


class GrowthRecords:
    """The run's per-cycle records (a view of the native buffer, in run order): each the
    complete chain of one cycle — K at the low and high state, per direction K_min, K_max,
    Delta K, R, K_eff, temperature and rate at stage 1 and at the midpoint, and the update."""
    FIELDS = tuple(name for name, _ in _ffi.GrowthRecord._fields_ if name not in ("struct_size",
                                                                                     "reserved"))

    def __init__(self, array, count: int):
        self._array = array
        self._count = count

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, k: int) -> Dict[str, float]:
        r = self._array[range(self._count)[k]]
        return {name: getattr(r, name) for name in self.FIELDS}

    def column(self, name: str) -> list:
        if name not in self.FIELDS:
            raise ArgumentError(f"no record field {name!r}")
        return [getattr(self._array[k], name) for k in range(self._count)]

    @property
    def buffer(self):
        """The ctypes array (records beyond len() are unused)."""
        return self._array


@dataclass(frozen=True)
class GrowthResult:
    summary: GrowthSummary
    records: GrowthRecords


def growth_capacity(cycles_per_mission: int, mission_repeats: int, record_stride: int = 1) -> int:
    """Records a run keeps at most: every stride-th cycle, the first and the last."""
    stride = max(int(record_stride), 1)
    return (cycles_per_mission * mission_repeats + stride - 1) // stride + 2


def grow(crack, mission, material: GrowthMaterial, *, weights: Optional[Sequence[float]] = None,
         law: str = "paris", mean_stress: str = "none", compression: str = "clip",
         temperature_policy: str = "interpolate", temperature_pairing: str = "at_kmax_state",
         sigmoidal_clamp: bool = False, mission_repeats: int = 1, record_stride: int = 1,
         capacity: Optional[int] = None, progress=None, progress_every: int = 0,
         phi_points: int = 0, depth_points: int = 0, angle_points: int = 0,
         a_cutoff: float = 0.0, c_cutoff: float = 0.0, cycle_cutoff: int = 0) -> GrowthResult:
    """The crack-growth run in turbocrack.dll (docs/CRACK_GROWTH.md): full rainflow cycles,
    K at both states of each cycle by the production solver of *crack*'s type, per-direction
    K_min/K_max, the mean-stress model, the law, the RK2 midpoint update, the 0.96 K_mat
    stop; *a_cutoff*, *c_cutoff* (lengths) and *cycle_cutoff* (cycles) are hard termination
    criteria checked after every applied cycle (0: not used).  *progress(total_cycles,
    mission, a, c)* returning True cancels.

    *mission* is one :class:`Mission`, or a sequence of them with *weights* — a weighted
    mission mix: per block the missions in the given order, each cycle's complete increment
    times its mission's weight (turbocrack_growth_run_mix)."""
    crack_type = _crack_type(crack)
    lib = _ffi.library()
    mix = isinstance(mission, (list, tuple))
    missions = list(mission) if mix else [mission]
    if mix and (weights is None or len(weights) != len(missions) or not missions):
        raise ArgumentError("a mission mix needs one weight per mission")
    if not mix and weights is not None:
        raise ArgumentError("weights belong to a sequence of missions")
    keep = []
    native_missions = (_ffi.Mission * len(missions))()
    for k, item in enumerate(missions):
        if not isinstance(item, Mission):
            raise ArgumentError(f"missions[{k}] must be a Mission")
        m, more = item._native(crack_type)
        native_missions[k] = m
        keep.extend(more)
    mat, keep_mat = material._native()
    g = _ffi.GrowthCrack()
    g.struct_size = ctypes.sizeof(g)
    g.crack_type = crack_type
    if crack_type == _ffi.CRACK_SURFACE:
        g.surface = crack._native()
    else:
        g.corner = crack._native()
    o = _ffi.GrowthOptions()
    o.struct_size = ctypes.sizeof(o)
    o.mean_stress = _choice(_ffi.MEAN_STRESS_MODELS, mean_stress, "mean_stress")
    o.compression = _choice(_ffi.COMPRESSION_POLICIES, compression, "compression")
    o.growth_law = _choice(_ffi.GROWTH_LAWS, law, "law")
    o.temperature_policy = _choice(_ffi.TEMPERATURE_POLICIES, temperature_policy,
                                   "temperature_policy")
    o.temperature_pairing = _choice(_ffi.TEMPERATURE_PAIRINGS, temperature_pairing,
                                    "temperature_pairing")
    o.sigmoidal_clamp = 1 if sigmoidal_clamp else 0
    o.mission_repeats = mission_repeats
    o.record_stride = record_stride
    o.corner = _ffi.CornerOptions(ctypes.sizeof(_ffi.CornerOptions), phi_points, depth_points,
                                  angle_points, 0)
    o.progress_every = progress_every
    o.a_cutoff = a_cutoff
    o.c_cutoff = c_cutoff
    o.cycle_cutoff = int(cycle_cutoff)
    callback = None
    if progress is not None:
        def _relay(_user, total, mission_no, a, c):
            try:
                return 1 if progress(total, mission_no, a, c) else 0
            except Exception:   # an error in the observer stops the run (reported as CANCELLED)
                return 1
        callback = _ffi.GrowthProgress(_relay)
        o.progress = callback
    if capacity is None:
        cycles = sum(len(rainflow(item)[0]) for item in missions) * mission_repeats
        if cycle_cutoff:
            cycles = min(cycles, int(cycle_cutoff))
        capacity = growth_capacity(cycles, 1, record_stride)
    records = (_ffi.GrowthRecord * max(int(capacity), 1))()
    records[0].struct_size = ctypes.sizeof(_ffi.GrowthRecord)
    summary = _ffi.GrowthSummary()
    summary.struct_size = ctypes.sizeof(summary)
    if mix:
        w = _ffi.doubles(weights)
        _ffi.check(lib, lib.turbocrack_growth_run_mix(
            ctypes.byref(g), native_missions, ctypes.cast(w, ctypes.POINTER(_ffi.f64)),
            len(missions), ctypes.byref(mat), ctypes.byref(o), ctypes.byref(summary), records,
            int(capacity)))
    else:
        _ffi.check(lib, lib.turbocrack_growth_run(ctypes.byref(g), native_missions,
                                                  ctypes.byref(mat), ctypes.byref(o),
                                                  ctypes.byref(summary), records, int(capacity)))
    del keep, keep_mat, callback
    s = summary
    return GrowthResult(
        summary=GrowthSummary(
            status=_ffi.GROWTH_STATUS_NAMES[s.status],
            stop_direction=_ffi.DIRECTION_NAMES[s.stop_direction],
            missions_started=s.missions_started, cycles_per_mission=s.cycles_per_mission,
            half_cycles_per_mission=s.half_cycles_per_mission,
            records_written=s.records_written, records_truncated=bool(s.records_truncated),
            stop_mission=s.stop_mission, stop_cycle=s.stop_cycle, stop_state=s.stop_state,
            total_cycles=s.total_cycles, a_initial=s.a_initial, c_initial=s.c_initial,
            a_final=s.a_final, c_final=s.c_final, stop_K=s.stop_K, stop_Kmat=s.stop_Kmat,
            stop_ratio=s.stop_ratio, stop_time=s.stop_time, mission_duration=s.mission_duration,
            equivalent_cycles=s.equivalent_cycles, n_missions=s.n_missions,
            stop_mix_index=s.stop_mix_index, message=s.message.decode("utf-8", "replace")),
        records=GrowthRecords(records, s.records_written))

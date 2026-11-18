"""ctypes binding of turbocrack.dll (native/include/turbocrack/turbocrack.h):
structures, constants, signatures, loading and errors — no fracture mechanics.

The DLL is found at ``TURBOCRACK_NATIVE_DLL``, in ``native/build/<config>/``
(development) or in ``runtime/bin/`` (distribution), and loaded by
core/Reader/common/native_loader.py (its own folder and System32 — never PATH).
On load the binding checks the ABI version and compares every ctypes structure
below with the layout the DLL reports (turbocrack_layout).
"""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

ABI_VERSION = 6
MAX_POLY_TERMS = 16
CRACK_ROOT = Path(__file__).resolve().parents[2]       # core/ExternalCalculations/CRACK_PROP
CORE_ROOT = CRACK_ROOT.parents[1]                       # core
BUILD_CONFIGS = ("Release", "RelWithDebInfo", "MinSizeRel", "Debug")


def _common_loader():
    """core/Reader/common/native_loader.py (loaded by path, once per process)."""
    name = "turbosection_reader_native_loader"
    module = sys.modules.get(name)
    if module is None:
        path = CORE_ROOT / "Reader" / "common" / "native_loader.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return module


_L = _common_loader()

# ------------------------------------------------------------------ statuses
OK, E_ARGUMENT, E_VALIDITY, E_UNSUPPORTED, E_VERSION, E_BUFFER, E_MEMORY, E_INTERNAL = \
    0, -1, -2, -3, -4, -5, -6, -7
STATUS_NAMES = {OK: "OK", E_ARGUMENT: "ARGUMENT", E_VALIDITY: "VALIDITY",
                E_UNSUPPORTED: "UNSUPPORTED", E_VERSION: "VERSION", E_BUFFER: "BUFFER",
                E_MEMORY: "MEMORY", E_INTERNAL: "INTERNAL"}

# ------------------------------------------------------------------- choices
STRESS_UNIFORM, STRESS_POLYNOMIAL, STRESS_SAMPLED_LINEAR, STRESS_SAMPLED_BILINEAR = 1, 2, 3, 4
DEPTH_Z_OVER_T, DEPTH_Z_OVER_A, DEPTH_Z = 1, 2, 3
METHOD_NEWMAN_RAJU, METHOD_WEIGHT_FUNCTION, METHOD_MALIK = 1, 2, 3
ANALYSIS_UNIFORM, ANALYSIS_GRADIENT = 1, 2
METHOD_NAMES = {METHOD_NEWMAN_RAJU: "NEWMAN_RAJU", METHOD_WEIGHT_FUNCTION: "WEIGHT_FUNCTION",
                METHOD_MALIK: "MALIK"}
COORDINATES = {"z/t": DEPTH_Z_OVER_T, "z/a": DEPTH_Z_OVER_A, "z": DEPTH_Z}

# corner crack
WIDTH_MALIK, WIDTH_NEWMAN_RAJU, WIDTH_NONE = 1, 2, 3
WIDTH_CORRECTIONS = {"malik": WIDTH_MALIK, "newman_raju": WIDTH_NEWMAN_RAJU, "none": WIDTH_NONE}
PLANE_SECTION, PLANE_CRACK, PLANE_LENGTH = 1, 2, 3
PLANE_COORDINATES = {"section": PLANE_SECTION, "crack": PLANE_CRACK, "length": PLANE_LENGTH}
K_LOCAL, K_EFFECTIVE = 1, 2
REPRESENTATION_NAMES = {K_LOCAL: "LOCAL", K_EFFECTIVE: "EFFECTIVE"}
EVIDENCE_VALIDATED, EVIDENCE_BENCHMARKED, EVIDENCE_EXPERIMENTAL = 1, 2, 3
EVIDENCE_NAMES = {EVIDENCE_VALIDATED: "VALIDATED", EVIDENCE_BENCHMARKED: "BENCHMARKED",
                  EVIDENCE_EXPERIMENTAL: "EXPERIMENTAL"}
FIELD_CONSTANT, FIELD_DEPTH, FIELD_WIDTH, FIELD_2D = 1, 2, 3, 4
FIELD_NAMES = {FIELD_CONSTANT: "CONSTANT", FIELD_DEPTH: "DEPTH", FIELD_WIDTH: "WIDTH", FIELD_2D: "2D"}
INTEGRATION_CLOSED_FORM, INTEGRATION_PANELS = 1, 2
INTEGRATION_NAMES = {INTEGRATION_CLOSED_FORM: "CLOSED_FORM", INTEGRATION_PANELS: "PANELS"}

FLAG_DEEP_CRACK = 0x1
FLAG_LOW_ASPECT = 0x2
FLAG_INFINITE_WIDTH = 0x4
FLAG_SURFACE_POINT = 0x8
FLAG_WIDTH_ESTIMATED = 0x10
FLAG_REFERENCE_EXTENDED = 0x20
FLAG_NAMES = {FLAG_DEEP_CRACK: "DEEP_CRACK", FLAG_LOW_ASPECT: "LOW_ASPECT",
              FLAG_INFINITE_WIDTH: "INFINITE_WIDTH", FLAG_SURFACE_POINT: "SURFACE_POINT",
              FLAG_WIDTH_ESTIMATED: "WIDTH_ESTIMATED",
              FLAG_REFERENCE_EXTENDED: "REFERENCE_EXTENDED"}

# crack growth
CRACK_SURFACE, CRACK_CORNER = 1, 2
FIELD_MODES = {"uniform_stress": 1, "per_state": 2, "scaled": 3}
MEAN_STRESS_MODELS = {"none": 1, "walker": 2}
COMPRESSION_POLICIES = {"clip": 1, "signed": 2}
GROWTH_LAWS = {"paris": 1, "sigmoidal": 2}
TEMPERATURE_POLICIES = {"interpolate": 1, "closest": 2, "next_highest": 3}
TEMPERATURE_PAIRINGS = {"at_kmax_state": 1, "max_of_pair": 2}
GROWTH_STATUS_NAMES = {1: "APPLIED", 2: "COMPLETED", 3: "FRACTURE_TOUGHNESS_96_PERCENT",
                       4: "LAW_DOMAIN_EXCEEDED", 5: "GEOMETRY_OUT_OF_RANGE", 6: "CANCELLED",
                       7: "CRACK_DEPTH_CUTOFF", 8: "CRACK_LENGTH_CUTOFF", 9: "CYCLE_CUTOFF"}
GROWTH_FLAG_NAMES = {0x1: "BELOW_THRESHOLD", 0x2: "NO_DRIVING_FORCE", 0x4: "KMIN_CLIPPED",
                     0x8: "K_ORDER_SWAPPED", 0x10: "SIGMOIDAL_CLAMPED", 0x20: "TEMPERATURE_HELD"}
DIRECTION_NAMES = {0: "", 1: "A", 2: "C"}

i32, i64, f64 = ctypes.c_int32, ctypes.c_int64, ctypes.c_double
vp = ctypes.c_void_p


class TurboCrackError(RuntimeError):
    """A failing native status (the message is the library's)."""


class UnavailableError(TurboCrackError):
    """turbocrack.dll missing, not loadable, or not matching this binding."""


class ArgumentError(TurboCrackError, ValueError):
    """An invalid input (non-finite, non-positive, malformed samples …)."""


class UnsupportedError(TurboCrackError):
    """A request outside what the formulation covers (TURBOCRACK_E_UNSUPPORTED)."""


class ValidityError(TurboCrackError, ValueError):
    """The geometry lies outside the published range of the formulation."""


_ERRORS = {E_ARGUMENT: ArgumentError, E_VALIDITY: ValidityError, E_UNSUPPORTED: UnsupportedError}


# ---------------------------------------------------------------- structures
class SurfaceGeometry(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("a", f64), ("c", f64), ("t", f64), ("width", f64)]


class NrFactors(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("phi", f64), ("a_over_c", f64), ("a_over_t", f64),
                ("c_over_b", f64), ("Q", f64), ("M1", f64), ("M2", f64), ("M3", f64),
                ("g", f64), ("f_phi", f64), ("f_w", f64), ("F", f64), ("p", f64), ("H1", f64),
                ("H2", f64), ("H", f64), ("H_slope", f64)]


class Stress1D(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("kind", i32), ("coordinate", i32), ("n", i32),
                ("reserved", i32), ("membrane", f64), ("bending", f64),
                ("depth", ctypes.POINTER(f64)), ("value", ctypes.POINTER(f64))]


class SurfaceResult(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("method", i32), ("flags", i32), ("tip_flags", i32 * 2),
                ("quadrature_points", i32), ("reserved", i32), ("a_over_c", f64),
                ("a_over_t", f64), ("c_over_b", f64), ("Q", f64), ("K", f64 * 2),
                ("phi", f64 * 2), ("F", f64 * 2), ("H", f64 * 2), ("Y0", f64 * 2),
                ("Y1", f64 * 2), ("M1", f64 * 2), ("M2", f64 * 2), ("M3", f64 * 2)]


class CornerGeometry(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("width_correction", i32), ("reserved", i32), ("a", f64),
                ("c", f64), ("t", f64), ("width", f64)]


class CornerFactors(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("phi", f64), ("a_over_c", f64), ("a_over_t", f64),
                ("c_over_b", f64), ("Q", f64), ("M1", f64), ("M2", f64), ("M3", f64),
                ("g1", f64), ("g2", f64), ("f_phi", f64), ("lambda", f64), ("f_w", f64),
                ("F", f64), ("p", f64), ("H1", f64), ("H2", f64), ("H", f64)]


class Stress2D(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("kind", i32), ("coordinate", i32), ("nx", i32),
                ("ny", i32), ("membrane", f64), ("bending", f64), ("x", ctypes.POINTER(f64)),
                ("y", ctypes.POINTER(f64)), ("value", ctypes.POINTER(f64))]


class CornerOptions(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("phi_points", i32), ("depth_points", i32),
                ("angle_points", i32), ("reserved", i32)]


class CornerResult(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("method", i32), ("representation", i32),
                ("field_class", i32), ("evidence", i32), ("flags", i32), ("tip_flags", i32 * 2),
                ("integration", i32), ("quadrature_points", i32), ("phi_points", i32),
                ("depth_points", i32), ("angle_points", i32), ("a_over_c", f64),
                ("a_over_t", f64), ("c_over_b", f64), ("lambda", f64), ("f_w", f64),
                ("K", f64 * 2), ("phi", f64 * 2), ("K_reference", f64 * 2),
                ("self_consistency", f64 * 2), ("U0", f64), ("dU0_da", f64), ("dU0_dc", f64)]


class CornerNrResult(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("flags", i32), ("tip_flags", i32 * 2), ("reserved", i32),
                ("a_over_c", f64), ("a_over_t", f64), ("c_over_b", f64), ("Q", f64),
                ("lambda", f64), ("f_w", f64), ("K_local", f64 * 2), ("phi", f64 * 2),
                ("K_effective", f64 * 2), ("F", f64 * 2), ("H", f64 * 2)]


# crack growth
class Mission(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n", i32), ("field_mode", i32), ("n_fields", i32),
                ("reserved", i32), ("time", ctypes.POINTER(f64)), ("stress", ctypes.POINTER(f64)),
                ("temperature", ctypes.POINTER(f64)), ("field_index", ctypes.POINTER(i32)),
                ("fields_1d", ctypes.POINTER(Stress1D)), ("fields_2d", ctypes.POINTER(Stress2D))]


GROWTH_MATERIAL_COLUMNS = ("temperature", "paris_C", "paris_n", "paris_threshold", "sigmoidal_B",
                           "sigmoidal_P", "sigmoidal_Q", "sigmoidal_D", "sigmoidal_threshold",
                           "sigmoidal_Kc", "walker_m_positive", "walker_m_negative", "toughness")


class GrowthMaterial(ctypes.Structure):
    _fields_ = ([("struct_size", i64), ("n", i32), ("reserved", i32)]
                + [(name, ctypes.POINTER(f64)) for name in GROWTH_MATERIAL_COLUMNS])


class GrowthCrack(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("crack_type", i32), ("reserved", i32),
                ("surface", SurfaceGeometry), ("corner", CornerGeometry)]


GrowthProgress = ctypes.CFUNCTYPE(i32, vp, i64, i32, f64, f64)


class GrowthOptions(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("mean_stress", i32), ("compression", i32),
                ("growth_law", i32), ("temperature_policy", i32), ("temperature_pairing", i32),
                ("sigmoidal_clamp", i32), ("mission_repeats", i32), ("record_stride", i32),
                ("corner", CornerOptions), ("progress_every", i64), ("progress", GrowthProgress),
                ("progress_user", vp), ("a_cutoff", f64), ("c_cutoff", f64),
                ("cycle_cutoff", i64)]


RAINFLOW_CYCLE_DOUBLES = ("low_time", "high_time", "sigma_low", "sigma_high", "stress_range",
                          "mean_stress", "T_low", "T_high")


class RainflowCycle(ctypes.Structure):
    _fields_ = ([("struct_size", i64), ("detection_order", i32), ("propagation_order", i32),
                 ("major", i32), ("low_index", i32), ("high_index", i32), ("closing_index", i32),
                 ("closed_on_repeat", i32), ("reserved", i32)]
                + [(name, f64) for name in RAINFLOW_CYCLE_DOUBLES])


class MissionInfo(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_states", i32), ("n_turning_points", i32),
                ("n_full", i32), ("n_half", i32), ("duration", f64), ("stress_min", f64),
                ("stress_max", f64), ("temperature_min", f64), ("temperature_max", f64),
                ("largest_half_range", f64)]


GROWTH_RECORD_INTS = ("mission", "cycle", "total_cycles", "low_index", "high_index",
                      "kmax_state_a", "kmax_state_c", "flags_a", "flags_c", "status", "major",
                      "detection_order", "mix_index")
GROWTH_RECORD_DOUBLES = (
    "time_low", "time_high", "T_low", "T_high", "a", "c",
    "Ka_low", "Ka_high", "Kc_low", "Kc_high",
    "Kmin_a", "Kmax_a", "dK_a", "R_a", "Keff_a", "T_eval_a", "rate_a",
    "Kmin_c", "Kmax_c", "dK_c", "R_c", "Keff_c", "T_eval_c", "rate_c",
    "Kmat_a", "Kmat_c", "a_mid", "c_mid",
    "Ka_low_mid", "Ka_high_mid", "Kc_low_mid", "Kc_high_mid",
    "Kmin_a_mid", "Kmax_a_mid", "Keff_a_mid", "T_eval_a_mid", "rate_a_mid",
    "Kmin_c_mid", "Kmax_c_mid", "Keff_c_mid", "T_eval_c_mid", "rate_c_mid",
    "a_new", "c_new", "da", "dc", "weight")


class GrowthRecord(ctypes.Structure):
    _fields_ = ([("struct_size", i64), ("mission", i32), ("cycle", i32), ("total_cycles", i64),
                 ("low_index", i32), ("high_index", i32), ("kmax_state_a", i32),
                 ("kmax_state_c", i32), ("flags_a", i32), ("flags_c", i32), ("status", i32),
                 ("major", i32), ("detection_order", i32), ("mix_index", i32)]
                + [(name, f64) for name in GROWTH_RECORD_DOUBLES])


class GrowthSummary(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("status", i32), ("stop_direction", i32),
                ("missions_started", i32), ("cycles_per_mission", i32),
                ("half_cycles_per_mission", i32), ("records_written", i32),
                ("records_truncated", i32), ("stop_mission", i32), ("stop_cycle", i32),
                ("stop_state", i32), ("n_missions", i32), ("stop_mix_index", i32),
                ("total_cycles", i64), ("a_initial", f64),
                ("c_initial", f64), ("a_final", f64), ("c_final", f64), ("stop_K", f64),
                ("stop_Kmat", f64), ("stop_ratio", f64), ("stop_time", f64),
                ("mission_duration", f64), ("equivalent_cycles", f64),
                ("message", ctypes.c_char * 256)]


STRUCTS = {"turbocrack_surface_geometry": SurfaceGeometry, "turbocrack_nr_factors": NrFactors,
           "turbocrack_stress_1d": Stress1D, "turbocrack_surface_result": SurfaceResult,
           "turbocrack_corner_geometry": CornerGeometry, "turbocrack_corner_factors": CornerFactors,
           "turbocrack_stress_2d": Stress2D, "turbocrack_corner_options": CornerOptions,
           "turbocrack_corner_result": CornerResult, "turbocrack_corner_nr_result": CornerNrResult,
           "turbocrack_mission": Mission, "turbocrack_growth_material": GrowthMaterial,
           "turbocrack_growth_crack": GrowthCrack, "turbocrack_growth_options": GrowthOptions,
           "turbocrack_rainflow_cycle": RainflowCycle, "turbocrack_mission_info": MissionInfo,
           "turbocrack_growth_record": GrowthRecord, "turbocrack_growth_summary": GrowthSummary}

_SIGNATURES = (
    ("turbocrack_abi_version", i32, []),
    ("turbocrack_version", i32, [ctypes.c_char_p, i32]),
    ("turbocrack_last_error", i32, [ctypes.c_char_p, i32]),
    ("turbocrack_layout", i32, [ctypes.c_char_p, i32]),
    ("turbocrack_surface_check", i32, [ctypes.POINTER(SurfaceGeometry), i32,
                                       ctypes.POINTER(i32)]),
    ("turbocrack_surface_nr_factors", i32, [ctypes.POINTER(SurfaceGeometry), f64,
                                            ctypes.POINTER(NrFactors)]),
    ("turbocrack_surface_sif", i32, [ctypes.POINTER(SurfaceGeometry), ctypes.POINTER(Stress1D),
                                     ctypes.POINTER(SurfaceResult)]),
    ("turbocrack_stress_evaluate", i32, [ctypes.POINTER(SurfaceGeometry),
                                         ctypes.POINTER(Stress1D), ctypes.POINTER(f64), i32,
                                         ctypes.POINTER(f64)]),
    # corner crack, production (Malik)
    ("turbocrack_corner_check", i32, [ctypes.POINTER(CornerGeometry), ctypes.POINTER(i32)]),
    ("turbocrack_corner_solve", i32, [ctypes.POINTER(CornerGeometry), ctypes.POINTER(Stress2D),
                                      ctypes.POINTER(CornerOptions), ctypes.POINTER(CornerResult)]),
    ("turbocrack_corner_stress_evaluate", i32, [ctypes.POINTER(CornerGeometry),
                                                ctypes.POINTER(Stress2D), ctypes.POINTER(f64),
                                                ctypes.POINTER(f64), i32, ctypes.POINTER(f64)]),
    # corner crack, Newman–Raju reference only
    ("turbocrack_corner_nr_check", i32, [ctypes.POINTER(CornerGeometry), ctypes.POINTER(i32)]),
    ("turbocrack_corner_nr_factors", i32, [ctypes.POINTER(CornerGeometry), f64,
                                           ctypes.POINTER(CornerFactors)]),
    ("turbocrack_corner_nr_front", i32, [ctypes.POINTER(CornerGeometry), f64, f64,
                                         ctypes.POINTER(f64), i32, ctypes.POINTER(f64)]),
    ("turbocrack_corner_nr_reference", i32, [ctypes.POINTER(CornerGeometry), f64, f64, i32,
                                             ctypes.POINTER(CornerNrResult)]),
    # crack growth (propagation)
    ("turbocrack_mission_check", i32, [i32, ctypes.POINTER(Mission), ctypes.POINTER(MissionInfo)]),
    ("turbocrack_rainflow", i32, [ctypes.POINTER(Mission), ctypes.POINTER(RainflowCycle), i32,
                                  ctypes.POINTER(RainflowCycle), i32, ctypes.POINTER(i32),
                                  ctypes.POINTER(i32)]),
    ("turbocrack_growth_run", i32, [ctypes.POINTER(GrowthCrack), ctypes.POINTER(Mission),
                                    ctypes.POINTER(GrowthMaterial), ctypes.POINTER(GrowthOptions),
                                    ctypes.POINTER(GrowthSummary), ctypes.POINTER(GrowthRecord),
                                    i32]),
    ("turbocrack_growth_run_mix", i32, [ctypes.POINTER(GrowthCrack), ctypes.POINTER(Mission),
                                        ctypes.POINTER(f64), i32, ctypes.POINTER(GrowthMaterial),
                                        ctypes.POINTER(GrowthOptions),
                                        ctypes.POINTER(GrowthSummary),
                                        ctypes.POINTER(GrowthRecord), i32]),
)

_lock = threading.Lock()
_library: Optional[ctypes.CDLL] = None
_load_error: Optional[str] = None
_loaded_dll: Optional[Path] = None


def dll_candidates() -> List[Path]:
    override = os.environ.get("TURBOCRACK_NATIVE_DLL")
    if override:
        return [Path(override)]
    built = [CRACK_ROOT / "native" / "build" / c / "turbocrack.dll" for c in BUILD_CONFIGS]
    return built + [CRACK_ROOT / "runtime" / "bin" / "turbocrack.dll"]


def loaded_dll() -> Optional[Path]:
    """The turbocrack.dll this process loaded (None: not loaded)."""
    return _loaded_dll


def _text(fn) -> str:
    n = fn(None, 0)
    buf = ctypes.create_string_buffer(n + 1)
    fn(buf, n + 1)
    return buf.value.decode("utf-8", "replace")


def native_layout(lib: ctypes.CDLL) -> Dict[str, dict]:
    """{struct name: {"size": n, "fields": {name: offset}}} as the DLL reports it."""
    return json.loads(_text(lib.turbocrack_layout))["structs"]


def layout_mismatches(lib: ctypes.CDLL) -> List[str]:
    """Every difference between the ctypes mirrors and the DLL's structs."""
    native = native_layout(lib)
    problems = [f"{name}: no ctypes mirror" for name in native if name not in STRUCTS]
    for name, cls in STRUCTS.items():
        spec = native.get(name)
        if spec is None:
            problems.append(f"{name}: not in the DLL")
            continue
        if spec["size"] != ctypes.sizeof(cls):
            problems.append(f"{name}: size {ctypes.sizeof(cls)} here, {spec['size']} in the DLL")
        mine = {f[0]: getattr(cls, f[0]).offset for f in cls._fields_}
        if mine != spec["fields"]:
            problems.append(f"{name}: fields {mine} here, {spec['fields']} in the DLL")
    return problems


def library() -> ctypes.CDLL:
    """The loaded turbocrack.dll (once per process); UnavailableError with every
    searched location, or the mismatch, when it cannot be used."""
    global _library, _load_error, _loaded_dll
    with _lock:
        if _library is not None:
            return _library
        if _load_error is not None:
            raise UnavailableError(_load_error)
        dll = next((p for p in dll_candidates() if p.is_file()), None)
        if dll is None:
            _load_error = ("the crack engine is not built: no turbocrack.dll at "
                           + ", ".join(str(p) for p in dll_candidates())
                           + " (cmake -S native -B native/build -A x64; "
                             "cmake --build native/build --config Release)")
            raise UnavailableError(_load_error)
        try:
            lib = _L.load_dll(dll, [])
        except OSError as exc:
            _load_error = f"cannot load {dll}: {exc}"
            raise UnavailableError(_load_error) from None
        for name, restype, argtypes in _SIGNATURES:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        version = lib.turbocrack_abi_version()
        if version != ABI_VERSION:
            _load_error = f"{dll} has ABI version {version}, this binding expects {ABI_VERSION}"
            raise UnavailableError(_load_error)
        problems = layout_mismatches(lib)
        if problems:
            _load_error = f"{dll} does not match this binding's structures: " + "; ".join(problems)
            raise UnavailableError(_load_error)
        _library, _loaded_dll = lib, dll
        return lib


def version_info(lib: ctypes.CDLL) -> dict:
    return json.loads(_text(lib.turbocrack_version))


def last_error(lib: ctypes.CDLL) -> str:
    """The calling thread's last native message."""
    return _text(lib.turbocrack_last_error)


def check(lib: ctypes.CDLL, status: int) -> None:
    """Raise the exception of a failing status (message: the native one)."""
    if status == OK:
        return
    message = last_error(lib) or f"native status {STATUS_NAMES.get(status, status)}"
    raise _ERRORS.get(status, TurboCrackError)(f"[{STATUS_NAMES.get(status, status)}] {message}")


def doubles(values) -> ctypes.Array:
    """A C double array holding *values* (kept alive by the caller)."""
    seq = [float(v) for v in values]
    return (f64 * len(seq))(*seq)

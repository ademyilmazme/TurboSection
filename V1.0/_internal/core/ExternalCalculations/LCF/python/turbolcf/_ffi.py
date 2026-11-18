"""ctypes binding of turbolcf.dll (native/include/turbolcf/turbolcf.h):
structures, constants, signatures, loading and errors — no fatigue logic.

The DLL is found at ``TURBOLCF_NATIVE_DLL``, in ``native/build/<config>/``
(development) or in ``runtime/bin/`` (distribution), and loaded by
core/Reader/common/native_loader.py (its own folder and System32 — never
PATH).  It is built with the static C++ runtime: no runtime bundle.  On load
the binding checks the ABI version and compares every ctypes structure below
with the layout the DLL reports (turbolcf_layout).
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

ABI_VERSION = 2
TEXT_SIZE = 512
LCF_ROOT = Path(__file__).resolve().parents[2]          # core/ExternalCalculations/LCF
CORE_ROOT = LCF_ROOT.parents[1]                          # core
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
OK, E_ARGUMENT, E_HANDLE, E_MATERIAL, E_OPTIONS, E_RANGE, E_BUFFER, E_UNSUPPORTED, E_STATE, \
    E_CANCELLED, E_MEMORY, E_INTERNAL, E_VERSION = 0, -1, -2, -3, -4, -5, -6, -7, -8, -9, \
    -10, -11, -12
STATUS_NAMES = {OK: "OK", E_ARGUMENT: "ARGUMENT", E_HANDLE: "HANDLE", E_MATERIAL: "MATERIAL",
                E_OPTIONS: "OPTIONS", E_RANGE: "RANGE", E_BUFFER: "BUFFER",
                E_UNSUPPORTED: "UNSUPPORTED", E_STATE: "STATE", E_CANCELLED: "CANCELLED",
                E_MEMORY: "MEMORY", E_INTERNAL: "INTERNAL", E_VERSION: "VERSION"}

# ------------------------------------------------------------ option choices
REDUCTION_MANSON_MCKNIGHT = 1
MEAN_NONE, MEAN_NASALIFE_WALKER = 1, 2
COUNTER_DAMAGE_RAINFLOW, COUNTER_EXTREME_PAIR = 1, 2
TEMPERATURE_MOST_DAMAGING_ENDPOINT, TEMPERATURE_MAXIMUM = 1, 2
CREEP_NONE, CREEP_LARSON_MILLER, CREEP_RUPTURE_TABLE = 1, 2, 3
ENVELOPE_TOKEN_LIFE, ENVELOPE_FAIL_LOCATION = 1, 2
ZERO_STRESS_HOLD_LAST_FINITE, ZERO_STRESS_TERMINAL = 1, 2
ABOVE_RANGE_FAIL, ABOVE_RANGE_CLAMP = 1, 2
CLOSURE_CYCLIC, CLOSURE_REFERENCE = 1, 2
ZERO_AMPLITUDE_LAST_RESORT, ZERO_AMPLITUDE_RANKED = 1, 2
LOW_STRESS_HOLD, LOW_STRESS_EXTRAPOLATE = 1, 2

# ---------------------------------------------------------- location results
LOC_OK, LOC_TOKEN_LIFE, LOC_NOT_EVALUATED, LOC_DATA_RANGE, LOC_MATERIAL, \
    LOC_NO_EVALUABLE_PAIR, LOC_NONFINITE_INPUT = 0, 1, -1, -2, -3, -4, -5
CYCLE_MAJOR, CYCLE_MINOR = 1, 2

FLAG_LCF_TEMPERATURE_ABOVE = 1 << 0
FLAG_LCF_TEMPERATURE_BELOW = 1 << 1
FLAG_LCF_STRESS_ABOVE = 1 << 2
FLAG_LCF_STRESS_BELOW = 1 << 3
FLAG_R_RESET = 1 << 4
FLAG_DEFAULT_WALKER_EXPONENT = 1 << 5
FLAG_YIELD_AMPLITUDE = 1 << 6
FLAG_YIELD_WALKER = 1 << 7
FLAG_PROPERTY_TEMPERATURE = 1 << 8
FLAG_RUPTURE_TEMPERATURE_ABOVE = 1 << 9
FLAG_RUPTURE_STRESS_ABOVE = 1 << 10
FLAG_RUPTURE_DEVIATION_DATA = 1 << 11
FLAG_RUPTURE_RAISED_TO_MINIMUM = 1 << 12
FLAG_TOKEN_LIFE = 1 << 13
FLAG_MEAN_SIGN_UNSTABLE = 1 << 14
FLAG_NON_PROPORTIONAL = 1 << 15
FLAG_CANDIDATES_SKIPPED = 1 << 16
FLAG_STEP6_LIFE_CHANGED = 1 << 17
FLAG_SMALL_CYCLES_REJECTED = 1 << 18
FLAG_DIAGNOSTIC_COUNTER = 1 << 19
FLAG_OPEN_MISSION_RECLOSED = 1 << 20
FLAG_ZERO_AMPLITUDE_SKIPPED = 1 << 21


# ---------------------------------------------------------------- Strain-Life
SL_UNIAXIAL, SL_CRITICAL_PLANE, SL_DANG_VAN = 1, 2, 3
SL_INPUT_DIRECT, SL_INPUT_ELASTIC, SL_INPUT_HOOKE, SL_INPUT_ELASTIC_PLANE = 1, 2, 3, 4
SL_NEUBER, SL_GLINKA = 1, 2
SL_MEAN_NONE, SL_MEAN_MORROW, SL_MEAN_SWT, SL_MEAN_WALKER, SL_MEAN_USER_TABLE = 1, 2, 3, 4, 5
SL_WALKER_LIFE, SL_WALKER_STRAIN = 1, 2
SL_MEAN_LIMIT_UNDEFINED, SL_MEAN_LIMIT_FAILURE = 1, 2
SL_ENDURANCE_NONE, SL_ENDURANCE_KNEE, SL_ENDURANCE_REDUCED = 1, 2, 3
SL_NORMAL_STRAIN, SL_MAX_SHEAR, SL_BROWN_MILLER, SL_WANG_BROWN, SL_FATEMI_SOCIE = 1, 2, 3, 4, 5
SL_WB_REVERSALS, SL_WB_PLANE, SL_WB_DAMAGE_PLANE = 1, 2, 3
SL_FS_PROGRAM, SL_FS_TORSIONAL, SL_FS_UNIAXIAL = 1, 2, 3
SL_SWT_CYCLE_PEAK, SL_SWT_TIPS = 1, 2
SL_PLANES_SPHERE, SL_PLANES_FESAFE = 1, 2
SL_EVALUATED, SL_NOT_EVALUATED, SL_MORROW_UNDEFINED, SL_NO_CONVERGENCE = 0, -1, -2, -3
SL_FLAG_SWT_COMPRESSIVE = 1 << 0
SL_FLAG_INFINITE = 1 << 1
SL_FLAG_BELOW_ONE = 1 << 2
SL_FLAG_CONSTANT = 1 << 3
SL_FLAG_NORMAL_COMPRESSIVE = 1 << 4
SL_FLAG_NOTCH_OPEN = 1 << 5
SL_FLAG_SIGN_AMBIGUOUS = 1 << 6
SL_FLAG_SHEAR_FREE = 1 << 7
SL_FLAG_WALKER_COMPRESSIVE = 1 << 8
SL_FLAG_MEAN_FAILURE = 1 << 9
SL_FLAG_BELOW_ENDURANCE = 1 << 10
SL_FLAG_TEMPERATURE_CLAMPED = 1 << 11
SL_FLAG_TABLE_CLAMPED = 1 << 12
SL_FLAG_FAILED = 1 << 13
SL_FLAG_BEYOND_CURVE = 1 << 14
SL_FLAG_NON_PROPORTIONAL = 1 << 15


# ---------------------------------------------------------------- Stress-Life
SS_UNIAXIAL, SS_CRITICAL_PLANE, SS_FATIGUE_LIMIT = 1, 2, 3
SS_CRITERION_FINDLEY, SS_CRITERION_MATAKE, SS_CRITERION_MCDIARMID, SS_CRITERION_CROSSLAND, \
    SS_CRITERION_SINES = 1, 2, 3, 4, 5
SS_REFINE_GRID, SS_REFINE_PATTERN = 1, 2
SS_CRACK_CASE_A, SS_CRACK_CASE_B = 1, 2
SS_PARAM_PRINCIPAL_DIRECTION, SS_PARAM_SIGNED_VON_MISES, SS_PARAM_ABS_MAX_PRINCIPAL, \
    SS_PARAM_MAX_PRINCIPAL, SS_PARAM_COMPONENT = 1, 2, 3, 4, 5
SS_VM_SIGN_HYDROSTATIC, SS_VM_SIGN_ABS_MAX_PRINCIPAL = 1, 2
SS_XX, SS_YY, SS_ZZ, SS_XY, SS_YZ, SS_XZ = 1, 2, 3, 4, 5, 6
SS_MEAN_NONE, SS_MEAN_GOODMAN, SS_MEAN_GERBER, SS_MEAN_SODERBERG = 1, 2, 3, 4
SS_MEAN_WALKER, SS_MEAN_SWT, SS_MEAN_MORROW, SS_MEAN_MORROW_FRACTURE, SS_MEAN_USER_TABLE = \
    5, 6, 7, 8, 9
SS_WALKER_EQUATION, SS_WALKER_TWO_EXPONENT = 1, 2
SS_COMPRESSIVE_MEAN_NO_EFFECT, SS_COMPRESSIVE_MEAN_AS_WRITTEN, SS_COMPRESSIVE_MEAN_FESAFE = 1, 2, 3
SS_COMPRESSIVE_CYCLES_COUNTED, SS_COMPRESSIVE_CYCLES_IGNORED = 1, 2
SS_CURVE_PROGRAM, SS_CURVE_TABLE, SS_CURVE_BASQUIN = 1, 2, 3
SS_EXTRAPOLATE_END_SLOPES, SS_EXTRAPOLATE_HOLD_LOW, SS_EXTRAPOLATE_REFUSE = 1, 2, 3
SS_TEMPERATURE_STRESS_LINEAR = 1
SS_TEMPERATURE_CYCLE_MAXIMUM, SS_TEMPERATURE_HISTORY_MAXIMUM, SS_TEMPERATURE_ENDPOINTS = 1, 2, 3
SS_ENDURANCE_KNEE, SS_ENDURANCE_IGNORED, SS_ENDURANCE_HAIBACH, SS_ENDURANCE_REDUCED = 1, 2, 3, 4
SS_CLOSURE_LARGEST_POINT = 1
SS_INTERP_LOG_LOG, SS_INTERP_SEMI_LOG, SS_INTERP_LINEAR = 1, 2, 3
SS_RATIO_NONE, SS_RATIO_MEAN, SS_RATIO_R, SS_RATIO_A = 1, 2, 3, 4
SS_RATIO_STRESS_LINEAR = 1
SS_STRESS_AMPLITUDE, SS_STRESS_RANGE = 1, 2
SS_LIFE_CYCLES, SS_LIFE_REVERSALS = 1, 2
SS_PROPERTY_UTS, SS_PROPERTY_YIELD, SS_PROPERTY_ENDURANCE_LIFE, SS_PROPERTY_ENDURANCE_STRESS = \
    1, 2, 3, 4
SS_PROPERTY_UCS, SS_PROPERTY_WALKER_GAMMA, SS_PROPERTY_WALKER_GAMMA_NEGATIVE, \
    SS_PROPERTY_FRACTURE_STRENGTH, SS_PROPERTY_STRENGTH_COEFFICIENT = 5, 6, 7, 8, 9
SS_PROPERTY_TENSION_LIMIT, SS_PROPERTY_TORSION_LIMIT, SS_PROPERTY_TORSION_LIMIT_B, \
    SS_PROPERTY_REPEATED_LIMIT = 10, 11, 12, 13
SS_QUERY_LIFE_OF_AMPLITUDE, SS_QUERY_AMPLITUDE_OF_LIFE = 1, 2
SS_EVALUATED, SS_NOT_EVALUATED, SS_MEAN_UNDEFINED, SS_CURVE_RANGE = 0, -1, -2, -3
SS_FLAG_INFINITE = 1 << 0
SS_FLAG_BELOW_ONE = 1 << 1
SS_FLAG_CONSTANT = 1 << 2
SS_FLAG_EXTRAPOLATED_HIGH = 1 << 3
SS_FLAG_EXTRAPOLATED_LOW = 1 << 4
SS_FLAG_BELOW_ENDURANCE = 1 << 5
SS_FLAG_TEMPERATURE_CLAMPED = 1 << 6
SS_FLAG_COMPRESSIVE_IGNORED = 1 << 7
SS_FLAG_MEAN_CLIPPED = 1 << 8
SS_FLAG_SIGN_AMBIGUOUS = 1 << 9
SS_FLAG_RATIO_CLAMPED = 1 << 10
SS_FLAG_SMAX_NONPOSITIVE = 1 << 11
SS_FLAG_ENDURANCE_EXTENDED = 1 << 12
SS_FLAG_HAIGH_CLAMPED = 1 << 13
SS_FLAG_NO_LIFE_CURVE = 1 << 14
SS_FLAG_FAILED = 1 << 15


# -------------------------------------------------------------------- errors
class TurboLCFError(Exception):
    """A turbolcf failure (the native message)."""


class UnavailableError(TurboLCFError):
    """turbolcf.dll is not built, cannot be loaded, or does not match this binding."""


class ArgumentError(TurboLCFError, ValueError):
    """An invalid argument (NULL, bad count, inconsistent offsets)."""


class ClosedError(TurboLCFError):
    """The context is destroyed (or was never one)."""


class MaterialError(TurboLCFError, ValueError):
    """Material data the library cannot use."""


class OptionsError(TurboLCFError, ValueError):
    """An option outside its range."""


class RangeError(TurboLCFError, IndexError):
    """An index out of range."""


class BufferSizeError(TurboLCFError):
    """Caller buffers missing or of the wrong size."""


class UnsupportedError(TurboLCFError):
    """Not available in this build of turbolcf.dll."""


class StateError(TurboLCFError):
    """Calls in the wrong order (copy before load)."""


class CancelledError(TurboLCFError):
    """The progress callback stopped the run."""


class VersionError(TurboLCFError):
    """A structure smaller than the library's (binding older than the DLL)."""


class NativeError(TurboLCFError):
    """Out of memory or an unexpected native failure."""


_ERRORS = {E_ARGUMENT: ArgumentError, E_HANDLE: ClosedError, E_MATERIAL: MaterialError,
           E_OPTIONS: OptionsError, E_RANGE: RangeError, E_BUFFER: BufferSizeError,
           E_UNSUPPORTED: UnsupportedError, E_STATE: StateError, E_CANCELLED: CancelledError,
           E_MEMORY: NativeError, E_INTERNAL: NativeError, E_VERSION: VersionError}

i32, i64, u64, f64, vp = ctypes.c_int32, ctypes.c_int64, ctypes.c_uint64, ctypes.c_double, \
    ctypes.c_void_p
P_char = ctypes.POINTER(ctypes.c_char)
P_i32, P_i64, P_f64 = ctypes.POINTER(i32), ctypes.POINTER(i64), ctypes.POINTER(f64)


# ---------------------------------------------------------------- structures
class Options(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("stress_reduction", i32), ("mean_stress", i32),
                ("cycle_counter", i32), ("temperature_select", i32), ("creep", i32),
                ("envelope_policy", i32), ("zero_stress_rule", i32), ("lmp_above_range", i32),
                ("lmp_require_stlo", i32), ("lmp_enforce_tmhi", i32), ("enable_step6", i32),
                ("apply_yield_cutoff", i32), ("rupture_max_increments", i32), ("threads", i32),
                ("small_cycle_tolerance", f64), ("rupture_time_tolerance", f64),
                ("rupture_damage_exponent", f64), ("closure_rule", i32),
                ("zero_amplitude_rule", i32), ("low_stress_rule", i32), ("reserved", i32)]


class LcfTable(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("a_ratio", f64), ("n_curves", i64),
                ("curve_temperature", P_f64), ("curve_offset", P_i64), ("life", P_f64),
                ("stress", P_f64), ("flags", P_i32)]


class Curve(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n", i64), ("temperature", P_f64), ("value", P_f64)]


class RambergOsgood(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n", i64), ("temperature", P_f64), ("strength", P_f64),
                ("exponent", P_f64), ("yield_offset_strain", f64),
                ("clamp_below_lowest_temperature", i32), ("reserved", i32)]


class RuptureLmp(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_coefficients", i64), ("coefficients", P_f64),
                ("constant", f64), ("stress_offset", f64), ("temperature_adder", f64),
                ("temperature_multiplier", f64), ("rupture_multiplier", f64),
                ("stress_low", f64), ("stress_high", f64), ("temperature_low", f64),
                ("temperature_high", f64), ("stress_type", i32), ("reserved", i32)]


class RuptureTable(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_curves", i64), ("curve_temperature", P_f64),
                ("curve_offset", P_i64), ("stress", P_f64), ("life", P_f64), ("flags", P_i32)]


class History(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_locations", i64), ("n_points", i64),
                ("time", P_f64), ("temperature", P_f64), ("stress", P_f64),
                ("location_ids", P_i64)]


class Summary(ctypes.Structure):
    _fields_ = [("location_id", i64), ("status", i32), ("error_code", i32), ("flags", u64),
                ("fatigue_damage", f64), ("creep_damage", f64), ("total_damage", f64),
                ("fatigue_life", f64), ("creep_life", f64), ("total_life", f64),
                ("fatigue_fraction", f64), ("creep_fraction", f64),
                ("creep_rupture_damage", f64),
                ("crit_i", i64), ("crit_j", i64), ("crit_t_i", f64), ("crit_t_j", f64),
                ("crit_count", f64), ("crit_sigma_a", f64), ("crit_sigma_m", f64),
                ("crit_sigma_max", f64), ("crit_sigma_min", f64), ("crit_r_raw", f64),
                ("crit_r_applied", f64), ("crit_a_data", f64), ("crit_walker_m", f64),
                ("crit_walker_stress", f64), ("crit_yield_strength", f64),
                ("crit_temperature", f64), ("crit_life", f64), ("crit_m_is_default", i32),
                ("crit_yield_cutoff", i32), ("n_cycles", i64), ("n_rejected", i64),
                ("major_i", i64), ("major_j", i64), ("major_life", f64),
                ("step6_replaced", i32), ("step6_life_changed", i32),
                ("candidates_evaluated", i64), ("candidates_failed", i64),
                ("largest_applied_stress", f64), ("small_cycle_threshold", f64),
                ("non_proportionality", f64), ("n_increments", i64),
                ("peak_effective_stress", f64), ("max_increment_damage", f64),
                ("time_of_max_increment", f64)]


class CycleRecord(ctypes.Structure):
    _fields_ = [("rank", i64), ("order", i64), ("i", i64), ("j", i64), ("count", f64),
                ("type", i32), ("rejected", i32), ("step6_replaced", i32), ("token_life", i32),
                ("candidate_life", f64), ("sigma_a", f64), ("sigma_m", f64), ("r_raw", f64),
                ("r_applied", f64), ("a_data", f64), ("walker_m", f64),
                ("walker_stress", f64), ("yield_strength", f64), ("temperature", f64),
                ("life", f64), ("damage", f64), ("t_lo", f64), ("t_hi", f64),
                ("t_fraction", f64), ("life_at_t_lo", f64), ("life_at_t_hi", f64),
                ("segment_lo", i64), ("segment_hi", i64), ("m_is_default", i32),
                ("yield_cutoff", i32), ("low_stress_extrapolated", i32), ("reserved", i32)]


class IncrementRecord(ctypes.Structure):
    _fields_ = [("time", f64), ("dt", f64), ("temperature", f64), ("stress_start", f64),
                ("stress_end", f64), ("life_start", f64), ("life_end", f64), ("damage", f64),
                ("cumulative", f64)]


class DiagnosticRecord(ctypes.Structure):
    _fields_ = [("code", i32), ("reserved", i32), ("cycle", i64), ("point", i64),
                ("value1", f64), ("value2", f64)]


class DetailInfo(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_cycles", i64), ("n_increments", i64),
                ("n_points", i64), ("n_turning_points", i64), ("n_closed_sequence", i64),
                ("n_diagnostics", i64), ("critical_plane_direction", f64 * 6),
                ("critical_plane_scale", f64), ("summary", Summary)]


class DetailBuffers(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_cycles", i64), ("cycles", ctypes.POINTER(CycleRecord)),
                ("n_increments", i64), ("increments", ctypes.POINTER(IncrementRecord)),
                ("n_points", i64), ("effective_stress", P_f64), ("projected_stress", P_f64),
                ("n_turning_points", i64), ("turning_points", P_i64),
                ("n_closed_sequence", i64), ("closed_sequence", P_i64),
                ("n_diagnostics", i64), ("diagnostics", ctypes.POINTER(DiagnosticRecord))]


class VersionInfo(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("abi_version", i32), ("major", i32), ("minor", i32),
                ("patch", i32), ("model_revision", i32), ("reserved", i32),
                ("build", ctypes.c_char * TEXT_SIZE)]


class SlOptions(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("analysis", i32), ("input", i32), ("correction", i32),
                ("mean_stress", i32), ("criterion", i32), ("refinement", i32),
                ("wb_method", i32), ("fs_form", i32), ("threads", i32), ("swt_stress", i32),
                ("infinite_life", f64), ("plane_increment", f64), ("walker_method", i32),
                ("mean_limit", i32), ("endurance_rule", i32), ("plane_set", i32),
                ("endurance_fraction", f64), ("stress_unit", f64), ("tensor_gate", f64)]


class SlMaterial(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("youngs_modulus", f64), ("strength_coefficient", f64),
                ("strength_exponent", f64), ("ductility_coefficient", f64),
                ("ductility_exponent", f64), ("cyclic_strength_coefficient", f64),
                ("cyclic_hardening_exponent", f64), ("cyclic_modulus", f64),
                ("poisson_ratio", f64), ("wang_brown_s", f64), ("fatemi_socie_k", f64),
                ("fatemi_socie_yield", f64), ("shear_strength_coefficient", f64),
                ("shear_strength_exponent", f64), ("shear_ductility_coefficient", f64),
                ("shear_ductility_exponent", f64), ("fatigue_limit_tension", f64),
                ("fatigue_limit_torsion", f64), ("endurance_reversals", f64),
                ("walker_gamma_positive", f64), ("walker_gamma_negative", f64),
                ("ultimate_tensile_strength", f64), ("ultimate_compressive_strength", f64)]


class SlMeanTable(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_temperatures", i64), ("n_rows", i64),
                ("temperature", P_f64), ("fraction", P_f64), ("factor", P_f64)]


class SlMaterialTable(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_temperatures", i64), ("temperature", P_f64),
                ("rows", ctypes.POINTER(SlMaterial)), ("mean_table", ctypes.POINTER(SlMeanTable))]


class SlHistory(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_nodes", i64), ("n_points", i64), ("stress", P_f64),
                ("strain", P_f64), ("node_ids", P_i64), ("temperature", P_f64)]


class SlRow(ctypes.Structure):
    _fields_ = [("node_id", i64), ("status", i32), ("flags", ctypes.c_uint32), ("life", f64),
                ("damage", f64), ("cycle_life", f64), ("cycle_reversals", f64),
                ("cycle_count", f64), ("tip_a", f64), ("tip_b", f64), ("swt_parameter", f64),
                ("strain_amplitude", f64), ("elastic_strain_amplitude", f64),
                ("plastic_strain_amplitude", f64), ("stress_amplitude", f64),
                ("mean_stress", f64), ("max_stress", f64), ("biaxiality", f64),
                ("theta", f64), ("phi", f64), ("psi", f64), ("normal_x", f64),
                ("normal_y", f64), ("normal_z", f64), ("criterion_value", f64),
                ("normal_strain_amplitude", f64), ("shear_strain_amplitude", f64),
                ("max_normal_stress", f64), ("mean_normal_stress", f64),
                ("shear_stress_amplitude", f64), ("dang_van_factor", f64),
                ("dang_van_shear", f64), ("dang_van_pressure", f64), ("temperature", f64)]


class SlStats(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("planes", i64), ("counted", i64), ("starts", i64),
                ("reversals", i64)]


class SlCycle(ctypes.Structure):
    _fields_ = [("tip_a", i64), ("tip_b", i64), ("status", i32), ("reversal", i32),
                ("eps_range", f64), ("sig_range", f64), ("sig_max", f64), ("sig_min", f64),
                ("sig_mean", f64), ("amplitude", f64), ("eps_n_amp", f64), ("sig_n_max", f64),
                ("sig_n_mean", f64), ("tau_amp", f64), ("param", f64), ("reversals", f64),
                ("cycles", f64), ("damage", f64), ("counted_a", f64), ("counted_b", f64),
                ("eps_n_a", f64), ("eps_n_b", f64), ("sig_n_a", f64), ("sig_n_b", f64)]


class SlDetailInfo(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_points", i64), ("n_reversals", i64),
                ("n_vias", i64), ("n_cycles", i64), ("n_branches", i64),
                ("n_branch_points", i64), ("mode", i32), ("reserved", i32),
                ("direction", f64 * 3), ("angles", f64 * 3), ("normal", f64 * 3),
                ("shear_direction", f64 * 3), ("dang_van_a", f64), ("dang_van_b", f64),
                ("row", SlRow)]


class SlDetailBuffers(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_points", i64), ("signal", P_f64), ("sigma", P_f64),
                ("eps_n", P_f64), ("gamma", P_f64), ("sig_n", P_f64), ("tau", P_f64),
                ("n_reversals", i64), ("reversal_index", P_i64), ("reversal_sigma", P_f64),
                ("reversal_eps", P_f64), ("reversal_origin", P_i64), ("via_offset", P_i64),
                ("n_vias", i64), ("vias", P_i64), ("n_cycles", i64),
                ("cycles", ctypes.POINTER(SlCycle)), ("n_branches", i64),
                ("branch_offset", P_i64), ("n_branch_points", i64), ("branch_eps", P_f64),
                ("branch_sigma", P_f64)]

class SsOptions(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("analysis", i32), ("parameter", i32), ("vm_sign", i32),
                ("component", i32), ("mean_stress", i32), ("compressive_mean", i32),
                ("compressive_cycles", i32), ("curve_source", i32), ("extrapolation", i32),
                ("temperature_rule", i32), ("temperature_select", i32),
                ("endurance_rule", i32), ("closure", i32), ("threads", i32),
                ("stress_scale", f64), ("kf", f64), ("infinite_life", f64),
                ("design_life", f64), ("walker_form", i32), ("ratio_rule", i32),
                ("residual_stress", f64), ("sn_scale", f64), ("endurance_fraction", f64),
                ("criterion", i32), ("plane_refinement", i32), ("crack_case", i32),
                ("reserved", i32), ("plane_increment", f64), ("stress_unit", f64)]


class SsSnTable(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_curves", i64), ("ratio_kind", i32),
                ("curve_kind", i32), ("stress_measure", i32), ("life_measure", i32),
                ("curve_temperature", P_f64), ("curve_ratio", P_f64), ("curve_offset", P_i64),
                ("life", P_f64), ("stress", P_f64)]


class SsBasquin(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n", i64), ("temperature", P_f64),
                ("strength_coefficient", P_f64), ("strength_exponent", P_f64)]


class SsHaighTable(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_temperatures", i64), ("n_rows", i64),
                ("temperature", P_f64), ("fraction", P_f64), ("factor", P_f64)]


class SsHistory(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_nodes", i64), ("n_points", i64), ("stress", P_f64),
                ("temperature", P_f64), ("node_ids", P_i64)]


class SsRow(ctypes.Structure):
    _fields_ = [("node_id", i64), ("status", i32), ("flags", ctypes.c_uint32), ("life", f64),
                ("damage", f64), ("cycle_life", f64), ("cycle_count", f64), ("tip_a", f64),
                ("tip_b", f64), ("stress_amplitude", f64), ("mean_stress", f64),
                ("max_stress", f64), ("min_stress", f64), ("stress_ratio", f64),
                ("equivalent_amplitude", f64), ("cycle_temperature", f64),
                ("endurance_amplitude", f64), ("allowable_amplitude", f64),
                ("safety_factor", f64), ("biaxiality", f64), ("theta", f64), ("phi", f64),
                ("normal_x", f64), ("normal_y", f64), ("normal_z", f64), ("factor", f64),
                ("criterion_value", f64), ("shear_amplitude", f64),
                ("normal_stress_max", f64), ("hydrostatic_stress", f64),
                ("invariant_amplitude", f64), ("equivalent_uniaxial", f64)]


class SsStats(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("cycles", i64), ("below_endurance", i64),
                ("extrapolated", i64), ("ignored", i64), ("planes", i64)]


class SsCycle(ctypes.Structure):
    _fields_ = [("tip_a", i64), ("tip_b", i64), ("flags", ctypes.c_uint32), ("reserved", i32),
                ("count", f64), ("s_max", f64), ("s_min", f64), ("s_a", f64), ("s_m", f64),
                ("r", f64), ("s_a0", f64), ("temperature", f64), ("life", f64),
                ("damage", f64), ("safety_factor", f64)]


class SsDetailInfo(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_points", i64), ("n_reversals", i64),
                ("n_cycles", i64), ("parameter", i32), ("reserved", i32),
                ("direction", f64 * 3), ("row", SsRow)]


class SsDetailBuffers(ctypes.Structure):
    _fields_ = [("struct_size", i64), ("n_points", i64), ("signal", P_f64),
                ("temperature", P_f64), ("n_reversals", i64), ("reversal_index", P_i64),
                ("n_cycles", i64), ("cycles", ctypes.POINTER(SsCycle))]


#: The C struct each mirror stands for (checked against turbolcf_layout on load).
STRUCTS = {"turbolcf_options": Options, "turbolcf_lcf_table": LcfTable,
           "turbolcf_curve": Curve, "turbolcf_ramberg_osgood": RambergOsgood,
           "turbolcf_rupture_lmp": RuptureLmp, "turbolcf_rupture_table": RuptureTable,
           "turbolcf_history": History, "turbolcf_summary": Summary,
           "turbolcf_cycle_record": CycleRecord, "turbolcf_increment_record": IncrementRecord,
           "turbolcf_diagnostic_record": DiagnosticRecord, "turbolcf_detail_info": DetailInfo,
           "turbolcf_detail_buffers": DetailBuffers, "turbolcf_version_info": VersionInfo,
           "turbolcf_sl_options": SlOptions, "turbolcf_sl_material": SlMaterial,
           "turbolcf_sl_mean_table": SlMeanTable, "turbolcf_sl_material_table": SlMaterialTable,
           "turbolcf_sl_history": SlHistory, "turbolcf_sl_row": SlRow,
           "turbolcf_sl_stats": SlStats, "turbolcf_sl_cycle": SlCycle,
           "turbolcf_sl_detail_info": SlDetailInfo,
           "turbolcf_sl_detail_buffers": SlDetailBuffers,
           "turbolcf_ss_options": SsOptions, "turbolcf_ss_sn_table": SsSnTable,
           "turbolcf_ss_basquin": SsBasquin, "turbolcf_ss_haigh_table": SsHaighTable,
           "turbolcf_ss_history": SsHistory,
           "turbolcf_ss_row": SsRow, "turbolcf_ss_stats": SsStats, "turbolcf_ss_cycle": SsCycle,
           "turbolcf_ss_detail_info": SsDetailInfo,
           "turbolcf_ss_detail_buffers": SsDetailBuffers}

#: turbolcf_progress_fn.  turbolcf_run takes it as void*: pass
#: ctypes.cast(PROGRESS_FN(callback), c_void_p) — or None for no callback,
#: which a CFUNCTYPE argument would refuse — and keep the PROGRESS_FN alive.
PROGRESS_FN = ctypes.CFUNCTYPE(i32, i64, i64, vp)

_SIGNATURES = (
    ("turbolcf_abi_version", i32, []),
    ("turbolcf_last_error", i32, [P_char, i32]),
    ("turbolcf_version", i32, [ctypes.POINTER(VersionInfo)]),
    ("turbolcf_layout", i32, [P_char, i32]),
    ("turbolcf_default_options", i32, [ctypes.POINTER(Options)]),
    ("turbolcf_create", i32, [ctypes.POINTER(Options), ctypes.POINTER(vp)]),
    ("turbolcf_destroy", i32, [vp]),
    ("turbolcf_set_lcf_table", i32, [vp, ctypes.POINTER(LcfTable)]),
    ("turbolcf_set_walker", i32, [vp, ctypes.POINTER(Curve)]),
    ("turbolcf_set_ramberg_osgood", i32, [vp, ctypes.POINTER(RambergOsgood)]),
    ("turbolcf_set_rupture_lmp", i32, [vp, ctypes.POINTER(RuptureLmp)]),
    ("turbolcf_set_rupture_table", i32, [vp, ctypes.POINTER(RuptureTable)]),
    ("turbolcf_validate", i32, [vp]),
    ("turbolcf_run", i32, [vp, ctypes.POINTER(History), vp, i64, i64, vp, vp]),
    ("turbolcf_detail_load", i32, [vp, ctypes.POINTER(History), ctypes.POINTER(DetailInfo)]),
    ("turbolcf_detail_copy", i32, [vp, ctypes.POINTER(DetailBuffers)]),
    ("turbolcf_detail_release", i32, [vp]),
    ("turbolcf_sl_default_options", i32, [i32, i32, i32, ctypes.POINTER(SlOptions)]),
    ("turbolcf_sl_check_options", i32, [ctypes.POINTER(SlOptions)]),
    ("turbolcf_sl_create", i32, [ctypes.POINTER(SlOptions), ctypes.POINTER(SlMaterial),
                                 ctypes.POINTER(vp)]),
    ("turbolcf_sl_create_table", i32, [ctypes.POINTER(SlOptions),
                                       ctypes.POINTER(SlMaterialTable), ctypes.POINTER(vp)]),
    ("turbolcf_sl_destroy", i32, [vp]),
    ("turbolcf_sl_run", i32, [vp, ctypes.POINTER(SlHistory), vp, i64, i64,
                              ctypes.POINTER(SlStats), vp, vp]),
    ("turbolcf_sl_detail_load", i32, [vp, ctypes.POINTER(SlHistory), P_f64,
                                      ctypes.POINTER(SlDetailInfo)]),
    ("turbolcf_sl_detail_copy", i32, [vp, ctypes.POINTER(SlDetailBuffers)]),
    ("turbolcf_sl_detail_release", i32, [vp]),
    ("turbolcf_ss_default_options", i32, [i32, ctypes.POINTER(SsOptions)]),
    ("turbolcf_ss_check_options", i32, [ctypes.POINTER(SsOptions)]),
    ("turbolcf_ss_create", i32, [ctypes.POINTER(SsOptions), ctypes.POINTER(vp)]),
    ("turbolcf_ss_destroy", i32, [vp]),
    ("turbolcf_ss_set_sn_table", i32, [vp, ctypes.POINTER(SsSnTable)]),
    ("turbolcf_ss_set_basquin", i32, [vp, ctypes.POINTER(SsBasquin)]),
    ("turbolcf_ss_set_property", i32, [vp, i32, i64, P_f64, P_f64]),
    ("turbolcf_ss_set_haigh_table", i32, [vp, ctypes.POINTER(SsHaighTable)]),
    ("turbolcf_ss_allowable", i32, [vp, f64, f64, i64, P_f64, P_f64, ctypes.POINTER(ctypes.c_uint32)]),
    ("turbolcf_ss_validate", i32, [vp]),
    ("turbolcf_ss_curve_query", i32, [vp, i32, f64, f64, P_f64,
                                      ctypes.POINTER(ctypes.c_uint32)]),
    ("turbolcf_ss_run", i32, [vp, ctypes.POINTER(SsHistory), vp, i64, i64,
                              ctypes.POINTER(SsStats), vp, vp]),
    ("turbolcf_ss_detail_load", i32, [vp, ctypes.POINTER(SsHistory),
                                      ctypes.POINTER(SsDetailInfo)]),
    ("turbolcf_ss_detail_copy", i32, [vp, ctypes.POINTER(SsDetailBuffers)]),
    ("turbolcf_ss_detail_release", i32, [vp]),
)

_lock = threading.Lock()
_library: Optional[ctypes.CDLL] = None
_load_error: Optional[str] = None
_loaded_dll: Optional[Path] = None


def dll_candidates() -> List[Path]:
    override = os.environ.get("TURBOLCF_NATIVE_DLL")
    if override:
        return [Path(override)]
    built = [LCF_ROOT / "native" / "build" / c / "turbolcf.dll" for c in BUILD_CONFIGS]
    return built + [LCF_ROOT / "runtime" / "bin" / "turbolcf.dll"]


def loaded_dll() -> Optional[Path]:
    """The turbolcf.dll this process loaded (None: not loaded)."""
    return _loaded_dll


def build_info(dll: Path) -> dict:
    """turbolcf_build_info.json next to the DLL ({} when absent)."""
    info_file = dll.with_name("turbolcf_build_info.json")
    if info_file.is_file():
        return json.loads(info_file.read_text(encoding="utf-8"))
    return {}


def native_layout(lib: ctypes.CDLL) -> Dict[str, dict]:
    """{struct name: {"size": n, "fields": {name: offset}}} as the DLL reports it."""
    n = lib.turbolcf_layout(None, 0)
    buf = ctypes.create_string_buffer(n + 1)
    lib.turbolcf_layout(buf, n + 1)
    return json.loads(buf.value.decode("ascii"))["structs"]


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
    """The loaded turbolcf.dll (once per process); UnavailableError with
    every searched location, or the mismatch, when it cannot be used."""
    global _library, _load_error, _loaded_dll
    with _lock:
        if _library is not None:
            return _library
        if _load_error is not None:
            raise UnavailableError(_load_error)
        dll = next((p for p in dll_candidates() if p.is_file()), None)
        if dll is None:
            _load_error = ("the LCF engine is not built: no turbolcf.dll at "
                           + ", ".join(str(p) for p in dll_candidates())
                           + " (cmake -S native -B native/build -A x64; "
                             "cmake --build native/build --config Release)")
            raise UnavailableError(_load_error)
        try:
            lib = _L.load_dll(dll, [])          # its folder and System32 — never PATH
        except OSError as exc:
            _load_error = f"cannot load {dll}: {exc}"
            raise UnavailableError(_load_error) from None
        for name, restype, argtypes in _SIGNATURES:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        version = lib.turbolcf_abi_version()
        if version != ABI_VERSION:
            _load_error = f"{dll} has ABI version {version}, this binding expects {ABI_VERSION}"
            raise UnavailableError(_load_error)
        problems = layout_mismatches(lib)
        if problems:
            _load_error = f"{dll} does not match this binding's structures: " + "; ".join(problems)
            raise UnavailableError(_load_error)
        _library, _loaded_dll = lib, dll
        return lib


def last_error(lib: ctypes.CDLL) -> str:
    """The calling thread's last native message."""
    buf = ctypes.create_string_buffer(2048)
    lib.turbolcf_last_error(buf, len(buf))
    return buf.value.decode("utf-8", "replace")


def check(lib: ctypes.CDLL, status: int) -> None:
    """Raise the exception of a failing status (message: the native one)."""
    if status == OK:
        return
    message = last_error(lib) or f"native status {STATUS_NAMES.get(status, status)}"
    raise _ERRORS.get(status, TurboLCFError)(f"[{STATUS_NAMES.get(status, status)}] {message}")


def text(raw) -> str:
    """A fixed char field as str."""
    if raw is None:
        return ""
    return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace")


def ptr(array, ctype):
    """A ctypes pointer to a NumPy array's data (None for an empty array)."""
    return array.ctypes.data_as(ctypes.POINTER(ctype)) if array.size else None


_NUMPY_TYPES = {i32: "<i4", i64: "<i8", u64: "<u8", f64: "<f8", ctypes.c_uint32: "<u4"}


def numpy_dtype(struct):
    """The NumPy structured dtype laid out exactly as a ctypes structure (same
    field offsets and item size), so the DLL writes straight into the array."""
    import numpy as np
    names, formats, offsets = [], [], []
    for name, ctype in struct._fields_:
        if hasattr(ctype, "_length_"):
            formats.append((_NUMPY_TYPES[ctype._type_], (ctype._length_,)))
        else:
            formats.append(_NUMPY_TYPES[ctype])
        names.append(name)
        offsets.append(getattr(struct, name).offset)
    return np.dtype({"names": names, "formats": formats, "offsets": offsets,
                     "itemsize": ctypes.sizeof(struct)})

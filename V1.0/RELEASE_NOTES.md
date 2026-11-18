# TurboSection V1.0 — Release Notes

**Version 1.0.0 · first official release · Windows x64 · 2026-11-18**

TurboSection V1.0 is a finite-element result post-processor for turbomachinery
components. It is distributed as a self-contained one-folder Windows
application: Python, Qt, VTK, NumPy and the calculation engines are included,
and no Python, Visual Studio or development tools are needed on the target
computer.

## Contents

| Component | Version in this release |
|---|---|
| Application | TurboSection 1.0.0 (display name *TurboSection V1.0*) |
| Fatigue engine `turbolcf.dll` (Stress-Life, Strain-Life, NASALIFE) | model revision **37**, ABI 2 |
| Crack engine `turbocrack.dll` (stress-intensity factors, crack growth) | model revision **6**, ABI 6 |
| ANSYS reader `turboio.dll` | ABI 8, built against ANSYS 2024 R2 (v242) |
| Abaqus reader `turboodb.dll` | ABI 3, built against the Abaqus 2024 ODB API |
| CalculiX reader `turbofrd.dll` | ABI 1 (no vendor runtime) |
| Nastran reader | pyNastran 1.4.1 in its own isolated runtime (Python 3.11, NumPy 1.26.4, SciPy 1.17.1; without test packages and pyNastran's GUI / converters, which the reader does not use) |
| Runtime | Python 3.11.0, PySide6 / Qt 6.12.0, VTK 9.7.1, NumPy 2.4.6, matplotlib 3.11.2, h5py 3.16.0, ansys-mapdl-reader 0.56.0 |

The engine DLLs were built from the current source for this release
(MSVC 19.44, Release, x64). The model revision is part of every fatigue
result's signature: results saved by an engine of another revision show as
obsolete. VERSION.txt lists the SHA-256 of every engine DLL.

## Validated capabilities

Every item below was exercised on this packaged build, launched from the
release folder, and gave results bit-identical to the validated source build
(see "How this release was validated").

* **FE result import** — File ▸ Open of ANSYS `.rst` / `.rth`, Abaqus `.odb`,
  CalculiX `.frd` and Nastran `.op2` (with its `.bdf` / `.dat` / `.nas`),
  the Open Results dialog, result sets, project files (`.tsproj`).
* **Visualization** — 2D and 3D meshes, contours with legend and Min / Max
  labels, the result graph, tabular data, CSV export.
* **Stress and strain results** — components, principal and equivalent
  values, elastic / plastic / total strain.
* **Thermal results** — nodal temperature (`.rth`, thermal sets).
* **Materials** — MatML import / export, the material editor, the reference
  material databases shipped with the engines (NASALIFE examples, DARWIN
  crack-growth examples).
* **Stress-Life (S-N)** — uniaxial and critical-plane evaluation, mean stress
  corrections, S-N families, endurance rules.
* **Strain-Life (ε-N)** — uniaxial and critical-plane criteria (normal strain,
  maximum shear strain, Brown-Miller, Fatemi-Socie, Wang-Brown, Dang Van),
  Neuber / Glinka corrections, fe-safe compatible plane sets and rules.
* **NASALIFE (LCF)** — the NASALIFE port with creep, mission definition and
  contour display.
* **Crack Growth** — surface and corner cracks (Newman-Raju / weight functions,
  Malik), Paris and sigmoidal laws, Walker, mission rainflow, the results
  window.

## Partially validated or experimental features

These features ship as they are in the validated source; their numerical
validation is incomplete and they are **not** declared production-ready:

* **Strain-Life — Elastic FE notch correction (Köttgen-Barkey-Socie 1995)**:
  labelled *Experimental* in the application.
* **fe-safe compatibility** (Strain-Life and Stress-Life): agreement with
  fe-safe 2024 is established on the recorded probe and parity sets within
  their documented tolerances; a registry of known differences remains
  (critical-plane nodes beyond 2 %, Fatemi-Socie on random histories,
  fe-safe's single-precision singular lives). Treat fe-safe-compatible
  options as validated only within that evidence.
* **fe-safe tensor gate** (Strain-Life option *Tensor Gate [%]*): opt-in, off
  by default. One engine test still pins a pre-revision-37 value (see Known
  issues).
* **Crack Growth**: corner-crack results carry an evidence class
  (BENCHMARKED / EXPERIMENTAL) — results flagged EXPERIMENTAL are outside the
  benchmarked range. Path- and surface-mapped stress gradients are for
  reference only.
* **Stress-Life items deferred** by design (not available): finite-life plane
  criteria beyond the equivalent uniaxial amplitude, Dang Van in Stress-Life,
  temperature-dependent tension / torsion limits, FOS / FRF iterations,
  probability / scatter.
* **Nastran**: solid elements only (GRID, CTETRA, CHEXA, CPENTA); validated on
  linear elements; quadratic solids are not validated; spherical CD frames
  and PSOLID CORDM ≠ 0 are refused.
* **Abaqus**: shells, contact and reaction fields are listed but not opened.

## Requirements and solver runtimes

* Windows 10 / 11, 64-bit; OpenGL 3.2 graphics.
* **The ANSYS and Abaqus vendor runtime libraries are proprietary and are not
  included.** They are taken from the user's own installation:
  * ANSYS 2024 R2 (`C:\Program Files\ANSYS Inc\v242`, or set
    `TURBOIO_ANSYS_ROOT`). Without it, `.rst` / `.rth` files are read by the
    bundled ansys-mapdl-reader instead (validated: every stress, strain and
    displacement array identical to the native reader; see Known issues for
    the unit-system word).
  * Abaqus 2024 (`C:\SIMULIA\EstProducts\2024`, or set
    `TURBOODB_ABAQUS_ROOT`). Without it, opening an `.odb` reports that the
    Abaqus runtime is missing; nothing else is affected.
* The Visual C++ runtime the engines need (msvcp140 ≥ 14.44) is included.

## Known issues and limitations

* **Extract to a short path.** The deepest file is 169 characters below
  the `TurboSection_V1.0` folder, which leaves at most 72 characters for the
  folder you extract into under Windows' 260-character limit. Unless long
  paths are enabled, extracting deeper fails with a "path too long" error.
* **Native ANSYS reader and the RST unit system (pre-existing).** The native
  reader does not pass the result file's `/UNITS` word to the application,
  so *Source Unit System: Program Controlled* assumes the MPA system (mm, t,
  s, MPa) for files read natively, while the ansys-mapdl-reader backend uses
  the declared system. For models in MPA units nothing changes; for a model
  declaring another system (for example SI), set *Source Unit System*
  explicitly in the model's Details. 81 reader parity tests record this.
* **Not-a-number sign** of "no value" entries differs between the native and
  the ansys-mapdl-reader backend (+NaN / −NaN); values and displays are equal.
* **Stale engine test** — `test_fesafe_tensor_gate.py` still pins the 48.41×
  difference that model revision 37 removed (design record §52 notes it has
  to go); the engine itself is the validated revision 37.
* **No application icon**: the source has none, so `TurboSection.exe` shows
  the Windows default icon.
* **Error text when a vendor runtime is missing** starts with PyInstaller's
  generic "dll was not found when the application was frozen"; the rest of
  the message names the missing installation folder, which is the cause.
* Viewport rendering needs a working OpenGL driver; remote desktop sessions
  and virtual machines without GPU acceleration may show an empty viewport.

## How this release was validated

* Native engines rebuilt clean from the current source; each engine's ctest
  passed; the packaged DLLs are byte-identical (SHA-256) to those builds.
* Engine and reader test suites (source build): Crack 1782 passed;
  Abaqus 189; CalculiX 199; Nastran 63; LCF 1148 passed, 1 stale test;
  ANSYS 2351 passed, 81 pre-existing unit-word parity failures.
* Application test suite, source environment: 3 407 passed, 101 skipped,
  0 failed. The same suite inside the packaged release: 3 393 passed,
  101 skipped (the same skips). The remaining 14 tests cannot run inside a
  frozen application: 9 need a test-only Qt module, and 5 start a Python
  interpreter. They are not defects of the release.
* Nastran: the reader's 63 tests and the application's 57 Nastran tests
  passed against the packaged standalone runtime.
* A release snapshot — ANSYS 2D / 3D / thermal, CalculiX, Abaqus, Nastran,
  NASALIFE, Strain-Life, Stress-Life, Crack Growth — evaluated through the
  application inside the packaged release and in the source environment:
  4 358 result arrays, every one bit-identical; the packaged engines report
  model revisions 37 (turbolcf) and 6 (turbocrack).
* TurboSection.exe launched directly from the release folder: startup, a
  saved project, contour evaluation, graph and table; every loaded module
  comes from the release folder or Windows (and, by design, the vendor
  installations).
* A clean-environment run from the extracted ZIP (other folder, path with
  spaces, no development environment variables, PATH = Windows only).
  **Limitation:** no separate clean Windows machine or VM was available; the
  checks ran on the build computer, which has ANSYS 2024 R2 and Abaqus 2024
  installed. A computer without them was emulated by pointing both runtime
  lookups at a missing folder.

# TurboSection V1.0 — Release Notes

**Version 1.0.0 · Windows x64 · first released 2026-11-18, rebuilt 2026-11-20**

The 2026-11-20 build replaces the first V1.0 package. It carries the
performance, rendering and stability work of 2026-11-18 – 2026-11-20 (see
"Changes in the 2026-11-20 build"); the version number, the engines and every
numerical result are unchanged.

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

## Changes in the 2026-11-20 build

* **Large models** — File ▸ Open, the Project tree, result switching, probing,
  hover and selection on models of a million nodes: the contour corruption
  ("fragmented colours") and the "load failed" errors on valid files are
  fixed; selections, Named Selection highlights and Generate Features are
  several times faster (models of thousands of Bodies included).
* **Open and Evaluate run in the background** with a progress display and
  Cancel (Esc); the window stays responsive. A cancelled Open opens nothing,
  a cancelled Evaluate leaves every result as it was.
* **The native ANSYS reader runs in a process of its own**: a file the ANSYS
  library cannot read ends only that process — TurboSection reports the
  failed read and continues.
* **Deformed results**: nodes, elements, faces, edges, vertices, box and lasso
  selections, highlights and measurement markers are picked and drawn on the
  deformed shape as displayed; ids, coordinates and values stay the model's.
* **Curved quadratic faces**: highlights lie exactly on the contour (one
  tessellation of the skin).
* **RST unit system**: the result file's `/UNITS` word is taken from every
  file by both readers. In the first build the ansys-mapdl-reader fallback
  (used without an ANSYS installation) took every file as undeclared (MPA
  assumed); the first build's notes described this the wrong way round.
* Lower memory: the ANSYS reader libraries no longer load into the
  application; caches are bounded.

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
* **The reader process's memory.** While it runs, the native reader's own
  process (a second `TurboSection.exe`) reserves about 375 MB for the ANSYS
  libraries (its working set is about 30–50 MB); it ends two minutes after
  the last read or with the application.
* **Thick 2D with a deformed result**: picks stay on the undeformed
  extrusion (the deformed extrusion is drawn; values are the model's).
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

The 2026-11-20 build was validated as the first one was, from scratch
(`TurboSection_V1.0_Validation_Report.md` next to the ZIP):

* Native engines: turbolcf, turbocrack, turboodb and turbofrd are the DLLs
  of the first build (byte-identical, unchanged sources); turboio was
  rebuilt on 2026-11-19 from its current source (ctest 3/3 passed). The
  packaged DLLs are byte-identical (SHA-256) to these builds.
* Reader test suite (ANSYS, source build): 2 588 passed, 0 failed (the first build's 81 unit-word parity failures are fixed).
* Application test suite, source environment: 3 565 passed, 101 skipped, 0 failed. The same
  suite inside the packaged release: 3 552 passed,
  101 skipped (the same skips). The remaining 15 tests cannot run inside a
  frozen application: 9 need a test-only Qt module, and 6 start a Python
  interpreter. They are not defects of the release.
* Nastran: the reader's 63 tests and the application's 57 Nastran tests passed
  against the packaged standalone runtime.
* A release snapshot — ANSYS 2D / 3D / thermal, CalculiX, Abaqus, Nastran,
  NASALIFE, Strain-Life, Stress-Life, Crack Growth — evaluated through the
  application inside the packaged release and in the source environment:
  4 358 result arrays, every one bit-identical; the packaged engines report
  model revisions 37 (turbolcf) and 6 (turbocrack).
* The native ANSYS reader runs in its own process in the packaged
  application: a second `TurboSection.exe --reader-host` holds turboio.dll and the ANSYS
  libraries (about 380 MB reserved, 38 MB working set); the application
  process loads none of them; no reader process is left after the
  application exits — also when it is killed.
* TurboSection.exe launched directly from the release folder: startup, a saved project, contour evaluation, graph and table, a result
  file through the Open Results dialog; every loaded module comes from the
  release folder or Windows (and, by design, the vendor installations).
* A clean-environment run from the extracted ZIP (other folder, path with
  spaces, no development environment variables, PATH = Windows only):
  probe, 4 358 / 4 358 result arrays bit-identical, TurboSection.exe with a
  saved project, an evaluation and a result file read by the reader
  process, the no-installation emulation and the frozen Nastran /
  reader-process tests; every one of the 7 683 files unchanged after use.
  **Limitation:** no separate clean Windows machine or VM was available; the
  checks ran on the build computer, which has ANSYS 2024 R2 and Abaqus 2024
  installed. A computer without them was emulated by pointing both runtime
  lookups at a missing folder.

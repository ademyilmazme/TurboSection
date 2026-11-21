TurboSection V1.0 - Windows x64
===============================

TurboSection is a finite-element result post-processor for turbomachinery
components: it reads ANSYS, Abaqus, CalculiX and Nastran results, shows
contours, paths and sections, and runs the Stress-Life, Strain-Life,
NASALIFE (LCF) and Crack Growth evaluations.


Installation
------------
1. Extract TurboSection_V1.0_Windows_x64.zip to a folder with a SHORT path,
   for example  C:\Tools  (giving C:\Tools\TurboSection_V1.0) or your
   Downloads folder.  The deepest file is 169 characters below the
   TurboSection_V1.0 folder, so the folder you extract into must have a path
   of at most 72 characters (Windows' 260-character path limit), unless long
   paths are enabled in Windows.  An extraction that hits the limit fails
   with a "path too long" error.
   Keep the folder structure exactly as it is: TurboSection.exe needs the
   _internal folder next to it.
2. Nothing else is installed: Python, Qt, VTK and the calculation engines are
   inside the folder.  No administrator rights, no registry entries.

Requirements
------------
* Windows 10 or Windows 11, 64-bit (V1.0 was validated on Windows 10 22H2).
* A graphics driver with OpenGL 3.2 or newer (the 3D viewport).  Remote
  desktop sessions and virtual machines without GPU acceleration may show an
  empty viewport.
* About 900 MB of disk space for the extracted folder.
* Abaqus .odb files: an Abaqus 2024 installation on the same computer (see
  "Solver runtimes" below).
* ANSYS .rst / .rth files open without an ANSYS installation (TurboSection's
  built-in reader).  With an ANSYS 2024 R2 installation TurboSection uses the
  faster native reader instead (see below).

Launch
------
Double-click TurboSection.exe, or from a command prompt:

    TurboSection.exe                    the empty application
    TurboSection.exe model.rst          File > Open of a result file
    TurboSection.exe project.tsproj     a saved TurboSection project

Solver runtimes
---------------
The ANSYS and Abaqus readers use the solver vendors' own libraries, which
are proprietary and are NOT included in this release.  They are found in
the vendor's installation:

* ANSYS 2024 R2 (v242), default folder C:\Program Files\ANSYS Inc\v242.
  For another folder set the environment variable
      TURBOIO_ANSYS_ROOT=D:\ANSYS Inc\v242
  Without an ANSYS installation, .rst / .rth files are read by the built-in
  ansys-mapdl-reader backend (the same stress, strain, displacement and
  temperature values, slower); the log file says which reader was used.
  The native reader runs in a process of its own: while a result file is
  read, Task Manager shows a second TurboSection.exe (started with
  --reader-host).  It ends two minutes after the last read, or with the
  application.  A file the ANSYS library cannot read ends only that
  process: TurboSection reports the failed read and carries on.
* Abaqus 2024, default folder C:\SIMULIA\EstProducts\2024.  For another
  folder set
      TURBOODB_ABAQUS_ROOT=D:\SIMULIA\EstProducts\2024
  Reading an .odb needs no Abaqus license, only the installation.

CalculiX (.frd) and Nastran (.op2 with its .bdf / .dat / .nas next to it)
need nothing else: their readers are complete in this release.

Log file
--------
%LOCALAPPDATA%\TurboSection\logs\TurboSection.log, rewritten at each start.
Please attach it to any problem report.

Files
-----
TurboSection.exe      the application
_internal\            Python 3.11, Qt 6 (PySide6) with its plugins, VTK, NumPy,
                      the readers and calculation engines (native DLLs under
                      _internal\core\...\runtime\bin), the material databases
licenses\             third-party licenses (THIRD_PARTY_NOTICES.txt)
README.txt            this file
RELEASE_NOTES.md      validated capabilities and known limitations
VERSION.txt           version, build and engine revision information

Uninstall: delete the folder.  TurboSection also writes its log to
%LOCALAPPDATA%\TurboSection and the working copies of open projects to
%TEMP%\TurboSection-work (TurboSection removes stale ones itself); delete
those folders too if you want to remove every trace.

# SPIKE 2 VPX

Play Stern SPIKE 2 games with Visual Pinball X on Windows x64. VPX runs the
playfield and the SPIKE 2 runtime runs the game, LCD, and audio. The bundled
`PAD.Controller` connects them.

## Set up

Open **SPIKE 2 VPX** from the Start menu, or run `python Spike2Vpx.pyw` from a
checkout with x64 Python and Tcl/Tk. For a checkout, install the bundled
Python dependencies with `python -m pip install -r requirements-windows.txt`.

The checklist covers WSL 2, WSLg, the private `SPIKE2-VPX` distribution,
Visual Pinball X 10.8+ x64, and the controller. Run **Set up** and follow
the checklist. If a restart is required, restart Windows and reopen the app.
Use **Get VPX** and **Choose VPX** to install or select `VPinballX64.exe`.
Settings and setup logs are under `%LOCALAPPDATA%\Spike2Vpx`.

## Prepare and play

Supply your own SPIKE 2 SD-card image and a compatible VPX table. Godzilla LE
is the reference title. The VPX kit can create a patched copy of a recognized
table. Keep the patched table and `PADSPIKE2.vbs` together.

```powershell
python tools/spike2_emu/vpx/padvpx.py patch "C:\Tables\Godzilla limited edition.vpx"
.\Play-Spike2Vpx.ps1 -Card "C:\Images\godzilla_le.sdcard.raw" -Table "C:\Tables\Godzilla limited edition (PAD).vpx"
```

The launcher builds the guest filesystem from the card on its first run. The
source image is opened read-only. You can override detection with `-VpxExe` or
select a prepared WSL distribution with `-Distro`. Closing VPX stops the run.
Setup is the current GUI milestone. Image preparation, table preparation,
and Play are available through the command-line tools.

## Build and test

Build the installer with `installer\build_spike2_vpx.ps1`, full x64 Python
with Tcl/Tk, and Inno Setup 6.3 or later. The installer bundles the tools and
Python. The first setup downloads the upstream runtime image and verifies
its pinned SHA-256. Uninstall preserves the private runtime and player data.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

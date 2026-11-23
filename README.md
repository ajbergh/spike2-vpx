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

Browse **Supported Tables** to find the table version for your game and open
its download page where one is listed. In **Games**, choose an image or folder.
The app identifies the title from the
card's contents and checks space for the optional cache. Choose a compatible
VPX table to make a playable copy, or use an existing prepared table. Prepared
pairings appear in the game library. Select **Play** to start the game and
open VPX. Closing VPX stops the run and returns to the library.

The runtime builds the guest filesystem from a card on its first run and
opens the source image read-only. The PowerShell launcher also accepts
`-VpxExe` and `-Distro` overrides. Manage LCD placement, game volume, and card
caching in **Settings**.

## Build and test

Run `.\scripts\build-windows-installer.ps1` with full x64 Python with Tcl/Tk
and Inno Setup 6.3 or later. The build writes
`installer\Output\SPIKE2-VPX-Setup.exe`. The installer bundles the tools and
Python. The first setup downloads the upstream runtime image and verifies
its pinned SHA-256. Uninstall preserves the private runtime and player data.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

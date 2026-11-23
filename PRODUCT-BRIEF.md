# SPIKE 2 VPX product brief

## What we're building

SPIKE 2 VPX is a standalone Windows app for playing Stern SPIKE 2 games in Visual Pinball X. The starting point is a clean Windows PC with no upstream PAD download, installation, settings, or runtime. A player supplies a SPIKE 2 SD-card image and a compatible VPX table. The app provisions the Windows and WSL2 components, matches the image to the table, creates a PAD-enabled copy, and launches the pair.

VPX simulates the playfield, balls, and mechanisms. The original Stern game runs under QEMU in WSL2 and owns the rules, game audio, and LCD. The app's bundled `PAD.Controller` carries switches, lamps, and coil events between them. During play, the visible experience is the VPX playfield and Stern LCD, with the setup window out of the way.

The first release focuses on setup, image preparation, table conversion, and play. Its installer packages the SPIKE 2 components and the shared code they rely on, including the PowerShell launcher. Asset editing, physical-card flashing, multiboot authoring, and the standalone virtual playfield stay outside the first release. Users supply the images and tables.

## Installation on Windows

- Ship a Windows x64 installer with its own app directory, Start menu entry, Python runtime, SPIKE 2 and VPX tools, and supported table maps. Keep app settings, logs, and the card cache in separate data locations so updates can replace the app without losing a library.
- On first launch, check WSL2, WSLg, the app's SPIKE 2 runtime, Visual Pinball X, and the controller. Show a short status checklist with one **Set up** or **Repair** action. Detect a compatible VPX installation, allow browsing to its executable, or guide the player through installing VPX if it is missing.
- Ask for elevation when Windows needs it. If enabling WSL requires a restart, show that clearly, save setup progress, and resume the checks on the next launch. A failed step should show the problem, the log location, and a retry action.
- Register the bundled controller with Windows under `PAD.Controller`, the name used by patched tables. Keep setup, upgrades, and uninstall aware of ownership so they manage this app's own controller registration. Offer a choice to remove the app's WSL distribution or keep it and its data when uninstalling.

## WSL2 setup and repair

1. Check that Windows can run WSL2 and enable the required Windows components when needed. Check WSLg availability for the Stern LCD and audio.
2. Import a versioned WSL2 distribution under an app-specific name. Provision its required Linux packages, the SPIKE 2 ARM root filesystem, and emulator helpers through the app's own setup process. Record the distribution and runtime versions so setup can resume after a reboot or repair an incomplete install.
3. Verify the components the game actually uses. Check QEMU user-mode execution, access to the ARM root filesystem and the card image, the device shim, LCD and audio helpers, and the `padlink` transport. Keep setup repeatable so running it again only installs or repairs missing pieces.
4. Verify the installed controller can reach the app's WSL2 runtime and confirm WSLg can display the LCD and route audio in the current Windows session before showing **Ready to play**. Keep setup and runtime logs separate. Scope repair to the app's distribution and preserve the player's images, tables, and settings.

## Player experience

Use one main window with a game list and a simple path through **Set up**, **Prepare image**, **Prepare table**, and **Play**. Bring forward the action that needs attention. Show the resolved game title and selected files so the player can check the pairing before starting.

| Step | What the player does | What the app does |
| --- | --- | --- |
| Set up | Runs the setup check and selects a VPX installation if needed. | Shows the components that are ready, runs the needed setup, and reports anything requiring a restart or repair. |
| Prepare image | Drops a `.raw` image or selects its folder. | Validates the image, identifies the title and edition, and checks disk space when preparing a cache. Launches usable images directly or prepares an optional runnable extraction. Mounts the source image read-only. When a folder has several candidates, selects a unique title match or asks the player to choose. |
| Prepare table | Picks a VPX table or an existing `(PAD)` copy. | Checks for a matching patch map before converting. Creates a separate `(PAD)` copy with its required script files and keeps the author’s table available. Shows a clear compatibility message if no map matches. |
| Play | Selects **Play**. | Starts the Stern runtime, waits for it to accept the bridge, and opens VPX. Closing VPX shuts down the run and releases its slot. Remembers the pairing so the next session starts from the game list. |

Put VPX location, LCD placement, volume, log access, and runtime repair in settings. Keep the PowerShell launcher available for cabinet front ends and direct startup. Show progress for setup, image preparation, and table conversion. Keep optional background tools out of live play to give VPX a consistent frame budget.

## First release acceptance

- On a clean Windows PC with neither upstream PAD nor VPX installed, install the app, complete any required reboot, install VPX through the guided workflow, and get to **Ready to play** through the GUI.
- Run setup again to confirm it reuses the app's existing distribution and controller registration. Verify repair can recover an incomplete setup and that uninstall can preserve the player's images, tables, and settings.
- Use Godzilla LE as the first reference workflow. Open the SD image, patch a compatible VPX table, play through a mode, and confirm VPX receives inputs and outputs while the Stern LCD and audio work. Verify that closing VPX cleans up the run and that the same pairing can launch again.
- Check playfield frame times during multiball and Stern video transitions. Use displayed frame times and missed refresh intervals to judge smoothness alongside the average FPS.

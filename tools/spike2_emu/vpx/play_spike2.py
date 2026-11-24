"""Run a prepared SPIKE 2 title alongside its PAD-patched VPX table."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

# PAD's embedded Windows Python does not add the script directory to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import padcontroller  # noqa: E402
import padlaunch  # noqa: E402


def embedded_script(table):
    import olefile
    from vpxfile import biff_records

    # Only the embedded script is needed. Skip the table's other streams.
    with olefile.OleFileIO(str(table)) as ole:
        data = ole.openstream("GameStg/GameData").read()
    return next((body[4:].decode("utf-8", "replace")
                 for tag, body, _ in biff_records(data) if tag == "CODE"), "")


def patched_script(script):
    return any(marker in script.lower() for marker in ("padspike2.vbs", "pad.controller"))


def read_title(table, script=None):
    from vpxsetup import script_title

    if script is None:
        script = embedded_script(table)
    title = script_title(script)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", title) or not patched_script(script):
        raise ValueError("The table needs an embedded PAD controller script and title.")
    return title


def resolve_vpx(exe):
    if not exe:
        from vpxsetup import find_vpx

        folder = find_vpx()
        exe = Path(folder) / "VPinballX64.exe" if folder else None
    if not exe or not exe.is_file():
        raise ValueError("Pass the path to VPinballX64.exe with -VpxExe.")
    return exe.resolve()


def check_controller():
    import winreg

    try:
        clsid = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, r"PAD.Controller\CLSID")
        server = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32")
    except OSError as exc:
        raise RuntimeError("Register PAD.Controller with the VPX kit before launching.") from exc
    if not server:
        raise RuntimeError("PAD.Controller has no registered COM server.")
    # A checkout can be launched against an older registered controller.
    # Only overlap startup when the actual COM server supports waiting for us.
    if not padcontroller.pythoncom:
        return False
    import win32com.client

    com = padcontroller.pythoncom
    com.CoInitialize()
    controller = None
    try:
        controller = win32com.client.Dispatch("PAD.Controller", clsctx=com.CLSCTX_LOCAL_SERVER)
        return controller.PadExternalStartup is True
    except (AttributeError, com.com_error):
        return False
    finally:
        del controller
        com.CoUninitialize()


def card_in_folder(folder, native_title):
    images = [path for path in folder.iterdir() if path.suffix.lower() == ".raw" and path.is_file()]
    title = native_title.lower()
    matches = [path for path in images if path.stem.lower() == title
               or path.name.lower().startswith((title + "-", title + "."))]
    candidates = matches or images
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        names = ", ".join(sorted(path.name for path in candidates))
        raise ValueError(f"Multiple .raw images in {folder}: {names}. Choose one with -Card.")
    raise ValueError(f"No game executable or .raw image in {folder}. Pass a prepared SD image with -Card.")


def startup_error(launch):
    problem = "Stern runtime did not become ready" if launch.alive() else "Stern runtime exited during startup"
    return RuntimeError(f"{problem}. See {launch.logfile}")


def wait_for_rig(launch, deadline):
    while launch.alive() and time.monotonic() < deadline:
        record = padcontroller.Rig.board_slot(launch.slot)
        if record and record.get("label") == launch.label:
            if record.get("game") != launch.title:
                raise RuntimeError("The rig started a different title from the table's map.")
            return padcontroller.Rig(launch.title, record=record)
        time.sleep(0.2)
    raise startup_error(launch)


def wait_for_game(launch, rig, deadline, player=None):
    while launch.alive() and time.monotonic() < deadline:
        if player is not None and player.poll() is not None:
            return False
        if padcontroller.block_in_wsl(rig, timeout=min(5, max(0.1, deadline - time.monotonic()))):
            return True
        time.sleep(0.5)
    if player is not None and player.poll() is not None:
        return False
    raise startup_error(launch)


def play(launch, exe, table, timeout, progress=None, *, overlap=False):
    # COM can discover the run from the board independently of VPX's environment.
    if padcontroller.Rig.board_run(launch.title):
        raise RuntimeError(f"Close the existing {launch.title} rig before starting this table.")
    try:
        launch.start()
        deadline = time.monotonic() + timeout
        rig = wait_for_rig(launch, deadline)
        overlap = overlap and rig.prepared_launch
        if progress:
            progress("Connecting controller", False)
        if not overlap:
            wait_for_game(launch, rig, deadline)
        env = dict(os.environ, PAD_WSL_DISTRO=rig.distro, PAD_ROOT_WSL=rig.root,
                   PAD_SLOT=str(launch.slot), PAD_VPX_LINK="1")
        if progress:
            progress("Opening VPX", False)
        with subprocess.Popen([str(exe), "-ExtMinimized", "-play", str(table)], cwd=exe.parent,
                              env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP) as vpx:
            if overlap:
                try:
                    if not wait_for_game(launch, rig, deadline, vpx):
                        return vpx.returncode
                except BaseException:
                    if vpx.poll() is None:
                        vpx.terminate()
                    raise
            if progress:
                progress("Playing in Visual Pinball X", True)
            while True:
                try:
                    return vpx.wait()
                except KeyboardInterrupt:
                    print("Close VPX to stop the Stern runtime and release its slot.", file=sys.stderr)
    finally:
        launch.stop()


def play_app_game(title, card, table, timeout):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from spike2_vpx.player import Game, Player
    from spike2_vpx_setup import Setup

    setup = Setup()
    try:
        player = Player(setup)
        game = Game(title, str(card), str(table))
        for warning in player.preflight(game):
            print(warning, file=sys.stderr, flush=True)
        return player.play(game, lambda message, _: print(message, flush=True), timeout)
    finally:
        for handler in setup.log.handlers:
            handler.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--game-dir", help="Executable title directory, or Windows folder containing a .raw image")
    source.add_argument("--card", type=Path, help="Prepared SPIKE 2 card image")
    parser.add_argument("--table", type=Path, required=True, help="PAD-patched .vpx file")
    parser.add_argument("--vpx-exe", type=Path, help="Override the installed VPX player")
    parser.add_argument("--distro", help="Override the prepared WSL distribution")
    parser.add_argument("--app-game", help="Launch an exported app pairing using the app settings")
    parser.add_argument("--startup-timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        if sys.platform != "win32":
            raise RuntimeError("This launcher requires Windows and WSL.")
        if args.startup_timeout < 1:
            raise ValueError("Startup timeout must be positive.")
        if args.app_game:
            if not args.card:
                raise ValueError("An exported game requires a card image.")
            return play_app_game(args.app_game, args.card, args.table, args.startup_timeout)
        table = args.table.resolve(strict=True)
        if not table.is_file() or table.suffix.lower() != ".vpx":
            raise ValueError("Pass a PAD-patched .vpx table.")
        title = read_title(table)
        mapping = padlaunch.table_map(title)
        if not mapping or mapping.get("platform", "spike2") != "spike2":
            raise ValueError(f"No SPIKE 2 map was found for {title}.")
        native_title = padlaunch.game_for(title)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", native_title):
            raise ValueError(f"Invalid native SPIKE 2 game name in the map for {title}.")
        exe = resolve_vpx(args.vpx_exe)
        overlap = check_controller()

        distro = args.distro or padlaunch.distro()
        card = args.card
        game_dir = args.game_dir
        if game_dir and not game_dir.startswith("/"):
            folder = Path(game_dir).resolve(strict=True)
            if not folder.is_dir():
                raise ValueError("-GameDir requires a directory. Pass an image file with -Card.")
            if (folder / "game").is_file():
                game_dir = padlaunch.padpath.to_wsl(str(folder), distro)
            else:
                card = card_in_folder(folder, native_title)
                game_dir = None
        if card:
            card = card.resolve(strict=True)
            if not card.is_file():
                raise ValueError("-Card requires a prepared SPIKE 2 image file.")
        else:
            game_dir = game_dir.rstrip("/")
            probe = subprocess.run(padlaunch.wsl_head(distro) + ["-e", "test", "-x", game_dir + "/game"],
                                   capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            if probe.returncode:
                detail = padlaunch._text(probe.stderr).strip()
                raise RuntimeError(f"WSL could not access an executable at {game_dir}/game. {detail}".strip())
        launch = padlaunch.Launch(title, str(card) if card else None, dist=distro, minutes=0,
                                  game_dir=game_dir, prepared=True)
        return play(launch, exe, table, args.startup_timeout, overlap=overlap)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as exc:
        print(f"SPIKE 2 VPX: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

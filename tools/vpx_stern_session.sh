#!/bin/bash
# One app-owned WSL session, including preparation and shutdown.
set -eu

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
runtime=/run/spike-vpx
[[ ${2:-} =~ ^PAD-VPX-[0-9]+-[a-f0-9]{12}$ ]] || exit 2
token=$2
mkdir -p -m 700 "$runtime"
pidfile=$runtime/$token.pid
cancel=$runtime/$token.stop

if [ "$1" = stop ]; then
    if [ ! -r "$pidfile" ]; then
        touch "$cancel"
        [ -r "$pidfile" ] || exit 0
    fi
    read -r pid < "$pidfile"
    if [[ $pid =~ ^[0-9]+$ ]] &&
        grep -zFxq "PAD_VPX_SESSION=$token" "/proc/$pid/environ" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || :
    fi
    exit 0
fi

platform=$1
slot=$3
card=$4
minutes=$5
title=$6
case "$platform:$slot" in spike1:0|spike3:[4-7]) ;; *) exit 2 ;; esac
[[ $title =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2

exec 9>"$runtime/$platform-$slot.lock"
flock -n 9 || { echo "This $platform runtime slot is already in use."; exit 1; }
exec 8>"$runtime/$platform-$title.lock"
flock -n 8 || { echo "This title already has a running session."; exit 1; }
printf '%s\n' "$$" > "$pidfile"
child=
started=0

cleanup() {
    trap - EXIT TERM INT HUP
    if [ -n "$child" ]; then
        kill -TERM -- "-$child" 2>/dev/null || :
        wait "$child" 2>/dev/null || :
    fi
    if [ "$platform" = spike1 ] && [ "$started" = 1 ]; then
        bash "$HERE/spike1_emu/stop.sh" || :
    fi
    rm -f -- "$pidfile" "$cancel" "$runtime/$token.game"
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP
[ ! -e "$cancel" ] || exit 143

spike1_payload() {
    local file=$1 expected=$2 source=$3 source_hash=$4 target=$5 download
    [ ! -x "$target" ] || return 0
    [ "$(s1_source_hash "$source")" = "$source_hash" ] || return 0
    command -v curl >/dev/null || return 0
    download=$(mktemp "$runtime/payload.XXXXXXXX")
    if curl -fsSL --proto '=https' --proto-redir '=https' --connect-timeout 5 --max-time 45 \
        "https://github.com/davidvanderburgh/pinball-asset-decryptor/releases/download/payloads-1/$file" \
        -o "$download" && printf '%s  %s\n' "$expected" "$download" | sha256sum --check --status; then
        install -D -m 0755 "$download" "$target"
    fi
    rm -f -- "$download"
}

if [ "$platform" = spike1 ]; then
    rig=$HERE/spike1_emu
    . "$rig/prereqs.sh"
    s1_paths
    if [ -n "$(bash "$rig/s1own.sh" game 2>/dev/null)" ]; then
        echo "A SPIKE 1 session is already running."
        exit 1
    fi
    if [ -n "$S1_DESKTOP_USER" ]; then
        install -d -m 0755 -o "$S1_DESKTOP_USER" -g "$(id -gn "$S1_DESKTOP_USER")" "$S1_WORK"
    fi
    spike1_payload qemu-arm af669cbaf5536e431315685a8c37bdd511b9496536aed553552cd450afc572bf \
        "$rig/patch_qemu.py" 968c137774290af7481db53cb75de1e0dfe89a066f9b70e41ed200fb5589c779 "$S1_QEMU"
    spike1_payload s1hwshim 27dbf3864d4ec2d7afe107c8fa834788be5fc7439b432277eebdee3529c0c4ba \
        "$rig/s1hwshim.c" 3fd77ece34cfde552330b838650a00678e2b0ed9202d12af734b50dd4a8eea28 "$S1_SHIM_DIR/s1hwshim"
    need=$(s1_build_groups "$rig")
    if [ -n "$need" ]; then
        mapfile -t packages < <(s1_prereq_missing $need | cut -d '|' -f 2)
        if [ "${#packages[@]}" -gt 0 ]; then
            echo "[setup] Installing SPIKE 1 runtime dependencies."
            DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 update -qq
            DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y -qq "${packages[@]}"
        fi
    fi
    started=1
    export S1_VPX_PID_FILE="$runtime/$token.game"
    setsid bash "$rig/start.sh" "$card" 8>&- 9>&- < /dev/null &
    child=$!
    wait "$child"
    child=
    # start.sh returns after transferring the game to its own process group.
    read -r game_pid < "$S1_VPX_PID_FILE"
    while kill -0 "$game_pid" 2>/dev/null; do
        sleep 1 & wait $!
    done
else
    rig=$HERE/spike3_emu
    if pgrep -f "s3run.sh .* --slot $slot( |$)" >/dev/null; then
        echo "A SPIKE 3 session is already running in slot $slot."
        exit 1
    fi
    if ! bash "$rig/s3launch.sh" --check; then
        echo "[setup] Installing SPIKE 3 runtime dependencies."
        PAD_S3_SKIP_SAVE_SETUP=1 bash "$rig/s3launch.sh" --setup < /dev/null
    fi
    # Background jobs otherwise receive /dev/null when started by a script.
    exec 7<&0
    options=()
    if [ "${PAD_AUDIO:-0}" = 1 ]; then options+=(--audio); fi
    setsid bash "$rig/s3launch.sh" "$card" "$slot" "$minutes" "${options[@]}" <&7 7<&- 8>&- 9>&- &
    child=$!
    exec 7<&-
    wait "$child"
    child=
fi

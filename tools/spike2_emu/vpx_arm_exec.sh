#!/bin/bash
# Test the WSL launcher's actual ARM execution path and repair its own handler.
set -eo pipefail
export LC_ALL=C

fail() { echo "[arm] $*" >&2; exit 1; }
case "${1:-}" in check|repair) mode=$1 ;; *) fail "Use check or repair." ;; esac
[ "${WSL_DISTRO_NAME:-}" = SPIKE2-VPX ] && [ "$(id -u)" = 0 ] ||
    fail "Run this in the SPIKE2-VPX WSL distribution as its Linux root user."

. "$(dirname "$0")/padpath.sh"
. "$RIG/ensurebuild.sh"

qemu=$(pad_qemu_arm) || fail "The SPIKE 2 runtime has no static QEMU ARM interpreter."
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
mkdir "$scratch/bin"
printf '%s' "$PAD_ARM_EXIT0_B64" | base64 -d > "$scratch/bin/sh"
chmod +x "$scratch/bin/sh"
"$qemu" "$scratch/bin/sh" || fail "QEMU could not run the ARM test program."

if [ "$mode" = repair ]; then
    exec 9>/run/spike2-vpx-arm.lock
    flock -w 10 9 || fail "Another ARM setup is in progress."
fi
if output=$(ROOT="$scratch" _pad_guest_probe); then
    echo "[arm] ARM execution inside the guest is ready."
    exit 0
fi
case "$output" in
    *"Exec format error"*|*"No such file or directory"*) ;;
    *) fail "Could not start the ARM test chroot: $output" ;;
esac
if [ "$mode" = check ]; then
    printf '[arm] WSL ARM registration needs repair: %s\n' "$output" >&2
    exit 10
fi

formats=/proc/sys/fs/binfmt_misc
entry=$formats/spike2-vpx-arm
if [ ! -e "$formats/register" ]; then
    mount -t binfmt_misc binfmt_misc "$formats" ||
        [ -e "$formats/register" ] || fail "WSL could not mount its binary format table."
fi
grep -qx enabled "$formats/status" || fail "The WSL binary format table is disabled."
# Use QEMU's ARM ELF match. F holds open the static interpreter so it is
# available from inside the Stern chroot. Leave other handlers alone.
magic='\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x28\x00'
mask='\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff'
if [ -e "$entry" ]; then
    printf '%s\n' -1 > "$entry"
fi
printf ':spike2-vpx-arm:M::%s:%s:%s:F\n' "$magic" "$mask" "$qemu" > "$formats/register" ||
    fail "WSL refused to register the SPIKE 2 ARM interpreter."
if ! output=$(ROOT="$scratch" _pad_guest_probe); then
    printf '%s\n' -1 > "$entry"
    fail "ARM execution still failed inside the test chroot: $output"
fi
echo "[arm] Restored WSL ARM execution and verified it inside the guest."

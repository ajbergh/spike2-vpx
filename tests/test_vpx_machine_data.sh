#!/bin/bash
# Run as root on Linux or WSL to exercise the private guest bind mount.
set -eu
helper=$(realpath "$(dirname "$0")/../tools/spike2_emu/vpx_machine_data.sh")
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
home=$scratch/home
old="$home/padslots/1/upper/data"
new="$home/padslots/2/upper/data"
mkdir -p "$scratch/slot1/data" "$scratch/slot2/data" \
    "$home/spike2root/data" "$old/nv/godzilla_le/LKRAM" "$new/nv/godzilla_le/LKRAM"
echo older > "$old/nv/godzilla_le/LKRAM/00000003"
touch -d '2 days ago' "$old/nv/godzilla_le/LKRAM/00000003"
echo legacy-score > "$new/nv/godzilla_le/LKRAM/00000004"
echo checksum > "$new/nv/godzilla_le/LKRAM/00000004.crc32"
truncate -s 65536 "$old/nvram-godzilla_le.bin"
printf broken > "$new/nvram-godzilla_le.bin"

unshare --mount --propagation private bash -es "$helper" "$scratch/slot1/data" "$home" <<'FIRST'
. "$1"
pad_vpx_mount_data "$2" "$3" godzilla_le
score="$2/nv/godzilla_le/LKRAM/00000004"
test "$(cat "$score")" = legacy-score
test "$(cat "$score.crc32")" = checksum
test ! -e "$2/nv/godzilla_le/LKRAM/00000003"
test "$(stat -c %s "$2/nvram-godzilla_le.bin")" = 65536
echo newly-saved-score > "$score"
# A second game of the same title must not have concurrent write access.
if bash -c '. "$1"; pad_vpx_mount_data "$2" "$3" godzilla_le' bash "$@"; then
    exit 1
fi
FIRST

test ! -e "$scratch/slot1/data/nv"
unshare --mount --propagation private bash -es "$helper" "$scratch/slot2/data" "$home" <<'SECOND'
. "$1"
pad_vpx_mount_data "$2" "$3" godzilla_le
test "$(cat "$2/nv/godzilla_le/LKRAM/00000004")" = newly-saved-score
SECOND

test "$(cat "$new/nv/godzilla_le/LKRAM/00000004")" = legacy-score
unshare --mount --propagation private bash -es "$helper" "$scratch/slot2/data" "$home" <<'OTHER_TITLE'
. "$1"
pad_vpx_mount_data "$2" "$3" deadpool_pro
test ! -e "$2/nv/godzilla_le"
OTHER_TITLE

#!/bin/bash
# The Stern firmware writes high scores to /data/nv/<title>/LKRAM and also
# uses a per-title EEPROM in /data. Bind a persistent /data for prepared VPX
# launches so both stores follow the title across rig slots.

pad_vpx_seed_data() (
    export LC_ALL=C TZ=UTC
    local parent=$1 home=$2 title=$3 stage legacy nv="" chip="" candidate
    local stamp latest_nv="" latest_chip=""
    local eeprom="nvram-$title.bin"
    stage=$(mktemp -d "$parent/.seed-$title.XXXXXXXX") || return 1
    trap 'rm -rf -- "$stage"' EXIT
    local base="$home/spike2root/data"
    if [ -d "$base" ]; then
        cp -a "$base/." "$stage/" || return 1
    fi

    # Keep a complete LKRAM tree from a single slot. Mixing generations and
    # CRC files from different slots can cause the firmware to reset its store.
    for legacy in "$base" "$home"/padslots/[0-9]*/upper/data; do
        candidate="$legacy/nv/$title"
        if [ -d "$candidate" ]; then
            if [ -d "$candidate/LKRAM" ]; then
                stamp=$(find "$candidate/LKRAM" -type f -printf '%TY-%Tm-%Td %TH:%TM:%TS\n' |
                    sort -r | head -n 1)
            else
                stamp=""
            fi
            if [ -n "$stamp" ] && [[ -z "$latest_nv" || "$stamp" > "$latest_nv" ]]; then
                nv=$candidate
                latest_nv=$stamp
            elif [ -z "$nv" ]; then
                nv=$candidate
            fi
        fi
        candidate="$legacy/$eeprom"
        if [ -f "$candidate" ] && [ "$(stat -c %s "$candidate")" = 65536 ]; then
            stamp=$(stat -c %y "$candidate")
            if [[ -z "$latest_chip" || "$stamp" > "$latest_chip" ]]; then
                chip=$candidate
                latest_chip=$stamp
            fi
        fi
    done
    if [ -n "$nv" ]; then
        mkdir -p "$stage/nv" || return 1
        rm -rf -- "$stage/nv/$title" || return 1
        cp -a "$nv" "$stage/nv/$title" || return 1
    fi
    if [ -n "$chip" ]; then
        cp -a "$chip" "$stage/$eeprom" || return 1
    elif [ -f "$stage/$eeprom" ]; then
        rm -f -- "$stage/$eeprom" || return 1
    fi
    mv -T -- "$stage" "$parent/$title"
)

pad_vpx_mount_data() {
    local target=$1 home=$2 title=$3 parent="$2/spike2-vpx-machine-data"
    case "$title" in
        ""|*[!a-zA-Z0-9_-]*) echo "[save] invalid Stern title: $title" >&2; return 1 ;;
    esac
    mkdir -p "$parent" || return 1
    # Keep the descriptor open in this namespace across the guest exec.
    exec {PAD_VPX_SAVE_LOCK}>"$parent/$title.lock" || return 1
    if ! flock -n "$PAD_VPX_SAVE_LOCK"; then
        echo "[save] $title already has a running Stern session using its saved data." >&2
        return 1
    fi
    if [ ! -d "$parent/$title" ] && ! pad_vpx_seed_data "$parent" "$home" "$title"; then
        echo "[save] could not initialize saved data for $title." >&2
        return 1
    fi
    if ! mount --bind "$parent/$title" "$target"; then
        echo "[save] could not mount saved data for $title." >&2
        return 1
    fi
    echo "[save] using persistent Stern machine data for $title"
}

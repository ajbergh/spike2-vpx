#!/bin/bash
# Build the read-only card's guest filesystem before taking a VPX slot.
set -eu
root=$1
card=$2
exec 9>"$HOME/.spike2-vpx-root.lock"
flock 9
export PAD_SLOT=0 PAD_HOME="$HOME" PAD_ROOT="$root"
source "$(dirname "$0")/padpath.sh"
if [ -z "$(pad_guest_missing)" ]; then
    exit 0
fi
if [ -z "$card" ]; then
    echo "No SPIKE 2 guest filesystem is available. Launch once with -Card to prepare it."
    exit 1
fi
exec bash "$RIG/rootfs.sh" "$card"

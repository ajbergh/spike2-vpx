# SPIKE 2 runtime

The Windows launcher runs this Linux rig inside the private `SPIKE2-VPX`
WSL 2 distribution. `rootfs.sh` reads the guest filesystem from the player's
card image. `watch.sh` builds and starts the emulator and its LCD and audio
helpers. `cardmount.sh` handles read-only card mounts and the optional cache.
`riglock.sh` owns run slots, and `padlink.py` transports inputs and outputs
between the running game and VPX.

See the [VPX kit](vpx/README.md) for table preparation and the
[project README](../../README.md) for setup and launch.

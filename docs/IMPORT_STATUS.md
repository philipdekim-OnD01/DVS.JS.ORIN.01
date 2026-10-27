# Source Import Status — 2026-10-27

Source: HappyTanuki/nrv-jetson-dvs @ e5ec37936732c3b365b4da995a61cb23d0434410

This is a **partial private source copy**, not a validated build or an official fork. 120/132 upstream file blobs were copied byte-for-byte and executable modes restored.

## Missing upstream paths (12)
- app/dvs_streamer/CMakePresets.json
- app/dvs_streamer/nrv-dvs-streamer.service
- app/dvs_streamer/settings/Delta_10_2200FPS_RAW2.txt
- drivers/dvs/include/tegra-cam/camera_common_sensor_ops.h
- drivers/dvs/scripts/compdb.py
- sdk/CMakeLists.txt
- sdk/CMakePresets.json
- sdk/include/nrv_dvs/stream.h
- sdk/src/nrv_dvs/device.cpp
- sdk/tests/CMakeLists.txt
- sdk/tests/external/compressor_test.cpp
- sdk/tests/internal/resources/raw_max_sensitivity.bin (8 MiB binary test fixture)

Eleven textual paths could not be transferred through the connected service's safety checks. The 8 MiB binary test fixture was excluded from this transport. **Important build files are missing. Do not assume `./build.sh all` will work from this repository until the source is complete.** The copied source was not built or receive-tested.

Refer to original private repository and its owner for authoritative files, permissions, and support. Keep this repository private and do not publicly redistribute without permission.

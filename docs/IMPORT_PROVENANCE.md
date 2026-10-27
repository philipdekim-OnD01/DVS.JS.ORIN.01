# Original Source & Import Record

This repository is a private working copy for Jetson Orin S5KRC1S DVS driver, SDK and AI development.

- Original upstream: https://github.com/HappyTanuki/nrv-jetson-dvs
- Source reference: `e5ec37936732c3b365b4da995a61cb23d0434410` (2026-10-27 import)
- Original owner: HappyTanuki. This repository is NOT a GitHub fork and does not preserve upstream Git commit history.
- The original code had no root LICENSE file at the time of review. Collaboration or read/write permission does not alone establish public redistribution rights. Keep this repository private until licensing and rehosting permissions are confirmed with the upstream owner.
- Do not commit Samsung confidential documents, passwords, camera calibration secrets, or personally identifying recordings.
- The 8 MiB raw test fixture `sdk/tests/internal/resources/raw_max_sensitivity.bin` is not included in this copy; it should be obtained from the upstream authorized repository if tests require it.
- The device driver is tied to Jetson Linux R39.2.1 / kernel 6.8.12-1021-tegra, P3768/P3767-0005 board configuration; do not install/flash on unmatched devices.
- Separate student AI modules should consume validated event data or an adapter; kernel code remains a maintained base.

# Source Import Status — 2026-10-29

## Baseline
- Original upstream (private): https://github.com/HappyTanuki/nrv-jetson-dvs
- Pinned original commit: `e5ec37936732c3b365b4da995a61cb23d0434410`
- Destination: `philipdekim-OnD01/DVS.JS.ORIN.01` (private)
- This is a private file copy, **not** a GitHub fork. Original Git history is not retained.

## Verified source parity
- Upstream original: **132 tracked files**
- Exactly matching blobs and executable modes in this repository: **131 files**
- Missing: **1 binary test fixture (8,388,608 bytes)**

```
sdk/tests/internal/resources/raw_max_sensitivity.bin
```

All upstream C/C++ source, driver headers, CMake build files, Jetson device tree, setfiles, scripts, and decoder implementations at the pinned commit are present and content-identical. Eleven previously missing text files were restored on 2026-10-29. The source-file and executable-mode comparison was performed against GitHub tree entries using Git blob SHA.

The missing fixture is binary test data, not a driver, decoder, SDK or build-script source. It may be required by some tests; obtain it from the authorized upstream clone (without altering its contents). The authenticated file connector returned an empty content body for that binary file, so it was **not** copied. Never create an empty stand-in.

### Restore the remaining binary from authenticated local clones

From a PC or Jetson shell where both private repositories can be authenticated:

```bash
git clone https://github.com/HappyTanuki/nrv-jetson-dvs.git upstream-nrv-dvs
git clone https://github.com/philipdekim-OnD01/DVS.JS.ORIN.01.git DVS.JS.ORIN.01
cd DVS.JS.ORIN.01
mkdir -p sdk/tests/internal/resources
git -C ../upstream-nrv-dvs show e5ec37936732c3b365b4da995a61cb23d0434410:sdk/tests/internal/resources/raw_max_sensitivity.bin > sdk/tests/internal/resources/raw_max_sensitivity.bin
git hash-object sdk/tests/internal/resources/raw_max_sensitivity.bin
# Expected blob SHA: be9578c7c35a23ce73356d8ea539b1dc7f31ad51
git add sdk/tests/internal/resources/raw_max_sensitivity.bin
git commit -m "Restore upstream binary decoder test fixture"
git push origin main
```

## Installation guardrails
- File presence and matching SHAs do **not** prove the Jetson build, boot, I2C, CSI or sensor reception works.
- Baseline targets Jetson Linux R39.2.1, kernel `6.8.12-1021-tegra`, Orin Nano Super P3767-0005 / P3768. Verify the actual device before module installation.
- `./build.sh all` only builds. It does not install the driver or device-tree overlay.
- Follow `scripts/boot-setup.sh check` and a dry run before a carefully planned install/reboot.
- Keep this repository private; confirm rehosting and redistribution terms with the original source owner. Do not commit confidential sensor documentation or sensitive captures.

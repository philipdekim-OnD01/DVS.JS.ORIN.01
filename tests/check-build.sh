#!/usr/bin/env bash
# Offline acceptance checks for a tree built with ./build.sh all (run ON the Jetson).
# Read-only towards the system: reads the stock base DTB and stock module metadata,
# writes only under build/tests/. NOT loaded, NOT booted, NOT receive-tested.
#
# Environment:
#   BASE_DTB  stock base DTB (default: P3768+P3767-0005 Super from /boot/dtb)
#   REF_DTB   previously validated merged DTB to compare camera nodes against
#             (default: /boot/dtb/nrv-power-h128-cam1-20261008.dtb; skipped if
#             absent unless REQUIRE_REF_DTB=1)
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
T="$ROOT/build/tests"
BASE_DTB=${BASE_DTB:-/boot/dtb/kernel_tegra234-p3768-0000+p3767-0005-nv-super.dtb}
REF_DTB=${REF_DTB:-/boot/dtb/nrv-power-h128-cam1-20261008.dtb}
mkdir -p "$T"
FAILED=()
step() { # step <name> <cmd...> ; logs to build/tests/<name>.log
  local name=$1; shift
  echo "=== $name"
  if "$@" > "$T/$name.log" 2>&1; then echo "PASS $name"; else echo "FAIL $name (see build/tests/$name.log)"; FAILED+=("$name"); fi
  sed 's/^/    /' "$T/$name.log" | tail -n 60
}

# AC3: modules (NVIDIA stack stays stock: no patch, only nrv_dvs.ko is built).
ac3_modules() { python3 "$ROOT/tests/checks.py" modules "$ROOT"; }
# AC4: overlay.
ac4_overlay() {
  [[ -f $ROOT/build/cam1.dtbo ]] || return 1
  grep -q '^/plugin/;' "$ROOT/device-tree/p3768-cam1/cam1.dts" || return 1
  dtc -@ -I dts -O dtb -o "$T/cam1.dtbo.rebuilt" "$ROOT/device-tree/p3768-cam1/cam1.dts" 2>/dev/null || return 1
  cmp "$T/cam1.dtbo.rebuilt" "$ROOT/build/cam1.dtbo" || return 1
  rm -f -- "$T/cam1.merged.dtb"
  fdtoverlay -i "$BASE_DTB" -o "$T/cam1.merged.dtb" "$ROOT/build/cam1.dtbo" || return 1
  local v
  v=$(fdtget -t s "$T/cam1.merged.dtb" /bus@0/i2c@3180000/nrv_dvs@10 compatible) || return 1
  echo "merged nrv_dvs@10 compatible = $v"
  echo "sha256 base=$(sha256sum < "$BASE_DTB" | cut -c1-64) dtbo=$(sha256sum < "$ROOT/build/cam1.dtbo" | cut -c1-64)"
}
ac4_compare() {
  if [[ ! -f $REF_DTB ]]; then
    echo "reference DTB $REF_DTB not present"; [[ ${REQUIRE_REF_DTB:-0} != 1 ]]; return
  fi
  python3 "$ROOT/tests/compare-dt.py" "$T/cam1.merged.dtb" "$REF_DTB"
}
# AC5: app.
ac5_app() {
  file "$ROOT/build/dvs_live" | tee /dev/stderr | grep -q 'ELF 64-bit' || return 1
  for f in build.sh dvs tests/check-build.sh scripts/boot-setup.sh; do bash -n "$ROOT/$f" || return 1; done
  python3 "$ROOT/tests/checks.py" app "$ROOT"
}
# AC1 (static part) + AC7: standalone, no forbidden actions.
ac7_scan() {
  python3 "$ROOT/tests/checks.py" scan "$ROOT" || return 1
  if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    local bad
    bad=$(git -C "$ROOT" ls-files | grep -E '^(\.cache|build)/|\.(o|ko|dtbo|dtb|cmd)$|(^|/)conftest\.h$|^app/dvs_live$' || true)
    echo "tracked build products/NVIDIA files: ${bad:-none}"; [[ -z $bad ]] || return 1
  fi
}

step ac3-modules ac3_modules
step ac4-overlay ac4_overlay
step ac4-compare-dt ac4_compare
step ac5-app ac5_app
step ac7-scan ac7_scan
echo "=== verification boundary: NOT loaded, NOT booted, NOT receive-tested."
if (( ${#FAILED[@]} )); then echo "check-build: FAILED: ${FAILED[*]}"; exit 1; fi
echo "check-build: all checks passed"

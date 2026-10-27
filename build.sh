#!/usr/bin/env bash
# nrv-dvs-jetson build script (runs ON the Jetson; build + offline artifacts only).
#
#   ./build.sh fetch    download + hash-verify NVIDIA R39.2.1 sources into .cache/
#                       (only NVIDIA's conftest scripts are used; NVIDIA modules
#                       stay stock)
#   ./build.sh drivers  build build/nrv_dvs.ko
#                       (via drivers/dvs/Makefile BUILD_DIR=build/dvs)
#                       (+ drivers/dvs/build/compile_commands.json for clangd)
#   ./build.sh compdb   regenerate compile_commands.json from the last build
#   ./build.sh dt       build build/cam1.dtbo (device-tree overlay)
#   ./build.sh app      build the local SDK and RAW2-capable dvs_streamer (Release)
#   ./build.sh all      fetch + drivers + dt + app
#   ./build.sh clean    remove build/ (keeps .cache/)
#
# This script never installs anything: it does not write /boot, /lib/modules or
# /etc, does not load or unload kernel modules, does not touch the bootloader.
#
# Environment overrides:
#   NRV_PUBLIC_SOURCES_ARCHIVE=/path/public_sources.tbz2  use a local copy of
#       the official archive instead of downloading (it is hash-verified too).
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
BSP_DIR="$ROOT/bsp/r39.2.1"
# shellcheck source=bsp/r39.2.1/manifest.env
source "$BSP_DIR/manifest.env"
CACHE="$ROOT/.cache"
BUILD="$ROOT/build"
LOGS="$BUILD/logs"
OUTER="$CACHE/public_sources.tbz2"
INNER="$CACHE/kernel_oot_modules_src.tbz2"
PRISTINE="$CACHE/pristine/nvidia-oot"
COMPAT="$BUILD/compat"
K="/lib/modules/$KERNEL_RELEASE/build"
NV_PUBLIC=/usr/src/nvidia/nvidia-public
NVTREE="$ROOT/tests/nvtree.py"
JOBS=${JOBS:-$(nproc)}

log() { printf '[build.sh] %s\n' "$*" >&2; }
die() { printf '[build.sh] ERROR: %s\n' "$*" >&2; exit 1; }

sha_ok() { # sha_ok <expected> <file>
  printf '%s  %s\n' "$1" "$2" | sha256sum --check --status -
}

run_logged() { # run_logged <logname> <cmd...>
  mkdir -p "$LOGS"
  log "running: ${*:2}  (log: build/logs/$1.log)"
  "${@:2}" 2>&1 | tee "$LOGS/$1.log"
}

preflight() {
  local c
  for c in curl tar bzip2 sha256sum make gcc python3 dtc modinfo; do
    command -v "$c" >/dev/null || die "required command not found: $c"
  done
  [[ $(uname -m) == aarch64 ]] || die "must run on the Jetson (aarch64), got $(uname -m)"
  [[ $(uname -r) == "$KERNEL_RELEASE" ]] || die "running kernel $(uname -r) != pinned $KERNEL_RELEASE"
  [[ -d $K ]] || die "kernel build dir missing: $K"
  sha_ok "$NVIDIA_SYMVERS_SHA256" "$NV_PUBLIC/Module.symvers" \
    || die "$NV_PUBLIC/Module.symvers does not match manifest pin (BSP mismatch)"
  sha_ok "$KERNEL_SYMVERS_SHA256" "$K/Module.symvers" \
    || die "$K/Module.symvers does not match manifest pin (kernel mismatch)"
  local v
  v=$(dpkg-query -W -f='${Version}' nvidia-l4t-core 2>/dev/null || true)
  [[ $v == "$BSP_PACKAGE_VERSION" ]] || die "nvidia-l4t-core version '$v' != pinned $BSP_PACKAGE_VERSION"
}

cmd_fetch() {
  mkdir -p "$CACHE"
  if [[ -e $OUTER ]]; then
    sha_ok "$PUBLIC_SOURCES_SHA256" "$OUTER" \
      || die "cached $OUTER fails the SHA-256 pin; remove it manually and re-run"
    log "cached public_sources.tbz2 verified"
  else
    local part="$OUTER.part"
    rm -f -- "$part"
    if [[ -n ${NRV_PUBLIC_SOURCES_ARCHIVE:-} ]]; then
      log "using local archive $NRV_PUBLIC_SOURCES_ARCHIVE"
      cp -- "$NRV_PUBLIC_SOURCES_ARCHIVE" "$part"
    else
      log "downloading $SOURCE_URL"
      mkdir -p "$LOGS"
      curl --fail --location --retry 3 --show-error --silent \
        --write-out 'HTTP %{http_code} url_effective=%{url_effective} size=%{size_download}\n' \
        -o "$part" "$SOURCE_URL" | tee "$LOGS/fetch-http.log"
    fi
    sha_ok "$PUBLIC_SOURCES_SHA256" "$part" || { rm -f -- "$part"; die "public_sources.tbz2 SHA-256 mismatch"; }
    mv -- "$part" "$OUTER"
    log "public_sources.tbz2 SHA-256 verified: $PUBLIC_SOURCES_SHA256"
  fi
  if [[ -e $INNER ]]; then
    sha_ok "$OOT_SOURCES_SHA256" "$INNER" || die "cached $INNER fails the pin; remove it manually"
  else
    tar -xOjf "$OUTER" "$OOT_SOURCES_MEMBER" > "$INNER.part"
    sha_ok "$OOT_SOURCES_SHA256" "$INNER.part" || { rm -f -- "$INNER.part"; die "kernel_oot_modules_src.tbz2 SHA-256 mismatch"; }
    mv -- "$INNER.part" "$INNER"
  fi
  log "kernel_oot_modules_src.tbz2 SHA-256 verified: $OOT_SOURCES_SHA256"
  if [[ -d $PRISTINE ]]; then
    [[ $(cat "$CACHE/pristine/.complete" 2>/dev/null) == "$OOT_SOURCES_SHA256" ]] \
      || die "$PRISTINE exists but is not a completed extraction; remove .cache/pristine manually"
    log "pristine nvidia-oot present"
    return 0
  fi
  python3 "$NVTREE" safe-tar "$INNER" nvidia-oot >/dev/null || die "unsafe or empty tar members"
  local tmp
  tmp=$(mktemp -d "$CACHE/pristine.tmp.XXXXXX")
  tar -xjf "$INNER" -C "$tmp" nvidia-oot
  printf '%s\n' "$OOT_SOURCES_SHA256" > "$tmp/.complete"
  [[ ! -e $CACHE/pristine ]] || die "$CACHE/pristine appeared concurrently"
  mv -- "$tmp" "$CACHE/pristine"
  log "pristine nvidia-oot extracted to .cache/pristine/nvidia-oot"
}

cmd_drivers() {
  [[ -f $CACHE/pristine/.complete ]] || die "pristine sources missing; run ./build.sh fetch"
  local C="$COMPAT/nvidia"
  mkdir -p "$C" "$BUILD/tmp"
  export TMPDIR="$BUILD/tmp"
  # Generate NVIDIA conftest compatibility headers OUTSIDE the NVIDIA tree with
  # NVIDIA's own (unmodified) conftest Makefile. IGNORE_CC_MISMATCH=1
  # acknowledges installed GCC 13.3 vs kernel GCC 13.2 (warning, not ABI proof).
  cp "$PRISTINE/scripts/conftest/Makefile" "$PRISTINE/scripts/conftest/conftest.sh" "$PRISTINE/scripts/conftest/conftest.h" "$C/"
  run_logged conftest env IGNORE_CC_MISMATCH=1 make -C "$C" src="$C" obj="$C" ARCH=arm64 CC=gcc \
    NV_KERNEL_SOURCES="$K" NV_KERNEL_OUTPUT="$K" -j"$JOBS"
  # Sensor driver: drivers/dvs/Makefile declares its own dependencies (kernel
  # tree, nvidia-public, build/compat). build.sh builds it into build/dvs; a plain
  # `make` in drivers/dvs (developer build) uses drivers/dvs/build instead.
  run_logged nrv_dvs make -C "$ROOT/drivers/dvs" BUILD_DIR="$BUILD/dvs" -j"$JOBS"
  cp "$BUILD/dvs/nrv_dvs.ko" "$BUILD/nrv_dvs.ko"
  log "built build/nrv_dvs.ko (NOT installed, NOT loaded)"
  cmd_compdb
}

# compile_commands.json for editors (VS Code C/C++ / clangd), from the kernel's own
# scripts/clang-tools/gen_compile_commands.py over the .cmd files of the module
# build. The DVS driver is compiled in build/dvs from per-file symlinks;
# those entries are rewritten to the real files in drivers/dvs.
# Output: drivers/dvs/build/compile_commands.json (git-ignored; device paths),
# found by clangd through drivers/dvs/.clangd (CompilationDatabase).
cmd_compdb() {
  local gen="$K/scripts/clang-tools/gen_compile_commands.py"
  [[ -f $gen ]] || die "missing $gen (kernel headers package incomplete)"
  [[ -f $BUILD/dvs/nrv_dvs.ko ]] || die "no DVS build found; run ./build.sh drivers first"
  mkdir -p "$BUILD/compdb" "$ROOT/drivers/dvs/build"
  python3 "$gen" -d "$K" -o "$BUILD/compdb/dvs.json" "$BUILD/dvs"
  python3 - "$ROOT" "$BUILD/compdb/dvs.json" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1])
out = []
for path in sys.argv[2:]:
    for e in json.load(open(path)):
        f = pathlib.Path(e['file'])
        if f.is_symlink():  # build/dvs/src/x.c -> drivers/dvs/src/x.c
            real = str(f.resolve())
            e['command'] = e['command'].replace(' ' + e['file'], ' ' + real)
            e['file'] = real
        out.append(e)
missing = [e['file'] for e in out if not pathlib.Path(e['file']).is_file()]
if missing:
    sys.exit('compile_commands: missing source files: %s' % missing[:3])
(root / 'drivers/dvs/build/compile_commands.json').write_text(json.dumps(out, indent=1) + '\n')
print('[build.sh] wrote drivers/dvs/build/compile_commands.json (%d entries)' % len(out), file=sys.stderr)
PY
}

cmd_dt() {
  mkdir -p "$BUILD"
  run_logged dtc dtc -@ -I dts -O dtb -o "$BUILD/cam1.dtbo" "$ROOT/device-tree/p3768-cam1/cam1.dts"
  log "built build/cam1.dtbo (overlay; NOT installed)"
}

cmd_app() {
  mkdir -p "$BUILD"
  (cd "$ROOT/app/dvs_streamer" &&
    run_logged app-configure cmake --preset GCC-arm64 -DNRV_DVS_BUILD_TESTS=OFF &&
    run_logged app cmake --build --preset Release-GCC-arm64 \
      --target dvs_streamer dvs_streamer_runtime nrv_dvs_dist --parallel 2)
  log "built app/dvs_streamer/build/GCC-arm64/Release (local SDK, RAW2 default)"
}

cmd_clean() {
  local target
  [[ -e $BUILD || -L $BUILD ]] || { log "nothing to clean"; return 0; }
  [[ ! -L $BUILD ]] || die "build/ is a symlink; refusing"
  target=$(cd -- "$BUILD" && pwd -P)
  [[ $target == "$ROOT/build" && $ROOT != / && -n $ROOT ]] || die "unexpected build path $target"
  rm -rf -- "$target"
  if [[ -L $ROOT/app/dvs_live ]]; then rm -f -- "$ROOT/app/dvs_live"; fi
  rm -f -- "$ROOT/drivers/dvs/build/compile_commands.json"
  [[ -d $ROOT/drivers/dvs/build ]] && rmdir -- "$ROOT/drivers/dvs/build" 2>/dev/null || true
  log "removed build/ (kept .cache/)"
}

main() {
  local action=${1:-}
  case $action in
    fetch) preflight; cmd_fetch ;;
    drivers) preflight; cmd_drivers ;;
    compdb) cmd_compdb ;;
    dt) cmd_dt ;;
    app) cmd_app ;;
    all) preflight; cmd_fetch; cmd_drivers; cmd_dt; cmd_app
         log "all done. NOT installed, NOT loaded, NOT booted, NOT receive-tested." ;;
    clean) cmd_clean ;;
    *) echo "usage: $0 fetch|drivers|compdb|dt|app|all|clean" >&2; exit 2 ;;
  esac
}
main "$@"

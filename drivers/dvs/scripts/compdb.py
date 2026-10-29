#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""compile_commands.json for the DVS driver (used by `make` in drivers/dvs).

usage: compdb.py KDIR BUILD_DIR DRIVER_DIR OUT

1. Runs the kernel's scripts/clang-tools/gen_compile_commands.py over the
   .cmd files in BUILD_DIR (only objects that compiled successfully have one).
2. Rewrites symlinked sources (BUILD_DIR/src/x.c) and -I BUILD_DIR/include* to
   the real files/dirs in DRIVER_DIR.
3. Adds an entry for every DRIVER_DIR/src/**/*.c that has none yet (e.g. a file
   that currently fails to compile), cloned from another src entry: all files of
   the module share the same flags, so clangd still finds the include paths.
"""
import json
import pathlib
import subprocess
import sys
import tempfile


def main():
    kdir, build, drv, out = (pathlib.Path(a) for a in sys.argv[1:5])
    gen = kdir / 'scripts/clang-tools/gen_compile_commands.py'
    with tempfile.NamedTemporaryFile(suffix='.json') as tmp:
        subprocess.run([sys.executable, str(gen), '-d', str(kdir), '-o', tmp.name, str(build)],
                       check=True)
        entries = json.load(open(tmp.name))

    build_inc, drv_inc = f'-I{build.resolve()}/include', f'-I{drv.resolve()}/include'
    for e in entries:
        f = pathlib.Path(e['file'])
        if f.is_symlink():
            real = str(f.resolve())
            e['command'] = e['command'].replace(' ' + e['file'], ' ' + real)
            e['file'] = real
        # BUILD_DIR/include mirrors DRIVER_DIR/include (symlinks): point clangd
        # at the real headers so go-to-definition opens the repo files.
        e['command'] = e['command'].replace(build_inc, drv_inc)

    src_root = (drv / 'src').resolve()
    have = {pathlib.Path(e['file']) for e in entries}
    template = next((e for e in entries if pathlib.Path(e['file']).is_relative_to(src_root)), None)
    added = 0
    if template:
        t_rel = pathlib.Path(template['file']).relative_to(src_root).with_suffix('')
        for c in sorted(src_root.rglob('*.c')):
            if c in have:
                continue
            rel = c.relative_to(src_root).with_suffix('')
            cmd = template['command'].replace(template['file'], str(c))
            cmd = cmd.replace(f'src/{t_rel}.o', f'src/{rel}.o')
            cmd = cmd.replace(f'src/{t_rel.parent}/.{t_rel.name}.o.d' if str(t_rel.parent) != '.'
                              else f'src/.{t_rel.name}.o.d',
                              f'src/{rel.parent}/.{rel.name}.o.d' if str(rel.parent) != '.'
                              else f'src/.{rel.name}.o.d')
            entries.append({'command': cmd, 'directory': template['directory'], 'file': str(c)})
            added += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=1) + '\n')
    note = f', {added} cloned for sources without a successful compile' if added else ''
    if not template:
        note = ' (no src/ file compiled yet: no DVS entries)'
    print(f'nrv_dvs: wrote {out} ({len(entries)} entries{note})', file=sys.stderr)


if __name__ == '__main__':
    main()

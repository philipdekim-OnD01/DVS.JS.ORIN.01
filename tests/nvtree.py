#!/usr/bin/env python3
"""Offline helpers for nrv-dvs-jetson (standard library only).

Subcommands
  safe-tar ARCHIVE PREFIX
      Validate that every member under PREFIX/ in a (bz2) tar archive has a
      relative, non-escaping path and link target. Exit 0 if safe.
"""
import json
import os
import sys
import tarfile
from pathlib import Path


def safe_tar(archive, prefix):
    bad = []
    count = 0
    with tarfile.open(archive, 'r:*') as tf:
        for m in tf:
            name = m.name
            if not (name == prefix or name.startswith(prefix + '/')):
                continue
            count += 1
            parts = Path(name).parts
            if name.startswith('/') or '..' in parts:
                bad.append(('path', name))
            if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                bad.append(('type', name))
            if m.issym():
                # Must resolve inside the extraction directory. (NVIDIA ships one
                # link to the sibling nvethernetrm/ tree, which stays inside it.)
                target = os.path.normpath(os.path.join(os.path.dirname(name), m.linkname))
                if m.linkname.startswith('/') or target == '..' or target.startswith('../'):
                    bad.append(('symlink', name, m.linkname))
            if m.islnk() and (m.linkname.startswith('/') or '..' in Path(m.linkname).parts):
                bad.append(('hardlink', name, m.linkname))
    print(json.dumps({'archive': str(archive), 'prefix': prefix, 'members': count, 'unsafe': bad}))
    if count == 0 or bad:
        return 1
    return 0


def main(argv):
    if len(argv) >= 3 and argv[0] == 'safe-tar':
        return safe_tar(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

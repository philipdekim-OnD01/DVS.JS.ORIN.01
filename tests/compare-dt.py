#!/usr/bin/env python3
"""Semantic comparison of camera DT nodes between two flattened DTBs.

usage: compare-dt.py BUILT_MERGED.dtb REFERENCE.dtb

Parses both binary FDTs (standard library only), collects every property of
the sensor, VI, NVCSI and tegra-camera-platform subtrees, normalizes phandles
(remote-endpoint, reset-gpios -> target node path) and ignores numeric phandle
allocation. Prints ALL differences as JSON and exits 0 only when the sole
differences are the two intentional sensor-node changes in EXPECTED plus the
new node mode1 (absent in the reference; must equal the built mode0 except the
VI buffer size active_w/active_h/line_length = 2880/61/2880):
the renamed compatible
(bring-up "nrv,s5krc1s-cam1-test" -> "nrv,fx20-sensor-board", "samsung,s5krc1s")
and mode0 pix_clk_hz "500000000" -> "1250000000" (pixel_clock 1.25e9, DPHY
mipi_clock 1.25 GHz for 4 lanes x 8 bit). The NVCSI mipi_clock equals what the
removed nrv_dvs.ko runtime override set; the VI bind-time pixel rate used for
tegra_camera_platform clock/ISO-BW requests changes from 500e6 to 1.25e9
(not runtime-tested, docs/OPEN-ISSUES.md issue 3).
"""
import json
import struct
import sys
from pathlib import Path

SENSOR = '/bus@0/i2c@3180000/nrv_dvs@10'
MODE1 = SENSOR + '/mode1'
MODE1_SIZE = {'active_w': b'2880\0', 'active_h': b'61\0', 'line_length': b'2880\0'}
ROOTS = [SENSOR, '/tegra-capture-vi', '/bus@0/host1x@13e00000/nvcsi@15a00000',
         '/tegra-camera-platform']
EXPECTED = [
    {'node': SENSOR, 'property': 'compatible',
     'built': (b'nrv,fx20-sensor-board\0samsung,s5krc1s\0').hex(),
     'reference': (b'nrv,s5krc1s-cam1-test\0').hex()},
    {'node': SENSOR + '/mode0', 'property': 'pix_clk_hz',
     'built': (b'1250000000\0').hex(), 'reference': (b'500000000\0').hex()},
]


def parse(path):
    b = Path(path).read_bytes()
    h = struct.unpack_from('>10I', b)
    if h[0] != 0xd00dfeed:
        raise SystemExit(f'{path}: not a flattened device tree')
    pos = h[2]
    strings = b[h[3]:h[3] + h[8]]
    stack, nodes = [], {}
    while True:
        tag = struct.unpack_from('>I', b, pos)[0]
        pos += 4
        if tag == 1:  # FDT_BEGIN_NODE
            end = b.index(0, pos)
            stack.append(b[pos:end].decode())
            pos = (end + 4) & ~3
            nodes['/'.join(stack) or '/'] = {}
        elif tag == 2:  # FDT_END_NODE
            stack.pop()
        elif tag == 3:  # FDT_PROP
            size, no = struct.unpack_from('>II', b, pos)
            pos += 8
            name = strings[no:strings.index(0, no)].decode()
            nodes['/'.join(stack) or '/'][name] = b[pos:pos + size]
            pos = (pos + size + 3) & ~3
        elif tag == 4:  # FDT_NOP
            pass
        elif tag == 9:  # FDT_END
            break
        else:
            raise ValueError(tag)
    return nodes


def normalized(nodes, label):
    for r in ROOTS:
        if r not in nodes:
            raise SystemExit(f'{label}: required node {r} missing')
    handles = {int.from_bytes(v, 'big'): path for path, props in nodes.items()
               for k, v in props.items() if k in ('phandle', 'linux,phandle')}
    result = {}
    for path, props in nodes.items():
        if not any(path == r or path.startswith(r + '/') for r in ROOTS):
            continue
        out = {}
        for k, v in props.items():
            if k in ('phandle', 'linux,phandle'):
                continue
            if k in ('remote-endpoint', 'reset-gpios'):
                out[k] = [handles.get(int.from_bytes(v[:4], 'big'), 'UNRESOLVED:' + v[:4].hex()),
                          v[4:].hex()]
            else:
                out[k] = v.hex()
        result[path] = out
    return result


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    a = normalized(parse(argv[0]), 'built')
    b = normalized(parse(argv[1]), 'reference')
    differences = []
    for path in sorted(set(a) | set(b)):
        for prop in sorted(set(a.get(path, {})) | set(b.get(path, {}))):
            x = a.get(path, {}).get(prop)
            y = b.get(path, {}).get(prop)
            if x != y:
                differences.append({'node': path, 'property': prop, 'built': x, 'reference': y})
    differences = [d for d in differences if d['node'] != MODE1]
    mode1 = {**a.get(SENSOR + '/mode0', {}), **{k: v.hex() for k, v in MODE1_SIZE.items()}}
    mode1_ok = MODE1 not in b and a.get(MODE1) == mode1
    unresolved = [(p, k) for side in (a, b) for p, props in side.items()
                  for k, v in props.items() if isinstance(v, list) and str(v[0]).startswith('UNRESOLVED')]
    summary = {'nodes_compared_built': len(a), 'nodes_compared_reference': len(b),
               'properties_compared': sum(len(v) for v in a.values()),
               'unresolved_phandles': unresolved, 'differences': differences,
               'mode1_equals_mode0_except_size': mode1_ok}
    print(json.dumps(summary, indent=2))
    if unresolved or differences != EXPECTED or not mode1_ok:
        print('FAIL: unexpected DT difference', file=sys.stderr)
        return 1
    print('PASS: only differences are the renamed compatible on the sensor node, '
          'mode0 pix_clk_hz 500000000 -> 1250000000 and the new mode1 (mode0 with 2880x61)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

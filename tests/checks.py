#!/usr/bin/env python3
"""Offline acceptance checks for nrv-dvs-jetson (standard library only).

usage: checks.py modules|app|scan ROOT
Nothing here loads modules, starts the receiver or touches /dev/video*.
"""
import ast
import hashlib
import json
import platform
import re
import struct
import subprocess
import sys
from pathlib import Path

FAILS = []


def check(cond, msg):
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# Every module parameter nrv_dvs.ko ever had: the six former settings (now the
# nrv_register_writes V4L2 control + DT receiver timing) and the earlier
# test-only ones. None may come back.
REMOVED_PARAMS = ['raw8_output', 'phy_rate_mbps', 'rx_rate_override', 'tx_clock_mode',
                  'rx_settle', 'rx_polarity']
TEST_ONLY_PARAMS = ['scope_stream', 'free_run', 'public_mode', 'phase_probe', 'readback']
REMOVED_SYMBOLS = ['active_sensor', 'update_receiver_rate', 'dvs_cam_attach', 'dvs_cam_detach']
OLD_CAMERA_PARAMS = ['nrv_cam1_match_mode', 'nrv_tpg_type31', 'nrv_tpg_byte_override']
# nrv_register_writes ABI (drivers/dvs/include/tegra-cam/register_control.h).
CID = 0x00980900 + 0x2000
# Receiver timing in cam1.dts mode0 (pixel_clock = mipi_clock = 1.25e9). The
# NVCSI CIL values (mipi_clock, settle, polarity) must equal what the removed
# runtime override (2500 Mbps) set at stream start. The VI bind-time pixel rate
# is NOT equal (500e6 in the observed baseline reload, now 1.25e9); see
# docs/OPEN-ISSUES.md issue 3.
DT_MODE0 = {'num_lanes': '4', 'phy_mode': 'DPHY', 'csi_pixel_bit_depth': '8',
            'pix_clk_hz': '1250000000', 'cil_settletime': '0', 'lane_polarity': '0'}


def c_function(src, name):
    """Body of a C function definition 'name(' (brace matched, comments kept)."""
    m = re.search(r'^[^\n;]*\b' + re.escape(name) + r'\([^;{]*\)\s*\{', src, re.M)
    if not m:
        return ''
    depth, i = 0, m.end() - 1
    for j in range(i, len(src)):
        depth += {'{': 1, '}': -1}.get(src[j], 0)
        if depth == 0:
            return src[m.start():j + 1]
    return ''


def strip_c_comments(s):
    return re.sub(r'/\*.*?\*/|//[^\n]*', '', s, flags=re.S)


def preset_entries(root):
    out = []
    for line in (root / 'config/Delta_10_2000FPS.txt').read_text().splitlines():
        m = re.match(r'\s*20:([0-9a-fA-F]{4})=([0-9a-fA-F]{2})\b', line)
        if m:
            out.append((int(m[1], 16), int(m[2], 16)))
    return out


def old_default_sequence(setfile):
    """I2C writes of the removed code at its validated defaults (phy_rate_mbps=1000,
    tx_clock_mode=2, raw8_output=N): reset, 0100=00, setfile with the 750/2304 PLL
    branches off and 0x3915[5:4] replaced by 2, no DI write. Its final DI readback
    (0x31) is left out: set_mode() no longer reads it."""
    seq = [('w', 0x0100, 0x00)]
    for a, v in setfile:
        if a == 0x3915:
            v = (v & ~0x30) | (2 << 4)
        seq.append(('w', a, v))
    return seq


def new_sequence(pairs):
    """set_mode() of this tree: reset, 0100=00, the stored nrv_register_writes
    pairs in order (the setfile that dvs_live sends)."""
    return [('w', 0x0100, 0x00)] + [('w', a, v) for a, v in pairs]


def elf_versions(path):
    """Return {symbol: crc} from the __versions section of an ELF64 LE module."""
    b = Path(path).read_bytes()
    assert b[:4] == b'\x7fELF' and b[4] == 2 and b[5] == 1, 'not ELF64 LE'
    shoff, = struct.unpack_from('<Q', b, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', b, 0x3a)
    secs = [struct.unpack_from('<IIQQQQIIQQ', b, shoff + i * shentsize) for i in range(shnum)]
    stroff = secs[shstrndx][4]
    out = {}
    for s in secs:
        name = b[stroff + s[0]:b.index(0, stroff + s[0])].decode()
        if name == '__versions':
            data = b[s[4]:s[4] + s[5]]
            # Variable-length layout used by this (Ubuntu-based) kernel:
            # { u32 next; u32 crc; char name[]; } with next = padded entry size.
            var, i = {}, 0
            while i + 8 <= len(data):
                nxt, crc = struct.unpack_from('<II', data, i)
                if nxt < 12 or nxt % 4 or i + nxt > len(data):
                    break
                var[data[i + 8:i + nxt].split(b'\0', 1)[0].decode('ascii', 'replace')] = crc
                i += nxt
            if var and not data[i:].strip(b'\0'):  # rest is zero padding
                out.update(var)
            else:
                # Classic layout: { unsigned long crc; char name[56]; }
                for i in range(0, len(data) - 63, 64):
                    crc, = struct.unpack_from('<Q', data, i)
                    out[data[i + 8:i + 64].split(b'\0', 1)[0].decode()] = crc
    return out


def symvers(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        f = line.split('\t')
        if len(f) >= 3:
            out[f[1]] = (int(f[0], 16), f[2])
    return out


def modules(root):
    build = root / 'build'
    sen = build / 'nrv_dvs.ko'
    rel = platform.release()
    stock = Path(f'/lib/modules/{rel}/updates/drivers/media/platform/tegra/camera/tegra-camera.ko')
    stock_vm = sh('modinfo', '-F', 'vermagic', str(stock)).strip() if stock.exists() else None
    check(sen.is_file(), f'{sen.name} exists')
    vm = sh('modinfo', '-F', 'vermagic', str(sen)).strip()
    print(f'  vermagic {sen.name}: {vm!r}   sha256={sha256(sen)}')
    check(vm.split()[0] == rel, f'{sen.name} vermagic release == uname -r ({rel})')
    if stock_vm:
        check(vm == stock_vm, f'{sen.name} full vermagic == stock tegra-camera.ko ({stock_vm!r})')
    check(not list((root / 'bsp').rglob('*.patch')), 'no NVIDIA patch under bsp/ (NVIDIA stack stays stock)')
    sen_p = sh('modinfo', '-p', str(sen))
    print('  modinfo -p nrv_dvs.ko: ' + repr(sen_p))
    check(sen_p.strip() == '', 'modinfo -p nrv_dvs.ko is empty (0 module parameters)')
    files = sorted((root / 'drivers/dvs/src').rglob('*.c')) + sorted((root / 'drivers/dvs/include').rglob('*.h'))
    src = '\n'.join(p.read_text() for p in sorted((root / 'drivers/dvs/src').rglob('*.c')))
    allsrc = '\n'.join(p.read_text() for p in files)
    code = strip_c_comments(allsrc)
    check(re.search(r'\b(module_param\w*|MODULE_PARM_DESC|kernel_param_ops)\b', allsrc) is None,
          'driver sources: no module_param*/MODULE_PARM_DESC/kernel_param_ops')
    for name in REMOVED_PARAMS + REMOVED_SYMBOLS:
        check(re.search(r'\b' + name + r'\b', code) is None, f'driver code: no {name}')
    for name in TEST_ONLY_PARAMS:  # 'readback' is also an ordinary local variable name
        check(re.search(r'module_param\w*\(\s*' + name + r'\b', allsrc) is None,
              f'source: test-only param {name} not declared')
    check(re.search(r'sensor_modes\[0\]\s*\.signal_properties', code) is None
          and 'signal_properties.' not in code,
          'driver does not modify DT-parsed receiver signal properties')
    check('static_assert(offsetof(struct camera_common_data, priv) == 592);' in src,
          'ABI offset 592 static_assert retained')

    # --- nrv_register_writes control (AC2) ---
    hdr = (root / 'drivers/dvs/include/tegra-cam/register_control.h').read_text()
    rc = (root / 'drivers/dvs/src/tegra-cam/register_control.c').read_text()
    ops = (root / 'drivers/dvs/src/tegra-cam/camera_common_sensor_ops.c').read_text()
    i2c = (root / 'drivers/dvs/src/dvs/i2c.c').read_text()
    check('#define V4L2_CID_NRV_REGISTER_WRITES (V4L2_CID_USER_BASE + 0x2000)' in hdr and CID == 0x00982900,
          'CID = V4L2_CID_USER_BASE + 0x2000 = 0x00982900')
    for macro in ('#define DVS_REGISTER_CTRL_NAME "nrv_register_writes"', '#define DVS_MAX_REGISTER_WRITES 4096U',
                  '#define DVS_REGISTER_WRITES_EMPTY 0xffffffffU', '#define DVS_REGISTER_WORD_RESERVED 0xff000000U'):
        check(macro in hdr, f'register_control.h: {macro}')
    cfg_m = re.search(r'struct v4l2_ctrl_config dvs_register_ctrl_config = \{(.*?)\n\};', rc, re.S)
    body = strip_c_comments(cfg_m[1]) if cfg_m else ''
    for item in ('.ops = &dvs_register_ctrl_ops', '.id = V4L2_CID_NRV_REGISTER_WRITES',
                 '.type = V4L2_CTRL_TYPE_U32',
                 '.flags = V4L2_CTRL_FLAG_DYNAMIC_ARRAY | V4L2_CTRL_FLAG_EXECUTE_ON_WRITE',
                 '.def = DVS_REGISTER_WRITES_EMPTY', '.dims = { DVS_MAX_REGISTER_WRITES }',
                 '.is_private = 0', '.max = 0xffffffffULL'):
        check(item in body, f'control config: {item}')
    check(len(re.findall(r'v4l2_ctrl_new_custom\(', strip_c_comments(allsrc))) == 1,
          'exactly one v4l2_ctrl_new_custom() in the driver')
    reg = c_function(rc, 'dvs_subdev_registered')
    check('v4l2_ctrl_new_custom(sd->ctrl_handler, &dvs_register_ctrl_config' in reg
          and '.registered = dvs_subdev_registered' in rc,
          'the control is created in the subdev .registered internal op on sd->ctrl_handler')
    check(not re.search(r'datatype|0x2001', code, re.I),
          'no data type control or reset data type readback (stock VI matches RAW8 0x2A)')
    cw = strip_c_comments(c_function(i2c, 'dvs_check_work'))
    check(0 <= cw.find('dvs_reset(dvs, true)') < cw.find('DVS_REG_CONNECTION_CHECK, &readback')
          < cw.find('WRITE_ONCE(dvs->ready, true)'),
          'check_work: reset, connection readback, then ready')
    check('tcctrl_ops' not in code and 'ctrl_cid_list' not in code, 'CID not added to the tegracam control table')
    probe = c_function(i2c, 'dvs_probe')
    a, b = probe.find('tc->v4l2sd_internal_ops = &dvs_internal_ops;'), probe.find('tegracam_v4l2subdev_register(')
    check(0 <= a < b and probe.find('tegracam_set_privdata(') < a,
          'probe: privdata, then v4l2sd_internal_ops, then tegracam_v4l2subdev_register()')
    sc = strip_c_comments(c_function(rc, 'dvs_register_s_ctrl'))
    check(re.search(r'READ_ONCE\(dvs->streaming\)\)\s*return -EBUSY;', sc) is not None, 's_ctrl: -EBUSY while streaming')
    v, s = sc.find('dvs_register_words_valid('), sc.find('memcpy(dvs->registers.words')
    check(0 <= v < s and 'ctrl->new_elems' in sc, 's_ctrl: validates the whole new payload (new_elems) before storing')
    tc = strip_c_comments(c_function(rc, 'dvs_register_try_ctrl'))
    check('dvs_register_words_valid(ctrl->p_new.p_u32, ctrl->new_elems)' in tc, 'try_ctrl validates the payload')
    banned = ['dvs_i2c_', 'write_reg', 'read_reg', 'msleep', 'gpiod_', 'dvs_reset_', 'schedule_',
              'mutex_lock', 'signal_properties']
    check(not [w for w in banned if w in sc + tc], 'control callbacks only validate/store (no I2C, reset, sleep, locks)')
    check('mutex_lock(&' not in strip_c_comments(rc) and 'mutex_init' not in strip_c_comments(rc),
          'no new driver mutex in register_control.c')
    locks = re.findall(r'mutex_lock\(&dvs->lock\)', strip_c_comments(i2c))
    check(len(locks) == 3 and 'mutex_lock' not in strip_c_comments(ops),
          'dvs->lock taken only in dvs_i2c_send/recv/transfer')
    sm = strip_c_comments(c_function(ops, 'set_mode'))
    order = [sm.find(x) for x in ('dvs_register_snapshot(', 'return -ENODATA;', 'dvs_reset(priv, true);',
                                    'write_reg(s, DVS_REG_STREAM, 0x00)', 'DVS_REGISTER_WORD_ADDR(writes[i])',
                                    'msleep(2)')]
    check(all(x >= 0 for x in order) and order == sorted(order),
          'set_mode: snapshot, -ENODATA if empty, reset, 0100=00, stored pairs, 2 ms')
    check(re.search(r'if \(!count\) \{[^}]*return -ENODATA;', sm, re.S) is not None,
          'set_mode: empty list -> -ENODATA (no stream without a stored list)')
    check(re.findall(r'write_reg\(s, (\w+)', sm) == ['DVS_REG_STREAM', 'DVS_REGISTER_WORD_ADDR', 'DVS_REG_STREAM']
          and not re.search(r'(write|read)_reg\(s, 0x', sm),
          'set_mode: writes only 0100 and the stored list (no register literals)')
    check('read_reg(' not in sm and 'of_property' not in code and 'csi-user-datatype' not in allsrc,
          'set_mode: no readback; driver reads no DT data type property')
    check(sm.count('kfree(writes)') >= 2
          and re.search(r'fail:\s*write_reg\(s, DVS_REG_STREAM, 0x00\);', sm) is not None,
          'set_mode: snapshot freed on every exit; failure path puts the sensor in standby')
    st = strip_c_comments(c_function(ops, 'start_streaming'))
    check(0 <= st.find('dvs_register_set_streaming(priv, true)') < st.find('write_reg(tc->s_data, DVS_REG_STREAM, 0x01)'),
          'start_streaming publishes streaming before the start write (EBUSY window)')
    wv = strip_c_comments(c_function(rc, 'dvs_register_words_valid'))
    check('DVS_REGISTER_WORD_ADDR(words[i]) == DVS_REG_STREAM' in wv and '#define DVS_REG_STREAM 0x0100' in allsrc,
          'control rejects the stream on/off register 0x0100')
    # --- no sensor configuration / identity in the (public) driver ---
    check(not (root / 'drivers/dvs/include/delta10_setfile.h').exists()
          and not re.search(r'delta|setfile|s5k|samsung|pad_control', allsrc, re.I),
          'driver sources: no built-in setfile, preset or sensor/register names')
    rs = strip_c_comments(c_function(i2c, 'dvs_reset'))
    check(code.count('gpiod_set_value') == 2 and rs.count('gpiod_set_value') == 2
          and 'msleep(10)' in rs and 'msleep(100)' in rs
          and not re.search(r'dvs_reset_(assert|release)', allsrc),
          'RSTN driven only in dvs_reset() (10 ms low, 100 ms after release)')
    snap = strip_c_comments(c_function(rc, 'dvs_register_snapshot'))
    check(0 <= snap.find('v4l2_ctrl_lock(ctrl)') < snap.find('kmemdup(') < snap.find('v4l2_ctrl_unlock(ctrl)'),
          'snapshot copied under the control handler lock (kmemdup, not on stack)')
    rm = strip_c_comments(c_function(i2c, 'dvs_remove'))
    check(0 <= rm.find('WRITE_ONCE(dvs->register_ctrl, NULL);') < rm.find('tegracam_v4l2subdev_unregister(')
          and strip_c_comments(rc).count('READ_ONCE(dvs->register_ctrl)') >= 2,
          'remove clears register_ctrl before the handler is freed; helpers read it with READ_ONCE')

    # --- default register sequence equivalence (config/Delta_10_2000FPS.txt) ---
    preset = preset_entries(root)
    setfile = preset[:-1]
    # The .txt ends with the stream-on write 0100=01, which start_streaming() does
    # (dvs_live drops it from the list).
    check(len(setfile) == 84 and preset[-1] == (0x0100, 0x01),
          'Delta_10_2000FPS.txt: 84 writes + final 0100=01')
    di = [i for i, (a, _) in enumerate(setfile) if a == 0x3901]
    check(len(di) == 1 and setfile[di[0]][1] == 0x2a, 'Delta_10_2000FPS.txt: one 3901=2A write (RAW8)')
    validated = [p for i, p in enumerate(setfile) if i not in di]
    old, new = old_default_sequence(validated), new_sequence(validated)
    seq_hash = hashlib.sha256(json.dumps(new_sequence(setfile)).encode()).hexdigest()
    print(f'  default sequence: {len(new) + len(di)} accesses sha256={seq_hash}')
    check(old == new, 'default setfile without 3901=2A: written sequence == the original driver at its validated defaults')
    # Stock VI matches only RAW8 (0x2A) for the SRGGB8 format of every DT mode.
    for f in sorted(p for d in ('config', 'sdk/settings', 'app/dvs_streamer/settings')
                    for p in (root / d).glob('*.txt')):
        di = re.findall(r'^\s*20:3901=([0-9a-fA-F]{2})\b', f.read_text(), re.M)
        check(di[-1:] == ['2A'], f'{f.relative_to(root)}: last 3901 write = 2A (RAW8)')
    pll = [v for a, v in setfile if a in (0x30a5, 0x30a6, 0x30a7)]
    check(pll == [0x00, 0x8d, 0x00] and dict(setfile)[0x3915] == 0x24,
          'PLL 30A5/6/7 = 00/8D/00 and 0x3915 = 0x24 unchanged')

    # --- receiver timing from DT (AC3) ---
    dts = (root / 'device-tree/p3768-cam1/cam1.dts').read_text()
    mode0 = re.search(r'mode0 \{(.*?)\n\t+\};', dts, re.S)
    props = dict(re.findall(r'(\w+) = "([^"]*)";', strip_c_comments(mode0[1]))) if mode0 else {}
    for k, v in DT_MODE0.items():
        check(props.get(k) == v, f'cam1.dts mode0 {k} = "{v}"')
    check('serdes_pix_clk_hz' not in props, 'cam1.dts mode0 has no serdes_pix_clk_hz')
    modes = {n: dict(re.findall(r'(\w+) = "([^"]*)";', strip_c_comments(m)))
             for n, m in re.findall(r'mode(\d+) \{(.*?)\n\t+\};', dts, re.S)}
    fmts = re.findall(r'\{ \{ (\d+), (\d+) \}, frame_rates, 1, false, (\d+) \}', ops)
    check(len(fmts) == len(modes) == 2
          and all(modes.get(m, {}).get('active_w') == w and modes[m].get('active_h') == h
                  and modes[m].get('line_length') == w for w, h, m in fmts),
          f'driver formats[] match DT mode sizes: {fmts}')
    check(all({k: v for k, v in modes[m].items() if k not in ('active_w', 'active_h', 'line_length')}
              == {k: v for k, v in props.items() if k not in ('active_w', 'active_h', 'line_length')}
              for m in modes),
          'every DT mode has the mode0 receiver settings (only the VI buffer size differs)')
    if all(props.get(k) for k in ('pix_clk_hz', 'csi_pixel_bit_depth', 'num_lanes')):
        pix = int(props['pix_clk_hz'])
        lane = pix * int(props['csi_pixel_bit_depth']) // int(props['num_lanes'])
        old_rate = 2500 * 1000000  # removed rx_rate_override default, Mbps
        check(pix == old_rate * 4 // 8 and lane // 2 == old_rate // 2,
              f'sensor_common.c: pixel_clock {pix} and DPHY mipi_clock {lane // 2} == removed runtime override '
              '(NVCSI equal; VI bind-time rate differs, OPEN-ISSUES 3)')

    # --- app consumer ---
    run_py = (root / 'app/run.py').read_text()
    check('/sys/module/' not in run_py and "'--settings'" in run_py,
          'app/run.py: no sysfs parameter table; passes a setfile to dvs_live')
    # Modversions: nrv_dvs imports vs the stock tegra-camera exports.
    imports = elf_versions(sen)
    nvpub = symvers('/usr/src/nvidia/nvidia-public/Module.symvers')
    stock_cam = {s: v for s, v in nvpub.items() if Path(v[1]).name == 'tegra-camera'}
    from_cam = {s: c for s, c in imports.items() if s in stock_cam}
    check(len(from_cam) > 0, f'nrv_dvs.ko imports {len(from_cam)} symbols exported by tegra-camera.ko')
    check(all(stock_cam[s][0] == c for s, c in from_cam.items()),
          'nrv_dvs.ko import CRCs == stock tegra-camera export CRCs (nvidia-public Module.symvers)')


def app(root):
    for p in sorted(list((root / 'app').glob('*.py')) + list((root / 'scripts').glob('*.py'))):
        ast.parse(p.read_text(), str(p))
        check(True, f'ast.parse {p.relative_to(root)}')
    words = ['tegra_camera'] + OLD_CAMERA_PARAMS
    hits = []
    for d in ('app', 'scripts', 'config'):
        for p in (root / d).rglob('*'):
            if p.is_file() and not p.is_symlink() and p.suffix in ('.py', '.json', '.c', '.txt', ''):
                t = p.read_text(errors='replace')
                hits += [(str(p.relative_to(root)), w) for w in words if w in t]
    t = (root / 'dvs').read_text()
    hits += [('dvs', w) for w in words if w in t]
    check(not hits, f'no tegra_camera / old camera param references in app, scripts, config, dvs: {hits}')
    link = root / 'app/dvs_live'
    check(link.is_symlink() and str(link.readlink()) == '../build/dvs_live', 'app/dvs_live -> ../build/dvs_live')
    check(link.resolve().is_file() and link.resolve().read_bytes()[:4] == b'\x7fELF',
          'app/dvs_live resolves to an ELF binary')


def scan(root):
    ignore = '# scan-' 'ignore'
    files = [root / 'build.sh', root / 'dvs'] + sorted((root / 'tests').glob('*')) \
        + sorted((root / 'drivers/dvs').glob('*')) + sorted((root / 'app').glob('*.py')) \
        + sorted((root / 'app').glob('*.cpp')) + sorted((root / 'scripts').glob('*')) \
        + sorted((root / 'config').glob('*')) + sorted((root / 'device-tree').rglob('*.dts')) \
        + [root / 'bsp/r39.2.1/manifest.env']
    ref = re.compile(r'(?<!nrv-)dvs-jetson')  # scan-ignore
    hits = []
    for p in files:
        if p.is_file():
            for i, line in enumerate(p.read_text(errors='replace').splitlines(), 1):
                if ref.search(line) and ignore not in line:
                    hits.append(f'{p.relative_to(root)}:{i}')
    check(not hits, f'no paths into the reference validation tree in build/runtime/test files: {hits}')
    build_files = [root / 'build.sh', root / 'tests/check-build.sh', root / 'tests/nvtree.py',
                   root / 'tests/compare-dt.py',
                   root / 'drivers/dvs/Makefile', Path(__file__)]
    banned = ['ins' 'mod', 'mod' 'probe', 'rm' 'mod', 'update-' 'initramfs', 're' 'boot',
              'su' 'do ', 'dpkg ' '-i', 'apt' '-get', 'apt ' 'install', 'extlinux' '.conf', 'boot' '.py']
    write_re = re.compile(r'(>\s*|\b(cp|mv|install|ln|tee|rm|dd|touch|mkdir)\s+[^\n]*?|-o\s+)'
                          r'["\']?(/boot|/lib/modules|/etc)\b')  # scan-ignore
    bad = []
    for p in build_files:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if ignore in line:
                continue
            code = line.split('#', 1)[0] if p.suffix in ('.sh', '') or p.name == 'Makefile' else line
            for w in banned:
                if w in code:
                    bad.append(f'{p.relative_to(root) if root in p.parents else p.name}:{i}:{w}')
            if write_re.search(code):
                bad.append(f'{p.name}:{i}:protected-write')
    check(not bad, f'build/test code invokes no module load/unload, initramfs, boot changes, '
                   f'package installs or protected-path writes: {bad}')


def main(argv):
    if len(argv) != 2 or argv[0] not in ('modules', 'app', 'scan'):
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    {'modules': modules, 'app': app, 'scan': scan}[argv[0]](root)
    print(f'{argv[0]}: {"FAILED " + str(len(FAILS)) if FAILS else "all checks passed"}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

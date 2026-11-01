"""Setfile inspection and bounded, read-only S5KRC1S I2C access.

The setfile snapshot is configuration intent, not sensor readback. The caller
must allow hardware requests only while its own capture is live and fresh.
Each register read uses the same two-message transaction as dvs_i2c_recv().
This deliberately exposes no register write, bus selection or device path API.
"""
import ctypes
import datetime
import errno
import hashlib
import os
from pathlib import Path
import re
import sys
import threading
import time


I2C_DEVICE = Path('/dev/i2c-2')
I2C_DRIVER = Path('/sys/bus/i2c/devices/2-0010/driver')
I2C_ADDRESS = 0x10
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
MAX_REGISTERS = 16
READ_INTERVAL = 1.0


class SensorUnavailable(RuntimeError):
    """The fixed sensor endpoint is unavailable or is not the expected driver."""


class SensorBusy(RuntimeError):
    """Another read is running or the read rate limit has been reached."""


class I2CMessage(ctypes.Structure):
    # linux/i2c.h: __u16 addr, flags, len; __u8 *buf (native alignment).
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16),
                ('len', ctypes.c_uint16), ('buf', ctypes.POINTER(ctypes.c_uint8))]


class I2CTransfer(ctypes.Structure):
    # linux/i2c-dev.h: struct i2c_msg *msgs; __u32 nmsgs.
    _fields_ = [('msgs', ctypes.POINTER(I2CMessage)), ('nmsgs', ctypes.c_uint32)]


def parse_setfile(data):
    """Mirror main.cpp's sscanf and NRV_DVS_OP, retaining file order/comments.

    Calling the C conversion avoids accepting a different grammar from the
    streamer (field widths, leading whitespace, short hex values and trailing
    comments). MODE_SELECT entries are visible but excluded from configuration
    operations, just as they are in main.cpp.
    """
    runtime = ctypes.CDLL('msvcrt' if os.name == 'nt' else None)
    scan = runtime.sscanf
    scan.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    scan.restype = ctypes.c_int
    entries = []
    for number, line in enumerate(data.splitlines(), 1):
        addr, value = ctypes.c_uint(), ctypes.c_uint()
        if scan(line, b' 20:%4x=%2x', ctypes.byref(addr), ctypes.byref(value)) != 2:
            continue
        address, byte = addr.value & 0xffff, value.value & 0xff
        comment = line.partition(b'//')[2].decode('utf-8', errors='replace').strip()
        entries.append({'line': number, 'address': address,
                        'address_hex': f'0x{address:04X}', 'value': byte,
                        'value_hex': f'0x{byte:02X}', 'comment': comment,
                        'applied': addr.value != 0x0100})
    return entries


def parse_addresses(addresses):
    """JSON integers are numeric; strings are 1-4 hex digits, optionally 0x."""
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= MAX_REGISTERS:
        raise ValueError(f'레지스터 주소를 1~{MAX_REGISTERS}개 입력하세요')
    result = []
    for address in addresses:
        if isinstance(address, str):
            if not re.fullmatch(r'(?:0[xX])?[0-9a-fA-F]{1,4}', address):
                raise ValueError('주소는 0x0000~0xFFFF 범위의 16진수여야 합니다')
            address = int(address, 16)
        if type(address) is not int or not 0 <= address <= 0xffff:
            raise ValueError('주소는 0x0000~0xFFFF 범위여야 합니다')
        result.append(address)
    return result


class SensorSettings:
    def __init__(self, settings_path):
        self.path = Path(settings_path).resolve()
        with self.path.open('rb') as source:
            data = source.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('설정 파일이 1 MiB를 초과합니다')
        self.digest = hashlib.sha256(data).hexdigest()
        self.entries = parse_setfile(data)
        self.expected = {entry['address']: entry['value'] for entry in self.entries
                         if entry['applied']}
        self.format = 'raw2' if self.expected.get(0x3260, 0) & 2 else 'group_aer'
        self._lock = threading.Lock()
        self._last_read = float('-inf')

    @staticmethod
    def _bound():
        try:
            return I2C_DRIVER.is_symlink() and I2C_DRIVER.resolve(strict=True).name == 'nrv_dvs'
        except OSError:
            return False

    def describe(self):
        bound = self._bound()
        return {'source': 'setfile_snapshot',
                'file': {'name': self.path.name, 'path': str(self.path), 'sha256': self.digest},
                'format': self.format, 'entries': self.entries,
                'operation_count': sum(entry['applied'] for entry in self.entries),
                'device': {'path': str(I2C_DEVICE), 'bus': 2, 'address': I2C_ADDRESS,
                           'address_hex': '0x10', 'driver': 'nrv_dvs', 'bound': bound,
                           'available': bound and os.access(I2C_DEVICE, os.R_OK | os.W_OK)},
                'read_limit': MAX_REGISTERS, 'read_interval_ms': round(READ_INTERVAL * 1000),
                'note': '설정 파일은 시작 시 보낸 값이며 현재 센서 값이 아닙니다. '
                        '0x0100은 드라이버가 제어하므로 설정 목록에서 제외됩니다.'}

    @staticmethod
    def _ioctl():
        if not sys.platform.startswith('linux'):
            raise SensorUnavailable('센서 레지스터 읽기는 Jetson Linux에서만 지원합니다')
        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.ioctl
        call.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
        call.restype = ctypes.c_int
        return call

    @staticmethod
    def _read_one(fd, address, ioctl):
        pointer = (ctypes.c_uint8 * 2)(address >> 8, address & 0xff)
        value = (ctypes.c_uint8 * 1)()
        messages = (I2CMessage * 2)(
            I2CMessage(I2C_ADDRESS, 0, 2, pointer),
            I2CMessage(I2C_ADDRESS, I2C_M_RD, 1, value))
        transfer = I2CTransfer(messages, 2)
        # One ioctl guarantees repeated START and an atomic message pair on
        # the adapter. There is no I2C_SLAVE_FORCE or sensor data-write message.
        result = ioctl(fd, I2C_RDWR, ctypes.byref(transfer))
        if result != 2:
            code = ctypes.get_errno() if result < 0 else errno.EIO
            raise OSError(code or errno.EIO, f'I2C read 0x{address:04X}: {os.strerror(code or errno.EIO)}')
        return value[0]

    def read_registers(self, addresses):
        addresses = parse_addresses(addresses)
        if not self._lock.acquire(blocking=False):
            raise SensorBusy('다른 레지스터 읽기가 진행 중입니다')
        try:
            now = time.monotonic()
            if now - self._last_read < READ_INTERVAL:
                raise SensorBusy('레지스터 읽기는 1초 간격으로 실행하세요')
            self._last_read = now
            if not self._bound():
                raise SensorUnavailable('I2C 2-0010에 nrv_dvs 드라이버가 연결되어 있지 않습니다')
            ioctl = self._ioctl()
            fd = os.open(I2C_DEVICE, os.O_RDWR | os.O_CLOEXEC)
            try:
                registers = []
                for address in addresses:
                    value = self._read_one(fd, address, ioctl)
                    expected = self.expected.get(address)
                    registers.append({'address': address, 'address_hex': f'0x{address:04X}',
                                      'value': value, 'value_hex': f'0x{value:02X}',
                                      'setfile_value': expected,
                                      'matches_setfile': None if expected is None else value == expected})
            finally:
                os.close(fd)
            return {'source': 'sensor_i2c',
                    'read_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'device': str(I2C_DEVICE), 'slave_address': I2C_ADDRESS,
                    'registers': registers}
        finally:
            self._lock.release()

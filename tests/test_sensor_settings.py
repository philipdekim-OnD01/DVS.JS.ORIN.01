"""Read-only sensor inspector: parser parity, validation and I2C wire contract."""
import ctypes
import errno
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'app/dvs_streamer/python/sensor_settings.py'
spec = importlib.util.spec_from_file_location('sensor_settings', SOURCE)
sensor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sensor)


class SensorSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.txt'
        self.path.write_bytes(b'// comment\n20:0100=00\n20:3260=02 // RAW2\n'
                              b'20:3901=2A\n20:3901=31 // last wins\n20:0100=01\n')
        self.settings = sensor.SensorSettings(self.path)

    def test_ordered_parser_and_driver_owned_stream_control(self):
        entries = self.settings.entries
        self.assertEqual([e['address'] for e in entries], [0x100, 0x3260, 0x3901, 0x3901, 0x100])
        self.assertEqual([e['applied'] for e in entries], [False, True, True, True, False])
        self.assertEqual(entries[3]['comment'], 'last wins')
        self.assertEqual(self.settings.expected[0x3901], 0x31)
        self.assertNotIn(0x100, self.settings.expected)
        self.assertEqual(self.settings.format, 'raw2')

    def test_scanner_matches_streamer_field_width_and_whitespace(self):
        entries = sensor.parse_setfile(b' 20:123= A // short\n20:ABCD=123\n'
                                      b'20:12345=FF\n#20:1234=FF\n20:1234 =01\n')
        self.assertEqual([(e['address'], e['value']) for e in entries], [(0x123, 10), (0xABCD, 0x12)])

    def test_snapshot_does_not_claim_later_file_edits_are_applied(self):
        self.path.write_bytes(b'20:3901=00\n')
        with patch.object(sensor.SensorSettings, '_bound', return_value=False):
            result = self.settings.describe()
        self.assertEqual(result['source'], 'setfile_snapshot')
        self.assertEqual(result['operation_count'], 3)
        self.assertEqual(result['entries'][3]['value'], 0x31)
        self.assertFalse(result['device']['available'])

    def test_reject_malformed_or_unbounded_addresses_without_touching_bus(self):
        bad = [None, {}, [], [0] * 17, [True], [-1], [65536], [2.0], ['0x10000'],
               ['0x'], ['+1'], ['12;'], [' 3901'], ['../2'], [None]]
        with patch.object(self.settings, '_bound') as bound:
            for addresses in bad:
                with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                    self.settings.read_registers(addresses)
            bound.assert_not_called()
        self.assertEqual(sensor.parse_addresses([0, '3901', '0Xffff']), [0, 0x3901, 65535])

    def test_two_message_read_and_partial_transfer_failure(self):
        def ioctl(fd, request, arg):
            self.assertEqual((fd, request), (7, sensor.I2C_RDWR))
            xfer = ctypes.cast(arg, ctypes.POINTER(sensor.I2CTransfer)).contents
            self.assertEqual(xfer.nmsgs, 2)
            first, second = xfer.msgs[0], xfer.msgs[1]
            self.assertEqual((first.addr, first.flags, first.len), (0x10, 0, 2))
            self.assertEqual(list(first.buf[:2]), [0x39, 0x01])
            self.assertEqual((second.addr, second.flags, second.len), (0x10, sensor.I2C_M_RD, 1))
            second.buf[0] = 0x2A
            return 2
        self.assertEqual(sensor.SensorSettings._read_one(7, 0x3901, ioctl), 0x2A)
        with self.assertRaises(OSError) as error:
            sensor.SensorSettings._read_one(7, 0x3901, lambda *args: 1)
        self.assertEqual(error.exception.errno, errno.EIO)

    def test_live_result_closes_fd_and_rate_limits(self):
        with patch.object(self.settings, '_bound', return_value=True), \
             patch.object(self.settings, '_ioctl'), \
             patch.object(self.settings, '_read_one', return_value=0x31), \
             patch.object(sensor.os, 'open', return_value=7) as opened, \
             patch.object(sensor.os, 'close') as closed, \
             patch.object(sensor.os, 'O_CLOEXEC', 0, create=True), \
             patch.object(sensor.time, 'monotonic', side_effect=[10, 10.5, 11]):
            result = self.settings.read_registers([0x3901, 0x100])
            self.assertEqual(result['source'], 'sensor_i2c')
            self.assertTrue(result['registers'][0]['matches_setfile'])
            self.assertIsNone(result['registers'][1]['matches_setfile'])
            with self.assertRaises(sensor.SensorBusy):
                self.settings.read_registers([0x3901])
            self.settings.read_registers([0x3901])
            self.assertEqual(opened.call_count, 2)
            self.assertEqual(closed.call_count, 2)

    def test_transaction_failure_closes_fd_and_releases_lock(self):
        with patch.object(self.settings, '_bound', return_value=True), \
             patch.object(self.settings, '_ioctl'), \
             patch.object(self.settings, '_read_one', side_effect=OSError(errno.EIO, 'test')), \
             patch.object(sensor.os, 'open', return_value=7), \
             patch.object(sensor.os, 'close') as closed, \
             patch.object(sensor.os, 'O_CLOEXEC', 0, create=True):
            with self.assertRaises(OSError):
                self.settings.read_registers([0x3901])
            closed.assert_called_once_with(7)
            self.assertFalse(self.settings._lock.locked())

    def test_refuse_wrong_driver_and_concurrent_read(self):
        with patch.object(self.settings, '_bound', return_value=False), \
             patch.object(sensor.os, 'open') as opened:
            with self.assertRaises(sensor.SensorUnavailable):
                self.settings.read_registers([0x3901])
            opened.assert_not_called()
        self.settings._lock.acquire()
        try:
            with self.assertRaises(sensor.SensorBusy):
                self.settings.read_registers([0x3901])
        finally:
            self.settings._lock.release()


if __name__ == '__main__':
    unittest.main()

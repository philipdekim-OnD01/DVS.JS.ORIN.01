#!/usr/bin/env python3
"""Exercise the running viewer's settings API; --read performs one real sensor read.

Run on the Jetson: python3 tests/check-settings-api.py --read
No register values are written and recording/streaming are left running.
"""
import argparse
import json
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--read', action='store_true')
    args = parser.parse_args()

    def request(path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(args.url.rstrip('/') + path, data=body,
                                     headers={'Content-Type': 'application/json'} | (headers or {}))
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    status, info = request('/api/settings')
    assert status == 200, (status, info)
    assert info['sensor']['entries'] and len(info['sensor']['file']['sha256']) == 64
    headers = {'X-DVS-Token': info['token']}
    checks = [
        ('token required', {}, {'addresses': [0x3901]}, 403),
        ('non-ASCII token refused', {'X-DVS-Token': '\u00e9'}, {'addresses': [0x3901]}, 403),
        ('foreign origin refused', headers | {'Origin': 'http://example.invalid'}, {'addresses': [0x3901]}, 403),
        ('foreign host refused', headers | {'Host': 'example.invalid:8080'}, {'addresses': [0x3901]}, 403),
        ('negative address', headers, {'addresses': [-1]}, 400),
        ('address overflow', headers, {'addresses': [65536]}, 400),
        ('boolean address', headers, {'addresses': [True]}, 400),
        ('batch limit', headers, {'addresses': list(range(17))}, 400),
        ('unknown fields', headers, {'addresses': [0x3901], 'bus': 0}, 400),
        ('wrong request shape', headers, [], 400),
    ]
    for name, hdr, data, expected in checks:
        actual, reply = request('/api/registers/read', data, hdr)
        assert actual == expected, (name, actual, reply)
        print(f'PASS {name}: HTTP {actual}')
    if args.read:
        time.sleep(1.1)
        addresses = [0x3901, 0x3906, 0x3907, 0x3908, 0x3915]
        status, reply = request('/api/registers/read', {'addresses': addresses}, headers)
        assert status == 200, (status, reply)
        assert reply['source'] == 'sensor_i2c'
        assert [r['address'] for r in reply['registers']] == addresses
        assert all(type(r['value']) is int and 0 <= r['value'] <= 255 for r in reply['registers'])
        print('PASS actual sensor read: ' + json.dumps(reply, ensure_ascii=False))
        status, reply = request('/api/registers/read', {'addresses': addresses}, headers)
        assert status == 429, (status, reply)
        print('PASS read rate limit: HTTP 429')
    print('PASS settings API checks')


if __name__ == '__main__':
    main()

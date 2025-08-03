#!/usr/bin/python3
# Copyright 2016 Google LLC. All rights reserved.
#
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file or at
# https://developers.google.com/open-source/licenses/bsd

"""A utility to parse and generate PSSH boxes."""

# This file itself is considered an invalid module name because of the dash in
# the filename: pssh-box.py
# pylint: disable=invalid-name

import argparse
import base64
import itertools
import os
import struct
import sys

import src.legacy.proto.WidevineCencHeader_pb2 as widevine_pssh_data_pb2


def to_code_point(value):
    """
  Return the unicode code point with `int` passthrough
  """
    if isinstance(value, int):
        return value

    return ord(value)


COMMON_SYSTEM_ID = base64.b16decode('1077EFECC0B24D02ACE33C1E52E2FB4B')
WIDEVINE_SYSTEM_ID = base64.b16decode('EDEF8BA979D64ACEA3C827DCD51D21ED')
PLAYREADY_SYSTEM_ID = base64.b16decode('9A04F07998404286AB92E65BE0885F95')


class BinaryReader(object):
    """A helper class used to read binary data from an binary string."""

    def __init__(self, data, little_endian):
        self.data = data
        self.little_endian = little_endian
        self.position = 0

    def has_data(self):
        """Returns whether the reader has any data left to read."""
        return self.position < len(self.data)

    def read_bytes(self, count):
        """Reads the given number of bytes into an array."""
        if len(self.data) < self.position + count:
            raise RuntimeError('Invalid PSSH box, not enough data')
        ret = self.data[self.position:self.position + count]
        self.position += count
        return ret

    def read_int(self, size):
        """Reads an integer of the given size (in bytes)."""
        data = self.read_bytes(size)
        ret = 0
        for i in range(0, size):
            if self.little_endian:
                ret |= (to_code_point(data[i]) << (8 * i))
            else:
                ret |= (to_code_point(data[i]) << (8 * (size - i - 1)))
        return ret


class Pssh(object):
    """Defines a PSSH box and related functions."""

    def __init__(self, version, system_id, key_ids, pssh_data):
        """Parses a PSSH box from the given data.

    Args:
      version: The version number of the box
      system_id: A binary string of the System ID
      key_ids: An array of binary strings for the key IDs
      pssh_data: A binary string of the PSSH data
    """
        self.version = version
        self.system_id = system_id
        self.key_ids = key_ids or []
        self.pssh_data = pssh_data or ''

    def binary_string(self):
        """Converts the PSSH box to a binary string."""
        ret = b'pssh' + _create_bin_int(self.version << 24)
        ret += self.system_id
        if self.version == 1:
            ret += _create_bin_int(len(self.key_ids))
            for key in self.key_ids:
                ret += key
        ret += _create_bin_int(len(self.pssh_data))
        ret += self.pssh_data
        return _create_bin_int(len(ret) + 4) + ret

    def human_string(self):
        """Converts the PSSH box to a human readable string."""
        system_name = ''
        convert_data = None
        if self.system_id == WIDEVINE_SYSTEM_ID:
            system_name = 'Widevine'
            convert_data = _parse_widevine_data
        elif self.system_id == PLAYREADY_SYSTEM_ID:
            system_name = 'PlayReady'
            convert_data = _parse_playready_data
        elif self.system_id == COMMON_SYSTEM_ID:
            system_name = 'Common'

        lines = [
            'PSSH Box v%d' % self.version,
            '  System ID: %s %s' % (system_name, _create_uuid(self.system_id))
        ]
        if self.version == 1:
            lines.append('  Key IDs (%d):' % len(self.key_ids))
            lines.extend(['    ' + _create_uuid(key) for key in self.key_ids])

        lines.append('  PSSH Data (size: %d):' % len(self.pssh_data))
        if self.pssh_data:
            if convert_data:
                lines.append('    ' + system_name + ' Data:')
                try:
                    extra = convert_data(self.pssh_data)
                    lines.extend(['      ' + x for x in extra])
                # pylint: disable=broad-except
                except Exception as e:
                    lines.append('      ERROR: ' + str(e))
            else:
                lines.extend([
                    '    Raw Data (base64):',
                    '      ' + base64.b64encode(self.pssh_data)
                ])

        return '\n'.join(lines)


def _split_list_on(elems, sep):
    """Splits the given list on the given separator."""
    return [list(g) for k, g in itertools.groupby(elems, lambda x: x == sep)
            if not k]


def _create_bin_int(value):
    """Creates a binary string as 4-byte array from the given integer."""
    return struct.pack('>i', value)


def _create_uuid(data):
    """Creates a human readable UUID string from the given binary string."""
    ret = base64.b16encode(data).decode().lower()
    return (ret[:8] + '-' + ret[8:12] + '-' + ret[12:16] + '-' + ret[16:20] +
            '-' + ret[20:])


def _generate_widevine_data(key_ids, content_id, provider, protection_scheme):
    """Generate widevine pssh data."""
    wv = widevine_pssh_data_pb2.WidevinePsshData()
    wv.key_id.extend(key_ids)
    if provider:
        wv.provider = provider
    if content_id:
        wv.content_id = content_id
    # 'cenc' is the default, so omitted to save bytes.
    if protection_scheme and protection_scheme != 'cenc':
        wv.protection_scheme = struct.unpack('>L', protection_scheme.encode())[0]
    return wv.SerializeToString()


def _parse_widevine_data(data):
    """Parses Widevine PSSH box from the given binary string."""
    wv = widevine_pssh_data_pb2.WidevinePsshData()
    wv.ParseFromString(data)

    ret = []
    if wv.key_id:
        ret.append('Key IDs (%d):' % len(wv.key_id))
        ret.extend(['  ' + _create_uuid(x) for x in wv.key_id])

    if wv.HasField('provider'):
        ret.append('Provider: ' + wv.provider)
    if wv.HasField('content_id'):
        ret.append('Content ID: ' + base64.b16encode(wv.content_id).decode())
    if wv.HasField('policy'):
        ret.append('Policy: ' + wv.policy)
    if wv.HasField('crypto_period_index'):
        ret.append('Crypto Period Index: %d' % wv.crypto_period_index)
    if wv.HasField('protection_scheme'):
        protection_scheme = struct.pack('>L', wv.protection_scheme)
        ret.append('Protection Scheme: %s' % protection_scheme)

    return ret


def _parse_playready_data(data):
    """Parses PlayReady PSSH data from the given binary string."""
    reader = BinaryReader(data, little_endian=True)
    size = reader.read_int(4)
    if size != len(data):
        raise RuntimeError('Length incorrect')

    ret = []
    count = reader.read_int(2)
    while count > 0:
        count -= 1
        record_type = reader.read_int(2)
        record_len = reader.read_int(2)
        record_data = reader.read_bytes(record_len)

        ret.append('Record (size %d):' % record_len)
        if record_type == 1:
            xml = record_data.decode('utf-16 LE')
            ret.extend([
                '  Record Type: Rights Management Header (1)',
                '  Record XML:',
                '    ' + xml
            ])
        elif record_type == 3:
            ret.extend([
                '  Record Type: License Store (1)',
                '  License Data:',
                '    ' + base64.b64encode(record_data)
            ])
        else:
            raise RuntimeError('Invalid record type %d' % record_type)

    if reader.has_data():
        raise RuntimeError('Extra data after records')

    return ret


def _parse_boxes(data):
    """Parses one or more PSSH boxes for the given binary data."""
    reader = BinaryReader(data, little_endian=False)
    boxes = []
    while reader.has_data():
        start = reader.position
        size = reader.read_int(4)

        box_type = reader.read_bytes(4)
        if box_type != b'pssh':
            raise RuntimeError(
                'Invalid box type 0x%s, not \'pssh\'' % box_type.encode('hex'))

        version_and_flags = reader.read_int(4)
        version = version_and_flags >> 24
        if version > 1:
            raise RuntimeError('Invalid PSSH version %d' % version)

        system_id = reader.read_bytes(16)

        key_ids = []
        if version == 1:
            count = reader.read_int(4)
            while count > 0:
                key = reader.read_bytes(16)
                key_ids.append(key)
                count -= 1

        pssh_data_size = reader.read_int(4)
        pssh_data = reader.read_bytes(pssh_data_size)

        if start + size != reader.position:
            raise RuntimeError('Box size does not match size of data')

        pssh = Pssh(version, system_id, key_ids, pssh_data)
        boxes.append(pssh)
    return boxes


def generate_pssh(kidB64: str) -> str:
    kid = base64.standard_b64decode(kidB64)
    pssh_data = _generate_widevine_data([kid], "", "", "cenc")
    pssh = Pssh(0, WIDEVINE_SYSTEM_ID, [kid], pssh_data)
    return base64.standard_b64encode(pssh.binary_string()).decode()
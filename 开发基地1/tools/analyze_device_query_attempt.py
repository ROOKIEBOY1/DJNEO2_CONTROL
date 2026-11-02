"""Summarize a saved device-query attempt without printing raw aircraft data."""

import argparse
import collections
import datetime
import json

from control_packets import find_command_reply, find_query_reply, valid_frames


def stamp(value):
    return datetime.datetime.fromtimestamp(value).strftime('%H:%M:%S.%f')[:-3]


def summarize(path):
    record = json.loads(open(path, encoding='utf-8').read())
    incoming = record.get('incoming_control', record['incoming'])
    print('status', record['status'])
    print('incoming', len(record['incoming']), 'control', len(incoming),
          'control_truncated', record.get('incoming_control_truncated'))
    print('stored_outgoing_transport', len(record.get('outgoing_transport', [])))
    for command in record['commands']:
        command_type = command.get('command')
        if command_type not in ('device_query', 'startup_queries'):
            continue
        sent = command['time']
        sequence = command['inner_sequence']
        packet = bytes.fromhex(command.get('wire_packet_hex', command['packet_hex']))
        print(command_type, stamp(sent), 'inner_sequence', f'0x{sequence:04x}',
              'packet_length', len(packet), 'transport_type', packet[6], 'header_cursor',
              f'0x{int.from_bytes(packet[8:10], "little"):04x}')
        for name, lower, upper in (('before_2s', -2, 0),
                                   ('after_2s', 0, 2)):
            rows = [row for row in incoming
                    if sent + lower <= row['time'] < sent + upper]
            channels = collections.Counter(row['channel'] for row in rows)
            lengths = collections.Counter(len(bytes.fromhex(row['hex']))
                                          for row in rows)
            shapes = collections.Counter()
            query_replies = []
            for row in rows:
                raw = bytes.fromhex(row['hex'])
                if command_type == 'startup_queries':
                    reply = find_command_reply(raw, int(record['status']['session'], 0),
                                               (sequence + 1) & 0xffff,
                                               0, 1, sender=0x1f)
                else:
                    reply = find_query_reply(raw, int(record['status']['session'], 0),
                                             sequence)
                if reply is not None:
                    query_replies.append(reply[:16].hex())
                if row['channel'] != 1 or len(raw) < 34:
                    continue
                for outer in valid_frames(raw[34:]):
                    frames = (valid_frames(outer[11:-2])
                              if outer[4:6] == b'\x27\xee' and outer[9:11] == b'\x51\x01'
                              else (outer,))
                    for frame in frames:
                        shapes[(frame[4:6].hex(), frame[9:11].hex(),
                                len(frame) - 13)] += 1
            print(name, 'packets', len(rows), 'channels', dict(channels),
                  'lengths', lengths.most_common(8),
                  'DUML_shapes', len(shapes), 'top', shapes.most_common(10),
                  'matching_query_replies', query_replies)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('record')
    summarize(parser.parse_args().record)

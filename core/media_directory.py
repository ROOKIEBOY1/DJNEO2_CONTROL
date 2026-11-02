"""Read-only album protocol observed in camera-media-20261030-141959.

00/26 requests yield an ACK; album data arrives as 00/27 DUML frames
over channel 3. Transport bodies start at raw offset 20 and may split DUML
frames. Reassemble the transport stream BEFORE passing CRC-checked frames
to this module. Physical session validation belongs to the caller.
No network connections or command transmission are performed here.
"""
import re
from datetime import datetime
from control_packets import valid_frames

OBSERVED_REQUEST = bytes.fromhex(
    '4a002110020000000000010000402d000d0100ffffffffffffffff000100000000')
NAME = re.compile(rb'\x0d\x1d(DJI_([0-9]{14})_([0-9]{4})_D\.(JPG|MP4))')


def directory_request_payload():
    """Exact captured read-only list request, session 2; unknown fields retained.

    Send as app 02 -> camera 01, command set 00/id 26, flags 40.
    The caller must coordinate/cancel previous album sessions: the capture
    contains session 1 cancel 4a040e1001000000000001000000 before session 2.
    Session allocation and unobserved request variants are deliberately absent.
    """
    return OBSERVED_REQUEST


def make_directory_request(session_id):
    """Use observed list shape with caller-allocated uint16 album session.

    Only the explicit session field changes. Session allocation, retry and
    cancellation are caller responsibilities; DUML length/CRC are built above.
    """
    if not isinstance(session_id, int) or not 1 <= session_id <= 65535:
        raise ValueError('Invalid album session')
    return OBSERVED_REQUEST[:4] + session_id.to_bytes(2, 'little') + OBSERVED_REQUEST[6:]


def directory_bytes(frames, *, session=2, max_bytes=2_000_000):
    """Validate complete observed 00/27 response; reorder/deduplicate fragments.

    FTP header: magic 4a, operation 01, LE uint16 (low 12 bits payload
    length, high nibble 0 or 1=end), LE uint16 album session, LE uint32
    fragment index. The enclosing DUML message sequence is independent of
    this per-transfer index and may continue across successive directory reads.
    Returns concatenated directory bytes, without the ten-byte FTP headers.
    """
    pieces, final, used = {}, None, 0
    for frame in frames:
        if list(valid_frames(frame)) != [frame]:
            raise ValueError('Invalid DUML frame or CRC')
        if frame[4:6] != b'\xe1\x02' or frame[8:11] != b'\x00\x00\x27':
            raise ValueError('Unexpected album response endpoint/command/flags')
        payload = frame[11:-2]
        if len(payload) < 10 or payload[:2] != b'\x4a\x01':
            raise ValueError('Invalid album response header')
        length_flags = int.from_bytes(payload[2:4], 'little')
        if length_flags & 0xfff != len(payload) or length_flags >> 12 not in (0, 1):
            raise ValueError('Invalid album response length/flags')
        if int.from_bytes(payload[4:6], 'little') != session:
            raise ValueError('Unexpected album session')
        index = int.from_bytes(payload[6:10], 'little')
        if index > 65535:
            raise ValueError('Unexpected album fragment sequence')
        if index in pieces and pieces[index] != payload:
            raise ValueError('Conflicting duplicate album fragment')
        if index not in pieces:
            used += len(payload)
            if used > max_bytes:
                raise ValueError('Album response exceeds bounded cache')
            pieces[index] = payload
        if length_flags >> 12 == 1:
            if final is not None and final != index:
                raise ValueError('Conflicting final fragment')
            final = index
    if final is None or len(pieces) != final + 1 or set(pieces) != set(range(final + 1)):
        raise ValueError('Incomplete album response: missing fragment/end')
    return b''.join(pieces[index][10:] for index in range(final + 1))


def parse_directory_frames(frames, session_id=2):
    """Return newest-first original-file entries from complete valid DUML frames."""
    return list_entries(directory_bytes(frames, session=session_id))


def list_entries(directory):
    """Extract captured explicit name/path tags, require advertised entry count.

    This parses observed JPG/MP4 name tags (0d, byte length 1d), followed by
    path tag 1a, LE uint32 tagged length, subtype 01, NUL-terminated DCIM
    extensionless path. Size comes from the observed fixed metadata prefix
    immediately after its FAT creation timestamp (see _observed_size).
    The header's first uint32 is the advertised count. Results sort newest
    first using the explicit DJI timestamp and numeric file identifier.
    """
    if len(directory) < 8:
        raise ValueError('Truncated album directory')
    count = int.from_bytes(directory[:4], 'little')
    if not 0 <= count <= 10000:
        raise ValueError('Unreasonable album entry count')
    entries = []
    for match in NAME.finditer(directory):
        end = match.end()
        if directory[end:end + 1] != b'\x1a' or directory[end + 5:end + 6] != b'\x01':
            raise ValueError('Missing explicit original DCIM path tag')
        length = int.from_bytes(directory[end + 1:end + 5], 'little')
        if not 6 <= length <= 1024:
            raise ValueError('Invalid DCIM path tag length')
        tagged = directory[end + 6:end + 1 + length]
        if not tagged.endswith(b'\0'):
            raise ValueError('Truncated DCIM path tag')
        stem = tagged[:-1].decode('ascii')
        name = match[1].decode('ascii')
        if not re.fullmatch(r'DCIM/DJI_[0-9]{3}/' + re.escape(name.rsplit('.', 1)[0]), stem):
            raise ValueError('Invalid or mismatched original DCIM path')
        entries.append(dict(name=name, filename=name, path=stem + '.' + match[4].decode(),
                            timestamp=match[2].decode(), file_id=int(match[3]),
                            kind='photo' if match[4] == b'JPG' else 'video',
                            size=_observed_size(directory, match)))
    if len(entries) != count or len({entry['path'] for entry in entries}) != count:
        raise ValueError('Album entry count mismatch or duplicate path')
    return sorted(entries, key=lambda entry: (entry['timestamp'], entry['file_id']), reverse=True)


def _observed_size(directory, match):
    """Decode the two observed metadata variants, with date/type guards.

    Record prefix: FAT timestamp u32, original byte size u32, opaque index
    u32, duration u16, media type u8 (0=JPG, 3=MP4). The name tag follows
    126 bytes after photo prefix, 166 after video prefix. These offsets are
    capture-specific: unknown variants return None instead of inventing size.
    Independent HTTP Content-Length/Content-Range confirms the latest JPG
    and MP4 values, not the remainder of the still-opaque metadata schema.
    """
    photo = match[4] == b'JPG'
    offset = match.start() - (126 if photo else 166)
    if offset < 8 or offset + 15 > match.start():
        return None
    prefix = directory[offset:offset + 15]
    if prefix[14] != (0 if photo else 3):
        return None
    packed = int.from_bytes(prefix[:4], 'little')
    try:
        created = datetime(1980 + (packed >> 25), (packed >> 21) & 15,
                           (packed >> 16) & 31, (packed >> 11) & 31,
                           (packed >> 5) & 63, (packed & 31) * 2)
        named = datetime.strptime(match[2].decode(), '%Y%m%d%H%M%S')
    except ValueError:
        return None
    if abs((created - named).total_seconds()) > 2:
        return None
    size = int.from_bytes(prefix[4:8], 'little')
    return size if size > 0 else None

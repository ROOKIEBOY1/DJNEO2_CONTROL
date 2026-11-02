"""Iterate validated direct and tunneled DUML frames from an outer packet."""


from control_packets import valid_frames


def duml_messages(raw):
    if len(raw) < 34 or raw[6] not in (1, 4, 5):
        return
    offset = 20 if raw[6] == 5 else 34
    for frame in valid_frames(raw[offset:]):
        if frame[4:6] in (b'\x3b\xe9', b'\x27\xee') and frame[9:11] == b'\x51\x01':
            for inner in valid_frames(frame[11:-2]):
                yield inner, frame
        else:
            yield frame, None

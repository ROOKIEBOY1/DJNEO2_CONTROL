"""Only native Follow shapes observed in the 2026-11-02/03 captures."""
SELECTION_PAYLOAD = bytes.fromhex('878867a30a000000')
START_PAYLOAD = bytes.fromhex('010a00')


def validate_request(receiver, command_set, command_id, payload):
    allowed = {(0x17, 3, 0xf9): SELECTION_PAYLOAD,
               (0x92, 0x23, 0x13): START_PAYLOAD,
               (0x92, 0x23, 0x15): b''}
    key = receiver, command_set, command_id
    if key not in allowed or payload != allowed[key]:
        raise ValueError('只允许已实测的跟随模式选择、启动和空负载停止')

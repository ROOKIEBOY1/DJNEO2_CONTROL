"""Whitelist from the operator-labeled 2026-11-02 captures; no network I/O."""
import struct

FRAMING={'半身':1,'全身':2,'远景':3}
START_PAYLOAD=bytes.fromhex('010d00')

def selection_payload():
    return bytes.fromhex('878867a30d000000')

def parameter_writes(framing='半身',zoom=2,retreat=True):
    if framing not in FRAMING or isinstance(zoom,bool) or zoom not in (2,3) or type(retreat) is not bool:
        raise ValueError('景别支持半身/全身/远景；倍率支持2或3；渐远支持开/关')
    return [bytes.fromhex('1756ad4101'),
            bytes.fromhex('ae918fe4')+bytes([FRAMING[framing]]),
            bytes.fromhex('c59f793d')+struct.pack('<f',zoom),
            bytes.fromhex('a602eb59')+bytes([retreat])]

def validate_request(receiver,cs,ci,payload):
    key=receiver,cs,ci
    if key==(0x17,3,0xf9) and payload==selection_payload():return
    if key==(0x92,0x23,0x13) and payload==START_PAYLOAD:return
    if key==(0x92,0x23,0x15) and payload==b'':return
    if key==(0x92,3,0xf9):
        allowed={p for framing in FRAMING for zoom in (2,3) for retreat in (False,True)
                 for p in parameter_writes(framing,zoom,retreat)}
        if payload in allowed:return
    raise ValueError('只允许本机实测的滑动变焦模式、参数、启动和空负载停止')

import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock
from landing_confirmation import eligible_landing_confirmation
from flight_controller import FlightController
from landing_status import LandingStatus
from duml import encode_frame
from control_packets import build_fly_packet
from messages import duml_messages

def state():
    return dict(land_requested=True,land_sent=True,land_acknowledged=True,
        landing_observation=dict(at=time.time(),requested_at=time.time()-4,telemetry_age_s=.01,
            landing_reason_raw=13,descending_duration_s=3.5,
            mode_raw=12,last_command_raw=2,motors_on=True,airborne_flag=True,relative_height_candidate_m=.3))

class LandingConfirmationTests(unittest.TestCase):
    def test_only_observed_pending_low_landing_state_is_eligible(self):
        self.assertTrue(eligible_landing_confirmation(state()))
        for field,value in [('mode_raw',6),('mode_raw',33),('last_command_raw',30),
                            ('telemetry_age_s',2),('landing_reason_raw',0),('descending_duration_s',.3),('relative_height_candidate_m',4),
                            ('relative_height_candidate_m',float('nan')),('motors_on',False)]:
            s=state();s['landing_observation'][field]=value
            self.assertFalse(eligible_landing_confirmation(s),(field,value))
        for key in ('land_requested','land_sent','land_acknowledged'):
            s=state();s[key]=False;self.assertFalse(eligible_landing_confirmation(s))

    def test_controller_requires_explicit_confirmation_and_records_observed_parameter(self):
        controller=FlightController.__new__(FlightController)
        controller._stop=threading.Event();controller._lock=threading.Lock()
        controller.device_verified=True;controller.last_receive_at=time.monotonic()
        controller.land_requested=controller.land_sent=controller.land_acknowledged=True
        controller.landing_status=LandingStatus();controller.landing_status.begin(time.time()-4)
        payload=bytearray(85);payload[16:18]=(3).to_bytes(2,'little');payload[30]=12;payload[31]=2;payload[32]=12
        payload[37]=13
        frame=encode_frame(3,14,1,0,3,67,payload)
        for delta in (3.2,2.7,2.2,1.7,1.2,.7,.2):
            controller.landing_status.observe(frame,time.time()-delta)
        controller.landing_status.observe(frame)
        controller._flight_command=Mock(return_value=b'\0')
        with self.assertRaises(ValueError):controller.confirm_landing()
        self.assertEqual(controller.confirm_landing(confirmed=True),b'\0')
        controller._flight_command.assert_called_once_with(0x1e,2.)
        with self.assertRaises(RuntimeError):controller.confirm_landing(confirmed=True)

    def test_telemetry_gap_resets_continuous_descent_duration(self):
        tracker=LandingStatus();tracker.begin(99)
        payload=bytearray(85);payload[30]=12;payload[31]=2;payload[32]=12;payload[37]=13
        frame=encode_frame(3,14,1,0,3,67,payload)
        tracker.observe(frame,100);tracker.observe(frame,104)
        self.assertEqual(tracker.snapshot(104)['descending_duration_s'],0)

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_wire_parameter_is_present_with_success_ack_in_new_phone_capture(self):
        path=Path(__file__).resolve().parents[1]/'captures/dolly-landing-20261102-162537.duml.jsonl'
        rows=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]
        request=next(r for r in rows if (r['side'],r['sender'],r['receiver'],r['command_set'],r['command_id'],r['payload'])
                     ==('app',2,3,3,42,'1e'))
        self.assertTrue(any(r['side']=='plane' and r['sender']==3 and r['receiver']==2
                            and r['sequence']==request['sequence'] and r['command_set']==3
                            and r['command_id']==42 and r['payload']=='00' and r['flags']&128 for r in rows))
        packet=build_fly_packet(1,2,2,2,3,4,0x1e)
        self.assertTrue(any(f[9:11]==b'\x03\x2a' and f[11:-2]==b'\x1e' for f,_ in duml_messages(packet)))

    @unittest.skip("Private capture fixture excluded from this code-only export")
    def test_replay_gate_accepts_successful_phone_phase_and_rejects_pc_interruptions(self):
        base=Path(__file__).resolve().parents[1]/'captures'
        rows=[json.loads(x) for x in (base/'dolly-landing-20261102-162537.duml.jsonl').read_text(encoding='utf-8').splitlines()]
        land=next(r['at'] for r in rows if r['side']=='app' and r['command_set']==3 and r['command_id']==42 and r['payload']=='02')
        follow=next(r['at'] for r in rows if r['side']=='app' and r['command_set']==3 and r['command_id']==42 and r['payload']=='1e')
        tracker=LandingStatus();tracker.begin(land)
        for r in rows:
            if land<=r['at']<follow and r['side']=='plane' and r['sender']==3 and (r['command_set'],r['command_id'])==(3,67):
                tracker.observe(encode_frame(r['sender'],r['receiver'],r['sequence'],r['flags'],3,67,bytes.fromhex(r['payload'])),r['at'])
        status=dict(land_requested=True,land_sent=True,land_acknowledged=True,landing_observation=tracker.snapshot(follow))
        self.assertTrue(eligible_landing_confirmation(status,follow))
        pc=json.loads((base/'control-attempt-20261102-160702.json').read_text(encoding='utf-8'))
        landings=iter(c['time'] for c in pc['commands'] if c.get('param')==2)
        next_land=next(landings,None);tracker=LandingStatus()
        for row in pc['incoming_control']:
            at=row['time']
            while next_land is not None and next_land<=at:
                tracker.begin(next_land);next_land=next(landings,None)
            for frame,_ in duml_messages(bytes.fromhex(row['hex'])):tracker.observe(frame,at)
            status=dict(land_requested=True,land_sent=True,land_acknowledged=True,landing_observation=tracker.snapshot(at))
            self.assertFalse(eligible_landing_confirmation(status,at))

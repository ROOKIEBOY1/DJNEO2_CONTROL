import hashlib
import unittest
from compare_video_layers import compare_records, compare_files, extract_drone_udp, replay_assembly, fragment_inventory
from test_video_stream import packet


def digest(raw):return hashlib.sha256(raw).hexdigest()


class LayerComparisonTests(unittest.TestCase):
    def test_paired_raw_and_hevc_files_are_compared_in_common_window(self):
        import tempfile
        from unittest.mock import patch
        from video_trace import VideoTrace
        from video_packet_trace import VideoPacketTrace
        with tempfile.TemporaryDirectory() as d:
            trace=VideoTrace(d)
            raw=VideoPacketTrace(trace.stream_path.with_suffix('.udp.bin'))
            nic=[]
            for i in range(1,6):
                body=bytes([64+i]);datagram=packet(i,payload=body)
                raw.submit(datagram,True,at=float(i));nic.append((float(i),datagram))
                with patch('video_trace.time.time',return_value=float(i)):
                    trace.record(body,dict(decoded=1,warnings=0))
            raw.close();trace.close()
            with patch('compare_video_layers.nic_records',return_value=iter(nic)):
                result=compare_files('unused.pcapng',raw.path)
            self.assertTrue(result['overlap_valid'])
            self.assertTrue(result['assembly_trace_complete'])
            self.assertEqual(result['nic_not_in_application_count'],0)
            self.assertEqual(result['replay_frames_not_in_actual_count'],0)
            self.assertEqual(result['decoder_progress']['decoded_frames'],5)

    def compare(self,nic,received,assembled,**quality):
        good=dict(closed=True,capped=False,queue_drops=0,error=None,
                  hevc_trace=dict(closed=True,capped=False))
        good.update(quality)
        return compare_records(nic,received,assembled,good,margin_s=0)

    def test_nic_packet_absent_from_application_is_distinguished(self):
        a=packet(1,payload=b'A');b=packet(2,payload=b'B')
        r=self.compare([(1.,a),(2.,b)],[(1.,True,a),(2.,True,a)],[(1.,digest(b'A'))])
        self.assertEqual(r['nic_not_in_application_count'],1)
        self.assertIn('网卡',r['findings'][0])

    def test_receiver_queue_loss_is_explicit(self):
        a=packet(1,payload=b'A');b=packet(2,payload=b'B')
        r=self.compare([(1.,a),(2.,b)],[(1.,True,a),(2.,False,b)],[(1.,digest(b'A'))])
        self.assertEqual(r['receiver_queue_rejections'],1)
        self.assertEqual(r['nic_not_in_application_count'],0)

    def test_replay_complete_frame_missing_in_actual_assembly_is_evidence(self):
        a=packet(1,payload=b'A');b=packet(2,payload=b'B')
        r=self.compare([(1.,a),(2.,b)],[(1.,True,a),(2.,True,b)],[(1.,digest(b'A'))])
        self.assertEqual(r['replay_frames_not_in_actual_count'],1)
        self.assertTrue(any('拼包' in x for x in r['findings']))

    def test_capped_or_dropped_raw_diagnostic_blocks_attribution(self):
        a=packet(1,payload=b'A');b=packet(2,payload=b'B')
        r=self.compare([(1.,a),(2.,b)],[(1.,True,a),(2.,True,a)],[],capped=True)
        self.assertFalse(r['application_trace_complete'])
        self.assertTrue(any('不完整' in x for x in r['findings']))
        self.assertFalse(any('系统收包' in x for x in r['findings']))

    def test_no_nic_coverage_cannot_prove_wireless_loss(self):
        a=packet(1,payload=b'A')
        r=self.compare([],[(1.,True,a)],[(1.,digest(b'A'))])
        self.assertFalse(r['overlap_valid'])
        self.assertTrue(any('无法' in x for x in r['findings']))

    def test_receive_delay_at_window_boundary_is_not_reported_as_packet_loss(self):
        nic=[(float(i),packet(i,payload=bytes([i]))) for i in range(1,6)]
        received=[(at+.2,True,raw) for at,raw in nic]
        assembled=[(at+.2,digest(raw[20:])) for at,raw in nic]
        quality=dict(closed=True,capped=False,queue_drops=0,error=None,
                     hevc_trace=dict(closed=True,capped=False))
        r=compare_records(nic,received,assembled,quality)
        self.assertEqual(r['nic_not_in_application_count'],0)
        self.assertEqual(r['application_not_in_nic_count'],0)

    def test_capped_hevc_cannot_be_called_an_assembly_failure(self):
        a=packet(1,payload=b'A');b=packet(2,payload=b'B')
        r=self.compare([(1.,a),(2.,b)],[(1.,True,a),(2.,True,b)],[],
                       hevc_trace=dict(closed=True,capped=True))
        self.assertFalse(r['assembly_trace_complete'])
        self.assertFalse(any('需核查队列处理' in x for x in r['findings']))
        r=self.compare([(1.,a),(2.,b)],[(1.,True,a),(2.,True,b)],[],
                       hevc_trace=dict(closed=True,capped=False,error='disk full'))
        self.assertFalse(r['assembly_trace_complete'])

    def test_assembler_replay_preserves_fragment_order_and_queue_decision(self):
        rows=[(1.,True,packet(1,2,1,b'B')),(1.01,False,packet(2,payload=b'X')),
              (1.02,True,packet(1,2,0,b'A'))]
        frames,stats=replay_assembly(rows)
        self.assertEqual(frames,[(1.02,digest(b'AB'))])

    def test_independent_inventory_detects_complete_but_late_fragments(self):
        rows=[(1.,True,packet(1,2,0,b'A')),(2.2,True,packet(1,2,1,b'B')),
              (2.3,True,packet(2,3,0,b'C'))]
        actual,stats=replay_assembly(rows)
        self.assertEqual(actual,[])
        inventory,counts=fragment_inventory(rows)
        self.assertEqual(inventory,[(2.2,digest(b'AB'))])
        self.assertEqual(counts['incomplete_groups'],1)
        self.assertEqual(counts['missing_fragment_examples'][0]['missing_parts'],[1,2])

    def test_pktmon_non_ethernet_prefix_is_located_without_truncation(self):
        import struct
        raw=packet(1,payload=b'payload')
        udp=struct.pack('!HHHH',9003,9003,len(raw)+8,0)+raw
        ip=bytearray(20);ip[0]=0x45;ip[2:4]=(20+len(udp)).to_bytes(2,'big');ip[9]=17
        ip[12:16]=bytes((192,168,2,1));ip[16:20]=bytes((192,168,2,156))
        data=b'prefix80211snap'+bytes(ip)+udp
        self.assertEqual(extract_drone_udp(data),raw)
        self.assertIsNone(extract_drone_udp(data[:-1]))

"""Boundary tests for capture-backed, read-only media transfer."""
import struct
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import contextlib
import io
import json

import media_transfer as m


def response(body, status=200, extra=b''):
    return (f'HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\n'.encode()
            + extra + b'\r\n' + body)


class ReassemblyTests(unittest.TestCase):
    def test_out_of_order_identical_retransmission_and_overlap(self):
        result = m.reassemble([(106,b'world'),(100,b'hello '),(103,b'lo wo'),(100,b'hello ')])
        self.assertEqual(result['data'],b'hello world')
        self.assertEqual(result['duplicate_bytes'],11)
        self.assertEqual(result['gaps'],[])

    def test_gap_is_reported_never_filled(self):
        result = m.reassemble([(10,b'abc'),(15,b'fg')])
        self.assertEqual(result['data'],b'abc')
        self.assertEqual(result['gaps'],[(13,15)])

    def test_conflicting_retransmission_rejected(self):
        with self.assertRaisesRegex(ValueError,'conflict'):
            m.reassemble([(1,b'abc'),(2,b'XX')])

    def test_sequence_wrap(self):
        self.assertEqual(m.reassemble([(0xfffffffe,b'ab'),(0,b'cd')])['data'],b'abcd')


class HttpTests(unittest.TestCase):
    def test_keepalive_and_binary_body_do_not_confuse_parser(self):
        body=b'GET /invalid HTTP/1.1\r\n\r\n'
        messages, error=m.parse_http(response(body)+response(b'next'),response=True)
        self.assertIsNone(error)
        self.assertEqual([x['body'] for x in messages],[body,b'next'])

    def test_truncated_body_is_not_a_complete_message(self):
        messages,error=m.parse_http(response(b'123')[:-1],response=True)
        self.assertEqual(messages,[])
        self.assertIn('incomplete',error)

    def test_ambiguous_length_rejected(self):
        _,error=m.parse_http(response(b'x',extra=b'Content-Length: 2\r\n'),response=True)
        self.assertIn('duplicate',error)

    def test_query_allowlist_and_path_traversal(self):
        target='/v2?storage=1&path=DCIM/DJI_001/DJI_123.jpg'
        self.assertEqual(m.media_path(target),'DCIM/DJI_001/DJI_123.jpg')
        for bad in ('/v2?storage=1&path=../bad.jpg','/v2?storage=1&path=DCIM/../bad.jpg',
                    '/v2?storage=1&path=DCIM/a.jpg&delete=1',
                    '/v2?storage=1&path=DCIM/%2e%2e/bad.jpg',
                    '/v2?storage=1&path=DCIM/a%5cb.jpg'):
            with self.subTest(bad=bad),self.assertRaises(ValueError): m.media_path(bad)

    def test_range_requires_consistent_exact_length(self):
        self.assertEqual(m.response_range({'status':206,'headers':{'content-range':'bytes 2-4/5'},'body':b'abc'}),(2,5,5))
        with self.assertRaises(ValueError):
            m.response_range({'status':206,'headers':{'content-range':'bytes 2-4/5'},'body':b'ab'})

    def test_collect_original_requires_full_coverage_same_etag(self):
        target='/v2?storage=1&path=DCIM/DJI_001/a.mp4'
        def exchange(start,body,etag='same'):
            return dict(request={'target':target,'headers':{'range':f'bytes={start}-{start+len(body)-1}'}},
                        response={'status':206,'body':body,'headers':{
                            'content-range':f'bytes {start}-{start+len(body)-1}/6','etag':etag}})
        entries,bodies=m.collect_originals([exchange(0,b'abc'),exchange(3,b'def')])
        self.assertTrue(entries[0]['complete'])
        self.assertEqual(bodies[entries[0]['sha256']],b'abcdef')
        entries,_=m.collect_originals([exchange(0,b'abc'),exchange(3,b'def','changed')])
        self.assertFalse(any(x['complete'] for x in entries))
        self.assertEqual(entries[0]['missing_ranges'],[(0,3)])

    def test_request_range_must_match_response(self):
        with self.assertRaisesRegex(ValueError,'request Range'):
            m.collect_originals([{'request':{'target':'/v2?storage=1&path=DCIM/DJI_001/a.mp4',
                                 'headers':{'range':'bytes=1-2'}},
                                 'response':{'status':206,'body':b'ab','headers':{'content-range':'bytes 0-1/2'}}}])

    def test_incomplete_capture_retains_exact_get_but_never_claims_original(self):
        target='/v2?storage=1&path=DCIM/DJI_001/a.mp4'
        parsed,error=m.parse_http(response(b'abcdef',206,b'Content-Range: bytes 0-5/6\r\n')[:-2],
                                  response=True,allow_partial=True)
        self.assertIn('incomplete',error)
        self.assertFalse(parsed[0]['complete'])
        parsed[0]['captured_ranges']=[(0,b'abcd')]
        entries,bodies=m.collect_originals([{'request':{'target':target,'headers':{'range':'bytes=0-5'}},'response':parsed[0]}])
        self.assertFalse(entries[0]['complete'])
        self.assertTrue(entries[0]['replayable'])
        self.assertEqual(entries[0]['requests'][0]['length'],6)
        self.assertEqual(entries[0]['requests'][0]['known_ranges'][0]['sha256'],m.digest(b'abcd'))
        self.assertEqual(bodies,{})

    def test_tcp_frame_seq_and_fragment_rejection(self):
        ip=bytearray(20); ip[0]=0x45; ip[9]=6
        ip[2:4]=(43).to_bytes(2,'big'); ip[12:16]=bytes([192,168,2,123]); ip[16:20]=bytes([192,168,2,1])
        tcp=bytearray(20); struct.pack_into('!HHI',tcp,0,64747,80,1234); tcp[12]=0x50
        frame=b'\0'*12+b'\x08\x00'+ip+tcp+b'abc'
        self.assertEqual(m.tcp_packet(frame)['sequence'],1234)
        self.assertEqual(m.tcp_packet(frame)['payload'],b'abc')
        ip[6:8]=(0x2000).to_bytes(2,'big')
        self.assertIsNone(m.tcp_packet(b'\0'*12+b'\x08\x00'+ip+tcp+b'abc'))


class SaveTests(unittest.TestCase):
    def test_jpeg_validation_and_no_overwrite(self):
        # Valid 1x1 JPEG generated with Pillow; full decoding is required.
        from PIL import Image
        import io
        buf=io.BytesIO(); Image.new('RGB',(1,1)).save(buf,format='JPEG')
        body=buf.getvalue()
        self.assertEqual(m.validate_media(body,'.jpg')['width'],1)
        self.assertEqual(m.validate_media(body+b'\0'*4096,'.jpg')['width'],1)
        self.assertEqual(m.validate_media(body+b'DJI-metadata\0','.jpg')['width'],1)
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'a.jpg'
            m.save_original(dest,body)
            with self.assertRaises(FileExistsError): m.save_original(dest,body)
            self.assertEqual(dest.read_bytes(),body)
            self.assertEqual(list(Path(folder).glob('*.part')),[])

    def test_truncated_mp4_rejected(self):
        with self.assertRaises(ValueError): m.validate_media(struct.pack('>I4s',100,b'ftyp')+b'isom','.mp4')

    def test_offline_download_does_not_open_connection(self):
        with patch.object(m.http.client,'HTTPConnection',side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError,'connect'):
                m.download_entry({'name':'a.jpg'},Path('a.jpg'),connect=False)




class LiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        buf=io.BytesIO(); Image.new('RGB',(2,3)).save(buf,format='JPEG')
        self.body=buf.getvalue()
        self.entry=dict(name='a.jpg',size=len(self.body),complete=False,replayable=True,etag='"one"',
                        requests=[dict(target='/v2?storage=1&path=DCIM/DJI_001/a.jpg',range=None,
                                       start=0,end=len(self.body),length=len(self.body),
                                       known_ranges=[dict(start=0,end=20,sha256=m.digest(self.body[:20]))])])

    def fake(self, *, status=200, length=None, body=None):
        original=self.body
        class Reply:
            def __init__(self): self.status=status
            def getheaders(self):
                return [('Content-Length',str(len(original) if length is None else length)),('ETag','"one"')]
            def read(self,n): return original if body is None else body
        class Connection:
            calls=[]
            def __init__(self,*a,**kw): self.calls.append(('connection',a))
            def request(self,*a,**kw): self.calls.append(('request',a))
            def getresponse(self): return Reply()
            def close(self): pass
        return Connection

    def test_success_verifies_known_bytes_then_publishes(self):
        connection=self.fake()
        with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',connection):
            dest=Path(folder)/'a.jpg'
            result=m.download_entry(self.entry,dest,connect=True)
            self.assertEqual(dest.read_bytes(),self.body)
            self.assertEqual((result['width'],result['height']),(2,3))
            self.assertEqual(connection.calls[0],('connection',(m.HOST,80)))
            self.assertEqual(connection.calls[1][1],('GET',self.entry['requests'][0]['target']))

    def test_redirect_wrong_length_and_corrupt_known_bytes_never_publish(self):
        for fake in (self.fake(status=302),self.fake(length=1),self.fake(body=b'X'+self.body[1:])):
            with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',fake):
                dest=Path(folder)/'a.jpg'
                with self.assertRaises(ValueError): m.download_entry(self.entry,dest,connect=True)
                self.assertEqual(list(Path(folder).iterdir()),[])

    def test_existing_destination_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',side_effect=AssertionError('network')):
            dest=Path(folder)/'a.jpg'; dest.write_bytes(b'old')
            with self.assertRaises(FileExistsError): m.download_entry(self.entry,dest,connect=True)
            self.assertEqual(dest.read_bytes(),b'old')


class DirectoryDownloadTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        buf=io.BytesIO(); Image.new('RGB',(2,3)).save(buf,format='JPEG')
        self.jpeg=buf.getvalue()
        def box(kind,body=b''): return struct.pack('>I4s',8+len(body),kind)+body
        self.mp4=box(b'ftyp',b'isom')+box(b'mdat',b'x'*139)+box(b'moov')

    def item(self,body,kind='photo'):
        name='DJI_20261030142114_0034_D.'+('JPG' if kind=='photo' else 'MP4')
        return dict(name=name,path='DCIM/DJI_001/'+name,size=len(body),kind=kind)

    def connection(self,body,*,etag_change=False,event=None,bad_range=False,short=False):
        class Connection:
            calls=[]
            reads=0
            closed=0
            def __init__(self,*args,**kw):
                if args!=(m.HOST,80): raise AssertionError(args)
            def request(self,method,target,headers):
                if method!='GET': raise AssertionError(method)
                self.calls.append((target,dict(headers)))
                self.rng=headers.get('Range')
            def getresponse(self):
                rng=self.rng
                if rng:
                    start,last=map(int,rng.removeprefix('bytes=').split('-')); end=last+1
                else: start,end=0,len(body)
                class Reply:
                    status=206 if rng else 200
                    def __init__(self): self.offset=start
                    def getheaders(self):
                        values=[('Content-Length',str(end-start)),('ETag','"changed"' if etag_change and start else '"current"')]
                        if rng: values.append(('Content-Range',f'bytes {start+int(bad_range)}-{end-1}/{len(body)}'))
                        return values
                    def read(self,n):
                        Connection.reads+=1
                        if event is not None: event.set()
                        limit=end-1 if short else end
                        data=body[self.offset:min(self.offset+n,limit)]; self.offset+=len(data)
                        return data
                return Reply()
            def close(self): Connection.closed+=1
        return Connection

    def test_default_offline_and_invalid_directory_items_never_connect(self):
        item=self.item(self.jpeg)
        invalid=[dict(item,path='DCIM/DJI_001/../x.JPG'),dict(item,name='../x.JPG'),
                 dict(item,size=True),dict(item,size=0),dict(item,size=m.MAX_MEDIA+1),
                 dict(item,kind='video'),dict(item,path=item['path'].replace('DJI_001','DJI_002'))]
        with patch.object(m.http.client,'HTTPConnection',side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError,'connect'):
                m.download_directory_item(item,Path('not-written.jpg'))
            for bad in invalid:
                with self.subTest(bad=bad),self.assertRaises(ValueError):
                    m.download_directory_item(bad,Path('not-written.jpg'),connect=True)

    def test_photo_lowercase_extension_current_hash_no_historical_claim(self):
        connection=self.connection(self.jpeg)
        with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',connection):
            dest=Path(folder)/'photo.jpg'
            result=m.download_directory_item(self.item(self.jpeg),dest,connect=True)
            self.assertEqual(dest.read_bytes(),self.jpeg)
            self.assertEqual(result['sha256'],m.digest(self.jpeg))
            self.assertEqual(result['etag'],'"current"')
            self.assertFalse(result['historical_hash_verified'])
            self.assertTrue(connection.calls[0][0].endswith('_D.jpg'))
            self.assertNotIn('Range',connection.calls[0][1])

    def test_video_exact_five_ranges_remainder_and_structure(self):
        connection=self.connection(self.mp4)
        with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',connection):
            dest=Path(folder)/'video.mp4'
            result=m.download_directory_item(self.item(self.mp4,'video'),dest,connect=True)
            self.assertEqual(dest.read_bytes(),self.mp4)
            step=len(self.mp4)//5
            self.assertEqual([h['Range'] for _,h in connection.calls],
                             [f'bytes={i*step}-{(i+1)*step-1 if i<4 else len(self.mp4)-1}' for i in range(5)])
            self.assertEqual(result['format'],'MP4')

    def test_etag_range_truncation_failure_never_publish(self):
        for connection in (self.connection(self.mp4,etag_change=True),self.connection(self.mp4,bad_range=True),
                           self.connection(self.mp4,short=True)):
            with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',connection):
                with self.assertRaises(ValueError):
                    m.download_directory_item(self.item(self.mp4,'video'),Path(folder)/'video.mp4',connect=True)
                self.assertEqual(list(Path(folder).iterdir()),[])

    def test_cancel_before_request_during_read_and_before_publication(self):
        for phase in ('before','read','publish'):
            event=threading.Event()
            if phase=='before': event.set()
            connection=self.connection(self.jpeg,event=event if phase=='read' else None)
            original_validation=m.validate_media
            def validation(body,suffix):
                result=original_validation(body,suffix)
                if phase=='publish': event.set()
                return result
            with tempfile.TemporaryDirectory() as folder,patch.object(m.http.client,'HTTPConnection',connection),patch.object(m,'validate_media',validation):
                with self.assertRaisesRegex(RuntimeError,'cancel'):
                    m.download_directory_item(self.item(self.jpeg),Path(folder)/'photo.jpg',connect=True,cancel_event=event)
                self.assertEqual(list(Path(folder).iterdir()),[])
                if phase=='before': self.assertEqual(connection.calls,[])


if __name__=='__main__': unittest.main()

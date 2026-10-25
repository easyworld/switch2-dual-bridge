import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import urllib.parse

from controller import Client
from pad_stream import NEUTRAL, StreamSender
from pro_controller import ProControllerView, report_for_actions

A=(4,15,128,128,128,128)
B=(2,15,128,128,128,128)
STATUS={'mode':'dual','boot_id':1,'usb_mounted':True,'usb_suspended':False,
        'gamepad':{'epoch':7,'input_ready':True,'release_pending':False,
                   'stream':{'supported':True}}}
CONFIG={'url':'http://127.0.0.1','api_key':'test-key'}


class StreamProtocolTests(unittest.TestCase):
    def client(self, response=None):
        client=Client(CONFIG)
        client.opener=Mock()
        client.opener.open.return_value=io.BytesIO(json.dumps(response or
            {'accepted':True,'next_sequence':1,'queued':2,'capacity':16}).encode())
        return client

    def test_packet_preserves_down_up_order_and_uses_short_timeout(self):
        client=self.client()
        client.stream('a'*32,0,7,[A+(16,),NEUTRAL+(16,)])
        request=client.opener.open.call_args.args[0]
        fields=urllib.parse.parse_qs(request.data.decode(),keep_blank_values=True)
        self.assertEqual(request.get_method(),'POST')
        self.assertEqual(request.full_url,'http://127.0.0.1/pad/stream')
        self.assertEqual(request.get_header('X-key'),'test-key')
        self.assertEqual(fields['frames'],['4,15,128,128,128,128,16;0,15,128,128,128,128,16'])
        self.assertEqual(client.opener.open.call_args.kwargs['timeout'],0.4)

    def test_heartbeat_has_no_frames_and_unconfirmed_packets_are_not_retried(self):
        client=self.client({'accepted':True,'next_sequence':2,'queued':0,'capacity':16})
        client.stream('a'*32,1,7,[])
        self.assertIn(b'frames=',client.opener.open.call_args.args[0].data)
        client.opener.open.side_effect=TimeoutError('network timeout')
        with self.assertRaises(TimeoutError):client.stream('a'*32,2,7,[])
        self.assertEqual(client.opener.open.call_count,2)

    def test_invalid_frame_and_incorrect_acknowledgement_stop_input(self):
        client=self.client()
        for frame in ((16384,15,128,128,128,128,16),A+(0,),A+(201,),A):
            with self.subTest(frame=frame),self.assertRaises(ValueError):
                client.stream('a'*32,0,7,[frame])
        client.opener.open.assert_not_called()
        client=self.client({'accepted':True,'next_sequence':9,'queued':0,'capacity':16})
        with self.assertRaisesRegex(RuntimeError,'未确认'):client.stream('a'*32,0,7,[A+(16,)])
        client.opener.open.assert_called_once()


class StreamSenderTests(unittest.TestCase):
    def setUp(self):
        self.started=threading.Event();self.release=threading.Event()
        self.events=[];self.packets=[]
        owner=self
        class SlowClient:
            def __init__(self,config):pass
            def stream(self,session,sequence,epoch,frames):
                owner.packets.append((session,sequence,epoch,frames))
                if len(owner.packets)==1:
                    owner.started.set()
                    if not owner.release.wait(2):raise TimeoutError('test release missing')
                return {'capacity':16,'queued':0}
        self.sender=StreamSender(lambda *event:self.events.append(event),SlowClient)
        self.sender.configure(CONFIG,STATUS)

    def tearDown(self):
        self.sender.close();self.release.set();self.sender.thread.join(2)
        self.assertFalse(self.sender.thread.is_alive())

    def until(self,condition):
        deadline=time.monotonic()+2
        while not condition():
            if time.monotonic()>deadline:self.fail('sender did not make progress')
            time.sleep(0.005)

    def test_slow_send_does_not_block_input_or_drop_short_presses(self):
        self.assertTrue(self.sender.set_state(A));self.assertTrue(self.started.wait(1))
        before=time.monotonic()
        for report in (NEUTRAL,B,NEUTRAL,A,NEUTRAL):self.assertTrue(self.sender.set_state(report))
        self.assertLess(time.monotonic()-before,0.05)
        self.release.set()
        self.until(lambda:sum(len(p[3]) for p in self.packets)>=6)
        frames=[frame[:6] for packet in self.packets for frame in packet[3]]
        self.assertEqual(frames,[A,NEUTRAL,B,NEUTRAL,A,NEUTRAL])
        self.assertEqual([p[1] for p in self.packets],list(range(len(self.packets))))

    def test_held_state_gets_heartbeats_without_repeated_press_frames(self):
        self.release.set();self.sender.set_state(A)
        self.until(lambda:len(self.packets)>=3)
        self.assertEqual([f[:6] for p in self.packets for f in p[3]],[A])
        self.assertTrue(all(not p[3] for p in self.packets[1:]))

    def test_stop_discards_pending_and_ignores_late_completion(self):
        self.sender.set_state(A);self.assertTrue(self.started.wait(1))
        self.sender.set_state(B);self.sender.stop();self.release.set()
        self.until(lambda:self.sender.session is None)
        time.sleep(0.12)
        self.assertEqual(len(self.packets),1)
        self.assertEqual(self.sender.desired,NEUTRAL)

    def test_queue_overflow_stops_instead_of_replaying_stale_input(self):
        self.sender.set_state(A);self.assertTrue(self.started.wait(1))
        for i in range(70):self.sender.set_state(B if i%2==0 else A)
        self.assertFalse(self.sender.enabled)
        self.assertFalse(self.sender.pending)
        self.assertTrue(any(event[0]=='stream_error' for event in self.events))
        self.release.set();time.sleep(0.05)
        self.assertEqual(len(self.packets),1)

    def test_expired_local_inputs_are_discarded_after_a_slow_request(self):
        self.sender.MAX_AGE=0.04
        self.sender.set_state(A);self.assertTrue(self.started.wait(1))
        self.sender.set_state(B);time.sleep(0.06);self.release.set()
        self.until(lambda:any(e[0]=='stream_error' for e in self.events))
        self.assertEqual(len(self.packets),1)
        self.assertFalse(self.sender.enabled)

    def test_board_epoch_change_discards_old_pending_input(self):
        self.sender.set_state(A);self.assertTrue(self.started.wait(1))
        self.sender.set_state(B)
        changed=dict(STATUS,gamepad=dict(STATUS['gamepad'],epoch=8))
        self.sender.configure(CONFIG,changed)
        self.release.set();time.sleep(0.05)
        self.assertEqual(len(self.packets),1)
        self.assertEqual(self.sender.desired,NEUTRAL)

    def test_unknown_live_session_is_not_replaced_after_reconnect(self):
        active=dict(STATUS,gamepad=dict(STATUS['gamepad'],stream={'supported':True,'active':True}))
        self.sender.configure(CONFIG,active)
        self.assertFalse(self.sender.set_state(A))
        self.assertFalse(self.sender.enabled)
        self.assertFalse(self.packets)


class ActionStateTests(unittest.TestCase):
    def test_combinations_and_opposing_directions(self):
        self.assertEqual(report_for_actions(['A','B','LS_UP','LS_RIGHT']), (6,15,255,0,128,128))
        self.assertEqual(report_for_actions(['LS_LEFT','LS_RIGHT','DPAD_UP','DPAD_DOWN']),NEUTRAL)
        self.assertEqual(report_for_actions(['DPAD_UP','DPAD_RIGHT'])[1],1)


@unittest.skipUnless(sys.platform=='win32','Windows Tk input check')
class StreamViewTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.temporary=tempfile.TemporaryDirectory()
        self.root=tk.Tk();self.root.withdraw();self.reports=[]
        self.view=ProControllerView(self.root,on_action=lambda **kw:True,
            mapping_path=Path(self.temporary.name)/'keys.json',on_state=lambda r:self.reports.append(r))
        self.view.set_streaming(True);self.view.set_available(True)

    def tearDown(self):
        self.root.destroy();self.temporary.cleanup()

    def key(self,name,code):return SimpleNamespace(keysym=name,keycode=code,state=0)

    def test_hold_combinations_and_release_do_not_disable_controls(self):
        self.view._key_press(self.key('j',74));self.view._key_press(self.key('j',74))
        self.view._key_press(self.key('k',75));self.view._key_release(self.key('j',74))
        self.view._key_release(self.key('k',75))
        self.assertEqual(self.reports,[A,(6,15,128,128,128,128),B,NEUTRAL])
        self.assertEqual(self.view.controls['A'].state,'normal')

    def test_repeat_generates_release_edges_and_stops_on_focus_loss(self):
        self.view.repeat.set(True)
        self.view.repeat_ms.set('50')
        self.view._key_press(self.key('j',74))
        self.root.after(90,self.root.quit);self.root.mainloop()
        self.assertEqual(self.reports[:3],[A,NEUTRAL,A])
        self.view.reset_keys()
        self.assertEqual(self.reports[-1],NEUTRAL)
        self.assertIsNone(self.view._repeat_timer)
        self.assertFalse(self.view.held_actions)

    def test_mouse_release_and_edit_mode_always_release_held_state(self):
        control=self.view.controls['A']
        self.view._click(SimpleNamespace(x=control.x,y=control.y))
        self.assertEqual(self.reports[-1],A)
        self.view._mouse_release(None)
        self.assertEqual(self.reports[-1],NEUTRAL)
        self.view._key_press(self.key('j',74));self.view.set_mode('edit')
        self.assertEqual(self.reports[-1],NEUTRAL)

    def test_stop_does_not_restart_from_os_key_repeat_until_key_is_released(self):
        event=self.key('j',74)
        self.view._key_press(event)
        self.view.reset_keys(block_held=True)
        self.view._key_press(event)
        self.assertEqual(self.reports,[A,NEUTRAL])
        self.view._key_release(event);self.view._key_press(event)
        self.assertEqual(self.reports[-1],A)


if __name__=='__main__':unittest.main()

import threading
import unittest
from unittest.mock import patch

import remote
from client import TransferCancelled


class CapsClient(remote.RemoteClient):
    def __init__(self,leds=2):
        self.leds=leds;self.toggles=[];self.waited=[];self.queries=0;self.boot=7
        self.return_boot=7
    def status(self):
        self.queries+=1
        return dict(mode='dual',boot_id=self.return_boot,input_ready=True,leds=self.leds,busy=False,release_pending=False)
    def tap(self,key,mod=0):
        self.toggles.append((key,mod));return {'job':'caps-toggle'}
    def wait_job(self,job,count,cancel,timeout):
        self.waited.append((job,count));self.leds&=~2


class CapsTests(unittest.TestCase):
    def test_caps_is_toggled_once_and_confirmed_before_text(self):
        client=CapsClient()
        def send(parent,text,gap,line,layout):
            self.assertEqual(parent.leds&2,0)
            self.assertEqual(parent.toggles,[(0x39,0)])
            return dict(accepted=True)
        with patch.object(remote.keyboard.Client,'send',send):
            self.assertTrue(client.send('Aa09')['accepted'])
        self.assertEqual(client.waited,[('caps-toggle',1)])

    def test_caps_already_off_sends_no_toggle(self):
        client=CapsClient(1);client.ensure_caps_off()
        self.assertEqual(client.toggles,[])

    def test_invalid_text_or_timing_never_adjusts_caps(self):
        client=CapsClient()
        for text,kwargs in (('中文',{}),('ok',dict(gap=0)),('ok',dict(layout='invalid'))):
            with self.subTest(text=text,kwargs=kwargs),self.assertRaises(ValueError):client.send(text,**kwargs)
        self.assertEqual(client.toggles,[])
        self.assertEqual(client.queries,0)

    def test_raw_shortcuts_are_not_transformed(self):
        client=CapsClient();client.tap(0x16,1)
        self.assertEqual(client.toggles,[(0x16,1)])

    def test_cancellation_prevents_caps_toggle(self):
        client=CapsClient();token=threading.Event();token.set()
        with self.assertRaises(TransferCancelled):client.ensure_caps_off(token)
        self.assertEqual(client.toggles,[])

    def test_reconnect_during_caps_adjustment_blocks_text(self):
        client=CapsClient()
        def wait(*args):client.leds=0;client.return_boot=8
        client.wait_job=wait
        with patch.object(remote.keyboard.Client,'send') as send:
            with self.assertRaisesRegex(RuntimeError,'重新连接'):client.send('Aa09')
            send.assert_not_called()

    def test_missing_led_confirmation_does_not_retry_toggle_or_send_text(self):
        client=CapsClient();client.wait_job=lambda *args:None
        with patch.object(remote.time,'monotonic',side_effect=[0,0,4]),patch.object(remote.keyboard.Client,'send') as send:
            with self.assertRaisesRegex(RuntimeError,'正文尚未发送'):client.send('Aa09')
            send.assert_not_called()
        self.assertEqual(client.toggles,[(0x39,0)])


if __name__=='__main__':unittest.main(verbosity=2)

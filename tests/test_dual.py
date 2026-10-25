import io
import json
import threading
import unittest

import controller
import remote


class Opener:
    def __init__(self):
        self.calls=[]
        self.root=dict(mode='dual',usb_mounted=True,usb_suspended=False,input_ready=True,
                       busy=True,release_pending=False,job='keyboard-job',sent=2,total=10,error='',
                       gamepad=dict(mode='gamepad',usb_mounted=True,usb_suspended=False,input_ready=True,
                                    logic_self_test=True,busy=False,release_pending=False,job='pad-job',
                                    epoch=123,error='',completed=False))

    def open(self,request,timeout=None):
        self.calls.append(request)
        if request.full_url.endswith('/pad'):
            self.root['gamepad'].update(job=request.get_header('X-job'),completed=True)
            reply=dict(accepted=True)
        else:reply=self.root
        return io.StringIO(json.dumps(reply))


class DualTests(unittest.TestCase):
    def setUp(self):
        self.client=controller.Client(dict(url='http://board',api_key='test'))
        self.opener=Opener();self.client.opener=self.opener

    def test_gamepad_reads_its_own_job_and_busy_state(self):
        status=self.client.status()
        self.assertEqual(status['job'],'pad-job')
        self.assertFalse(status['busy'])
        self.assertEqual(status['device_mode'],'dual')

    def test_pad_command_can_complete_while_keyboard_is_busy(self):
        result=self.client.pulse(buttons='A')
        self.assertTrue(result['completed'])
        self.assertEqual(self.opener.root['job'],'keyboard-job')
        self.assertEqual(sum(r.full_url.endswith('/pad') for r in self.opener.calls),1)

    def test_unready_keyboard_does_not_block_ready_gamepad(self):
        self.opener.root['input_ready']=False
        self.assertTrue(self.client.pulse(buttons='B')['completed'])

    def test_unready_gamepad_blocks_press_even_if_keyboard_ready(self):
        self.opener.root['gamepad']['input_ready']=False
        with self.assertRaisesRegex(RuntimeError,'正在连接'):self.client.pulse(buttons='A')
        self.assertFalse(any(r.full_url.endswith('/pad') for r in self.opener.calls))

    def test_keyboard_guard_accepts_dual_but_requires_connection_grace(self):
        client=remote.RemoteClient(dict(url='http://board',api_key='test'))
        client.opener=self.opener
        self.assertEqual(client.require_mode('keyboard')['job'],'keyboard-job')
        self.opener.root['input_ready']=False
        with self.assertRaisesRegex(RuntimeError,'正在连接'):client.require_mode('keyboard')

    def test_missing_pad_status_is_rejected(self):
        del self.opener.root['gamepad']
        with self.assertRaisesRegex(RuntimeError,'缺少手柄状态'):self.client.status()

    def test_cancel_is_global_and_never_submits_a_press(self):
        self.client.cancel()
        self.assertEqual(len(self.opener.calls),1)
        self.assertTrue(self.opener.calls[0].full_url.endswith('/cancel'))


if __name__=='__main__':unittest.main(verbosity=2)

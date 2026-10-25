import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
import urllib.parse

from controller import BUTTONS, Cancelled, Client, pad_state


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.dispatch()

            def do_POST(self):
                self.dispatch()

            def dispatch(self):
                owner = self.server.owner
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                fields = urllib.parse.parse_qs(body.decode())
                owner.calls.append((self.command, self.path, dict(self.headers), fields))
                code, result = 200, {}
                if self.headers.get('X-Key') != 'test-key':
                    code, result = 401, {'error': 'authentication_required'}
                elif self.path == '/status':
                    result = dict(owner.status)
                    if owner.on_status:
                        owner.on_status()
                elif self.path == '/pad':
                    if owner.reject:
                        code, result = 409, {'error': owner.reject}
                    else:
                        owner.status['job'] = self.headers['X-Job']
                        owner.status['completed'] = True
                        result = {'accepted': True, 'job': owner.status['job']}
                        code = 202
                elif self.path == '/cancel':
                    result = {'cancelled': True}
                else:
                    code, result = 404, {'error': 'not_found'}
                data = json.dumps(result).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.owner = self
        self.calls = []
        self.reject = None
        self.on_status = None
        self.status = dict(mode='gamepad', logic_self_test=True, usb_mounted=True,
                           usb_suspended=False, busy=False, release_pending=False,
                           epoch=4294967295, job='', error='', completed=False)
        self.client = Client(dict(url=f'http://127.0.0.1:{self.server.server_port}', api_key='test-key'))

    def test_combo_axes_and_maximum_epoch_are_encoded(self):
        self.client.pulse(buttons='L+R+A', dpad='UP_LEFT', lx=0, ry=255, hold=500)
        presses = [call for call in self.calls if call[1] == '/pad']
        self.assertEqual(len(presses), 1)
        method, _, headers, data = presses[0]
        self.assertEqual(method, 'POST')
        self.assertEqual(headers['X-Key'], 'test-key')
        self.assertEqual(len(headers['X-Job']), 32)
        self.assertEqual(data, {'buttons': [str(BUTTONS['L'] | BUTTONS['R'] | BUTTONS['A'])],
                                'dpad': ['7'], 'lx': ['0'], 'ly': ['128'], 'rx': ['128'],
                                'ry': ['255'], 'hold': ['500'], 'epoch': ['4294967295']})

    def test_wrong_firmware_cannot_receive_a_press(self):
        self.status['mode'] = 'keyboard'
        with self.assertRaisesRegex(RuntimeError, '不是手柄'):
            self.client.pulse(buttons='A')
        self.assertEqual([call[1] for call in self.calls], ['/status'])

    def test_no_press_when_usb_unavailable(self):
        self.status['usb_mounted'] = False
        with self.assertRaisesRegex(RuntimeError, 'USB'):
            self.client.pulse(buttons='A')
        self.assertFalse(any(call[1] == '/pad' for call in self.calls))

    def test_busy_and_unconfirmed_release_block_input(self):
        for key in ('busy', 'release_pending'):
            with self.subTest(key=key):
                self.status[key] = True
                with self.assertRaisesRegex(RuntimeError, '忙碌'):
                    self.client.pulse(buttons='A')
                self.status[key] = False
        self.assertFalse(any(call[1] == '/pad' for call in self.calls))

    def test_self_test_failure_blocks_input(self):
        self.status['logic_self_test'] = False
        with self.assertRaisesRegex(RuntimeError, '自检'):
            self.client.pulse(buttons='A')

    def test_pre_cancelled_task_never_contacts_board(self):
        token = threading.Event()
        token.set()
        with self.assertRaises(Cancelled):
            self.client.pulse(cancel=token, buttons='A')
        self.assertEqual(self.calls, [])

    def test_cancel_during_status_does_not_send_delayed_press(self):
        token = threading.Event()
        self.on_status = token.set
        with self.assertRaises(Cancelled):
            self.client.pulse(cancel=token, buttons='A')
        self.assertEqual([call[1] for call in self.calls], ['/status'])

    def test_conflict_is_not_retried(self):
        self.reject = 'stale_epoch'
        with self.assertRaisesRegex(RuntimeError, 'stale_epoch'):
            self.client.pulse(buttons='A')
        self.assertEqual(sum(call[1] == '/pad' for call in self.calls), 1)

    def test_authentication_error_is_not_retried(self):
        self.client.config['api_key'] = 'wrong-key'
        with self.assertRaisesRegex(RuntimeError, 'authentication_required'):
            self.client.status()
        self.assertEqual(len(self.calls), 1)

    def test_cancel_is_post_without_automatic_press(self):
        self.assertTrue(self.client.cancel()['cancelled'])
        self.assertEqual([(call[0], call[1]) for call in self.calls], [('POST', '/cancel')])


class ValidationTests(unittest.TestCase):
    def test_rejects_invalid_controls_before_network(self):
        for arguments in ({'buttons': 'C'}, {'buttons': 'A++BOGUS'}, {'lx': -1}, {'rx': 256},
                          {'hold': 49}, {'hold': 2001}, {'hold': True}, {'ly': '128'}, {'dpad': '8'}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                pad_state(**arguments)

    def test_neutral_and_button_layout(self):
        self.assertEqual(pad_state(), dict(buttons=0, dpad=15, lx=128, ly=128, rx=128, ry=128, hold=150))
        self.assertEqual(pad_state(buttons='a,a')['buttons'], 4)
        self.assertEqual(pad_state(buttons='HOME+CAPTURE')['buttons'], 0x3000)

    def test_rejects_non_http_or_embedded_credentials(self):
        for url in ('https://example.com', 'file:///tmp/a', 'http://user:pass@host', 'bad'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Client(dict(url=url, api_key='test'))


if __name__ == '__main__':
    unittest.main(verbosity=2)

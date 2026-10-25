import io
import json
import threading
import unittest
from unittest.mock import Mock

from client import TransferCancelled
from remote import RemoteClient


class WakeProtocolTests(unittest.TestCase):
    def client(self, response=None):
        client = RemoteClient({'url': 'http://127.0.0.1', 'api_key': 'test-key'})
        client.opener = Mock()
        client.opener.open.return_value = io.BytesIO(json.dumps(
            response if response is not None else {'accepted': True, 'duration_ms': 1000}
        ).encode())
        return client

    def test_wake_is_one_authenticated_post_without_usb_precondition(self):
        client = self.client()
        client.status = Mock(side_effect=AssertionError('USB readiness must not gate wake'))
        self.assertTrue(client.wakeup()['accepted'])
        client.opener.open.assert_called_once()
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.full_url, 'http://127.0.0.1/switch2/wakeup')
        self.assertEqual(request.get_header('X-key'), 'test-key')
        self.assertEqual(request.data, b'')

    def test_timeout_is_never_retried(self):
        client = self.client()
        client.opener.open.side_effect = TimeoutError('unconfirmed')
        with self.assertRaises(TimeoutError):
            client.wakeup()
        self.assertEqual(client.opener.open.call_count, 1)

    def test_pre_cancelled_wake_never_sends(self):
        client = self.client()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(TransferCancelled):
            client.wakeup(stop)
        client.opener.open.assert_not_called()

    def test_missing_acknowledgement_is_not_reported_as_success(self):
        for response in ({'ok': True}, {'accepted': True}, {'accepted': False, 'duration_ms': 1000}):
            with self.subTest(response=response):
                client = self.client(response)
                with self.assertRaisesRegex(RuntimeError, '未确认'):
                    client.wakeup()
                self.assertEqual(client.opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()

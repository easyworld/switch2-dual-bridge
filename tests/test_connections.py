import io
import json
import unittest
from unittest.mock import Mock

from remote import RemoteClient


class ConnectionProtocolTests(unittest.TestCase):
    def client(self, response):
        client=RemoteClient({'url':'http://127.0.0.1','api_key':'test-key'})
        client.opener=Mock()
        client.opener.open.return_value=io.BytesIO(json.dumps(response).encode())
        return client

    def test_connect_and_disconnect_are_authenticated_posts(self):
        for enabled,route in ((True,'/connect'),(False,'/disconnect')):
            with self.subTest(enabled=enabled):
                client=self.client({'usb_enabled':enabled})
                client.set_host_connection(enabled)
                client.opener.open.assert_called_once()
                request=client.opener.open.call_args.args[0]
                self.assertEqual(request.full_url,'http://127.0.0.1'+route)
                self.assertEqual(request.get_method(),'POST')
                self.assertEqual(request.data,b'')
                self.assertEqual(request.get_header('X-key'),'test-key')

    def test_unconfirmed_state_is_not_success(self):
        for enabled,response in ((False,{}),(False,{'usb_enabled':True}),(True,{'usb_enabled':False})):
            with self.subTest(enabled=enabled,response=response):
                client=self.client(response)
                with self.assertRaisesRegex(RuntimeError,'未确认'):
                    client.set_host_connection(enabled)
                client.opener.open.assert_called_once()

    def test_connection_timeout_does_not_retry(self):
        client=self.client({})
        client.opener.open.side_effect=TimeoutError('unconfirmed')
        with self.assertRaises(TimeoutError):client.set_host_connection(False)
        client.opener.open.assert_called_once()

    def test_invalid_state_is_rejected_before_request(self):
        client=self.client({})
        for value in (0,1,None,'false'):
            with self.assertRaises(ValueError):client.set_host_connection(value)
        client.opener.open.assert_not_called()


if __name__=='__main__':unittest.main()

import http.server
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import urllib.parse

from client import Client, normalize_text, TransferCancelled


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()
            def do_POST(self):
                self.respond()
            def respond(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                cls.calls.append((self.command, self.path, self.headers, body))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            def log_message(self, *args):
                pass
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1',0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.client = Client({'url':f'http://127.0.0.1:{cls.server.server_port}','api_key':'test-key'})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_text_keeps_basic_symbols_and_normalizes_line_endings(self):
        text = 'V=START(39380,192,168,3,20)\r\nREMOVE NSR,&H60000000,&H3FFFFFFF\r\n'
        self.client.send(text,12,80,'jis')
        method,path,headers,body=self.calls[-1]
        self.assertEqual(method,'POST')
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(path).query)['layout'],['jis'])
        self.assertEqual(headers['X-Key'],'test-key')
        self.assertEqual(len(headers['X-Job']),32)
        self.assertEqual(body,text.replace('\r\n','\n').encode())

    def test_duplicate_submissions_use_distinct_ids(self):
        self.client.send('a')
        first=self.calls[-1][2]['X-Job']
        self.client.send('a')
        self.assertNotEqual(first,self.calls[-1][2]['X-Job'])

    def test_rejects_unicode_controls_and_oversize_before_request(self):
        count=len(self.calls)
        for text in ('中文','\x00','\x1b','a'*16385,''):
            with self.assertRaises(ValueError):
                self.client.send(text)
        self.assertEqual(count,len(self.calls))

    def test_every_printable_ascii_is_accepted(self):
        normalize_text(''.join(chr(c) for c in range(32,127))+'\t\n')

    def test_cancel_is_post_without_job(self):
        self.client.cancel()
        self.assertEqual(self.calls[-1][0:2],('POST','/cancel'))
        self.assertIsNone(self.calls[-1][2].get('X-Job'))


class AutoIndentEditor(Client):
    """Model copied leading whitespace, selections, and empty-line deletion."""
    def __init__(self):
        self.lines=['']
        self.column=0
        self.selection=None
        self.job=0
        self.count=0
        self.cancelled=False

    def accepted(self,count):
        self.job+=1
        self.count=count
        return {'job':str(self.job)}

    def tap(self,key,mod=0):
        if key==0x4a:
            self.column=0
            self.selection=None
        elif key==0x4d and mod==2:
            self.selection=(self.column,len(self.lines[-1]))
            self.column=len(self.lines[-1])
        elif key==0x4c:
            start,end=self.selection or (self.column,self.column+1)
            self.lines[-1]=self.lines[-1][:start]+self.lines[-1][end:]
            self.column=start
            self.selection=None
        elif key==0x2a:
            raise AssertionError('Backspace would merge an empty line with the previous one')
        else:
            raise AssertionError(f'Unexpected editor key {key}/{mod}')
        return self.accepted(1)

    def send(self,text,gap=12,line=80,layout='us'):
        for c in text:
            if c=='\n':
                previous=self.lines[-1]
                prefix=previous[:len(previous)-len(previous.lstrip(' \t'))]
                self.lines.append(prefix)
                self.column=len(prefix)
            else:
                row=self.lines[-1]
                self.lines[-1]=row[:self.column]+c+row[self.column:]
                self.column+=1
        return self.accepted(len(text))

    def status(self):
        return {'job':str(self.job),'busy':False,'sent':self.count,'error':''}

    def cancel(self):
        self.cancelled=True


class EditorTransferTests(unittest.TestCase):
    def test_nested_and_decreasing_indentation_are_exact(self):
        source='COMMON DEF A()\n VAR X=0\n FOR I=0 TO 3\n  X=X+1\n NEXT\n RETURN X\nEND\n'
        editor=AutoIndentEditor()
        result=editor.send_editor(source,line=0)
        self.assertEqual('\n'.join(editor.lines),source)
        self.assertEqual(result['sent'],len(source))

    def test_blank_lines_and_final_indentation_are_exact(self):
        for source in ('a\n\n b\n\nEND\n','a\n b\n','a\n b','\n\n','a\n\t b\n'):
            editor=AutoIndentEditor()
            editor.send_editor(source,line=0)
            self.assertEqual('\n'.join(editor.lines),source)

    def test_cancel_prevents_further_lines(self):
        stop=threading.Event()
        editor=AutoIndentEditor()
        def progress(*args):
            stop.set()
        with self.assertRaises(TransferCancelled):
            editor.send_editor('a\nb\n',line=0,cancel=stop,progress=progress)
        self.assertEqual('\n'.join(editor.lines),'a\n')
        self.assertTrue(editor.cancelled)

    def test_replaced_job_aborts(self):
        editor=AutoIndentEditor()
        with self.assertRaises(RuntimeError):
            editor.wait_job('another-job',1,threading.Event(),1)


@unittest.skipUnless(sys.platform=='win32','Windows GUI lifecycle check')
class GuiLifecycleTests(unittest.TestCase):
    def test_close_exits_process_while_network_requests_are_blocked(self):
        script=r'''
import json,tempfile,time,tkinter as tk
from pathlib import Path
import remote
config=json.loads((remote.ROOT/'client-config.json').read_text(encoding='utf-8-sig'))
temporary=tempfile.TemporaryDirectory()
remote.ROOT=Path(temporary.name)
remote.launch_cancel=lambda config:None
def blocked(self):
    time.sleep(30)
remote.RemoteClient.status=blocked
original=tk.Tk
def make_root(*a,**k):
    root=original(*a,**k)
    root.withdraw()
    root.after(250,lambda:root.tk.call(root.protocol('WM_DELETE_WINDOW')))
    return root
tk.Tk=make_root
remote.gui(config)
print('GUI_CLOSE_OK')
'''
        result=subprocess.run([sys.executable,'-B','-c',script],cwd=Path(__file__).resolve().parents[1],
                              capture_output=True,text=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('GUI_CLOSE_OK',result.stdout)


if __name__=='__main__':
    unittest.main()

"""Exercise polling and clicks in the real Tk event loop without hardware."""
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows GUI check')
class GuiPollingTests(unittest.TestCase):
    def test_stream_input_stays_enabled_while_network_send_is_blocked(self):
        script = r'''
import tempfile, threading, tkinter as tk
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
import remote
from pro_controller import ProControllerView
temporary = tempfile.TemporaryDirectory()
remote.ROOT = Path(temporary.name)
config = {'url':'http://127.0.0.1','api_key':'test-key'}
started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
packets, failures = [], []
remote.RemoteClient.status = lambda self: {
    'mode':'dual','boot_id':1,'usb_mounted':True,'usb_suspended':False,
    'input_ready':True,'busy':False,'release_pending':False,
    'gamepad':{'epoch':7,'input_ready':True,'legacy_busy':False,'release_pending':False,
               'busy':True,'stream':{'supported':True,'active':bool(packets)}}}
remote.RemoteClient.cancel = lambda self: cancelled.set()
def stream(self,session,sequence,epoch,frames):
    packets.append(list(frames))
    if len(packets) == 1:
        started.set()
        assert release.wait(3), 'GUI failed to release blocked transport'
    return {'capacity':16,'queued':0}
remote.pad.Client.stream = stream
def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)
original_tk = tk.Tk
def make_root(*args,**kwargs):
    root = original_tk(*args,**kwargs)
    root.withdraw()
    phase = 0
    def observe():
        nonlocal phase
        try:
            widgets=list(descendants(root))
            view=next(w for w in widgets if isinstance(w,ProControllerView))
            key=lambda name,code:SimpleNamespace(keysym=name,keycode=code,state=0)
            if phase==0 and view.streaming and view.available:
                view._key_press(key('j',74));phase=1
            elif phase==1 and started.is_set():
                assert view.controls['A'].state=='normal', 'Stream disabled the controller'
                view._key_release(key('j',74))
                view._key_press(key('k',75));view._key_release(key('k',75))
                assert view.controls['X'].invoke(), 'Rapid click was rejected'
                send=next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='发送全文')
                assert str(send.cget('state'))=='normal', 'Gamepad blocked the keyboard channel'
                release.set();phase=2
            elif phase==2 and sum(map(len,packets))>=6:
                frames=[frame for packet in packets for frame in packet]
                assert [frame[0] for frame in frames[:6]]==[4,0,2,0,8,0], frames
                stop=next(w for w in widgets if isinstance(w,ttk.Button) and str(w.cget('text')).startswith('停止 /'))
                stop.invoke();phase=3
            elif phase==3 and cancelled.is_set():
                assert not view.held_actions
                root.destroy();return
        except Exception as error:
            failures.append(repr(error));release.set();root.destroy();return
        root.after(10,observe)
    def timeout():
        failures.append('Asynchronous GUI timed out');release.set();root.destroy()
    root.after(10,observe);root.after(4000,timeout)
    return root
tk.Tk=make_root
remote.gui(config)
assert not failures, failures
print('GUI_STREAM_OK')
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,
            text=True,encoding='utf-8',timeout=7)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('GUI_STREAM_OK',result.stdout)

    def test_callback_failure_does_not_lock_gamepad_after_first_press(self):
        script = r'''
import tempfile, time, tkinter as tk
from pathlib import Path
from tkinter import ttk
import remote
from pro_controller import ProControllerView

temporary = tempfile.TemporaryDirectory()
remote.ROOT = Path(temporary.name)
config = {'url': 'http://127.0.0.1', 'api_key': 'test-key'}
presses, failures, injected = [], [], []
remote.RemoteClient.status = lambda self: {
    'mode': 'dual', 'usb_mounted': True, 'usb_suspended': False,
    'input_ready': True, 'busy': False, 'release_pending': False,
    'gamepad': {'input_ready': True, 'busy': False, 'release_pending': False}}

def pulse(self, cancel=None, **values):
    presses.append(values)
    time.sleep(0.08)
    if len(presses) == 2:
        raise TimeoutError('simulated network timeout')
    return {}
remote.pad.Client.pulse = pulse

original_setitem = ttk.Progressbar.__setitem__
def setitem(self, key, value):
    if key == 'value' and value == 100 and not injected:
        injected.append(True)
        raise RuntimeError('simulated Tk refresh failure')
    return original_setitem(self, key, value)
ttk.Progressbar.__setitem__ = setitem

def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)

original_tk = tk.Tk
def make_root(*args, **kwargs):
    root = original_tk(*args, **kwargs)
    root.withdraw()
    submitted = 0
    def observe():
        nonlocal submitted
        try:
            view = next(w for w in descendants(root) if isinstance(w, ProControllerView))
            button = view.controls['A']
            if button.state == 'normal':
                if submitted == 3:
                    assert len(presses) == 3, presses
                    assert injected, 'Refresh failure was not exercised'
                    log = (remote.ROOT / 'remote-errors.log').read_text(encoding='utf-8')
                    assert 'simulated Tk refresh failure' in log, log
                    root.destroy()
                    return
                assert button.invoke()
                submitted += 1
                assert button.state == 'disabled', 'Duplicate input must stay blocked'
                assert not button.invoke()
        except Exception as error:
            failures.append(repr(error))
            root.destroy()
            return
        root.after(10, observe)
    def timeout():
        failures.append('Gamepad stayed disabled after callback failure')
        root.destroy()
    root.after(10, observe)
    root.after(4000, timeout)
    return root
tk.Tk = make_root
remote.gui(config)
assert not failures, failures
print('GUI_RECOVERY_OK')
'''
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-B', '-c', script],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, encoding='utf-8', timeout=7,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('GUI_RECOVERY_OK', result.stdout)

    def test_wake_can_run_while_usb_is_suspended_and_recovers(self):
        script = r'''
import tempfile, threading, tkinter as tk
from pathlib import Path
from tkinter import ttk
import remote
from pro_controller import ProControllerView

temporary = tempfile.TemporaryDirectory()
remote.ROOT = Path(temporary.name)
config = {'url': 'http://127.0.0.1', 'api_key': 'test-key'}
requests, failures = [], []
started, release, completed = (threading.Event() for _ in range(3))

def read_status(self):
    awake = completed.is_set()
    return {'mode': 'dual', 'usb_mounted': awake, 'usb_suspended': not awake,
            'input_ready': awake, 'busy': False,
            'gamepad': {'input_ready': awake, 'busy': False},
            'wake': {'supported': True, 'ready': True, 'advertising': False, 'error': ''}}

def request(self, route, data=None, **kwargs):
    assert route == '/switch2/wakeup' and data == b''
    requests.append(route)
    started.set()
    release.wait(3)
    completed.set()
    return {'accepted': True, 'duration_ms': 1000}

remote.RemoteClient.status = read_status
remote.RemoteClient.request = request
def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)

original_tk = tk.Tk
def make_root(*args, **kwargs):
    root = original_tk(*args, **kwargs)
    root.withdraw()
    clicked = False
    def observe():
        nonlocal clicked
        try:
            widgets = list(descendants(root))
            buttons = {w.cget('text'): w for w in widgets if isinstance(w, ttk.Button)}
            buttons.update(next(w for w in widgets if isinstance(w,ProControllerView)).controls)
            wake, pad = buttons['唤醒 Switch 2'], buttons['A']
            if not clicked and str(pad.cget('state')) == 'disabled' and str(wake.cget('state')) == 'normal':
                wake.invoke()
                assert str(wake.cget('state')) == 'disabled', 'Duplicate wake must be blocked'
                wake.invoke()
                clicked = True
            if clicked and started.is_set():
                release.set()
            if completed.is_set() and str(pad.cget('state')) == 'normal':
                assert len(requests) == 1
                labels = [str(w.cget('text')) for w in widgets if isinstance(w, ttk.Label)]
                assert any('广播已发送；USB 已就绪' in text for text in labels), labels
                root.destroy()
                return
        except Exception as error:
            failures.append(repr(error))
            release.set()
            root.destroy()
            return
        root.after(10, observe)
    def timeout():
        failures.append('Wake GUI timed out')
        release.set()
        root.destroy()
    root.after(10, observe)
    root.after(4000, timeout)
    return root

tk.Tk = make_root
remote.gui(config)
assert not failures, failures
assert len(requests) == 1
print('GUI_WAKE_OK')
'''
        result = subprocess.run(
            [sys.executable, '-B', '-c', script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding='utf-8', timeout=7,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('GUI_WAKE_OK', result.stdout)

    def test_polling_keeps_buttons_stable_and_accepts_one_click(self):
        script = r'''
import tempfile, threading, time, tkinter as tk
from pathlib import Path
from tkinter import ttk
import remote
from pro_controller import ProControllerView, CanvasControl

temporary = tempfile.TemporaryDirectory()
remote.ROOT = Path(temporary.name)
config = {'url': 'http://127.0.0.1', 'api_key': 'test-key'}
reads, presses, changes, failures = [], [], [], []
query_waiting, query_release, input_done = (threading.Event() for _ in range(3))
status = {'mode': 'dual', 'usb_mounted': True, 'usb_suspended': False,
          'input_ready': True, 'busy': False, 'release_pending': False,
          'gamepad': {'input_ready': True, 'busy': False, 'release_pending': False}}

def read_status(self):
    reads.append(1)
    if len(reads) == 3:
        query_waiting.set()
        query_release.wait(5)
        # A pre-input snapshot must not overwrite an input already queued.
        return dict(status, usb_mounted=False)
    time.sleep(0.15)
    return status

def pulse(self, cancel=None, **values):
    assert not cancel.is_set()
    presses.append(values)
    time.sleep(0.2)
    input_done.set()
    return {}

remote.RemoteClient.status = read_status
remote.pad.Client.pulse = pulse
original_configure = CanvasControl.configure
def configure(self, *args, **kwargs):
    if self.cget('text') == 'A' and 'state' in kwargs:
        changes.append(kwargs['state'])
    return original_configure(self, *args, **kwargs)
CanvasControl.configure = configure

def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)

original_tk = tk.Tk
def make_root(*args, **kwargs):
    root = original_tk(*args, **kwargs)
    root.withdraw()
    ready = clicked = False
    def observe():
        nonlocal ready, clicked
        try:
            widgets = list(descendants(root))
            button = next(w for w in widgets if isinstance(w,ProControllerView)).controls['A']
            send = next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '发送全文')
            if not ready and reads and str(button.cget('state')) == 'normal':
                ready = True
                changes.clear()
            if ready and not clicked:
                assert str(button.cget('state')) == 'normal', 'Poll disabled gamepad controls'
                assert str(send.cget('state')) == 'normal', 'Poll disabled keyboard controls'
                assert not changes, f'Poll repainted unchanged button state: {changes}'
                if query_waiting.is_set():
                    button.invoke()
                    assert str(button.cget('state')) == 'disabled', 'Input must prevent duplicate clicks'
                    button.invoke()
                    clicked = True
                    query_release.set()
            if clicked and input_done.is_set() and len(reads) >= 4 and str(button.cget('state')) == 'normal':
                assert len(presses) == 1, f'Expected one input, got {len(presses)}'
                assert presses[0]['buttons'] == 'A'
                root.destroy()
                return
        except Exception as error:
            failures.append(repr(error))
            query_release.set()
            root.destroy()
            return
        root.after(10, observe)
    def timeout():
        failures.append('GUI polling test timed out')
        query_release.set()
        root.destroy()
    root.after(10, observe)
    root.after(6000, timeout)
    return root

tk.Tk = make_root
remote.gui(config)
assert not failures, failures
assert len(presses) == 1
print('GUI_POLL_OK')
'''
        result = subprocess.run(
            [sys.executable, '-B', '-c', script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, encoding='utf-8', timeout=9,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('GUI_POLL_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()

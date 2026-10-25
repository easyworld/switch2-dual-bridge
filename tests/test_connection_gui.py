"""Connection controls must preempt input and reject stale status snapshots."""
from pathlib import Path
import subprocess
import sys
import unittest


HARNESS=r'''
import tempfile, threading, time, tkinter as tk
from pathlib import Path
from tkinter import ttk
import remote
from pro_controller import ProControllerView, CanvasControl
temporary=tempfile.TemporaryDirectory()
remote.ROOT=Path(temporary.name)
config={'url':'http://127.0.0.1','api_key':'test-key'}
enabled=True
changes=[]
failures=[]
def status():
    return {'mode':'dual','usb_mounted':enabled,'usb_suspended':False,
            'input_ready':enabled,'busy':False,'gamepad':{'input_ready':enabled,'busy':False},
            'connection_control':{'supported':True,'usb_enabled':enabled},
            'wake':{'supported':True,'ready':True,'advertising':False,'error':''}}
def change(self,value):
    global enabled
    changes.append(value)
    enabled=value
    return {'usb_enabled':value}
remote.RemoteClient.set_host_connection=change
def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)
def all_buttons(root):
    widgets=list(descendants(root))
    buttons={w.cget('text'):w for w in widgets if isinstance(w,ttk.Button)}
    buttons.update(next(w for w in widgets if isinstance(w,ProControllerView)).controls)
    return buttons
original_tk=tk.Tk
'''


@unittest.skipUnless(sys.platform=='win32','Windows GUI check')
class ConnectionGuiTests(unittest.TestCase):
    def run_gui(self,script):
        result=subprocess.run([sys.executable,'-B','-c',HARNESS+script],
                              cwd=Path(__file__).resolve().parents[1],capture_output=True,
                              text=True,encoding='utf-8',timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('CONNECTION_GUI_OK',result.stdout)

    def test_disconnect_preempts_input_and_reconnect_restores_controls(self):
        self.run_gui(r'''
input_started=threading.Event()
queried=threading.Event()
presses=[]
def read_status(self):
    queried.set()
    return status()
remote.RemoteClient.status=read_status
def pulse(self,cancel=None,**values):
    presses.append(values)
    input_started.set()
    assert cancel.wait(4),'Disconnect did not cancel the active transfer'
    raise remote.keyboard.TransferCancelled('cancelled')
remote.pad.Client.pulse=pulse
def make_root(*args,**kwargs):
    root=original_tk(*args,**kwargs)
    root.withdraw()
    phase=0
    def observe():
        nonlocal phase
        try:
            buttons=all_buttons(root)
            pad=buttons['A'];disconnect=buttons['断开所有连接'];connect=buttons['重新连接主机'];wake=buttons['唤醒 Switch 2']
            if phase==0 and queried.is_set() and str(pad.cget('state'))=='normal' and str(disconnect.cget('state'))=='normal':
                pad.invoke();phase=1
            elif phase==1 and input_started.is_set():
                assert str(pad.cget('state'))=='disabled'
                assert str(disconnect.cget('state'))=='normal','Disconnect was blocked by input'
                disconnect.invoke();phase=2
            elif phase==2 and str(connect.cget('state'))=='normal':
                assert str(pad.cget('state'))=='disabled'
                assert str(wake.cget('state'))=='disabled'
                assert changes==[False]
                connect.invoke();phase=3
            elif phase==3 and str(pad.cget('state'))=='normal':
                assert str(wake.cget('state'))=='normal'
                assert changes==[False,True]
                assert len(presses)==1
                root.destroy();return
        except Exception as error:
            failures.append(repr(error));root.destroy();return
        root.after(10,observe)
    def timeout():failures.append('timeout');root.destroy()
    root.after(100,observe);root.after(5000,timeout)
    return root
tk.Tk=make_root
remote.gui(config)
assert not failures,failures
print('CONNECTION_GUI_OK')
''')

    def test_pre_disconnect_poll_cannot_reenable_disconnected_buttons(self):
        self.run_gui(r'''
query_waiting=threading.Event()
query_release=threading.Event()
query_returned=threading.Event()
reads=[]
repaints=[]
def read_status(self):
    reads.append(1)
    if len(reads)==2:
        stale=status()
        query_waiting.set()
        query_release.wait(4)
        query_returned.set()
        return stale
    return status()
remote.RemoteClient.status=read_status
original_configure=CanvasControl.configure
def configure(self,*args,**kwargs):
    if self.cget('text')=='A' and query_release.is_set() and kwargs.get('state')=='normal':
        repaints.append('stale snapshot enabled input')
    return original_configure(self,*args,**kwargs)
CanvasControl.configure=configure
def make_root(*args,**kwargs):
    root=original_tk(*args,**kwargs)
    root.withdraw()
    phase=0
    returned_at=None
    def observe():
        nonlocal phase,returned_at
        try:
            buttons=all_buttons(root)
            if phase==0 and query_waiting.is_set():
                assert str(buttons['A'].cget('state'))=='normal'
                buttons['断开所有连接'].invoke();phase=1
            elif phase==1 and str(buttons['重新连接主机'].cget('state'))=='normal':
                query_release.set();phase=2
            elif phase==2 and query_returned.is_set():
                if returned_at is None:returned_at=time.monotonic()
                if time.monotonic()-returned_at>0.2:
                    assert not repaints,repaints
                    assert str(buttons['A'].cget('state'))=='disabled'
                    assert changes==[False]
                    root.destroy();return
        except Exception as error:
            failures.append(repr(error));query_release.set();root.destroy();return
        root.after(10,observe)
    def timeout():failures.append('timeout');query_release.set();root.destroy()
    root.after(100,observe);root.after(5000,timeout)
    return root
tk.Tk=make_root
remote.gui(config)
assert not failures,failures
print('CONNECTION_GUI_OK')
''')


if __name__=='__main__':unittest.main()

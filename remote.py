"""Simultaneous USB keyboard and gamepad control for Switch 2."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import client as keyboard
import controller as pad

ROOT = Path(__file__).resolve().parent


def error_logger():
    logger = logging.getLogger('switch2-dual')
    if not logger.handlers:
        handler = RotatingFileHandler(ROOT / 'remote-errors.log', maxBytes=262144,
                                      backupCount=1, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    return logger


class RemoteClient(keyboard.Client):
    def set_host_connection(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('连接状态必须为布尔值。')
        result = self.request('/connect' if enabled else '/disconnect', b'')
        if result.get('usb_enabled') is not enabled:
            raise RuntimeError('连接控制未确认，请先查询状态。')
        return result

    def wakeup(self, cancel=None):
        """Request one BLE broadcast; USB may be disconnected or suspended."""
        if cancel is not None and cancel.is_set():
            raise keyboard.TransferCancelled('已停止。')
        result = self.request('/switch2/wakeup', b'')
        if result.get('accepted') is not True or result.get('duration_ms') != 1000:
            raise RuntimeError('未确认唤醒广播已启动，请先查询状态。')
        return result

    def ensure_caps_off(self, cancel=None):
        """One confirmed toggle before text; never retry an unobserved toggle."""
        cancel=cancel or threading.Event()
        if cancel.is_set():raise keyboard.TransferCancelled('已停止。')
        before=self.require_mode('keyboard')
        if not before.get('leds',0)&2:return
        if before.get('busy') or before.get('release_pending'):
            raise RuntimeError('键盘正在输入或释放，暂时不能调整 CapsLock。')
        accepted=self.tap(0x39,0)
        self.wait_job(accepted['job'],1,cancel,5)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if cancel.is_set():raise keyboard.TransferCancelled('已停止。')
            after=self.status()
            if after.get('boot_id')!=before.get('boot_id') or after.get('mode')!=before.get('mode'):
                raise RuntimeError('调整 CapsLock 时设备已重新连接，请重试文本输入。')
            if not after.get('leds',0)&2:return
            cancel.wait(0.05)
        raise RuntimeError('尚未确认 CapsLock 关闭，正文尚未发送；请查询状态。')

    def send(self,text,gap=12,line=80,layout='us'):
        keyboard.normalize_text(text)
        if not 5<=gap<=100 or not 0<=line<=2000 or layout not in ('us','jis'):
            raise ValueError('无效的速度或键盘布局。')
        self.ensure_caps_off()
        return super().send(text,gap,line,layout)

    def require_mode(self, mode):
        status = self.status()
        if status.get('mode') not in (mode, 'dual'):
            label = '键盘' if mode == 'keyboard' else '手柄'
            raise RuntimeError('请先切换到' + label + '模式。')
        if status.get('mode') == 'dual' and status.get('input_ready') is False:
            raise RuntimeError('USB 正在连接，请等待就绪后再输入。')
        if status.get('switch_pending'):
            raise RuntimeError('正在切换 USB 模式，请等待重连。')
        return status


def launch_cancel(config):
    kwargs = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, str(ROOT / 'remote.py'), '--url', config['url'], 'cancel'], **kwargs)


def gui(config, smoke=False):
    import tkinter as tk
    from tkinter import ttk, filedialog
    from tkinter.scrolledtext import ScrolledText
    from pro_controller import ProControllerView
    from pad_stream import StreamSender

    root = tk.Tk()
    root.title('Switch 2 USB 键盘与手柄')
    root.geometry('1000x830')
    root.minsize(900, 760)
    panel = ttk.Frame(root, padding=14)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='Switch 2 同时键盘与手柄', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(panel, text='键盘与手柄使用两个独立 USB 接口；切换标签页即可操作，板子保持连接。').pack(anchor='w', pady=(4, 10))
    top = ttk.Frame(panel)
    top.pack(fill='x')
    url = tk.StringVar(value=config['url'])
    ttk.Label(top, text='板子地址').pack(side='left')
    ttk.Entry(top, textvariable=url, width=32).pack(side='left', padx=8)
    status_text = tk.StringVar(value='正在查询连接状态…')
    mode_text = tk.StringVar(value='设备状态：未知')
    ttk.Label(top, textvariable=mode_text).pack(side='right')
    mode_bar = ttk.Frame(panel)
    mode_bar.pack(fill='x', pady=10)
    # One query and one input may be pending; the worker keeps requests ordered.
    events, work = queue.Queue(), queue.Queue(maxsize=2)
    stream_sender = StreamSender(lambda kind,result,error:events.put((kind,result,error)))
    state = {'inflight': False, 'query_inflight': False, 'control_inflight': False,
             'revision': 0, 'query_revision': 0, 'input_revision': 0,
             'closing': False, 'wake_requested': False, 'stream_fault': False,
             'status': {}, 'last_poll': 0.0}
    token = threading.Event()

    def settings():
        current = dict(config, url=url.get().strip().rstrip('/'))
        pad.Client(current)  # Validate URL without any I/O.
        return current

    def worker():
        while True:
            item = work.get()
            if item is None:
                return
            kind, current, action, stop = item
            try:
                result = action(RemoteClient(current), pad.Client(current), stop)
                events.put((kind, result, None))
            except Exception as error:
                events.put((kind, None, str(error)))
    threading.Thread(target=worker, daemon=True).start()

    def submit(kind, action):
        nonlocal token
        query = kind in ('status', 'poll')
        if state['inflight'] or state['control_inflight'] or state['closing']:
            if kind != 'poll':
                status_text.set('当前操作进行中，请等待完成或点击停止。')
            return
        if query and state['query_inflight']:
            return
        try:
            current = settings()
        except ValueError as error:
            status_text.set(str(error))
            return
        stop = threading.Event()
        if query:
            state['query_inflight'] = True
            state['query_revision'] = state['revision']
            state['last_poll'] = time.monotonic()
        else:
            token = stop
            state['inflight'] = True
            state['input_revision'] = state['revision']
        if kind != 'poll':
            status_text.set('正在执行…')
        work.put_nowait((kind, current, action, stop))
        update_controls()
        return True

    ttk.Button(top, text='连接 / 查询', command=lambda: submit('status', lambda c,p,t:c.status())).pack(side='left')
    wake_button = ttk.Button(top, text='唤醒 Switch 2', command=lambda: submit('wake', lambda c,p,t:c.wakeup(t)))
    wake_button.pack(side='left', padx=8)
    wake_text = tk.StringVar(value='蓝牙唤醒：正在查询…')
    ttk.Label(mode_bar,textvariable=wake_text).pack(side='left')

    tabs = ttk.Notebook(panel)
    bottom = ttk.Frame(panel)
    bottom.pack(side='bottom',fill='x')
    tabs.pack(fill='both', expand=True)
    key_tab, pad_tab = ttk.Frame(tabs, padding=10), ttk.Frame(tabs, padding=10)
    tabs.add(key_tab, text='键盘 / 文本输入')
    tabs.add(pad_tab, text='手柄 / 按钮与摇杆')
    key_controls = []
    line_mode = tk.BooleanVar(value=config.get('editor_mode', True))
    layout = tk.StringVar(value=config.get('layout', 'us'))
    gap = tk.StringVar(value=str(config.get('gap_ms', 30)))
    line = tk.StringVar(value=str(config.get('line_ms', 200)))
    options = ttk.Frame(key_tab)
    options.pack(fill='x')
    ttk.Label(options, text='布局').pack(side='left')
    ttk.Combobox(options, textvariable=layout, values=('us','jis'), width=5, state='readonly').pack(side='left', padx=6)
    ttk.Label(options, text='按下/释放间隔 ms').pack(side='left')
    ttk.Spinbox(options, textvariable=gap, from_=5, to=100, width=5).pack(side='left', padx=6)
    ttk.Label(options, text='换行等待 ms').pack(side='left')
    ttk.Spinbox(options, textvariable=line, from_=0, to=2000, width=6).pack(side='left', padx=6)
    ttk.Checkbutton(key_tab, text='SmileBASIC 逐行输入（防止自动缩进累积）', variable=line_mode).pack(anchor='w', pady=8)
    editor = ScrolledText(key_tab, font=('Consolas', 11), undo=True, wrap='none', height=17)
    editor.pack(fill='both', expand=True)
    draft = ROOT/'last-draft.txt'
    if draft.exists():
        editor.insert('1.0', draft.read_text(encoding='utf-8'))
    tools = ttk.Frame(key_tab)
    tools.pack(fill='x', pady=8)

    def open_file():
        path = filedialog.askopenfilename(filetypes=(('源码/文本','*.bas *.txt'),('所有文件','*.*')))
        if path:
            try:
                contents = Path(path).read_text(encoding='utf-8-sig')
                editor.delete('1.0','end'); editor.insert('1.0',contents)
            except Exception as error:
                status_text.set(str(error))

    def send_text(selection=False):
        try:
            text = editor.get('sel.first','sel.last') if selection else editor.get('1.0','end-1c')
            keyboard.normalize_text(text)
            gap_ms, line_ms, use_lines, selected_layout = int(gap.get()), int(line.get()), line_mode.get(), layout.get()
            if not 5 <= gap_ms <= 100 or not 0 <= line_ms <= 2000:
                raise ValueError('速度参数超出范围。')
        except (ValueError, tk.TclError) as error:
            status_text.set(str(error)); return
        def action(client, pad_client, stop):
            client.require_mode('keyboard')
            if use_lines:
                return client.send_editor(text,gap_ms,line_ms,selected_layout,cancel=stop,
                    progress=lambda done,total,row,rows:events.put(('progress',(done,total,row,rows),None)))
            accepted=client.send(text,gap_ms,line_ms,selected_layout)
            return client.wait_job(accepted['job'],len(keyboard.normalize_text(text)),stop,
                                   len(text)*(2*gap_ms+line_ms)/1000+15)
        submit('text', action)

    ttk.Button(tools,text='打开文件',command=open_file).pack(side='left',padx=(0,6))
    for label, selected in (('发送全文',False),('发送选中',True)):
        button=ttk.Button(tools,text=label,command=lambda s=selected:send_text(s))
        button.pack(side='left',padx=4); key_controls.append(button)
    shortcuts=ttk.Frame(key_tab);shortcuts.pack(fill='x')
    def key_tap(key, mod):
        def action(client,pad_client,stop):
            client.require_mode('keyboard');return client.tap_wait(key,mod,stop)
        submit('key',action)
    for label,key,mod in (('编辑 slot 0',0x1e,1),('Direct',0x27,1),('保存 Ctrl+S',0x16,1)):
        button=ttk.Button(shortcuts,text=label,command=lambda k=key,m=mod:key_tap(k,m))
        button.pack(side='left',padx=(0,6));key_controls.append(button)
    def filename():
        selected_layout=layout.get()
        def action(client,pad_client,stop):
            client.require_mode('keyboard')
            accepted=client.send('NS2\n',gap=30,line=0,layout=selected_layout)
            return client.wait_job(accepted['job'],4,stop,6)
        submit('key',action)
    filename_button=ttk.Button(shortcuts,text='输入 NS2 并回车',command=filename)
    filename_button.pack(side='left');key_controls.append(filename_button)
    ttk.Label(key_tab,text='在空白编辑区发送程序；发送完成后仍需在主机上保存。文件名按钮仅用于已打开的文件名输入框。',wraplength=880).pack(anchor='w',pady=8)

    def pad_input(**arguments):
        try:
            arguments['hold']=int(pro_view.hold.get());pad.pad_state(**arguments)
        except ValueError as error:
            status_text.set(str(error));return False
        if pro_view.streaming:
            values=pad.pad_state(**arguments)
            report=tuple(values[name] for name in ('buttons','dpad','lx','ly','rx','ry'))
            return stream_sender.tap(report,values['hold'])
        return submit('pad',lambda c,p,t:p.pulse(cancel=t,**arguments))
    pro_view=ProControllerView(pad_tab,on_action=pad_input,mapping_path=ROOT/'gamepad-keymap.json',
                              on_state=stream_sender.set_state)
    pro_view.pack(fill='both',expand=True)
    def tab_changed(event):
        pro_view.reset_keys()
        if tabs.select()==str(pad_tab):pro_view.canvas.focus_set()
    tabs.bind('<<NotebookTabChanged>>',tab_changed,add='+')

    progress=ttk.Progressbar(bottom,maximum=100);progress.pack(fill='x',pady=(10,4))
    def stop_input():
        token.set()
        pro_view.reset_keys(block_held=True)
        stream_sender.stop()
        state['wake_requested']=False
        try:current=settings()
        except ValueError as error:status_text.set(str(error));return
        def cancel():
            try:RemoteClient(current).cancel();events.put(('notice',None,'已请求释放所有按键。'))
            except Exception as error:events.put(('notice',None,str(error)))
        threading.Thread(target=cancel,daemon=True).start()
    def host_connection(enabled):
        if state['closing'] or state['control_inflight'] or (enabled and state['inflight']):return
        try:current=settings()
        except ValueError as error:status_text.set(str(error));return
        # Disconnect bypasses the input queue, including a long text transfer.
        token.set()
        pro_view.reset_keys(block_held=True)
        stream_sender.stop()
        state['revision']+=1
        state['control_inflight']=True
        state['wake_requested']=False
        status_text.set('正在重新连接主机…' if enabled else '正在释放按键并断开主机连接，Wi-Fi 保持连接…')
        update_controls()
        def change():
            kind='connect' if enabled else 'disconnect'
            try:
                client=RemoteClient(current)
                client.set_host_connection(enabled)
                events.put((kind,client.status(),None))
            except Exception as error:events.put((kind,None,str(error)))
        threading.Thread(target=change,daemon=True).start()
    actions=ttk.Frame(bottom);actions.pack(fill='x',pady=6)
    ttk.Button(actions,text='停止 / 全部释放（Esc）',command=stop_input).pack(side='left',fill='x',expand=True)
    disconnect_button=ttk.Button(actions,text='断开所有连接',command=lambda:host_connection(False))
    disconnect_button.pack(side='left',padx=6)
    reconnect_button=ttk.Button(actions,text='重新连接主机',command=lambda:host_connection(True))
    reconnect_button.pack(side='left')
    ttk.Label(bottom,textvariable=status_text,wraplength=940).pack(anchor='w')

    def update_controls():
        status=state['status'];active=status.get('mode')
        channel=status.get('gamepad',{}) if active=='dual' else status
        streaming=channel.get('stream',{}).get('supported',False)
        pro_view.set_streaming(streaming)
        stream_sender.configure(dict(config,url=url.get().strip().rstrip('/')),
                                {} if state['control_inflight'] or state['stream_fault'] else status)
        connection=status.get('connection_control',{})
        usb_enabled=connection.get('usb_enabled',True)
        for button,available in (
            (disconnect_button,connection.get('supported') and usb_enabled and not state['control_inflight']),
            (reconnect_button,connection.get('supported') and not usb_enabled and
             not state['control_inflight'] and not state['inflight'])):
            target='normal' if available else 'disabled'
            if str(button.cget('state')) != target:button.configure(state=target)
        wake=status.get('wake',{})
        wake_available=(usb_enabled and not state['control_inflight'] and not state['inflight'] and wake.get('supported') and
                        wake.get('ready') and not wake.get('advertising'))
        wake_target='normal' if wake_available else 'disabled'
        if str(wake_button.cget('state')) != wake_target:
            wake_button.configure(state=wake_target)
        connected=usb_enabled and not state['control_inflight'] and status.get('usb_mounted') and not status.get('usb_suspended')
        for mode,controls in (('keyboard',key_controls),('gamepad',())):
            channel=status.get('gamepad',{}) if active=='dual' and mode=='gamepad' else status
            live=mode=='gamepad' and streaming
            available=(connected and (stream_sender.enabled if live else not state['inflight']) and active in (mode,'dual') and
                       not channel.get('legacy_busy' if live else 'busy') and not channel.get('release_pending') and
                       channel.get('input_ready',True))
            if mode=='gamepad':pro_view.set_available(available);continue
            target = 'normal' if available else 'disabled'
            for button in controls:
                if str(button.cget('state')) != target:
                    button.configure(state=target)

    def process_events():
        try:
            while True:
                kind,result,error=events.get_nowait()
                if kind in ('stream_sent','stream_error'):
                    if kind=='stream_error':
                        state['stream_fault']=True
                        pro_view.reset_keys(block_held=True)
                        state['last_poll']=0.0
                        status_text.set(error)
                    elif result and not state['inflight'] and not state['control_inflight']:
                        status_text.set('实时手柄：输入已提交，可继续按键；松开即释放。')
                    continue
                if kind=='notice':
                    if not state['control_inflight']:status_text.set(error)
                    continue
                if kind=='progress':
                    if state['input_revision']!=state['revision']:continue
                    done,total,row,rows=result;progress['value']=100*done/total
                    status_text.set(f'逐行输入：{row}/{rows} 行，{done}/{total} 字符');continue
                if kind in ('status','poll'):
                    state['query_inflight']=False
                    # This snapshot predates the queued input; keep its UI state.
                    if (state['inflight'] or state['control_inflight'] or
                            state['query_revision']!=state['revision']):continue
                elif kind in ('connect','disconnect'):
                    state['control_inflight']=False
                    state['last_poll']=0.0
                else:
                    state['inflight']=False
                    state['last_poll']=0.0
                    if state['input_revision']!=state['revision']:continue
                if error:
                    if kind in ('poll','connect','disconnect'):state['status']={}
                    status_text.set(('唤醒请求未确认，请先查询状态。' if kind=='wake' else '')+error);continue
                if kind in ('status','poll','connect','disconnect'):
                    previous_mode=state['status'].get('mode')
                    state['stream_fault']=False
                    state['status']=result
                    wake=result.get('wake',{})
                    host_enabled=result.get('connection_control',{}).get('usb_enabled',True)
                    if not host_enabled:
                        bluetooth=('蓝牙状态：'+wake['error'] if wake.get('error') else
                                   '蓝牙广播尚未停止。' if wake.get('advertising') else '蓝牙广播已停止。')
                        wake_text.set('主机 USB 已断开，Wi-Fi 保持连接。'+bluetooth)
                    elif not wake.get('supported'):
                        wake_text.set('蓝牙唤醒：当前固件不支持，请刷入新版固件。')
                    elif wake.get('initializing'):
                        wake_text.set('蓝牙唤醒：正在初始化，USB 输入可正常使用。')
                    elif wake.get('error'):
                        wake_text.set('蓝牙唤醒：'+wake['error'])
                    elif wake.get('advertising'):
                        wake_text.set('蓝牙唤醒：正在发送 1 秒唤醒广播…')
                    elif state['wake_requested']:
                        usb_ready=(result.get('usb_mounted') and not result.get('usb_suspended')
                                   and result.get('input_ready') and result.get('gamepad',{}).get('input_ready'))
                        wake_text.set('蓝牙唤醒：广播已发送；'+('USB 已就绪。' if usb_ready else '等待主机恢复 USB 连接。'))
                        if usb_ready:state['wake_requested']=False
                    else:
                        wake_text.set('蓝牙唤醒：'+('就绪，主机休眠时也可点击唤醒。' if wake.get('ready') else '不可用。'))
                    if wake.get('ready'):
                        wake_text.set(wake_text.get()+f"（已发送 {wake.get('sent_count',0)} 次）")
                    active=result.get('mode');label={'keyboard':'USB 键盘','gamepad':'USB 手柄','dual':'USB 键盘＋手柄'}.get(active,'旧固件 / 未知')
                    mode_text.set('设备状态：'+label)
                    connected='已连接' if result.get('usb_mounted') else '未连接'
                    suspended='（主机休眠）' if result.get('usb_suspended') else ''
                    if not host_enabled: activity='已主动断开，点击“重新连接主机”恢复'
                    elif not result.get('usb_mounted'): activity='等待主机识别'
                    elif result.get('usb_suspended'): activity='等待主机唤醒'
                    elif result.get('input_ready') is False: activity='连接中，请稍候'
                    else:
                        activity='键盘输入中' if result.get('busy') else '键盘就绪'
                        if active=='dual':
                            gamepad=result.get('gamepad',{})
                            activity+=' / '+('手柄输入中' if gamepad.get('busy') else '手柄就绪' if gamepad.get('input_ready') else '手柄连接中')
                    caps=' | CapsLock 已开，发送文本前会自动关闭' if result.get('leds',0)&2 else ''
                    status_text.set(f'USB {connected}{suspended} | {label} | '+activity+caps)
                    if previous_mode is None:
                        tabs.select(key_tab if active in ('keyboard','dual') else pad_tab)
                elif kind=='wake':
                    state['wake_requested']=True
                    wake_text.set('蓝牙唤醒：已请求发送 1 秒广播，等待状态确认。')
                    status_text.set('唤醒广播已启动；USB 恢复就绪后即可操作。')
                else:
                    progress['value']=100
                    status_text.set('输入完成，按键已释放。' + ('请在主机上保存程序。' if kind=='text' else ''))
        except queue.Empty:pass
        update_controls()
        if (not smoke and not state['closing'] and not state['inflight'] and not state['control_inflight']
                and not state['query_inflight'] and time.monotonic()-state['last_poll']>1.2):
            submit('poll',lambda c,p,t:c.status())

    def report_callback_exception(kind, error, traceback):
        try:
            error_logger().error('GUI callback failed', exc_info=(kind,error,traceback))
        except OSError:
            pass
        status_text.set('界面刷新出错：'+str(error)+'；正在继续刷新，详情见 remote-errors.log。')

    root.report_callback_exception = report_callback_exception

    def process():
        if state['closing']:
            return
        # Schedule before consuming results. A Tk callback exception must not
        # permanently stop the only consumer and leave every input disabled.
        root.after(70,process)
        try:
            process_events()
        except Exception:
            root.report_callback_exception(*sys.exc_info())

    def close():
        state['closing']=True;token.set()
        stream_sender.close()
        try:
            draft.write_text(editor.get('1.0','end-1c'),encoding='utf-8')
            config.update(url=url.get().strip(),layout=layout.get(),gap_ms=int(gap.get()),line_ms=int(line.get()),editor_mode=line_mode.get())
            (ROOT/'client-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        except (OSError,ValueError):
            pass
        try:
            launch_cancel(dict(config,url=url.get().strip()))
        except OSError:
            pass
        finally:root.destroy()
    root.bind('<Escape>',lambda event:stop_input())
    root.protocol('WM_DELETE_WINDOW',close)
    update_controls()
    root.after(70,process)
    if smoke:root.after(180,root.destroy)
    root.mainloop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url')
    commands=parser.add_subparsers(dest='command')
    for command in ('status','cancel','wake','connect','disconnect','gui-smoke'):
        commands.add_parser(command)
    text=commands.add_parser('text');text.add_argument('text');text.add_argument('--editor',action='store_true')
    pulse=commands.add_parser('pulse');pulse.add_argument('--buttons',default='');pulse.add_argument('--dpad',default='CENTER',choices=tuple(pad.DPADS));pulse.add_argument('--hold',type=int,default=150)
    for axis in ('lx','ly','rx','ry'):pulse.add_argument('--'+axis,type=int,default=128)
    args=parser.parse_args();config=json.loads((ROOT/'client-config.json').read_text(encoding='utf-8-sig'))
    if args.url:config['url']=args.url
    c=RemoteClient(config)
    if args.command in (None,'gui-smoke'):gui(config,args.command=='gui-smoke');return
    if args.command=='status':result=c.status()
    elif args.command=='cancel':result=c.cancel()
    elif args.command=='wake':result=c.wakeup()
    elif args.command in ('connect','disconnect'):result=c.set_host_connection(args.command=='connect')
    elif args.command=='pulse':result=pad.Client(config).pulse(**{k:getattr(args,k) for k in ('buttons','dpad','hold','lx','ly','rx','ry')})
    else:
        c.require_mode('keyboard')
        result=c.send_editor(args.text,30,200,config.get('layout','us')) if args.editor else c.send(args.text,30,200,config.get('layout','us'))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)

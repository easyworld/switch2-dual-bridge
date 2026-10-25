"""Pro controller diagram, local keyboard bindings, and two interaction modes."""
import json
import os
from pathlib import Path
import string
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from controller import BUTTONS, DPADS


# name, short label, description, x, y, width, height, shape, pulse arguments
CONTROLS = [
    ('ZL','ZL','左扳机 ZL',245,57,104,36,'pill',{'buttons':'ZL'}),
    ('L','L','左肩键 L',245,105,116,36,'pill',{'buttons':'L'}),
    ('ZR','ZR','右扳机 ZR',675,57,104,36,'pill',{'buttons':'ZR'}),
    ('R','R','右肩键 R',675,105,116,36,'pill',{'buttons':'R'}),
    ('MINUS','−','减号',400,169,48,48,'circle',{'buttons':'MINUS'}),
    ('PLUS','＋','加号',520,169,48,48,'circle',{'buttons':'PLUS'}),
    ('CAPTURE','截图','截图',422,237,48,48,'circle',{'buttons':'CAPTURE'}),
    ('HOME','HOME','HOME',506,237,52,52,'circle',{'buttons':'HOME'}),
    ('X','X','X',665,162,58,58,'circle',{'buttons':'X'}),
    ('Y','Y','Y',607,220,58,58,'circle',{'buttons':'Y'}),
    ('A','A','A',723,220,58,58,'circle',{'buttons':'A'}),
    ('B','B','B',665,278,58,58,'circle',{'buttons':'B'}),
    ('L3','L3','左摇杆按下 L3',262,221,66,66,'circle',{'buttons':'L3'}),
    ('LS_UP','↑','左摇杆 ↑',262,160,44,44,'circle',{'ly':0}),
    ('LS_DOWN','↓','左摇杆 ↓',262,282,44,44,'circle',{'ly':255}),
    ('LS_LEFT','←','左摇杆 ←',201,221,44,44,'circle',{'lx':0}),
    ('LS_RIGHT','→','左摇杆 →',323,221,44,44,'circle',{'lx':255}),
    ('R3','R3','右摇杆按下 R3',556,329,66,66,'circle',{'buttons':'R3'}),
    ('RS_UP','↑','右摇杆 ↑',556,270,44,44,'circle',{'ry':0}),
    ('RS_DOWN','↓','右摇杆 ↓',556,388,44,44,'circle',{'ry':255}),
    ('RS_LEFT','←','右摇杆 ←',497,329,44,44,'circle',{'rx':0}),
    ('RS_RIGHT','→','右摇杆 →',615,329,44,44,'circle',{'rx':255}),
    ('DPAD_UP','↑','十字键 ↑',365,273,46,46,'circle',{'dpad':'UP'}),
    ('DPAD_DOWN','↓','十字键 ↓',365,375,46,46,'circle',{'dpad':'DOWN'}),
    ('DPAD_LEFT','←','十字键 ←',314,324,46,46,'circle',{'dpad':'LEFT'}),
    ('DPAD_RIGHT','→','十字键 →',416,324,46,46,'circle',{'dpad':'RIGHT'}),
    ('DPAD_UP_LEFT','↖','十字键 ↖',328,287,28,28,'circle',{'dpad':'UP_LEFT'}),
    ('DPAD_UP_RIGHT','↗','十字键 ↗',402,287,28,28,'circle',{'dpad':'UP_RIGHT'}),
    ('DPAD_DOWN_LEFT','↙','十字键 ↙',328,361,28,28,'circle',{'dpad':'DOWN_LEFT'}),
    ('DPAD_DOWN_RIGHT','↘','十字键 ↘',402,361,28,28,'circle',{'dpad':'DOWN_RIGHT'}),
    ('DPAD_CENTER','·','回中 / 释放',365,324,36,36,'circle',{'dpad':'CENTER'}),
    ('L+R','L+R','组合键 L+R',455,463,112,34,'pill',{'buttons':'L+R'}),
]
ACTION_ARGUMENTS = {row[0]: row[-1] for row in CONTROLS}


def report_for_actions(actions):
    buttons=0
    vectors={'dpad':[0,0],'left':[0,0],'right':[0,0]}
    directions={'UP':(0,-1),'UP_RIGHT':(1,-1),'RIGHT':(1,0),'DOWN_RIGHT':(1,1),
                'DOWN':(0,1),'DOWN_LEFT':(-1,1),'LEFT':(-1,0),'UP_LEFT':(-1,-1),'CENTER':(0,0)}
    for name in actions:
        arguments=ACTION_ARGUMENTS[name]
        for button in arguments.get('buttons','').split('+'):
            if button:buttons|=BUTTONS[button]
        dx,dy=directions[arguments.get('dpad','CENTER')]
        vectors['dpad'][0]+=dx;vectors['dpad'][1]+=dy
        for axis,group,index in (('lx','left',0),('ly','left',1),('rx','right',0),('ry','right',1)):
            if axis in arguments:vectors[group][index]+=(-1 if arguments[axis]<128 else 1)
    def sign(value):return (value>0)-(value<0)
    dx,dy=map(sign,vectors['dpad'])
    dpad=next(DPADS[name] for name,vector in directions.items() if vector==(dx,dy))
    axes=tuple(128 if value==0 else 0 if value<0 else 255
               for group in ('left','right') for value in vectors[group])
    return (buttons,dpad)+axes
DEFAULT_BINDINGS = dict.fromkeys(ACTION_ARGUMENTS)
DEFAULT_BINDINGS.update({
    'A':'j','B':'k','X':'u','Y':'i','L':'q','R':'e','ZL':'z','ZR':'c',
    'MINUS':'minus','PLUS':'equal','HOME':'h','CAPTURE':'p','L3':'f','R3':'g',
    'DPAD_UP':'w','DPAD_DOWN':'s','DPAD_LEFT':'a','DPAD_RIGHT':'d',
    'LS_UP':'Up','LS_DOWN':'Down','LS_LEFT':'Left','LS_RIGHT':'Right',
    'RS_UP':'8','RS_DOWN':'2','RS_LEFT':'4','RS_RIGHT':'6',
    'DPAD_CENTER':'space','L+R':'Return',
})
NAMED_KEYS = {
    'Up':'↑','Down':'↓','Left':'←','Right':'→','space':'Space','Return':'Enter',
    'BackSpace':'Back','Delete':'Del','Insert':'Ins','Home':'Home','End':'End',
    'Prior':'PgUp','Next':'PgDn','minus':'−','equal':'=','bracketleft':'[',
    'bracketright':']','backslash':'\\','semicolon':';','apostrophe':"'",
    'comma':',','period':'.','slash':'/','grave':'`',
}
ALLOWED_KEYS = set(string.ascii_lowercase + string.digits) | set(NAMED_KEYS)
ALLOWED_KEYS.update(f'F{i}' for i in range(1,13))
ALLOWED_KEYS.update(f'KP_{i}' for i in range(10))
ALLOWED_KEYS.update(('KP_Add','KP_Subtract','KP_Multiply','KP_Divide','KP_Decimal','KP_Enter'))
VK_KEYS = {37:'Left',38:'Up',39:'Right',40:'Down',13:'Return',32:'space',
           8:'BackSpace',46:'Delete',45:'Insert',36:'Home',35:'End',33:'Prior',34:'Next',
           189:'minus',187:'equal',219:'bracketleft',221:'bracketright',220:'backslash',
           186:'semicolon',222:'apostrophe',188:'comma',190:'period',191:'slash',192:'grave'}
KP_ALIASES = dict(zip(
    ('KP_Insert','KP_End','KP_Down','KP_Next','KP_Left','KP_Begin','KP_Right','KP_Home','KP_Up','KP_Prior','KP_Delete'),
    tuple(f'KP_{i}' for i in range(10))+('KP_Decimal',)))


def key_label(key):
    if not key:return '—'
    if key.startswith('KP_'):
        return 'N'+{'Add':'+','Subtract':'−','Multiply':'×','Divide':'/','Decimal':'.','Enter':'Ent'}.get(key[3:],key[3:])
    return NAMED_KEYS.get(key,key.upper())


def key_from_event(event):
    """Keep physical letters/digits stable across Shift, CapsLock and punctuation."""
    symbol = event.keysym
    if symbol in KP_ALIASES:return KP_ALIASES[symbol]
    if symbol.startswith('KP_') and symbol in ALLOWED_KEYS:return symbol
    code = getattr(event,'keycode',0)
    if sys.platform == 'win32':
        if 65 <= code <= 90:return chr(code).lower()
        if 48 <= code <= 57:return chr(code)
        if 96 <= code <= 105:return f'KP_{code-96}'
        if 112 <= code <= 123:return f'F{code-111}'
        if code in VK_KEYS:return VK_KEYS[code]
    if len(symbol)==1 and symbol.isascii() and symbol.isalpha():symbol=symbol.lower()
    return symbol if symbol in ALLOWED_KEYS else None


def clean_bindings(mapping):
    if not isinstance(mapping,dict):raise ValueError('按键映射格式应为对象。')
    result=dict.fromkeys(ACTION_ARGUMENTS)
    used=set()
    for name in result:
        key=mapping.get(name)
        if isinstance(key,str) and key in ALLOWED_KEYS and key not in used:
            result[name]=key;used.add(key)
    return result


def assign_binding(mapping,name,key):
    if name not in ACTION_ARGUMENTS or (key is not None and key not in ALLOWED_KEYS):
        raise ValueError('无效的手柄按键或键盘按键。')
    previous=None
    if key is not None:
        for other,value in mapping.items():
            if other!=name and value==key:previous=other;mapping[other]=None
    mapping[name]=key
    return previous


def load_bindings(path):
    path=Path(path)
    if not path.exists():return dict(DEFAULT_BINDINGS)
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('version')!=1:
        raise ValueError('不支持的映射文件版本。')
    return clean_bindings(data.get('bindings'))


def save_bindings(path,mapping):
    path=Path(path)
    payload=json.dumps({'version':1,'bindings':clean_bindings(mapping)},ensure_ascii=False,indent=2)+'\n'
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix='.gamepad-keymap-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);stream.write(payload)
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


class CanvasControl:
    """A hit target with button-like invocation and state on the diagram."""
    def __init__(self,view,definition):
        self.view=view
        (self.name,self.label,self.description,self.x,self.y,self.width,self.height,
         self.shape,self.arguments)=definition
        self.state='disabled'
        self.items=()

    def cget(self,option):
        return self.state if option=='state' else self.name if option=='text' else None

    def configure(self,*,state):
        if self.state!=state:self.state=state;self.paint()

    def hit(self,x,y):
        dx,dy=(x-self.x)/(self.width/2),(y-self.y)/(self.height/2)
        return dx*dx+dy*dy<=1 if self.shape=='circle' else abs(dx)<=1 and abs(dy)<=1

    def invoke(self):
        if self.state=='disabled':return False
        self.view.canvas.focus_set()
        if self.view.mode=='edit':self.view.select(self.name);return False
        accepted=self.view.on_action(**self.arguments)
        if accepted:
            self.view.flash(self.name)
            self.view.info.set(f'{self.description} · 键盘 {key_label(self.view.bindings[self.name])} · 已提交一次输入')
        return accepted

    def paint(self):
        if not self.items:return
        view=self.view
        selected=view.mode=='edit' and view.selected==self.name
        disabled=self.state=='disabled'
        active=view.active==self.name or self.name in view.held_actions
        hover=view.hover==self.name and not disabled
        fill='#725b2e' if selected else '#286653' if active else '#46596c' if hover else '#303c4a'
        outline='#f2ce7a' if selected else '#8dd5bb' if active else '#a1b8cf' if hover else '#647386'
        if disabled and not active:fill,outline='#2a333f','#414d5b'
        key=(view.draft if view.mode=='edit' else view.bindings)[self.name]
        view.canvas.itemconfigure(self.items[0],fill=fill,outline=outline,width=2 if selected or active else 1)
        view.canvas.itemconfigure(self.items[1],fill='#f1f4f8' if not disabled or active else '#7f8a99')
        view.canvas.itemconfigure(self.items[2],text=key_label(key),fill='#f3d17e' if not disabled or active else '#818995')


class ProControllerView(ttk.Frame):
    def __init__(self,parent,*,on_action,mapping_path,on_state=None):
        super().__init__(parent)
        self.on_action=on_action
        self.on_state=on_state
        self.streaming=False
        self.held_actions=set()
        self._key_actions={}
        self._mouse_action=None
        self._repeat_timer=None
        self._repeat_phase=True
        self.mapping_path=Path(mapping_path)
        load_error=''
        try:self.bindings=load_bindings(self.mapping_path)
        except (OSError,ValueError) as error:
            self.bindings=dict(DEFAULT_BINDINGS);load_error='映射文件未能读取，使用默认映射：'+str(error)
        self.draft=None
        self.mode='click'
        self.available=False
        self.selected=self.hover=self.active=None
        self._down=set()
        self._blocked_keys=set()
        self._flash_timer=None
        self._resize_timer=None
        self.scale=1.0;self.offset=(0,0)
        self.controls={row[0]:CanvasControl(self,row) for row in CONTROLS}

        toolbar=ttk.Frame(self);toolbar.pack(fill='x',pady=(0,6))
        ttk.Label(toolbar,text='Switch Pro 手柄',font=('Microsoft YaHei UI',12,'bold')).pack(side='left',padx=(0,18))
        self.mode_var=tk.StringVar(value='click')
        for title,value in (('点击模式','click'),('编辑模式','edit')):
            ttk.Radiobutton(toolbar,text=title,value=value,variable=self.mode_var,
                            command=lambda:self.set_mode(self.mode_var.get())).pack(side='left',padx=6)
        self.hold=tk.StringVar(value='150')
        self.hold_input=ttk.Spinbox(toolbar,from_=50,to=2000,increment=50,width=6,textvariable=self.hold)
        self.hold_input.pack(side='right')
        ttk.Label(toolbar,text='兼容点击 ms').pack(side='right',padx=8)
        repeat_bar=ttk.Frame(self);repeat_bar.pack(fill='x',pady=(0,4))
        self.repeat=tk.BooleanVar(value=False)
        self.repeat_ms=tk.StringVar(value='120')
        self.repeat_button=ttk.Checkbutton(repeat_bar,text='按钮长按连发',variable=self.repeat,
                                          command=self._restart_repeat)
        self.repeat_button.pack(side='left')
        ttk.Label(repeat_bar,text='连发周期 ms').pack(side='left',padx=(12,4))
        self.repeat_input=ttk.Spinbox(repeat_bar,from_=50,to=1000,increment=10,width=6,
                                      textvariable=self.repeat_ms)
        self.repeat_input.pack(side='left')
        self.hint=tk.StringVar()
        self.hint_label=ttk.Label(self,textvariable=self.hint,wraplength=880,foreground='#526174')
        self.hint_label.pack(fill='x',pady=(0,8))
        self.canvas=tk.Canvas(self,background='#f3f5f8',highlightthickness=1,
                              highlightbackground='#dce2e9',takefocus=True,width=900,height=490)
        footer=ttk.Frame(self)
        self.edit_state=tk.StringVar(value='上行：手柄按键   下行：键盘绑定   —：未绑定')
        ttk.Label(footer,textvariable=self.edit_state,foreground='#657286').pack(side='left')
        self.save_button=ttk.Button(footer,text='保存映射',command=self.save)
        self.cancel_button=ttk.Button(footer,text='取消修改',command=self.cancel_edit)
        self.clear_button=ttk.Button(footer,text='清除绑定',command=self.clear_selected)
        self.reset_button=ttk.Button(footer,text='恢复默认',command=self.reset_defaults)
        for button in (self.reset_button,self.clear_button,self.cancel_button,self.save_button):
            button.pack(side='right',padx=(5,0))
        self.info=tk.StringVar(value=load_error or '点击手柄区域后，可使用图上显示的键盘按键。')
        self.info_label=ttk.Label(self,textvariable=self.info,wraplength=880)
        self.info_label.pack(side='bottom',fill='x',pady=(6,0))
        footer.pack(side='bottom',fill='x',pady=(8,0))
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',self._resize)
        self.canvas.bind('<Button-1>',self._click)
        self.canvas.bind('<ButtonRelease-1>',self._mouse_release)
        self.canvas.bind('<Motion>',self._motion)
        self.canvas.bind('<Leave>',lambda event:self._set_hover(None))
        self.canvas.bind('<KeyPress>',self._key_press)
        self.canvas.bind('<KeyRelease>',self._key_release)
        self.canvas.bind('<FocusOut>',lambda event:self.reset_keys())
        self.canvas.bind('<FocusIn>',lambda event:self._blocked_keys.clear())
        self.set_mode('click',focus=False)
        if load_error:self.info.set(load_error)

    @property
    def dirty(self):return self.draft is not None and self.draft!=self.bindings

    def reset_keys(self,block_held=False):
        had_input=bool(self.held_actions)
        if block_held:self._blocked_keys.update(self._down)
        self._down.clear();self._key_actions.clear();self._mouse_action=None
        self.held_actions.clear()
        if self._repeat_timer is not None:
            self.after_cancel(self._repeat_timer);self._repeat_timer=None
        try:
            if self.canvas.grab_current()==self.canvas:self.canvas.grab_release()
        except tk.TclError:pass
        if had_input and self.streaming and self.on_state:self.on_state(report_for_actions(()))
        for control in self.controls.values():control.paint()

    def destroy(self):
        self.reset_keys()
        for timer in (self._flash_timer,self._resize_timer):
            if timer is not None:
                try:self.after_cancel(timer)
                except tk.TclError:pass
        super().destroy()

    def set_mode(self,mode,focus=True):
        if mode not in ('click','edit'):raise ValueError('无效的交互模式。')
        if mode=='edit' and self.draft is None:self.draft=dict(self.bindings)
        self.reset_keys()
        self.mode=mode;self.mode_var.set(mode);self.selected=None
        self.hint.set(('实时模式：按下保持，松开释放；支持组合键，发送期间可继续操作。可选按钮长按连发。' if self.streaming else
                       '兼容模式：每次触发一次定时输入，长按不连发；实时输入需要升级固件。') if mode=='click' else
                      '编辑模式：点击一个部位，再按下新的键盘按键；编辑不会发送输入，修改后点击“保存映射”。Esc 取消选键。')
        self.info.set('点击手柄开始操作。' if mode=='click' else '点击要修改的部位；也可以离线编辑和保存映射。')
        self._update_controls()
        if focus:self.canvas.focus_set()
        for control in self.controls.values():control.paint()

    def set_available(self,available):
        available=bool(available)
        if available!=self.available:
            if not available:self.reset_keys()
            self.available=available;self._update_controls()

    def set_streaming(self,enabled):
        enabled=bool(enabled and self.on_state)
        if enabled!=self.streaming:
            self.reset_keys();self.streaming=enabled
            self.set_mode(self.mode,focus=False)

    def _update_controls(self):
        for control in self.controls.values():
            control.configure(state='normal' if self.mode=='edit' or self.available else 'disabled')
        editing=self.mode=='edit'
        self.repeat_button.configure(state='normal' if self.streaming and not editing else 'disabled')
        self.repeat_input.configure(state='normal' if self.streaming and not editing else 'disabled')
        self.hold_input.configure(state='disabled' if self.streaming else 'normal')
        self.save_button.configure(state='normal' if editing and self.dirty else 'disabled')
        self.cancel_button.configure(state='normal' if editing else 'disabled')
        self.clear_button.configure(state='normal' if editing and self.selected and self.draft[self.selected] else 'disabled')
        self.reset_button.configure(state='normal' if editing else 'disabled')
        self.edit_state.set('修改尚未保存' if self.dirty else '上行：手柄按键   下行：键盘绑定   —：未绑定')

    def select(self,name):
        previous=self.selected;self.selected=name
        if previous:self.controls[previous].paint()
        self.controls[name].paint()
        self.info.set(f'正在编辑 {self.controls[name].description} · 当前 {key_label(self.draft[name])} · 请按一个键（Esc 取消）')
        self._update_controls()

    def bind_selected(self,key):
        if self.mode!='edit' or self.selected is None:return
        name=self.selected
        previous=assign_binding(self.draft,name,key)
        self.selected=None
        self.info.set(f'{self.controls[name].description} → {key_label(key)}'+
                      (f'；原来绑定到 {self.controls[previous].description} 的同一键已移除。' if previous else '；保存后生效。'))
        self._update_controls()
        for control in self.controls.values():control.paint()

    def clear_selected(self):self.bind_selected(None)

    def reset_defaults(self):
        if self.mode!='edit':return
        self.draft=dict(DEFAULT_BINDINGS);self.selected=None
        self.info.set('已恢复默认映射，点击“保存映射”后生效。')
        self._update_controls()
        for control in self.controls.values():control.paint()

    def save(self):
        if self.mode!='edit':return
        try:save_bindings(self.mapping_path,self.draft)
        except OSError as error:
            self.info.set('保存失败，修改仍保留在编辑模式：'+str(error));return
        self.bindings=dict(self.draft);self.draft=None
        self.set_mode('click');self.info.set('映射已保存，已切回点击模式。')

    def cancel_edit(self):
        self.draft=None;self.set_mode('click');self.info.set('已取消修改，继续使用已保存的映射。')

    def flash(self,name):
        previous=self.active
        if self._flash_timer is not None:self.after_cancel(self._flash_timer)
        self.active=name
        if previous:self.controls[previous].paint()
        self.controls[name].paint()
        try:duration=max(100,min(2000,int(self.hold.get())))
        except ValueError:duration=150
        def clear():
            self.active=None;self._flash_timer=None;self.controls[name].paint()
        self._flash_timer=self.after(duration,clear)

    def _hit(self,x,y):
        x=(x-self.offset[0])/self.scale;y=(y-self.offset[1])/self.scale
        return next((c for c in reversed(tuple(self.controls.values())) if c.hit(x,y)),None)

    def _click(self,event):
        self.canvas.focus_set()
        target=self._hit(event.x,event.y)
        if self.streaming and self.mode=='click':
            if target and target.state!='disabled':
                if target.name=='DPAD_CENTER':self.reset_keys();return 'break'
                self._mouse_action=target.name
                self.canvas.grab_set()
                self._emit_state()
            return 'break'
        if target:target.invoke()
        return 'break'

    def _mouse_release(self,event):
        if self._mouse_action is not None:
            self._mouse_action=None
            try:self.canvas.grab_release()
            except tk.TclError:pass
            self._emit_state()

    def _emit_state(self):
        self.held_actions=set(self._key_actions.values())
        if self._mouse_action:self.held_actions.add(self._mouse_action)
        effective=self.held_actions
        if self.repeat.get() and not self._repeat_phase:
            effective={name for name in effective if 'buttons' not in ACTION_ARGUMENTS[name]}
        if self.streaming and self.on_state:self.on_state(report_for_actions(effective))
        for control in self.controls.values():control.paint()
        repeating=self.repeat.get() and any('buttons' in ACTION_ARGUMENTS[name] for name in self.held_actions)
        if repeating and self._repeat_timer is None:
            try:interval=max(50,min(1000,int(self.repeat_ms.get())))
            except ValueError:interval=120
            self._repeat_timer=self.after(interval//2,self._repeat_tick)
        elif not repeating:
            if self._repeat_timer is not None:self.after_cancel(self._repeat_timer);self._repeat_timer=None
            self._repeat_phase=True

    def _repeat_tick(self):
        self._repeat_timer=None;self._repeat_phase=not self._repeat_phase
        self._emit_state()

    def _restart_repeat(self):
        if self._repeat_timer is not None:self.after_cancel(self._repeat_timer);self._repeat_timer=None
        self._repeat_phase=True
        self._emit_state()

    def _set_hover(self,name):
        previous=self.hover
        if name==previous:return
        self.hover=name
        if previous:self.controls[previous].paint()
        if name:self.controls[name].paint()
        self.canvas.configure(cursor='hand2' if name and self.controls[name].state=='normal' else '')

    def _motion(self,event):
        target=self._hit(event.x,event.y)
        self._set_hover(target.name if target else None)

    def _key_press(self,event):
        if event.keysym=='Escape':
            if self.mode=='edit':
                previous=self.selected;self.selected=None
                if previous:self.controls[previous].paint()
                self.info.set('已取消本次选键。');self._update_controls();return 'break'
            return None
        # Ctrl/Alt shortcuts and modifier-only keys are never gamepad mappings.
        # Windows Tk uses Mod1 (0x08) for Num Lock and 0x20000 for Alt.
        shortcut_mask=0x20004 if sys.platform=='win32' else 0x0c
        if event.state & shortcut_mask:return None
        key=key_from_event(event)
        if key is None:
            if self.mode=='edit' and self.selected:
                self.info.set('请选择字母、数字、方向键、功能键或常用单键；Esc 保留用于停止。')
                return 'break'
            return None
        if key in self._blocked_keys:return 'break'
        if key in self._down:return 'break'
        self._down.add(key)
        if self.mode=='edit':
            if self.selected:self.bind_selected(key)
            return 'break'
        for name,bound in self.bindings.items():
            if bound==key:
                if self.streaming:
                    if self.available:
                        if name=='DPAD_CENTER':self.reset_keys()
                        else:self._key_actions[key]=name;self._emit_state()
                else:self.controls[name].invoke()
                return 'break'
        return None

    def _key_release(self,event):
        key=key_from_event(event)
        if key:self._blocked_keys.discard(key)
        if key:self._down.discard(key)
        if key in self._key_actions:
            del self._key_actions[key]
            self._emit_state()

    def _resize(self,event):
        self.hint_label.configure(wraplength=max(350,event.width-16))
        self.info_label.configure(wraplength=max(350,event.width-16))
        if self._resize_timer is not None:self.after_cancel(self._resize_timer)
        self._resize_timer=self.after_idle(self._draw)

    def _draw(self):
        self._resize_timer=None
        canvas=self.canvas;canvas.delete('all')
        w,h=max(canvas.winfo_width(),1),max(canvas.winfo_height(),1)
        self.scale=max(0.1,min((w-18)/920,(h-12)/490))
        s=self.scale;self.offset=((w-920*s)/2,(h-490*s)/2)
        ox,oy=self.offset
        def coords(values):return [ox+v*s if i%2==0 else oy+v*s for i,v in enumerate(values)]
        def polygon(values,**kwargs):return canvas.create_polygon(coords(values),smooth=True,splinesteps=32,**kwargs)
        def oval(x,y,r,**kwargs):return canvas.create_oval(*coords((x-r,y-r,x+r,y+r)),**kwargs)
        def text(x,y,value,size,color,weight='normal'):
            return canvas.create_text(*coords((x,y)),text=value,fill=color,font=('Microsoft YaHei UI',-max(8,round(size*s)),weight))
        outline=(202,115,254,94,372,108,460,104,548,108,666,94,718,115,752,180,
                 790,380,778,445,733,462,690,440,605,419,535,421,460,422,
                 385,421,315,419,230,440,187,462,142,445,130,380,168,180)
        polygon(tuple(v+6 if i%2 else v for i,v in enumerate(outline)),fill='#d4dbe4',outline='')
        polygon(outline,fill='#293441',outline='#526172',width=2)
        polygon((190,170,212,132,294,124,352,146,363,255,304,340,229,429,193,441,160,421,157,369),fill='#202a35',outline='')
        polygon((730,170,708,132,626,124,568,146,557,255,616,340,691,429,727,441,760,421,763,369),fill='#202a35',outline='')
        oval(262,221,83,fill='#1d2733',outline='#5c6979',width=1)
        oval(556,329,79,fill='#1d2733',outline='#5c6979',width=1)
        oval(262,221,45,fill='#202b37',outline='#778496',width=1)
        oval(556,329,45,fill='#202b37',outline='#778496',width=1)
        # Four-way cross, with smaller diagonal targets at its corners.
        canvas.create_rectangle(*coords((341,252,389,397)),fill='#202a35',outline='')
        canvas.create_rectangle(*coords((292,300,438,348)),fill='#202a35',outline='')
        text(460,278,'SWITCH PRO',11,'#a6b1bf','bold')
        for x in (442,451,460,469):oval(x,306,2.3,fill='#86ba9d' if x==442 else '#52606e',outline='')
        text(195,413,'L',13,'#657284','bold');text(724,413,'R',13,'#657284','bold')
        for control in self.controls.values():
            x,y,bw,bh=control.x,control.y,control.width,control.height
            box=coords((x-bw/2,y-bh/2,x+bw/2,y+bh/2))
            if control.shape=='circle':shape=canvas.create_oval(*box)
            else:
                r=8
                shape=polygon((x-bw/2+r,y-bh/2,x+bw/2-r,y-bh/2,x+bw/2,y-bh/2+r,
                               x+bw/2,y+bh/2-r,x+bw/2-r,y+bh/2,x-bw/2+r,y+bh/2,
                               x-bw/2,y+bh/2-r,x-bw/2,y-bh/2+r))
            small=bw<32
            label=text(x,y-(5 if small else 9),control.label,12 if not small else 11,'#f1f4f8','bold')
            binding=text(x,y+(6 if small else 9),'',13 if bw>=48 else 11 if not small else 9,'#f3d17e','bold')
            control.items=(shape,label,binding)
            canvas.addtag_withtag('control:'+control.name,shape)
            control.paint()

"""Finite-duration USB gamepad commands."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


BUTTONS = {name: 1 << bit for bit, name in enumerate(
    ('Y', 'B', 'A', 'X', 'L', 'R', 'ZL', 'ZR', 'MINUS', 'PLUS', 'L3', 'R3', 'HOME', 'CAPTURE'))}
DPADS = {'CENTER': 15, 'UP': 0, 'UP_RIGHT': 1, 'RIGHT': 2, 'DOWN_RIGHT': 3,
         'DOWN': 4, 'DOWN_LEFT': 5, 'LEFT': 6, 'UP_LEFT': 7}


class Cancelled(Exception):
    pass


def pad_state(buttons='', dpad='CENTER', lx=128, ly=128, rx=128, ry=128, hold=150):
    if not isinstance(buttons, str):
        raise ValueError('buttons 必须是按键名，以 + 分隔。')
    mask = 0
    for name in filter(None, buttons.upper().replace(',', '+').split('+')):
        if name not in BUTTONS:
            raise ValueError('未知按键：' + name)
        mask |= BUTTONS[name]
    if dpad.upper() not in DPADS:
        raise ValueError('方向键无效。')
    for value in (lx, ly, rx, ry):
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError('摇杆范围为 0–255，128 为居中。')
    if type(hold) is not int or not 50 <= hold <= 2000:
        raise ValueError('按住时间范围为 50–2000 ms。')
    return dict(buttons=mask, dpad=DPADS[dpad.upper()], lx=lx, ly=ly, rx=rx, ry=ry, hold=hold)


class Client:
    def __init__(self, config):
        self.config = dict(config)
        parsed = urllib.parse.urlparse(config['url'])
        if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('板子地址应为 http://IP 或 http://主机名。')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, route, values=None, job=None, timeout=3):
        headers = {'X-Key': self.config['api_key']}
        data = None
        if values is not None:
            data = urllib.parse.urlencode(values).encode('ascii')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if job:
            headers['X-Job'] = job
        request = urllib.request.Request(self.config['url'].rstrip('/') + route, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # No automatic retries: an unobserved press must never be repeated.
            raise RuntimeError(error.read().decode('utf-8', errors='replace')) from error

    def status(self):
        result = self.request('/status')
        if result.get('mode') == 'dual':
            nested = result.get('gamepad')
            if not isinstance(nested, dict):
                raise RuntimeError('同时模式缺少手柄状态。')
            result = dict(result, **nested, device_mode='dual')
        if result.get('mode') != 'gamepad':
            raise RuntimeError('板子当前不是手柄或双接口固件。')
        return result

    def cancel(self):
        return self.request('/cancel', {})

    def stream(self, session, sequence, epoch, frames):
        if (len(session) != 32 or any(c not in '0123456789abcdef' for c in session) or
                type(sequence) is not int or not 0 <= sequence < 4294967295 or
                type(epoch) is not int or not 0 <= epoch <= 4294967295 or len(frames) > 8):
            raise ValueError('无效的连续输入数据包。')
        rows=[]
        for frame in frames:
            if (len(frame) != 7 or any(type(value) is not int for value in frame) or
                    not 0 <= frame[0] <= 16383 or frame[1] not in (*range(8),15) or
                    any(not 0 <= axis <= 255 for axis in frame[2:6]) or not 8 <= frame[6] <= 200):
                raise ValueError('无效的连续输入状态。')
            rows.append(','.join(map(str,frame)))
        result=self.request('/pad/stream',dict(session=session,sequence=sequence,epoch=epoch,
                                             frames=';'.join(rows)),timeout=0.4)
        if (result.get('accepted') is not True or result.get('next_sequence') != sequence+1 or
                result.get('capacity') != 16 or type(result.get('queued')) is not int or
                not 0 <= result['queued'] <= 16):
            raise RuntimeError('连续输入未确认，已停止发送。')
        return result

    def pulse(self, cancel=None, **values):
        cancel = cancel or threading.Event()
        payload = pad_state(**values)
        if cancel.is_set():
            raise Cancelled('已停止。')
        status = self.status()
        if status.get('input_ready') is False:
            raise RuntimeError('USB 正在连接，请等待就绪后再输入。')
        if not status.get('logic_self_test'):
            raise RuntimeError('板子控制逻辑自检未通过。')
        if not status.get('usb_mounted') or status.get('usb_suspended'):
            raise RuntimeError('USB 未连接或主机正在休眠。')
        if status.get('busy') or status.get('release_pending'):
            raise RuntimeError('板子忙碌或正在释放，请稍后重试。')
        payload['epoch'] = status['epoch']
        if cancel.is_set():
            raise Cancelled('已停止。')
        job = uuid.uuid4().hex
        self.request('/pad', payload, job)
        deadline = time.monotonic() + payload['hold'] / 1000 + 6
        while True:
            if cancel.wait(0.04):
                self.cancel()
                raise Cancelled('已停止并请求释放。')
            result = self.status()
            if result.get('job') != job:
                raise RuntimeError('任务已被其他客户端替换，停止后续输入。')
            if result.get('error'):
                raise RuntimeError('板子：' + result['error'])
            if result.get('completed') and not result.get('release_pending'):
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError('未确认输入完成；请先查询状态，避免重复按键。')

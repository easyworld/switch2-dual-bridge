"""USB keyboard HTTP protocol and SmileBASIC text transfer."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class TransferCancelled(Exception):
    pass


def normalize_text(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    for i, c in enumerate(text):
        if c not in '\t\n' and not 32 <= ord(c) <= 126:
            raise ValueError(f'第 {i+1} 个字符 {c!r} 不能直接通过英文键盘输入；支持 ASCII、换行和 Tab。')
    data = text.encode('ascii')
    if not 1 <= len(data) <= 16384:
        raise ValueError('每次发送需要 1–16384 个 ASCII 字符。')
    return data


class Client:
    def __init__(self, config):
        self.config = dict(config)
        # Do not route local board requests through the computer's HTTP proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, route, data=None, content_type=None, job=None):
        headers = {'X-Key': self.config['api_key']}
        if content_type:
            headers['Content-Type'] = content_type
        if job:
            headers['X-Job'] = job
        req = urllib.request.Request(self.config['url'].rstrip('/') + route,
                                     data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=4) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(error.read().decode('utf-8', errors='replace')) from error

    def status(self):
        return self.request('/status')

    def send(self, text, gap=12, line=80, layout='us'):
        data = normalize_text(text)
        if not 5 <= gap <= 100 or not 0 <= line <= 2000 or layout not in ('us', 'jis'):
            raise ValueError('无效的速度或键盘布局。')
        args = urllib.parse.urlencode({'gap': gap, 'line': line, 'layout': layout})
        return self.request('/text?' + args, data, 'text/plain; charset=us-ascii', uuid.uuid4().hex)

    def tap(self, key, mod=0):
        args = urllib.parse.urlencode({'key': key, 'mod': mod, 'gap': 20, 'line': 0})
        return self.request('/tap', args.encode(), 'application/x-www-form-urlencoded', uuid.uuid4().hex)

    def cancel(self):
        return self.request('/cancel', b'')

    def wait_job(self, job, count, cancel, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if cancel.is_set():
                raise TransferCancelled('已停止输入。')
            status = self.status()
            if status['job'] != job:
                raise RuntimeError('板子任务被其他操作替换，已停止后续输入。')
            if not status['busy']:
                if status.get('error') or status['sent'] != count:
                    raise RuntimeError('板子输入未完成：' + status.get('error', '字符数不符'))
                return status
            if time.monotonic() > deadline:
                raise TimeoutError('等待板子完成任务超时，已停止后续输入。')
            if cancel.wait(0.04):
                raise TransferCancelled('已停止输入。')

    def tap_wait(self, key, mod, cancel):
        if cancel.is_set():
            raise TransferCancelled('已停止输入。')
        accepted = self.tap(key, mod)
        return self.wait_job(accepted['job'], 1, cancel, 5)

    def send_editor(self, text, gap=30, line=200, layout='us', cancel=None, progress=None):
        """Type exact source lines into an empty editor, removing copied indentation."""
        cancel = cancel if cancel is not None else threading.Event()
        data = normalize_text(text)
        if not 5 <= gap <= 100 or not 0 <= line <= 2000 or layout not in ('us', 'jis'):
            raise ValueError('无效的速度或键盘布局。')
        lines = data.decode('ascii').splitlines(keepends=True)
        sent = 0
        try:
            for index, source_line in enumerate(lines):
                # At an empty, newly inserted line: select copied whitespace and Delete.
                # Delete is a no-op at EOF when indentation is empty; Backspace would
                # merge the previous line, so it must never be used here.
                self.tap_wait(0x4a, 0, cancel)   # Home
                self.tap_wait(0x4d, 2, cancel)   # Shift+End
                self.tap_wait(0x4c, 0, cancel)   # Delete selected indentation
                if cancel.is_set():
                    raise TransferCancelled('已停止输入。')
                accepted = self.send(source_line, gap, 0, layout)
                final_status = self.wait_job(accepted['job'], len(source_line), cancel,
                                             8 + len(source_line) * (gap * 0.002 + 0.05))
                sent += len(source_line)
                if progress:
                    progress(sent, len(data), index + 1, len(lines))
                if source_line.endswith('\n') and cancel.wait(line / 1000):
                    raise TransferCancelled('已停止输入。')
            if lines[-1].endswith('\n'):
                self.tap_wait(0x4a, 0, cancel)
                self.tap_wait(0x4d, 2, cancel)
                final_status = self.tap_wait(0x4c, 0, cancel)
            return {'sent': sent, 'total': len(data), 'lines': len(lines),
                    'board_job': final_status['job']}
        except BaseException:
            try:
                self.cancel()
            except Exception:
                pass
            raise

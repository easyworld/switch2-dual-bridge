"""Bounded, ordered gamepad transitions sent independently of the Tk thread."""
from collections import deque
import threading
import time
import uuid

from controller import Client

NEUTRAL = (0,15,128,128,128,128)


class StreamSender:
    MAX_PENDING = 64
    MAX_AGE = 0.5
    HEARTBEAT = 0.1

    def __init__(self, notify, client_factory=Client):
        self.notify = notify
        self.client_factory = client_factory
        self.condition = threading.Condition()
        self.pending = deque()
        self.desired = NEUTRAL
        self.config = None
        self.identity = None
        self.enabled = False
        self.closed = False
        self.generation = 0
        self.session = None
        self.sequence = 0
        self.free = 16
        self.last_send = 0.0
        self.thread = threading.Thread(target=self._run, name='gamepad-stream', daemon=True)
        self.thread.start()

    def configure(self, config, status):
        channel=status.get('gamepad',{}) if status.get('mode')=='dual' else status
        enabled=(status.get('mode') in ('dual','gamepad') and channel.get('stream',{}).get('supported') and
                 status.get('usb_mounted') and not status.get('usb_suspended') and
                 status.get('connection_control',{}).get('usb_enabled',True) and
                 channel.get('input_ready') and not channel.get('legacy_busy') and
                 not channel.get('release_pending'))
        identity=(config['url'],config['api_key'],status.get('boot_id'),channel.get('epoch'))
        with self.condition:
            # An unknown active session may belong to another client or to a
            # timed-out request. Wait for its firmware lease to expire.
            if self.session is None and channel.get('stream',{}).get('active'):
                enabled=False
            if identity != self.identity or not enabled:
                self._reset()
            self.identity=identity
            self.config=dict(config,epoch=channel.get('epoch'))
            self.enabled=bool(enabled)
            self.condition.notify_all()

    def _reset(self):
        self.generation+=1
        self.pending.clear()
        self.desired=NEUTRAL
        self.session=None
        self.sequence=0
        self.free=16

    def stop(self):
        with self.condition:
            self._reset()
            self.enabled=False
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self._reset()
            self.closed=True
            self.condition.notify_all()

    def set_state(self, report):
        report=tuple(report)
        with self.condition:
            if not self.enabled or self.closed:
                return False
            if report == self.desired:
                return True
            if len(self.pending) >= self.MAX_PENDING:
                self._fail('输入积压过多，已清空并停止；请松开按键后重试。')
                return False
            self.desired=report
            self.pending.append((time.monotonic(),report+(16,)))
            self.condition.notify_all()
            return True

    def tap(self, report, duration):
        with self.condition:
            if not self.enabled or self.closed:
                return False
            if len(self.pending)+2 > self.MAX_PENDING:
                self._fail('输入积压过多，已停止。')
                return False
            now=time.monotonic()
            self.pending.append((now,tuple(report)+(max(8,min(200,duration)),)))
            self.pending.append((now,self.desired+(16,)))
            self.condition.notify_all()
            return True

    def _fail(self, message):
        self._reset()
        self.enabled=False
        # Notifications contain no Tk calls. The GUI consumes its event queue.
        self.notify('stream_error',None,message)

    def _run(self):
        client=None
        client_generation=-1
        while True:
            with self.condition:
                if self.closed:
                    return
                now=time.monotonic()
                if not self.enabled or (not self.pending and self.session is None):
                    self.condition.wait(0.1)
                    continue
                if self.pending and now-self.pending[0][0] > self.MAX_AGE:
                    self._fail('网络发送滞后，已丢弃过期操作并停止；请松开按键后重试。')
                    continue
                # Reserve board capacity; a heartbeat obtains fresh queue depth
                # without polling /status for every individual press.
                size=min(len(self.pending),8,self.free)
                if not size and now-self.last_send < self.HEARTBEAT:
                    self.condition.wait(self.HEARTBEAT-(now-self.last_send))
                    continue
                if self.session is None:
                    self.session=uuid.uuid4().hex
                generation=self.generation
                config=dict(self.config)
                frames=[self.pending[i][1] for i in range(size)]
                session,sequence=self.session,self.sequence
                self.last_send=now
            try:
                if generation != client_generation:
                    client=self.client_factory(config)
                    client_generation=generation
                result=client.stream(session,sequence,config['epoch'],frames)
                with self.condition:
                    if generation != self.generation:
                        continue
                    for _ in range(size):
                        self.pending.popleft()
                    self.sequence+=1
                    self.free=result['capacity']-result['queued']
                self.notify('stream_sent',len(frames),None)
            except Exception as error:
                with self.condition:
                    if generation == self.generation:
                        self._fail('连续输入已停止：'+str(error))

"""WebSocket Listener - 监听 LCU 实时事件"""

import json
import queue
import ssl
import threading
import time
from typing import Optional, Callable, Dict
from websocket import WebSocketApp


class WebSocketListener:
    def __init__(self, connector=None):
        # v4.4.0 修复：connector 改为可选（默认 None）。
        # 背景：本类只把 connector 当"客户端是否已连接 + 端口/token 的只读来源"，
        # 不依赖任何 LCUConnector 专有行为。允许无参构造后，本模块可以在没跑
        # 客户端、甚至没建 connector 的环境里被 import 并实例化做探活/自检
        # （原签名会让 `WebSocketListener()` 直接 TypeError）。
        # 向后兼容：main.py:547 的 WebSocketListener(self._conn) 行为完全不变。
        # connector 为 None 时 connect() 返回 'no-client'，不会 AttributeError。
        self._c = connector
        self._ws: Optional[WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._handlers: Dict[str, list] = {}
        self._connected = False
        self._manual_close = False
        self._on_disconnected: Optional[Callable] = None
        self._on_connected: Optional[Callable] = None  # WS 真正建立连接(on_open)后的回调
        self._last_uri: str = ''  # 避免重复处理相同URI
        # v4.4.2(P1-19): connect()/disconnect() 的进程级单飞锁。原 :72-73 的
        # "检查 is_alive → 再 _ensure_consumer + 构造 WebSocketApp → start" 之间
        # 无锁, 两个调用方(main.py:1594 受 _connecting 保护 / main.py:2000 受
        # _starting 保护)互不检查, 可并发起出两条 run_forever。此锁把
        # "检查 + 赋值 self._thread" 收成临界区, 不动公共签名与返回值。
        self._connect_lock = threading.Lock()
        # v4.4.2(P1-6): 队列改有界 —— 原先 queue.Queue() 无界, 消费线程被慢 handler
        # 卡住时只进不出, 内存无上限。设上限后满员时计数丢弃(打一次诊断, 不刷屏)。
        self._Q_MAXSIZE = 2048
        self._q: 'queue.Queue' = queue.Queue(maxsize=self._Q_MAXSIZE)
        self._consumer: Optional[threading.Thread] = None
        self._consumer_stop = threading.Event()
        # v4.4.0 修复：处理器异常原先被 `except Exception: pass` 静默吞掉，
        # 线上无法定位。改为至少打印一次（避免刷屏），降低排障成本。
        # v4.4.2(P1-7): 由一次性闸门改为 60s 时间窗 —— 原 `_err_reported` 置 True
        # 后永不复位, 第一条之后的完全不同异常被永久静音。
        self._last_err_t = 0.0
        self._err_window_s = 60.0
        self._q_dropped = 0
        self._q_drop_reported = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_on_disconnected(self, callback):
        self._on_disconnected = callback

    def set_on_connected(self, callback):
        """注册"连接真正建立"回调：仅在 WebSocket on_open 后触发一次，
        避免 connect() 一返回（此时还只是启动了后台线程）就误报已连接。
        """
        self._on_connected = callback

    def on(self, uri: str, handler: Callable):
        self._handlers.setdefault(uri, []).append(handler)

    def connect(self) -> str:
        """启动 WS 监听线程。

        v4.4.0 修复：原实现在未连接客户端时 raise ConnectionError，调用方每次
        都要 try/except；现改为返回状态字符串（公开行为向后兼容——现有调用方
        均忽略返回值）：
          'ok'              —— 本次调用真的启动了监听线程
          'already-running' —— 已有存活线程，未重复启动（单飞保护）
          'no-client'       —— LCU 未连接，未启动
        """
        # v4.4.0 修复：connector 可能是 None（无参构造的场景），此处必须用
        # getattr 兜底，否则会抛 AttributeError 而不是返回 'no-client'。
        if self._c is None or not getattr(self._c, 'is_connected', False):
            return 'no-client'
        # v4.4.2(P1-19): 持锁进入临界区 —— 把"单飞检查 + 赋值 self._thread + start"
        # 收成原子操作。两个调用方(main.py:1594 / main.py:2000)各自受不同标志
        # 保护、互不检查, 只有锁能挡住它们并发起出两条 run_forever。
        with self._connect_lock:
            return self._connect_locked()

    def _connect_locked(self) -> str:
        """connect() 的临界区实现(调用方必须持有 _connect_lock)。

        单飞保护：on_open 触发前 5s 重连 tick 会重复进入，禁止再起新线程，
        避免旧 run_forever 线程的 on_close 重置 _manual_close 干扰新连接状态机
        """
        if self._thread is not None and self._thread.is_alive():
            return 'already-running'
        # 防引用覆盖遗留旧连接对象
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        # v4.4.0 修复：确保消费线程常驻（异常退出后可自愈重启）
        self._ensure_consumer()

        # v4.4.2(P2-9): 走 connector 的公开 port property, 不再直读私有 _port。
        ws_url = f'wss://127.0.0.1:{self._c.port}/'
        token = self._c.auth[1]

        # v4.4.0 修复：用「每代连接独立的标记」替代全局 _manual_close 判定。
        # 旧实现里 connect() 会把 _manual_close 复位为 False，若上一代读线程
        # 尚未退干净，它的 on_close 会误判为"非手动关闭"并触发一次假的
        # on_disconnected（重连抖动根因）。现在每代闭包持有自己的 state。
        gen = {'manual': False}
        self._gen = gen

        def on_open(ws):
            self._connected = True
            ws.send(json.dumps([5, 'OnJsonApiEvent']))
            try:
                print(f'[WS] on_open 成功: port={self._c.port}')
            except Exception:
                pass
            # 通知外部：WS 此刻才真正建立，可安全把 UI 置为"已连接"
            if self._on_connected:
                try:
                    self._on_connected()
                except Exception:
                    pass

        def on_message(ws, msg):
            if not msg:
                return
            if isinstance(msg, bytes):
                try:
                    msg = msg.decode('utf-8', errors='ignore')
                except Exception:
                    return
            msg = msg.strip()
            if not msg:
                return
            try:
                data = json.loads(msg)
                if isinstance(data, list) and len(data) >= 2:
                    if data[0] != 8:
                        return          # 非 OnJsonApiEvent 操作码, 忽略
                    # v4.3.10 关键修复: LCU 实际下发的帧是
                    #   [8, "OnJsonApiEvent", {"uri":..., "eventType":..., "data":...}]
                    # —— **事件对象在下标 2**, 不是下标 1。
                    # 原实现只认 "data[1] 是 dict" 或 "len(data)>=4" 两种形状,
                    # 而真实帧 len==3 且 data[1] 是字符串 "OnJsonApiEvent",
                    # 两个分支都不命中 → **所有实时事件被静默丢弃**,
                    # 整条 WebSocket 通道形同虚设(只剩 1s 轮询兜底)。
                    # 参考实现一致取下标 2: lcu-driver `loads(msg)[2]`、
                    # league-connect `json.slice(2)`。
                    # 旧形状仍保留兼容(少数封装/自测会发 [8, obj] 或 [8,x,y,obj])。
                    event = None
                    if len(data) >= 3 and isinstance(data[2], dict):
                        event = data[2]
                    elif isinstance(data[1], dict):
                        event = data[1]
                    elif len(data) >= 4:
                        event = {'uri': data[1], 'eventType': data[2],
                                 'data': data[3]}
                    if event:
                        self._enqueue(event)
                elif isinstance(data, dict):
                    self._enqueue(data)
            except (json.JSONDecodeError, ValueError):
                pass

        def on_error(ws, error):
            # 之前是空实现，导致 WS 握手失败/SSL 错误等被静默吞掉
            # 表现就是"日志说连上了但 UI 一直未连接"——根因在这里
            try:
                print(f'[WS] 错误: {error!r}')
            except Exception:
                pass

        def on_close(ws, code, msg):
            self._connected = False
            try:
                print(f'[WS] 关闭: code={code} msg={msg!r} manual={gen["manual"]}')
            except Exception:
                pass
            # v4.4.0 修复：只认本代连接的 manual 标记，避免重连窗口内
            # 新连接的 connect() 复位全局标记后旧线程触发假 on_disconnected
            if not gen['manual'] and self._on_disconnected:
                try:
                    self._on_disconnected()
                except Exception:
                    pass

        self._ws = WebSocketApp(
            ws_url,
            header=[f'Authorization: Basic {self._encode(token)}'],
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._manual_close = False
        gen['manual'] = False
        self._thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={
                'sslopt': {'cert_reqs': ssl.CERT_NONE, 'check_hostname': False},
                'skip_utf8_validation': True,
                # v4.4.0 修复（P0-1）：原先不传 ping，LCU 侧 socket 半开（休眠/
                # 网络切换/客户端静默重启）时读线程会永久阻塞，on_close 永不触发
                # → _connected 恒 True、connect() 单飞守卫永久提前 return，
                # WS 通道静默死亡且无人知道。开启应用层 ping 后 ping_timeout
                # 到期会由 websocket-client 自行关连接并触发 on_close → 重连。
                'ping_interval': 20,
                'ping_timeout': 10,
            },
            daemon=True
        )
        self._thread.start()
        return 'ok'

    # ==================== 事件消费线程（P1-5） ====================

    def _ensure_consumer(self):
        """保证单条常驻消费线程存活（重复调用无副作用）。

        v4.4.0 修复：若上一代消费线程还活着（处理器卡住导致 join 超时），
        **不**再起第二条 —— 两个消费者共享同一个队列会打乱事件顺序。
        此时直接复用旧线程，它会在处理器返回后自行看到停止位退出。

        v4.4.2(P2-43): 复用前先检查该代的 _consumer_stop 是否已被 set ——
        disconnect() 会 set 停止位 + 投毒丸, 若此时旧线程还没退干净(处理器
        仍卡住), 直接复用它只会让它消费完当前项即退出, 之后无人消费(即
        P1-6 的后半段)。此时必须换新 Event 起新线程。
        """
        if self._consumer is not None and self._consumer.is_alive():
            stop = getattr(self, '_consumer_stop', None)
            if stop is None or not stop.is_set():
                # 停止位未置 → 这一代还能继续消费, 复用即可。
                return
            # 停止位已置 → 旧线程随时会退出, 不能复用, 落到下面起新线程。
        # 每代消费线程持有**自己的**停止位：否则新一代 clear() 会把旧一代
        # 刚被置上的停止位清掉，让本该退出的旧线程继续抢食队列。
        stop = threading.Event()
        self._consumer_stop = stop
        self._consumer = threading.Thread(
            target=self._consume_loop, args=(stop,),
            name='lcu-ws-consumer', daemon=True)
        self._consumer.start()

    def _consume_loop(self, stop: threading.Event):
        """顺序消费事件队列：读到 None 视为退出信号。

        整个循环外层兜底 try/except——消费线程若被处理器异常打死，
        后续事件会永久堆积且无人报错（比内联执行更隐蔽），故此处绝不允许抛出。
        """
        while not stop.is_set():
            try:
                # v4.4.0 修复：带超时取值而非死等——disconnect() 清空残留队列后，
                # 旧消费线程若仍阻塞在无限 get() 上就会永久泄漏并抢食新事件
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            except Exception as e:
                self._report_error(f'[WS] 事件队列读取异常: {type(e).__name__}: {e}')
                time.sleep(0.05)
                continue
            if item is None:
                break
            try:
                self._dispatch(item)
            except Exception as e:
                self._report_error(f'[WS] 事件分发异常: {type(e).__name__}: {e}')

    def _enqueue(self, event):
        """由 websocket-client 读线程调用：只投递，不做任何业务处理。

        v4.4.2(P1-6): 有界队列满时丢弃并计数 —— 至少报一次, 不逐条刷屏。
        丢弃优于阻塞读线程(阻塞会卡死收包, 连心跳都收不到)。
        """
        try:
            self._q.put_nowait(event)
        except queue.Full:
            self._q_dropped += 1
            if not self._q_drop_reported:
                self._q_drop_reported = True
                self._report_error(f'[WS] 事件队列已满({self._Q_MAXSIZE}), 开始丢弃'
                                   f'积压事件(消费线程被慢处理器卡住)')
        except Exception as e:
            self._report_error(f'[WS] 事件入队失败: {type(e).__name__}: {e}')

    def _report_error(self, msg):
        """异常按 60s 时间窗节流打印, 避免高频事件把日志刷爆(但保证窗口内可见)。

        v4.4.2(P1-7): 原一次性闸门(_err_reported 置 True 后永不复位)会让第一条
        之后**完全不同**的异常被永久静音。改为时间窗: 每类窗口内打一次。
        """
        try:
            now = time.monotonic()
            if now - self._last_err_t >= self._err_window_s:
                self._last_err_t = now
                print(msg)
        except Exception:
            pass

    def _stop_consumer(self, timeout: float = 2.0):
        """优雅停掉消费线程：置停止位并投毒丸，唤醒阻塞在 get() 上的线程。"""
        t = self._consumer
        # 只置**当前这一代**的停止位（每次 _ensure_consumer 都会换新 Event）
        stop = getattr(self, '_consumer_stop', None)
        if stop is not None:
            try:
                stop.set()
            except Exception:
                pass
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        if t is not None and t.is_alive() and t is not threading.current_thread():
            try:
                t.join(timeout=timeout)
            except Exception:
                pass
        # v4.4.0 修复：只有线程**确实退出**才置 None。若处理器卡死导致 join 超时，
        # 保留引用可让下一次 _ensure_consumer 复用同一线程，避免出现两个消费者
        # 同时抢同一个队列（事件顺序会被打乱）。
        if t is None or not t.is_alive():
            self._consumer = None
        # 清空残留事件，避免重连后把上一代事件重放给处理器
        try:
            while True:
                self._q.get_nowait()
        except Exception:
            pass

    def _encode(self, token: str) -> str:
        import base64
        return base64.b64encode(f'riot:{token}'.encode()).decode()

    def _dispatch(self, event_data):
        if isinstance(event_data, str):
            for pattern, handlers in self._handlers.items():
                if event_data == pattern or (pattern.endswith('*') and event_data.startswith(pattern[:-1])):
                    for h in handlers:
                        try:
                            h({'uri': event_data, 'eventType': 'Update', 'data': event_data})
                        except Exception as e:
                            self._report_error(
                                f'[WS] 处理器异常 uri={event_data}: {type(e).__name__}: {e}')
            return
        if not isinstance(event_data, dict):
            return
        uri = event_data.get('uri', '')
        data = event_data.get('data')
        etype = event_data.get('eventType', '')
        payload = {'uri': uri, 'eventType': etype, 'data': data}

        if uri in self._handlers:
            for h in self._handlers[uri]:
                try:
                    h(payload)
                except Exception as e:
                    self._report_error(
                        f'[WS] 处理器异常 uri={uri}: {type(e).__name__}: {e}')
        for pattern, handlers in self._handlers.items():
            if pattern != uri and pattern.endswith('*') and uri.startswith(pattern[:-1]):
                for h in handlers:
                    try:
                        h(payload)
                    except Exception as e:
                        self._report_error(
                            f'[WS] 处理器异常 uri={uri} pattern={pattern}: '
                            f'{type(e).__name__}: {e}')

    def disconnect(self):
        # v4.4.0 修复（P0-1）：先标记本代"手动关闭"，再 close，最后 join 等读线程
        # 真正退出。原实现只 close 不 join：close() 只是发关闭帧，on_close 要等
        # 读线程收到对端回应才执行；这段时间里 on_close 会把全局 _manual_close
        # 复位、甚至把新连接的标记一起清掉，形成"断开/重连窗口竞态"。
        # 顺序必须是：置标记 → close → join → 复位，才能让 on_close 看到 True
        # 并抑制 on_disconnected（手动断开不该上报掉线）。
        # v4.4.2(P1-19): 与 connect() 共持 _connect_lock, 避免"断开清 _thread=None"
        # 与"连接检查 is_alive 后起新线程"交错成两条并存。
        with self._connect_lock:
            self._disconnect_locked()

    def _disconnect_locked(self):
        """disconnect() 的临界区实现(调用方必须持有 _connect_lock)。"""
        self._manual_close = True
        gen = getattr(self, '_gen', None)
        if isinstance(gen, dict):
            gen['manual'] = True
        ws = self._ws
        self._ws = None
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            try:
                t.join(timeout=2.0)   # v4.4.0 修复：等读线程收尾，消除重连竞态
            except Exception:
                pass
        self._thread = None           # v4.4.0 修复：置空，否则单飞守卫会永久短路
        self._connected = False
        self._manual_close = False
        self._stop_consumer()

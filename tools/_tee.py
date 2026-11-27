"""诊断脚本共用: 把 print 同时写到控制台与 utf-8 文件(_Tee)。

v4.4.2(P2-14): 原先 tools/diag_probe.py、diag_pickable.py、diag_monitor.py、
diag_cardshot.py、diag_myselect.py 各自复制了同一份 17 行 _Tee。抽到这里
统一维护, 避免五份实现漂移。用法(脚本以 `python tools/diag_xxx.py` 直接跑,
Python 会把 tools/ 加进 sys.path[0], 所以下面这行直接可用):

    from _tee import _Tee
    sys.stdout = _Tee(sys.__stdout__, f)
"""


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass

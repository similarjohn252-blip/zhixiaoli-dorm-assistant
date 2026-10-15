"""请求链路追踪（工程债 B4）：一个 request_id 串起 ASR → 大脑 → TTS，并记下每段耗时。

为什么需要：
  一次语音请求跨 4 段（识别 / 平台 / 合成 / 板子播放）✗ —— 出问题时只能靠猜 ✓
现在：
  · 每次请求一个短 id（如 `a1b2c3`），**所有日志行都带它** → 一条 grep 串出全链路 ✓
  · id 会随响应返回 → 板子串口也打出来 → **用户侧现象**与**服务端日志**能对上 ✓
  · 记录各段耗时（ASR / 平台 / TTS …）→ 慢在哪一段一眼可见 ✓
  · 日志是 **JSON lines + 滚动切割**（2MB × 3）→ 不会把磁盘写满 ✓

⚠️ 两条硬规矩：
  ① **追踪本身绝不能让请求失败** —— 任何写日志的异常都被吞掉（只 print 一行提示 ✓）
  ② 线程安全用 `threading.local`（Flask 是多线程的 ✓）
"""
import json
import logging
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler

from .config import LOG_ENABLE, LOG_FILE

_local = threading.local()
_logger = None


# ==================== 请求 id ====================
def new_id() -> str:
    """短 id：6 位十六进制（够用且好念 ✓）"""
    return uuid.uuid4().hex[:6]


def start(rid: str = None) -> str:
    """开始一次请求：生成/接收 id，清空计时。返回 id ✓"""
    rid = rid or new_id()
    _local.rid = rid
    _local.t0 = time.time()
    _local.stages = []
    return rid


def rid() -> str:
    return getattr(_local, "rid", "-")


def _stages():
    if not hasattr(_local, "stages"):
        _local.stages = []
    return _local.stages


# ==================== 分段计时 ====================
class _Timer:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t = time.time()
        return self

    def __exit__(self, *exc):
        _stages().append((self.name, round(time.time() - self.t, 3)))
        return False


def timed(name: str) -> _Timer:
    """用法：with trace.timed("平台"): ...  —— 自动记下这一段耗时 ✓"""
    return _Timer(name)


def timings() -> dict:
    """各段耗时（秒）。同名段会覆盖 ✓（够用即可）"""
    return {n: s for n, s in _stages()}


def total() -> float:
    return round(time.time() - getattr(_local, "t0", time.time()), 3)


# ==================== 日志落盘（JSON lines + 滚动）====================
def _get_logger():
    global _logger
    if _logger is not None:
        return _logger
    lg = logging.getLogger("zhixiaoli.trace")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        h = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
    except Exception as e:            # 目录不可写等 → 只提示，不影响请求 ✓
        print(f"[链路] 日志文件打不开（{LOG_FILE}）：{e}", flush=True)
    _logger = lg
    return lg


def log(event: str, **fields):
    """写一行 JSON 日志；任何异常都不许冒泡 ✗"""
    if not LOG_ENABLE:
        return
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "rid": rid(),
            "event": event}
    for k, v in fields.items():
        line[k] = (str(v)[:200] if isinstance(v, str) else v)
    try:
        _get_logger().info(json.dumps(line, ensure_ascii=False))
    except Exception:
        pass


def summary(note: str = ""):
    """在控制台打一行总览（人看的）—— 这是排障时最有用的那一行 ✓"""
    t = timings()
    parts = " ".join(f"{k} {v}s" for k, v in t.items())
    print(f"[链路 {rid()}] 总耗时 {total()}s"
          f"{('  ' + parts) if parts else ''}"
          f"{('  ' + note) if note else ''}", flush=True)
    log("done", total=total(), timings=t, note=note)

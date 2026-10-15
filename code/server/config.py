"""配置：环境变量、路径、常量。

所有"会因环境而变"的东西集中在这里：
换网络 / 换端口 / 换音频格式，只改 .env，不改任何业务代码。
"""
import os
import time

from dotenv import load_dotenv

load_dotenv()   # 会从本文件所在目录逐级向上找 .env → 能找到 E:\天翼\.env


def require(name: str) -> str:
    """读必需配置；缺了就立刻退出，而不是等运行时才炸。"""
    v = os.environ.get(name)
    if not v:
        raise SystemExit(
            f"[配置错误] 缺少必需环境变量 {name}；"
            f"请在 E:\\天翼\\.env 中填写（可参考 .env.example）")
    return v


# ---------------- 大脑 ----------------
BRAIN = os.environ.get("ZHIXIAOLI_BRAIN", "cloud").lower()
CLOUD_URL = os.environ.get("ZHIXIAOLI_CLOUD_URL",
                           "https://aiagent.cqut.edu.cn:31134"
                           "/v1/chat-messages")
CLOUD_KEY = require("ZHIXIAOLI_CLOUD_KEY")
CLOUD_USER = os.environ.get("ZHIXIAOLI_CLOUD_USER", "esp32_stu_001")
CLOUD_TIMEOUT = float(os.environ.get("ZHIXIAOLI_CLOUD_TIMEOUT", "20"))
# 是否把 conversation_id 交给平台（让平台自己记上下文）。
# 默认 0 = 不交，上下文完全由我们自己拼。原因（实测）：平台自己那份历史是坏的
#   —— 一长就 400；而且和我们注入的记忆叠在一起会互相打架，模型开始吐模板标记。
CLOUD_MULTITURN = os.environ.get("ZHIXIAOLI_CLOUD_MULTITURN", "0") == "1"
# 应用声明了一个必填输入变量 query（上限 200 字）。预览界面会自动填，走 API 必须自己传。
CLOUD_INPUT_MAX = int(os.environ.get("ZHIXIAOLI_CLOUD_INPUT_MAX", "200"))
# 校园平台证书不一定被系统信任，默认不校验（要严格校验就设 ZHIXIAOLI_CLOUD_VERIFY=1）
CLOUD_VERIFY = os.environ.get("ZHIXIAOLI_CLOUD_VERIFY", "0") == "1"
if not CLOUD_VERIFY:
    import urllib3
    urllib3.disable_warnings()

# ---------------- 目录与音频 ----------------
AUDIO_DIR = os.environ.get("ZHIXIAOLI_AUDIO_DIR", r"E:\天翼\语音")
# 音频格式：wav（默认，aue=6，16kHz/单声道/16位 PCM）/ mp3（aue=3）
TTS_FORMAT = os.environ.get("ZHIXIAOLI_TTS_FORMAT", "wav").lower()
BAIDU_AUE = 6 if TTS_FORMAT == "wav" else 3
# 给 ESP32 的下载地址里用的 IP，必须是电脑的局域网 IP
PUBLIC_HOST = os.environ.get("ZHIXIAOLI_PUBLIC_HOST", "192.168.137.1")
PUBLIC_PORT = int(os.environ.get("ZHIXIAOLI_PUBLIC_PORT", "5000"))
PORT = int(os.environ.get("ZHIXIAOLI_PORT", "5000"))

# TTS 后端：baidu（默认，实测可用）/ edge / 其他值=先百度失败再 edge
TTS_ENGINE = os.environ.get("ZHIXIAOLI_TTS_ENGINE", "baidu").lower()

# 百度语音 Key（缺任一项都会在启动时 fail fast）
BAIDU_API_KEY = require("BAIDU_API_KEY")
BAIDU_SECRET_KEY = require("BAIDU_SECRET_KEY")

AUDIO_FILENAME = "output.wav" if TTS_FORMAT == "wav" else "output.mp3"
ACCESS_LOG = os.path.join(AUDIO_DIR, "access.log")
MIC_LAST_WAV = os.path.join(AUDIO_DIR, "mic_last.wav")

# ---------------- 链路日志（工程债 B4）----------------
# 一次语音请求跨"识别 / 平台 / 合成 / 播放"四段，出问题只能靠猜 ✗
# → 每次请求一个 request_id，所有日志行都带它（JSON lines + 滚动切割 ✓）
LOG_FILE = os.environ.get(
    "ZHIXIAOLI_LOG_FILE",
    os.path.join(AUDIO_DIR, "链路日志.jsonl"))
LOG_ENABLE = os.environ.get("ZHIXIAOLI_LOG", "1") == "1"

BAIDU_CHUNK = 60  # 百度短文本合成单次上限约 60 字，超长自动分段拼接

try:
    os.makedirs(AUDIO_DIR, exist_ok=True)
except OSError as e:
    # 演练实测：以前这里会甩一个裸 FileNotFoundError，看日志的人根本不知道是"配置错了" ✗
    raise SystemExit(
        f"[配置错误] 音频目录不可用：{AUDIO_DIR}\n"
        f"        原因：{e}\n"
        f"        处理：在 E:\\天翼\\.env 里把 ZHIXIAOLI_AUDIO_DIR 改成一个**可写目录**")

# ---------------- 记忆 ----------------
# ⚠️ 锚点用"项目根"，不要用 __file__ 所在目录：
#    __file__ 会随文件搬家而变（本文件从 code\ 搬进 code\server\ 时，
#    默认记忆路径会静默变成 code\server\记忆.json ✗）
CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # → E:\天翼\code
MEMORY_FILE = os.environ.get(
    "ZHIXIAOLI_MEMORY_FILE",
    os.path.join(CODE_DIR, "记忆.json"))
MEMORY_MAX_FACTS = int(os.environ.get("ZHIXIAOLI_MEMORY_MAX", "20"))
HISTORY_TURNS = int(os.environ.get("ZHIXIAOLI_HISTORY_TURNS", "3"))

# ---------------- 报修工单台账（SQLite）----------------
# 为什么用 SQLite：标准库自带、零依赖、有并发保护（WAL 模式），比裸 JSON 可靠 ✓
TICKETS_DB = os.environ.get(
    "ZHIXIAOLI_TICKETS_DB",
    os.path.join(CODE_DIR, "工单.db"))

# ---------------- 板子（ESP32）与网页代理 ----------------
# 只有服务端需要知道板子的地址：网页通过 /v1/device/* 代理访问（见 device.py）
BOARD_HOST = os.environ.get("ZHIXIAOLI_BOARD_HOST", "192.168.137.39")
BOARD_TIMEOUT = float(os.environ.get("ZHIXIAOLI_BOARD_TIMEOUT", "4"))

# 可选鉴权：留空 = 不启用（局域网演示的默认状态）。
# ⚠️ 部署到公网/校园网可达处之前必须填一个随机串 —— 这是本项目的硬约束（工程债指南 D4）
API_TOKEN = os.environ.get("ZHIXIAOLI_API_TOKEN", "").strip()

# ---------------- 进程自报（给网页与排障用）----------------
# 为什么要自报：改完代码忘了重启 Flask，从外面完全看不出来 ✗
# （本项目真实踩过：追了半小时的"服务端坏了"，其实是旧进程在跑）
STARTED_AT = time.strftime("%Y-%m-%d %H:%M:%S")


def _code_version() -> str:
    """取 git 短 hash：直接读 .git，不起子进程（快、且受限环境也能用 ✓）。

    ⚠️ 坑：refs 可能**被打包**进 `.git/packed-refs` ✗ ——
       只读 `.git/refs/heads/xxx` 会 FileNotFound（实测踩到：版本一直显示 unknown ✓）。
       所以两条路都试：先找未打包的 ref 文件，再查 packed-refs ✓
    取不到就返回 unknown —— 绝不抛异常 ✗
    """
    try:
        root = os.path.dirname(CODE_DIR)          # E:\天翼\code → E:\天翼
        with open(os.path.join(root, ".git", "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref:"):
            return head[:7]                        # 分离头指针：HEAD 里直接是 sha
        ref = head.split(" ", 1)[1].strip()
        # ① 未打包
        p = os.path.join(root, ".git", ref.replace("/", os.sep))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()[:7]
        # ② 已打包：packed-refs 里形如 "<sha> <ref>"
        with open(os.path.join(root, ".git", "packed-refs"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0][:7]
    except Exception:
        pass
    return "unknown"


CODE_VERSION = _code_version()
# -*- coding: utf-8 -*-
"""配置：环境变量、路径、常量。

所有"会因环境而变"的东西集中在这里：
换网络 / 换端口 / 换音频格式，只改 .env，不改任何业务代码。
"""
import os

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
BAIDU_CHUNK = 60  # 百度短文本合成单次上限约 60 字，超长自动分段拼接

os.makedirs(AUDIO_DIR, exist_ok=True)
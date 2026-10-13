# -*- coding: utf-8 -*-
"""
智小理 · 本地保底服务（Flask）

接口：
  POST /v1/chat       收 ESP32 的 query（文字），返回 voice_text /
                      device_action / audio_url
  POST /v1/voice      收 ESP32 上传的 WAV（麦克风录音），一次完成 识别->意图->合成
  POST /v1/asr        只做语音识别，方便在电脑上单独验证一段音频
  GET  /audio/<file>  给 ESP32 下载音频

本次修复：
  1. 【500 的根因】edge-tts 已被微软拒绝：wss 握手返回 403 WSServerHandshakeError，
     异常没人接住 → Flask 返回 HTML 500 页 → ESP32 的 ArduinoJson 报 InvalidInput。
     现在改成「百度 TTS 优先，edge-tts 兜底」，路由和全局都加了异常兜底，
     任何情况都返回 JSON，绝不再吐 HTML。
  2. TTS 失败不再影响文本回复：没合成出音频就只回文本，不带 audio_url。
  3. 开发服务器开 threaded=True：否则 ESP32 一边拉音频一边发新请求会互相阻塞。
"""
import os                      # 已有
from dotenv import load_dotenv  # 新增

load_dotenv()                   # 新增：把 .env 里的值载进环境变量
import asyncio
import hashlib
import json
import os
import re
import time
import traceback

import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# ================= 配置 =================
# 大脑：cloud = 优先问智小理平台（失败自动回退本地规则，断网也能演示）/ local = 只用本地规则

def require(name: str) -> str:
    """读必需配置；缺了就立刻退出，而不是等运行时才炸。"""
    v = os.environ.get(name)
    if not v:
        raise SystemExit(
            f"[配置错误] 缺少必需环境变量 {name}；"
            f"请在 E:\\天翼\\.env 中填写（可参考 .env.example）")
    return v

BRAIN = os.environ.get("ZHIXIAOLI_BRAIN", "cloud").lower()
CLOUD_URL = os.environ.get("ZHIXIAOLI_CLOUD_URL",
                           "https://aiagent.cqut.edu.cn:31134"
                           "/v1/chat-messages")
CLOUD_KEY = require("ZHIXIAOLI_CLOUD_KEY")
CLOUD_USER = os.environ.get("ZHIXIAOLI_CLOUD_USER", "esp32_stu_001")
CLOUD_TIMEOUT = float(os.environ.get("ZHIXIAOLI_CLOUD_TIMEOUT", "20"))
# 是否把 conversation_id 交给平台（让平台自己记上下文）。
# 默认 0 = 不交，上下文完全由我们自己拼。原因（实测）：平台自己那份历史是坏的
#   —— 一长就 400；而且和我们注入的记忆叠在一起会互相打架，模型开始吐 </assistant><user> 这种模板标记。
CLOUD_MULTITURN = os.environ.get("ZHIXIAOLI_CLOUD_MULTITURN", "0") == "1"
# 这个应用用 GET /v1/parameters 查出来声明了一个必填输入变量 query（标签"语音识别文本"，
# 上限 200 字）。预览界面会自动填上，走 API 就必须自己在 inputs 里传 —— 否则
# 提示词/知识库检索节点引用它时拿到的是空值。所以这里按声明传，并做长度保护。
CLOUD_INPUT_MAX = int(os.environ.get("ZHIXIAOLI_CLOUD_INPUT_MAX", "200"))
# 校园平台的证书不一定被系统信任，默认不校验（要严格校验就设 ZHIXIAOLI_CLOUD_VERIFY=1）
CLOUD_VERIFY = os.environ.get("ZHIXIAOLI_CLOUD_VERIFY", "0") == "1"
if not CLOUD_VERIFY:
    import urllib3
    urllib3.disable_warnings()
AUDIO_DIR = os.environ.get("ZHIXIAOLI_AUDIO_DIR", r"E:\天翼\语音")
# 音频格式：wav（默认，aue=6，16kHz/单声道/16位 PCM）/ mp3（aue=3）
# ESP32 端那个库对百度这种 MPEG2/16kHz 的 mp3 帧头可能认不出来，先吐 WAV 排除这一层。
TTS_FORMAT = os.environ.get("ZHIXIAOLI_TTS_FORMAT", "wav").lower()
BAIDU_AUE = 6 if TTS_FORMAT == "wav" else 3
# 给 ESP32 的下载地址里用的 IP，必须是电脑的局域网 IP（手机热点下通常就是 192.168.137.1）
PUBLIC_HOST = os.environ.get("ZHIXIAOLI_PUBLIC_HOST", "192.168.137.1")
PUBLIC_PORT = int(os.environ.get("ZHIXIAOLI_PUBLIC_PORT", "5000"))
PORT = int(os.environ.get("ZHIXIAOLI_PORT", "5000"))

# TTS 后端：baidu（默认，实测可用）/ edge / 其他值=先百度失败再 edge
TTS_ENGINE = os.environ.get("ZHIXIAOLI_TTS_ENGINE", "baidu").lower()

# 百度语音合成 Key（沿用你 test_tts.py 里的那对）
BAIDU_API_KEY = require("BAIDU_API_KEY")
BAIDU_SECRET_KEY = require("BAIDU_SECRET_KEY")

AUDIO_FILENAME = "output.wav" if TTS_FORMAT == "wav" else "output.mp3"
ACCESS_LOG = os.path.join(AUDIO_DIR, "access.log")  # 谁在什么时候要了什么、服务端回了多少字节
# ESP32 最近一次上传的录音，麦克风不对时直接听它
MIC_LAST_WAV = os.path.join(AUDIO_DIR, "mic_last.wav")
BAIDU_CHUNK = 60  # 百度短文本合成单次上限约 60 字，超长自动分段拼接

os.makedirs(AUDIO_DIR, exist_ok=True)

# 百度 access_token 缓存（有效期 30 天）
_baidu_token = {"value": None, "expire_at": 0.0}


# ================= TTS：百度 =================
def _get_baidu_token():
    now = time.time()
    if _baidu_token["value"] and now < _baidu_token["expire_at"]:
        return _baidu_token["value"]
    r = requests.get(
        "https://aip.baidubce.com/oauth/2.0/token",
        params={
            "grant_type": "client_credentials",
            "client_id": BAIDU_API_KEY,
            "client_secret": BAIDU_SECRET_KEY,
        },
        timeout=10,
    )
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"百度取 token 失败: {data}")
    _baidu_token["value"] = token
    _life = int(data.get("expires_in", 2592000))
    _baidu_token["expire_at"] = now + _life - 3600
    return token


def baidu_tts(text, out_path):
    """调用百度短文本在线合成，分段拼接后写入 out_path。"""
    token = _get_baidu_token()
    audio = bytearray()
    chunks = [text[i:i + BAIDU_CHUNK]
              for i in range(0, len(text), BAIDU_CHUNK)] or [text]
    for chunk in chunks:
        r = requests.get(
            "https://tsn.baidu.com/text2audio",
            params={
                "tex": chunk,
                "tok": token,
                "cuid": "ESP32",
                "ctp": 1,
                "lan": "zh",
                "spd": 5,
                "pit": 5,
                "vol": 5,
                "per": 0,  # 0 女声 / 1 男声 / 3 情感男声 / 4 情感女声
                "aue": BAIDU_AUE,  # 3 = mp3, 6 = wav(16k/单声道/16位 PCM)
            },
            timeout=20,
        )
        ctype = r.headers.get("Content-Type", "")
        if "audio" not in ctype:
            raise RuntimeError(f"百度 TTS 返回非音频: {r.text[:200]}")
        audio.extend(r.content)

    with open(out_path, "wb") as f:
        f.write(bytes(audio))
    return out_path


# ================= TTS：edge-tts（兜底，当前会被微软 403） =================
def edge_tts_generate(text, out_path):
    import edge_tts  # 延迟导入，库有问题也不影响启动

    async def _run():
        await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(out_path)

    asyncio.run(_run())
    return out_path


def synthesize(text, out_path):
    """返回实际使用的引擎名；全部失败则抛异常。"""
    engines = {"baidu": [baidu_tts], "edge": [edge_tts_generate]}.get(
        TTS_ENGINE, [baidu_tts, edge_tts_generate]
    )
    last_err = None
    for fn in engines:
        try:
            fn(text, out_path)
            return fn.__name__
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[TTS] {fn.__name__} 失败: "
                  f"{type(e).__name__}: {e}", flush=True)
    raise RuntimeError(f"所有 TTS 引擎都失败，最后一个错误: {last_err}")


# ================= ASR：百度短语音识别（标准版） =================
def baidu_asr(wav_bytes):
    """16k/单声道/16位 的 wav 或 pcm 字节流 -> 文本。

    实测两种调用方式（raw body / JSON+base64）都可用，这里用 raw，省一次 base64。
    dev_pid: 1537=普通话(纯中文)  1536=普通话(带简单英文)  1737=英语
    """
    token = _get_baidu_token()  # 和 TTS 同一个 app 的 token
    r = requests.post(
        "https://vop.baidu.com/server_api",
        params={"dev_pid": 1537, "cuid": "ESP32", "token": token},
        headers={"Content-Type": "audio/wav; rate=16000"},
        data=wav_bytes,
        timeout=30,
    )
    j = r.json()
    if j.get("err_no") != 0:
        # 常见: 3301 音质太差/没听清, 3302 鉴权失败, 3303 服务端问题, 3305 音频过长
        raise RuntimeError(
            f"百度 ASR 失败: err_no={j.get('err_no')} msg={j.get('err_msg')}")
    return (j.get("result") or [""])[0]


# ================= 大脑 1：智小理平台（Dify 接口） =================
# 多轮对话靠 conversation_id：平台每次都会回一个新的，带回去才能延续上下文。
# ⚠️ 实测这块平台的多轮是坏的：带 conversation_id 时它的 workflow 会 400
#    （template:2: function "query" not defined）。所以这里做了自动降级：
#    一旦带上下文失败，就不再每轮都去撞，改成单轮对话；平台修好后 POST /v1/reset 即可恢复。
_cloud_conv = {"id": "", "multiturn_ok": True}
# 平台"答非所问"故障：实测提示词变量没取到值时，模型收到的是字符串 null，
# 于是对任何问题都只回一句自我介绍。识别到就切本地规则，隔几分钟再试探一次。
_cloud_state = {"degraded": False, "checked_at": 0.0}
RECHECK_SEC = float(os.environ.get("ZHIXIAOLI_CLOUD_RECHECK", "300"))
_DEGRADED_MARKS = ("user input is", "user message is", "no actual question",
                   "there's no query", "no user query")


def _cloud_is_degraded(raw_answer):
    """模型收到 null 时的特征（它会在思考里抱怨输入是 null、没有 query）。"""
    a = (raw_answer or "").lower()
    return "null" in a and any(m in a for m in _DEGRADED_MARKS)


def _clean_voice(text):
    """清洗模型回复：平台偶尔会吐聊天模板标记，或把我的注入块回声出来。
    这些既不能念给用户听，也不能显示在屏幕上。"""
    t = str(text or "")
    # 1) 砍掉被回声的注入块（从第一个标记开始全部丢弃）
    for mark in ("【已知信息】", "【最近对话】", "【当前问题】", "(背景：", "（背景："):
        i = t.find(mark)
        if i > 0:
            t = t[:i]
    # 2) 去掉聊天模板标记
    t = re.sub(r"</?(?:assistant|user|system|human|ai)>", " ", t, flags=re.I)
    # 3) 收掉多余空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _cloud_request(query, conv_id):
    """真正发一次请求，返回 (voice_text, device_action)。非 200 抛异常。"""
    # 平台是聊天模板（<user>…</user>），换行和方括号会被它当成标记 → 统一压成单行再发
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    # 按应用声明的必填输入变量传（inputs.query），同时保留对话入口的 query
    send_q = query[:CLOUD_INPUT_MAX]
    if len(query) > CLOUD_INPUT_MAX:
        print(f"[云端] 输入超过应用声明的 {CLOUD_INPUT_MAX} 字上限，已截断", flush=True)
    body = {"inputs": {"query": send_q}, "query": send_q,
            "response_mode": "blocking", "user": CLOUD_USER}
    if conv_id:
        body["conversation_id"] = conv_id

    t0 = time.time()
    r = requests.post(
        CLOUD_URL,
        headers={"Authorization": f"Bearer {CLOUD_KEY}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=CLOUD_TIMEOUT,
        verify=CLOUD_VERIFY,
    )
    dt = time.time() - t0
    if r.status_code != 200:
        raise RuntimeError(f"平台返回 HTTP {r.status_code}: {r.text[:200]}")

    j = r.json()
    if j.get("conversation_id"):
        _cloud_conv["id"] = j["conversation_id"]

    answer = (j.get("answer") or "").strip()
    # 平台故障检测：变量没取到值时模型收到的是 null，只会打招呼 —— 立刻切本地规则
    if _cloud_is_degraded(answer):
        _cloud_state["degraded"] = True
        _cloud_state["checked_at"] = time.time()
        print("[云端] ⚠️ 平台没把用户输入传给模型（模型收到的是 null）——"
              "已改用本地规则；修好平台后 POST /v1/reset，或等几分钟自动重试",
              flush=True)
    # 平台会先吐一段 <think>…</think> 推理过程，用户不需要听，去掉
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
    print(f"[云端] {dt:.1f}s 会话={'有' if conv_id else '无'} "
          f"答复={answer[:120]!r}", flush=True)

    # 平台约定：答复的最后是一个 JSON {"voice_text": "...", "device_action": "..."}
    voice_text, action = "", "NONE"
    s, e = answer.rfind("{"), answer.rfind("}")
    if 0 <= s < e:
        try:
            inner = json.loads(answer[s:e + 1])
            voice_text = str(inner.get("voice_text") or "")
            action = str(inner.get("device_action") or "NONE")
        except Exception:
            # JSON 不合法时退一步用正则捞
            m = re.search(r'"voice_text"\s*:\s*"([^"]*)"', answer)
            if m:
                voice_text = m.group(1)
            m = re.search(r'"device_action"\s*:\s*"([^"]*)"', answer)
            if m:
                action = m.group(1)
    if not voice_text:
        voice_text = answer  # 平台没按约定回 JSON，就把整段念出来
    return _clean_voice(voice_text), action


def ask_cloud(query):
    """问智小理平台。默认不传 conversation_id（上下文我们自己拼），
    要交给平台就把 ZHIXIAOLI_CLOUD_MULTITURN 设成 1（它那份历史有 bug，不推荐）。"""
    # 平台处于"收到 null"的故障状态时先别再问它（隔几分钟自动重试一次）
    if _cloud_state["degraded"]:
        if time.time() - _cloud_state["checked_at"] < RECHECK_SEC:
            raise RuntimeError("平台故障中（模型收到的是 null），暂用本地规则")
        _cloud_state["degraded"] = False
        print("[云端] 到点重试平台…", flush=True)

    conv_id = _cloud_conv["id"] if (
        CLOUD_MULTITURN and _cloud_conv["multiturn_ok"]) else ""
    try:
        return _cloud_request(query, conv_id)
    except Exception as e:
        if conv_id:
            print(f"[云端] 带上下文失败，自动降级为单轮: {e}", flush=True)
            _cloud_conv["multiturn_ok"] = False   # 不再每轮都白撞一次
            _cloud_conv["id"] = ""
            return _cloud_request(query, "")
        raise


# ================= 记忆 1：长期记忆（跨重启，存 记忆.json） =================
# 为什么不用平台的 conversation_id 做记忆：实测平台的多轮 workflow 是坏的（带历史就 400）。
# 所以记忆完全由我们自己管：把【已知信息 + 最近对话】拼进 query 一起发给平台，它照样能懂上下文。
MEMORY_FILE = os.environ.get(
    "ZHIXIAOLI_MEMORY_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "记忆.json"),
)
MEMORY_MAX_FACTS = int(os.environ.get("ZHIXIAOLI_MEMORY_MAX", "20"))
HISTORY_TURNS = int(os.environ.get("ZHIXIAOLI_HISTORY_TURNS", "3"))

_memory = {"facts": [], "events": []}   # facts=长期已知信息，events=历史事件
_history = []                           # 短期记忆：[(用户说的, 助手回的)]，只在内存里


def load_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            m = json.load(f)
        _memory["facts"] = [str(x) for x in
                            (m.get("facts") or [])][-MEMORY_MAX_FACTS:]
        _memory["events"] = list(m.get("events") or [])[-200:]
        print(f"[记忆] 载入 {MEMORY_FILE}："
              f"已知信息 {len(_memory['facts'])} 条，"
              f"历史事件 {len(_memory['events'])} 条", flush=True)
    except FileNotFoundError:
        print(f"[记忆] 还没有 {MEMORY_FILE}，从零开始（跟它说“我叫…”就会自动记下来）", flush=True)
    except Exception as e:
        print(f"[记忆] 读取失败（忽略，按空记忆跑）: {e}", flush=True)


def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[记忆] 保存失败: {e}", flush=True)


def add_fact(fact):
    """加一条已知信息（重复不加）。返回是否新增。"""
    fact = (fact or "").strip()
    if not fact or fact in _memory["facts"]:
        return False
    _memory["facts"].append(fact)
    _memory["facts"] = _memory["facts"][-MEMORY_MAX_FACTS:]
    print(f"[记忆] 记住：{fact}", flush=True)
    save_memory()
    return True


# 只认这几种很明确的说法，避免把普通句子误记成"记忆"
_FACT_RULES = (
    (r"我(?:的名字)?叫([\u4e00-\u9fa5A-Za-z0-9]{1,8})", "用户叫{}"),
    (r"我的名字是([\u4e00-\u9fa5A-Za-z0-9]{1,8})", "用户叫{}"),
    (r"我是([\u4e00-\u9fa5]{2,10}(?:学院|专业|系|班))", "用户是{}"),
    (r"我(?:喜欢|最爱|偏好)([\u4e00-\u9fa5A-Za-z0-9]{1,12})", "用户喜欢{}"),
    (r"我(?:的)?学号(?:是)?([A-Za-z0-9]{4,20})", "用户学号是{}"),
    (r"我(?:的)?(?:手机|电话|手机号)(?:是)?(1\d{10})", "用户手机号是{}"),
    (r"^记住[，,：:\s]*(.{2,40})$", "{}"),
    (r"帮我记住[，,：:\s]*(.{2,40})$", "{}"),
)


# 问句里的"我叫什么""我是哪个学院的"不是记忆，这些词一出现就跳过
_NOT_A_FACT = ("什么", "哪个", "哪些", "哪", "谁", "吗", "呢", "怎么", "怎样",
               "多少", "几", "是否", "是不是", "在哪", "有没有")


def extract_facts(text):
    """从一句话里提取值得长期记住的信息。"""
    out = []
    for pat, tpl in _FACT_RULES:
        m = re.search(pat, text or "")
        if not m:
            continue
        val = m.group(1).strip()
        if any(w in val for w in _NOT_A_FACT):   # 问句不算记忆
            continue
        if tpl == "{}" and val.startswith("我"):
            val = "用户" + val[1:]               # "记住我喜欢X" → "用户喜欢X"，与其他规则合并去重
        out.append(tpl.format(val))
    return out


# ================= 记忆 2：短期记忆（最近几轮对话） =================
def build_query(query):
    """把记忆和最近对话压成【一行】背景，拼在问题前面一起发给平台。

    为什么必须压成一行：平台是聊天模板（<user>…</user>），带换行和【】的文本会被它当成模板标记，
    实测模型会开始吐 </assistant><user>，回答也退化成"我在呢，有什么需要帮忙的吗？"这类敷衍话术。
    """
    def flat(s):
        return re.sub(r"\s+", " ", str(s or "")).strip()

    parts = []
    if _memory["facts"]:
        _facts = "；".join(flat(f) for f in _memory["facts"][-5:])
        parts.append("(背景：" + _facts + ")")
    if _history:
        turns = []
        for u, a in _history[-HISTORY_TURNS:]:
            turns.append("用户说「" + flat(u) + "」，你答「" + flat(a) + "」")
        parts.append("(刚才的对话：" + "；".join(turns) + ")")
    parts.append(flat(query))
    return " ".join(p for p in parts if p)


def remember_turn(query, reply):
    """一轮结束后更新短期记忆和历史事件。"""
    _history.append((query, reply))
    del _history[:-HISTORY_TURNS]     # 只保留最近几轮
    _memory["events"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "user": query, "reply": reply})
    _memory["events"] = _memory["events"][-200:]
    save_memory()


load_memory()


# ================= 找资源：音乐（上网搜歌 → 下载到本地 → 交给板子播） =================
# 架构：电脑做桥。搜索和下载都在电脑上完成，只把「局域网 HTTP 地址」给 ESP32，
#       所以板子不用访问外网、不用 TLS，音源也能随时换。
# 音源：Apple 官方 30 秒试听接口（免费、不需要 key，直链是 m4a/AAC）。
#       已确认音频库能解 m4a：Audio.cpp:757 有 AACDecoder，:2778 有完整 MP4 盒子解析。
MUSIC_ENABLE = os.environ.get("ZHIXIAOLI_MUSIC", "1") == "1"
# 实测：带 country=cn 去搜"周杰伦"会返回 0 条，不带地区（默认美区）反而能搜到中文歌，
# 所以默认留空＝不传地区；要用特定区就自己设，搜不到会自动去掉地区再搜一次。
MUSIC_COUNTRY = os.environ.get("ZHIXIAOLI_MUSIC_COUNTRY", "")
MUSIC_DIR = os.path.join(AUDIO_DIR, "music")

_MUSIC_TRIGGERS = ("放歌", "放一首", "放个歌", "放首歌", "来一首", "来首", "放首",
                   "听歌", "听一首", "听首", "放音乐", "听音乐", "播放", "我想听", "我要听")
_MUSIC_STOPWORDS = ("帮我", "请", "给我", "我想", "我要", "能不能", "可以", "一下", "现在",
                    "播放一首", "播放", "放一首", "放一个", "放个", "放首", "来一首", "来一个",
                    "来首", "听一首", "听首", "放歌", "听歌", "放音乐", "听音乐",
                    "的歌", "歌曲", "首歌", "的音乐")
# 宽松说法（"放七里香""听稻香"）容易误伤这些常见词和疑问句，命中就绝不走音乐分支
_NOT_MUSIC_WORDS = ("放心", "放假", "放学", "放在", "放到", "放这", "放那", "放弃", "放大",
                    "方便", "放好", "放开", "播放器", "听说", "听话", "听写")
_QUESTION_WORDS = ("怎么", "为什么", "什么", "吗", "呢", "哪", "谁", "多少", "几",
                   "是不是", "有没有", "何时", "几点")
_COVER_WORDS = ("cover", "翻唱", "版", "piano", "instrumental", "伴奏", "纯音乐",
                "remix", "karaoke", "tribute", "dj ")


def music_request(query):
    """判断是不是"放歌"。返回 (搜索词, 是否明确说法)。

    明确说法：放一首X / 放首歌 / 来一首X / 我想听X / 播放X …
    宽松说法：放X / 来X / 听X / 播X（很短、不像问句、不是"放心/放假"这类词）
              —— 宽松这种如果搜不到，就放弃音乐分支，交给云端正常回答，不会瞎回"没找到"。
    """
    q = (query or "").strip()
    if not q:
        return None, False

    strict = any(t in q for t in _MUSIC_TRIGGERS)
    loose = False
    if not strict and len(q) <= 14 and q[0] in "放来听播":
        if (not any(w in q for w in _NOT_MUSIC_WORDS)
                and not any(w in q for w in _QUESTION_WORDS)):
            loose = True
    if not (strict or loose):
        return None, False

    term = q
    for w in _MUSIC_STOPWORDS:
        term = term.replace(w, "")
    term = term.strip(" 的了，,。.！!？?、")
    if term[:1] in ("放", "来", "听", "播"):      # 宽松说法剩下的开头动词也去掉
        term = term[1:].strip(" 的了，,。.！!？?、")
    if term in ("歌", "音乐", "歌曲", "曲子", "一首", "首", "个", "点"):   # 只说了"放首歌"
        term = ""
    return term, strict


# 中文歌手名 → iTunes 里的英文名：带上英文名搜，原唱命中率高得多（否则中文热歌常搜到翻唱）
_ARTIST_ALIAS = {
    "周杰伦": "Jay Chou", "林俊杰": "JJ Lin", "陈奕迅": "Eason Chan",
    "邓紫棋": "G.E.M.", "薛之谦": "Joker Xue", "刘若英": "Rene Liu",
    "周深": "Zhou Shen", "李荣浩": "Li Ronghao", "毛不易": "Mao Buyi",
    "张杰": "Jason Zhang", "汪苏泷": "Silence Wang", "许嵩": "Vae",
    "田馥甄": "Hebe Tien", "五月天": "Mayday", "张学友": "Jacky Cheung",
    "王菲": "Faye Wong", "孙燕姿": "Stefanie Sun", "梁静茹": "Fish Leong",
    "蔡依林": "Jolin Tsai", "陶喆": "David Tao", "王力宏": "Leehom Wang",
}


def _artist_alias_hits(term):
    """搜索词里出现的中文歌手名，返回对应的英文名列表。"""
    return [en for zh, en in _ARTIST_ALIAS.items() if zh in term]


def _score_result(it, term):
    """给搜索结果打分：优先原唱、扣掉翻唱/钢琴版。"""
    name = (it.get("trackName") or "").lower()
    artist = (it.get("artistName") or "").lower()
    t = term.lower()
    score = 0
    aliases = [a.lower() for a in _artist_alias_hits(term)]
    if artist and (artist in t or t in artist):
        score += 3                                    # 搜索词里直接带了歌手名
    if any(a and (a in artist or artist in a) for a in aliases):
        score += 3                                    # 中文歌手名对上了英文歌手（原唱）
    if name and name in t:
        score += 2                                    # 歌名匹配
    if any(w in name for w in _COVER_WORDS):
        score -= 2                                    # 翻唱/钢琴版/伴奏往后排
    return score


def search_music(term):
    """搜 30 秒试听。返回 (歌名, 歌手, 试听地址)；找不到返回 None。"""
    # 搜索时补上歌手的英文名（"周杰伦的七里香" → "周杰伦的七里香 Jay Chou"）
    query_term = term
    aliases = _artist_alias_hits(term)
    if aliases:
        query_term = term + " " + " ".join(aliases)

    tries = []
    if MUSIC_COUNTRY:
        tries.append({"term": query_term, "media": "music", "limit": 10,
                      "country": MUSIC_COUNTRY})
    # 去掉地区再试一次
    tries.append({"term": query_term, "media": "music", "limit": 10})
    if query_term != term:
        # 补英文名还搜不到就用原词
        tries.append({"term": term, "media": "music", "limit": 10})
    for params in tries:
        try:
            r = requests.get("https://itunes.apple.com/search",
                             params=params, timeout=12)
            r.raise_for_status()
            items = [it for it in (r.json().get("results") or [])
                     if it.get("previewUrl")]
            if items:
                best = max(items, key=lambda it: _score_result(it, term))
                return ((best.get("trackName") or "").strip(),
                        (best.get("artistName") or "").strip(),
                        best["previewUrl"])
        except Exception as e:
            print(f"[音乐] 搜索出错: {type(e).__name__}: {e}", flush=True)
    return None


def download_music(term, url):
    """下载到 AUDIO_DIR/music 并返回局域网可播地址；失败返回 ""。"""
    try:
        os.makedirs(MUSIC_DIR, exist_ok=True)
        _digest = hashlib.md5(term.encode("utf-8")).hexdigest()[:10]
        name = "music_" + _digest + ".m4a"
        path = os.path.join(MUSIC_DIR, name)
        if not (os.path.exists(path) and os.path.getsize(path) > 10000):
            data = requests.get(
                url, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).content
            if len(data) < 10000:
                print(f"[音乐] 下载太小（{len(data)} 字节），放弃", flush=True)
                return ""
            # 音频库只认 ftyp 品牌 M4A /isom/mp42（Audio.cpp:2839），其它品牌就地改 4 个字节
            if (data[4:8] == b"ftyp"
                    and data[8:12] not in (b"M4A ", b"isom", b"mp42")):
                print(f"[音乐] ftyp 品牌 {data[8:12]!r} 不被音频库支持，"
                      "改成 M4A ", flush=True)
                data = data[:8] + b"M4A " + data[12:]
            with open(path, "wb") as f:
                f.write(data)
        print(f"[音乐] 就绪: {path}（{os.path.getsize(path)} 字节）", flush=True)
        return f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/music/{name}"
    except Exception as e:
        print(f"[音乐] 下载失败: {type(e).__name__}: {e}", flush=True)
        return ""


def make_tts(text):
    """把文本合成为 output.wav，返回可下载地址（失败返回 ""）。"""
    path = os.path.join(AUDIO_DIR, AUDIO_FILENAME)
    try:
        engine = synthesize(text, path)
        if os.path.getsize(path) > 0:
            print(f"[TTS] {engine} 合成成功: {path}", flush=True)
            return f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/{AUDIO_FILENAME}"
        print("[TTS] 生成了 0 字节文件，视为失败", flush=True)
    except Exception:
        print("[TTS] 合成失败，本次只回文本：", flush=True)
        traceback.print_exc()
    return ""


def handle_music(song, raw="", strict=True):
    """放歌分支：不走云端（云端给不了音频）。返回与 handle_query 相同结构的 dict，
    audio_url=播报、audio_url2=歌曲本体，板子会"先播报、再放歌"。

    返回 None = 这次交给云端正常回答（只有"宽松说法搜不到"时才会这样）。
    """
    music_url, title, artist = "", "", ""
    if not song:
        reply, action, source = "你想听谁的歌？说个歌名或者歌手就行。", "NONE", "音乐"
    else:
        found = None
        # 先用解析出来的关键词搜；搜不到再拿原句搜一次（"放七里香"这种原句也可能被模糊匹配到）
        for term in ([song, raw] if raw and raw != song else [song]):
            try:
                found = search_music(term)
            except Exception as e:
                print(f"[音乐] 搜索失败: {type(e).__name__}: {e}", flush=True)
                found = None
            if found:
                break
        if not found:
            if not strict:
                # 宽松说法又搜不到 → 很可能根本不是点歌，交给云端正常回答
                print(f"[音乐] “{raw}”宽松匹配但搜不到，交给云端处理", flush=True)
                return None
            reply, action, source = f"没找到《{song}》，换个歌名或者歌手再试试？", "NONE", "音乐"
        else:
            title, artist, purl = found
            print(f"[音乐] 搜到: {artist} - {title}", flush=True)
            music_url = download_music(song, purl)
            if music_url:
                reply, action, source = (
                    f"好的，给你放{artist}的《{title}》", "PLAY_MUSIC", "音乐")
            else:
                reply, action, source = (
                    f"找到了{artist}的《{title}》，但下载失败了，稍后再试试。",
                    "NONE", "音乐")

    print(f"[音乐] {reply}", flush=True)
    remember_turn(song or "放歌", reply)

    audio_url = make_tts(reply)
    if not audio_url and music_url:      # 播报合成失败就直奔歌曲，别让这次请求白跑
        audio_url, music_url = music_url, ""

    return {
        "voice_text": reply,
        "device_action": action,
        "source": source,
        "audio_url": audio_url,
        "audio_url2": music_url,
        "memory": len(_memory["facts"]),
        "history": len(_history),
    }


# ================= 大脑 2：本地保底规则（断网也能演示） =================
# 用户明确要办事务时，平台的答复可能不办事（改提示词/知识库之后就出现过），
# 这两张表用来"保证有硬件动作 + 答非所问就换成本地话术"。
_ACTION_RULES = (
    (("会议室", "订会议", "预约会议"), "LED_GREEN_ON",
     "好的，已经帮你预约好会议室啦。"),
    (("图书馆", "借书", "还书"), "LED_BLUE_ON",
     "好的，已经帮你查到图书馆的信息啦。"),
    (("闹钟", "叫我起床", "提醒我"), "LED_BLUE_ON",
     "好的，闹钟已经帮你设好啦。"),
)
# 这些词说明用户在问学校的事（平台故障时用来决定要不要说"知识库连不上"）
_SCHOOL_WORDS = ("校训", "校区", "学校", "学院", "规章", "制度", "学生手册",
                 "宿舍", "食堂", "教务处", "辅导员", "校历", "校车", "图书馆")


def _action_guard(query, reply, action):
    """返回 (reply, action, source_override)。平台没办事时本地兜底，正常时原样返回。"""
    q = query or ""
    for words, act, local_text in _ACTION_RULES:
        if not any(w in q for w in words):
            continue
        final_action = act if (not action or action == "NONE") else action
        # 平台故障中，或答复里根本没提这件事（只是把"会议室"念在能力清单里）→ 本地兜底
        if _cloud_state["degraded"] or not any(w in (reply or "") for w in words):
            print(f"[动作] 平台答复没办事，本地兜底：{local_text}", flush=True)
            return local_text, final_action, "本地兜底"
        return reply, final_action, None
    return reply, action, None


def local_reply(query):
    # 断网时也能回答"我叫什么" —— 证明记忆是真存下来的
    if (("我叫什么" in query or "我是谁" in query or "你记得我" in query)
            and _memory["facts"]):
        return "你跟我说过：" + "；".join(_memory["facts"][-3:]), "NONE"
    if "会议室" in query:
        return "好的，已经帮你预约好会议室啦。", "LED_GREEN_ON"
    if "图书馆" in query or "借书" in query:
        return "好的，已经帮你查到图书馆的信息啦。", "LED_BLUE_ON"
    if "闹钟" in query or "叫我起床" in query:
        return "好的，闹钟已经帮你设好啦。", "LED_BLUE_ON"
    if not query:
        return "我没听清，你再说一遍好吗？", "NONE"
    if _cloud_state["degraded"] and any(w in query for w in _SCHOOL_WORDS):
        # 平台故障、用户又问的是学校信息时说人话，别拿"我在呢"敷衍
        return ("后台知识库暂时连不上，学校信息这类问题我过会儿再答你；"
                "预约会议室、查图书馆、放歌这些现在都能用。"), "NONE"
    return "我在呢，有什么可以帮你的吗？", "NONE"


# ================= 意图 + 合成（文字入口和语音入口共用） =================
def handle_query(query):
    """返回 dict：voice_text / device_action / audio_url /
    source / memory / history。"""
    query = (query or "").strip()

    # 1) 先从这句话里提取长期记忆（"我叫小李"、"记住…"）
    for fact in extract_facts(query):
        add_fact(fact)

    # 2) 放歌类请求单独走音乐分支：云端给不了音频
    if MUSIC_ENABLE:
        song, strict = music_request(query)
        if song is not None:
            result = handle_music(song, query, strict)
            if result is not None:
                return result            # None = 宽松匹配又搜不到，下面交给云端

    # 3) 带着【已知信息 + 最近对话】去问大脑
    enriched = build_query(query)
    if BRAIN == "local":
        reply, action = local_reply(query)
        source = "本地规则"
    else:
        try:
            reply, action = ask_cloud(enriched)
            source = "云端平台"
            # 平台要是答非所问/没下发动件动作，这里按用户原话补上
            reply, action, override = _action_guard(query, reply, action)
            if override:
                source = override
        except Exception as e:
            print(f"[云端] 调用失败，回退本地规则: {type(e).__name__}: {e}", flush=True)
            reply, action = local_reply(query)
            source = "本地回退"

    # 4) 记下这一轮（短期记忆 + 历史事件）
    remember_turn(query, reply)

    # 5) 生成音频：失败只记日志，绝不影响这次响应
    audio_url = make_tts(reply)

    return {
        "voice_text": reply,
        "device_action": action,
        "source": source,
        "audio_url": audio_url,
        "memory": len(_memory["facts"]),     # 长期记忆条数（给屏幕显示）
        "history": len(_history),            # 本轮带上的上下文轮数
    }


# ================= 路由 =================
@app.route("/v1/chat", methods=["POST"])
def chat():
    # silent + force：Content-Type 不规范也不会抛 415/400 HTML
    data = request.get_json(silent=True, force=True) or {}
    query = str(data.get("query", "") or "")

    return jsonify(handle_query(query)), 200


@app.route("/v1/asr", methods=["POST"])
def asr_only():
    """只做识别：把一段 wav 直接 POST 上来，返回识别文本（用来单独验证麦克风）。"""
    wav = request.get_data()
    try:
        text = baidu_asr(wav)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e), "bytes": len(wav)}), 200
    return jsonify({"ok": True, "text": text, "bytes": len(wav)}), 200


@app.route("/v1/voice", methods=["POST"])
def voice():
    """ESP32 上传一段录音（16k/单声道/16位 WAV）→ 识别 → 意图 → 合成，一次往返搞定。"""
    wav = request.get_data()
    # 留一份原始录音：麦克风接线不对时，可以在电脑上直接播它，听板子到底录到了什么
    try:
        with open(MIC_LAST_WAV, "wb") as f:
            f.write(wav)
    except Exception:
        pass
    print(f"[ASR] 收到 {len(wav)} 字节录音，开始识别…", flush=True)

    if len(wav) < 2000:
        return jsonify({"asr_text": "", "voice_text": "没收到声音，检查一下麦克风吧。",
                        "device_action": "NONE",
                        "error": "upload too small"}), 200

    try:
        text = baidu_asr(wav)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"asr_text": "", "voice_text": "识别失败了，你再说一遍好吗？",
                        "device_action": "NONE", "error": str(e)}), 200

    print(f"[ASR] 识别结果: {text!r}", flush=True)
    result = handle_query(text)
    result["asr_text"] = text
    return jsonify(result), 200


@app.route("/v1/reset", methods=["POST"])
def reset_conversation():
    """开一段新会话：清掉 conversation_id 和最近对话（长期记忆保留）。
    同时解除"平台故障"状态，让下一轮重新去试平台 —— 改完平台提示词后调它一次即可。"""
    _cloud_conv["id"] = ""
    _cloud_conv["multiturn_ok"] = True
    _history.clear()
    _cloud_state["degraded"] = False
    _cloud_state["checked_at"] = 0.0
    return jsonify({"ok": True, "msg": "已开启新会话（长期记忆保留，平台状态已重置）"}), 200


@app.route("/v1/memory", methods=["GET", "POST"])
def memory_api():
    """GET 查看记忆；POST {"text": "..."} 手动加一条已知信息。"""
    if request.method == "POST":
        data = request.get_json(silent=True, force=True) or {}
        added = add_fact(str(data.get("text", "") or ""))
        return jsonify({"ok": True, "added": added,
                        "facts": _memory["facts"]}), 200
    return jsonify({
        "file": MEMORY_FILE,
        "facts": _memory["facts"],
        "fact_count": len(_memory["facts"]),
        "events": _memory["events"][-10:],
        "event_count": len(_memory["events"]),
        "history_turns": len(_history),
    }), 200


@app.route("/v1/memory/clear", methods=["POST"])
def memory_clear():
    """清空长期记忆里的已知信息（历史事件保留）。"""
    _memory["facts"] = []
    save_memory()
    return jsonify({"ok": True, "msg": "已知信息已清空（历史事件保留）"}), 200


@app.route("/v1/music", methods=["POST"])
def music_api():
    """单独测放歌：{"query": "周杰伦"} 或 {"query": "放七里香"} 或 {"query": "放一首七里香"}。"""
    data = request.get_json(silent=True, force=True) or {}
    term = str(data.get("query", "") or "").strip()
    song, strict = music_request(term)
    if song is None:
        song, strict = term, True       # 直接给歌名/歌手也当搜索词
    result = handle_music(song, term, strict)
    if result is None:
        result = {"voice_text": f"“{term}”没搜到歌曲",
                  "device_action": "NONE", "source": "音乐"}
    return jsonify(result), 200


@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)


@app.errorhandler(Exception)
def _any_error(e):
    """兜底：任何未捕获异常也返回 JSON，避免 ESP32 收到 HTML 而报 InvalidInput。"""
    traceback.print_exc()
    return jsonify({"voice_text": "", "device_action": "NONE",
                    "error": f"{type(e).__name__}: {e}"}), 200


@app.after_request
def _access_log(response):
    """每个请求都往 access.log 追一行：谁能看到 ESP32 到底取没取音频、服务端回了多少字节。"""
    try:
        _fmt = ("%s  %-4s %-24s -> %s  %s字节  "
                "ua=%-28s  range=%-14s  from=%s\n")
        line = _fmt % (
            time.strftime("%H:%M:%S"),
            request.method,
            request.full_path.rstrip("?"),
            response.status_code,
            response.headers.get("Content-Length", "-"),
            (request.headers.get("User-Agent") or "-")[:28],
            (request.headers.get("Range") or "-")[:14],
            request.remote_addr,
        )
        with open(ACCESS_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    return response


if __name__ == "__main__":
    print(f"音频目录: {AUDIO_DIR}")
    print(f"TTS 后端: {TTS_ENGINE}  格式: {TTS_FORMAT}")
    _brain = "智小理平台 " + CLOUD_URL if BRAIN != "local" else "仅本地规则"
    print(f"大脑: {BRAIN}  ({_brain})")
    print(f"记忆: {MEMORY_FILE}")
    print(f"      已知信息 {len(_memory['facts'])} 条，短期记忆保留最近 {HISTORY_TURNS} 轮")
    print(f"找资源: 音乐 {'开' if MUSIC_ENABLE else '关'}"
          f"  (iTunes 试听 30 秒, 地区 {MUSIC_COUNTRY})")
    print(f"        下载到 {MUSIC_DIR}")
    print(f"监听: 0.0.0.0:{PORT}  （ESP32 下载地址 "
          f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/...）")
    print("语音入口: POST /v1/voice (ESP32 上传录音)")
    print("          POST /v1/chat (文字)   POST /v1/reset (新会话)")
    print("记忆接口: GET/POST /v1/memory (查看/添加)   POST /v1/memory/clear (清空)")
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False)

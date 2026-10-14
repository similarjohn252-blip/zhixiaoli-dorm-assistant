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
import os
import asyncio
import hashlib
import json
import re
import time
import traceback

import requests
from flask import Flask, jsonify, request, send_from_directory

from server.config import (
    ACCESS_LOG, AUDIO_DIR, AUDIO_FILENAME, BAIDU_API_KEY, BAIDU_AUE,
    BAIDU_CHUNK, BAIDU_SECRET_KEY, BRAIN, CLOUD_INPUT_MAX, CLOUD_KEY,
    CLOUD_MULTITURN, CLOUD_TIMEOUT, CLOUD_URL, CLOUD_USER, CLOUD_VERIFY,
    HISTORY_TURNS, MEMORY_FILE, MEMORY_MAX_FACTS,
    MIC_LAST_WAV, PORT, PUBLIC_HOST, PUBLIC_PORT, TTS_ENGINE, TTS_FORMAT,
)

from server.music import (
    MUSIC_COUNTRY, MUSIC_DIR, MUSIC_ENABLE,
    handle_music, make_tts, music_request,
)

from server import memory
from server.speech import baidu_asr, synthesize

app = Flask(__name__)

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
            and memory._memory["facts"]):
        return "你跟我说过：" + "；".join(memory._memory["facts"][-3:]), "NONE"
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
    for fact in memory.extract_facts(query):
        memory.add_fact(fact)

    # 2) 放歌类请求单独走音乐分支：云端给不了音频
    if MUSIC_ENABLE:
        song, strict = music_request(query)
        if song is not None:
            result = handle_music(song, query, strict)
            if result is not None:
                return result            # None = 宽松匹配又搜不到，下面交给云端

    # 3) 带着【已知信息 + 最近对话】去问大脑
    enriched = memory.build_query(query)
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
    memory.remember_turn(query, reply)

    # 5) 生成音频：失败只记日志，绝不影响这次响应
    audio_url = make_tts(reply)

    return {
        "voice_text": reply,
        "device_action": action,
        "source": source,
        "audio_url": audio_url,
        "memory": len(memory._memory["facts"]),     # 长期记忆条数（给屏幕显示）
        "history": len(memory._history),            # 本轮带上的上下文轮数
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
    memory._history.clear()
    _cloud_state["degraded"] = False
    _cloud_state["checked_at"] = 0.0
    return jsonify({"ok": True, "msg": "已开启新会话（长期记忆保留，平台状态已重置）"}), 200


@app.route("/v1/memory", methods=["GET", "POST"])
def memory_api():
    """GET 查看记忆；POST {"text": "..."} 手动加一条已知信息。"""
    if request.method == "POST":
        data = request.get_json(silent=True, force=True) or {}
        added = memory.add_fact(str(data.get("text", "") or ""))
        return jsonify({"ok": True, "added": added,
                        "facts": memory._memory["facts"]}), 200
    return jsonify({
        "file": MEMORY_FILE,
        "facts": memory._memory["facts"],
        "fact_count": len(memory._memory["facts"]),
        "events": memory._memory["events"][-10:],
        "event_count": len(memory._memory["events"]),
        "history_turns": len(memory._history),
    }), 200


@app.route("/v1/memory/clear", methods=["POST"])
def memory_clear():
    """清空长期记忆里的已知信息（历史事件保留）。"""
    memory._memory["facts"] = []
    memory.save_memory()
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
    print(f"      已知信息 {len(memory._memory['facts'])} 条，短期记忆保留最近 {HISTORY_TURNS} 轮")
    print(f"找资源: 音乐 {'开' if MUSIC_ENABLE else '关'}"
          f"  (iTunes 试听 30 秒, 地区 {MUSIC_COUNTRY})")
    print(f"        下载到 {MUSIC_DIR}")
    print(f"监听: 0.0.0.0:{PORT}  （ESP32 下载地址 "
          f"http://{PUBLIC_HOST}:{PUBLIC_PORT}/audio/...）")
    print("语音入口: POST /v1/voice (ESP32 上传录音)")
    print("          POST /v1/chat (文字)   POST /v1/reset (新会话)")
    print("记忆接口: GET/POST /v1/memory (查看/添加)   POST /v1/memory/clear (清空)")
    app.run(host="0.0.0.0", port=PORT, threaded=True, debug=False)

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
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

from server import memory
from server.config import (
    ACCESS_LOG, AUDIO_DIR, BRAIN, CLOUD_URL, HISTORY_TURNS, MEMORY_FILE, MIC_LAST_WAV, PORT,
    PUBLIC_HOST, PUBLIC_PORT, TTS_ENGINE, TTS_FORMAT,
)
from server.music import (
    MUSIC_COUNTRY, MUSIC_DIR, MUSIC_ENABLE,
    handle_music, music_request,
)
from server.speech import baidu_asr

from server import brain
from server.brain import handle_query

app = Flask(__name__)


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
    brain._cloud_conv["id"] = ""
    brain._cloud_conv["multiturn_ok"] = True
    memory._history.clear()
    brain._cloud_state["degraded"] = False
    brain._cloud_state["checked_at"] = 0.0
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

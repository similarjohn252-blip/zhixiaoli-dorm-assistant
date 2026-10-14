"""Flask 应用与路由：只做"接线"，业务逻辑都在 brain / music / memory / speech 里。

接口：
  POST /v1/chat       收 query（文字），返回 voice_text / device_action / audio_url
  POST /v1/voice      收 ESP32 上传的 WAV，一次完成 识别→意图→合成
  POST /v1/asr        只做语音识别，方便在电脑上单独验证一段音频
  GET  /audio/<file>  给 ESP32 下载音频
  ---- 给网页用的（见 网页前端_对接文档.md 第 11 节）----
  GET      /v1/device/status    板子在线吗（服务端代理，网页不直连板子的私有地址）
  GET/POST /v1/device/volume    读 / 写板子音量
  POST     /v1/device/profile   把网页填的个人信息转给板子
  GET      /v1/health           健康自检 + 版本号（演示前一条命令看能不能上台）
"""
import os
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory

from . import brain, device, memory, tickets

from .brain import handle_query

from .config import (
    ACCESS_LOG, API_TOKEN, AUDIO_DIR, BOARD_HOST, CODE_VERSION, MEMORY_FILE,
    MIC_LAST_WAV, STARTED_AT,
)
from .music import (
    handle_music, music_request,
)
from .speech import baidu_asr

app = Flask(__name__)

_BOOT_TS = time.time()          # 给 /v1/health 算运行时长

# ================= 可选鉴权（部署到公网前必须开）=================
# 只保护"会动硬件 / 涉及隐私"的接口；/v1/chat、/v1/voice、/audio **保持开放**：
# 板子固件里没有 token，评委的网页也要用它们 —— 拦了就演示不了 ✗
_PROTECTED_PREFIXES = ("/v1/device/", "/v1/memory")


@app.before_request
def _check_token():
    if not API_TOKEN:
        return None                 # 未配置 = 不启用（局域网演示的默认状态）
    path = request.path or ""
    if not any(path.startswith(p) for p in _PROTECTED_PREFIXES):
        return None
    got = (request.headers.get("Authorization") or "").replace("Bearer", "").strip()
    if got != API_TOKEN:
        return jsonify({"ok": False, "error": "未授权：token 缺失或不正确"}), 401
    return None

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


# ================= 设备代理（网页不直连板子的私有地址）=================
# 为什么需要：网页部署到"评委能访问的地方"后，和板子就不在同一网络了 ✗
# 私有地址 192.168.137.x 跨网不可达 → 统一由服务端转发（详见 device.py）
# 子路径与板子**完全一致**，所以前端只要把 base 换成 <服务端>/v1/device 即可 ✓
@app.route("/v1/device/status", methods=["GET"])
def device_status():
    return jsonify(device.board_status()), 200


@app.route("/v1/device/volume", methods=["GET", "POST"])
def device_volume():
    if request.method == "GET":
        return jsonify(device.board_volume_get()), 200
    data = request.get_json(silent=True, force=True) or {}
    return jsonify(device.board_volume_set(data.get("volume"))), 200


@app.route("/v1/device/profile", methods=["POST"])
def device_profile():
    data = request.get_json(silent=True, force=True) or {}
    return jsonify(device.board_profile(str(data.get("name", "") or ""),
                                        str(data.get("room", "") or ""))), 200


# ================= 健康自检（演示前一条命令看能不能上台）=================
@app.route("/v1/health", methods=["GET"])
def health():
    checks = {}

    b = device.board_status()
    checks["board"] = {"ok": bool(b.get("online")),
                       "detail": b.get("error") or "在线"}

    # 云端：只报告服务端记录的降级状态，**不主动发请求**（避免消耗额度）
    degraded = bool(brain._cloud_state.get("degraded"))
    checked = brain._cloud_state.get("checked_at") or 0
    checks["cloud"] = {
        "ok": not degraded, "degraded": degraded,
        "last_check_s": round(time.time() - checked, 1) if checked else None,
        "note": "报告服务端记录的平台状态，不主动请求（省额度）"}

    try:
        probe = os.path.join(AUDIO_DIR, ".health_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        checks["disk"] = {"ok": True, "dir": AUDIO_DIR}
    except Exception as e:
        checks["disk"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    checks["memory"] = {"ok": True, "file": MEMORY_FILE,
                        "exists": os.path.exists(MEMORY_FILE),
                        "facts": len(memory._memory.get("facts", []))}

    critical = [k for k in ("disk", "memory", "cloud") if not checks[k]["ok"]]
    return jsonify({
        "ok": not critical,
        "failing": critical,
        "version": CODE_VERSION,
        "started_at": STARTED_AT,
        "uptime_s": round(time.time() - _BOOT_TS, 1),
        "board_host": BOARD_HOST,
        "auth": "on" if API_TOKEN else "off",
        "checks": checks,
    }), 200


# ================= 报修工单台账（工程债 C1）=================
# 说"我们宿舍的灯坏了" → /v1/chat 会**真的建一张工单**，并把工单号写进回答 ✓
# 这几个接口是给"网页 / 答辩时当场看台账"用的 —— 用来证明"这是真数据，不是话术" ✓
@app.route("/v1/tickets", methods=["GET", "POST"])
def tickets_api():
    if request.method == "POST":
        d = request.get_json(silent=True, force=True) or {}
        t = tickets.create(room=str(d.get("room", "") or ""),
                           category=str(d.get("category", "") or ""),
                           note=str(d.get("note", "") or ""))
        return jsonify(t), 200
    return jsonify({"tickets": tickets.latest(20),
                    "stats": tickets.stats(),
                    "status_text": tickets.STATUS_TEXT}), 200


@app.route("/v1/tickets/<tid>", methods=["GET", "POST"])
def ticket_one(tid):
    if request.method == "POST":
        d = request.get_json(silent=True, force=True) or {}
        ok = tickets.set_status(tid, str(d.get("status", "") or ""))
        return jsonify({"ok": ok, "ticket": tickets.get(tid),
                        "valid_status": list(tickets.VALID_STATUS)}), 200
    t = tickets.get(tid)
    return jsonify(t or {"ok": False, "error": "没有这张工单"}), 200


@app.route("/v1/tickets/clear", methods=["POST"])
def tickets_clear():
    """一键清空工单台账（隐私承诺 C4：能当场演示"说清就清"）。"""
    return jsonify({"ok": True, "cleared": tickets.clear_all()}), 200


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

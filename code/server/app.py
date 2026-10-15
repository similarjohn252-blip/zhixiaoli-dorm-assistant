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

from . import brain, device, memory, tickets, trace

from .brain import handle_query

from .config import (
    ACCESS_LOG, API_TOKEN, AUDIO_DIR, BOARD_HOST, CODE_VERSION, MEMORY_DB,
    MEMORY_FILE,
    MIC_LAST_WAV, STARTED_AT,
)
from .music import (
    handle_music, music_request,
)
from .speech import baidu_asr

app = Flask(__name__)

# JSON 里的中文**不要**转成 \u62a5\u544a… —— 实测：手机打开 /v1/health 满屏转义码，
# 解析虽然不受影响，但评委/队友直接开 URL 看时可读性很差 ✗（两种写法都给上，兼容新旧 Flask）
try:
    app.json.ensure_ascii = False        # Flask ≥ 2.3
except Exception:
    pass
app.config["JSON_AS_ASCII"] = False      # 旧写法（新版本会忽略，无害 ✓）


# ================= 链路追踪（工程债 B4）=================
# 每次请求一个短 id（也接受调用方用 X-Request-Id 指定 ✓ —— 板子后续可以自己传），
# 并把 id **回写到响应头**：客户端拿着它就能一次串出服务端全链路 ✓
# （出错时也回写 ✓ —— 异常响应最需要这个 id）
@app.before_request
def _trace_start():
    trace.start(request.headers.get("X-Request-Id") or None)
    # 记下本次请求自己的 Host → 音频地址跟着请求走，IP 再变也不会把死地址发给板子 ✓
    trace.set_base(request.host_url)


@app.after_request
def _trace_header(resp):
    resp.headers["X-Request-Id"] = trace.rid()
    return resp


# ================= 跨源 CORS（队友的网页在别的源上 ✓）=================
# 实测：Flask 侧原来**完全没有** CORS 响应头 ✗ →
#   队友的网页（例如 http://localhost:8000 或另一台机器）调 /v1/chat 会被浏览器**直接拦掉** ✗
#   （板子的 WebServer 早就开好了 ✓ —— 这里是服务端那一半，之前漏了 ✓）
# 用 `*` 而不是回显 Origin：我们不依赖 Cookie 凭据 ✓，`*` 最简单也够用 ✓
# ⚠️ 代价：任何网站都能调这些接口（/v1/chat 会花平台与百度的额度）
#    → 对外部署时用 token 保护敏感接口（/v1/device/*、/v1/memory* 已保护 ✓），
#      并注意 D4 那条"部署公网前必须加鉴权"的硬约束 ✓
@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Request-Id")
    resp.headers["Access-Control-Max-Age"] = "600"
    return resp

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

    # 板子：用**短超时**（1.2 秒）—— 健康检查必须快 ✗
    # （默认 4 秒超时会让健康检查卡 4~5 秒，前端轮询会误判"服务不可用" ✓）
    b = device.board_status(timeout=1.2)
    checks["board"] = {"ok": bool(b.get("online")),
                       "probe_timeout_s": 1.2,
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

    # ⚠️ 2026-10-15 修正 ✗：记忆已迁到 SQLite（工程债 B6），
    #    这里原来报的是 MEMORY_FILE（记忆.json）—— 典型的"代码写 A、自检报 B" ✓
    #    旧文件保留作迁移来源/备份 ✓，所以两个都报出来 ✓
    checks["memory"] = {"ok": True,
                        "file": MEMORY_DB,
                        "exists": os.path.exists(MEMORY_DB),
                        "legacy_json": MEMORY_FILE,
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
    # 异常响应最需要链路 id（最难查的那一次，反而是唯一没有线索的 ✗）
    trace.log("error", error=f"{type(e).__name__}: {e}", path=request.path)
    print(f"[链路 {trace.rid()}] ✗ {type(e).__name__}: {e}", flush=True)
    return jsonify({"voice_text": "", "device_action": "NONE",
                    "request_id": trace.rid(),
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

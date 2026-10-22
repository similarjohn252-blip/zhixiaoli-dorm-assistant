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
import tempfile
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory, Response

from . import brain, device, memory, reminders, tickets, timetable, trace

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
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024   # 课表上传 5MB 上限 ✓


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

# ================= 把网页本身送出去（2026-10-18 新增 ✓）=================
# 为什么加 ✗：原来要另开 `python -m http.server 8000` 送网页 ✓ →
#   ① 手机要访问【两个端口】(8000 拿页面 + 5000 调接口) ✗ 防火墙要开两条 ✗
#   ② 不同源 → 又多一层 CORS 依赖 ✗
#   → 现在由服务端在【5000 一个端口】上把网页送出去 ✓✓
#   页面里的 CONFIG 用 `location.origin` ✓ → 服务端地址变了它自动跟着变 ✓✓
# 安全性 ✓：这个页面本来就是【对外公开】的 ✓ 且不含任何密钥 ✓（已用提交护栏验证过 ✓）
_WEB_PAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "给队友", "给队友的网页修复版_index.html")


@app.route("/", methods=["GET"])
def web_index():
    """送网页首页 ✓ —— 手机/评委打开 http://<服务端地址>:5000/ 就能直接用 ✓"""
    if not os.path.exists(_WEB_PAGE):
        return jsonify({
            "ok": False,
            "error": "网页文件不在（给队友/给队友的网页修复版_index.html）",
            "hint": "API 照常可用：/v1/health  /v1/chat  /v1/board",
        }), 404
    from flask import send_file
    return send_file(_WEB_PAGE)


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
        # ⚠️ 与 /v1/health 一样曾报旧文件名 ✗ —— 统一报 SQLite 那个 ✓
        "file": MEMORY_DB,
        "legacy_json": MEMORY_FILE,
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
@app.route("/v1/board/volume", methods=["POST"])
def board_volume_public():
    """网页用的【免 token】音量写接口（2026-10-15 应网页前端诉求新增 ✓）

    ⭐ 为什么敢免 token：网页同学的论证成立 ✓ —— 音量确实不像
       「记忆 / 个人信息」那样敏感 ✓（它不泄露任何内容 ✓ 只影响响度 ✓）

    ⚠️ 但**必须加边界** ✗，因为这是个【公开的写接口】✓：
       任何能打开这个页面的人都能调它 ✓ —— 最坏情况不是隐私泄露 ✗
       而是【演示现场把板子弄哑】✗✗
       （固件实测音量曲线：vol=5 → -52dB 几乎无声 ✗ ｜ vol=15 → -13dB ✓ ｜ vol=21 → 0dB ✓）
       → 所以**只允许 10~21** ✓：最坏只是"变小声"✓ 不会哑 ✗✓（10 ≈ -33dB，仍听得清 ✓）
       真要更小或静音 → 用**带 token** 的 `/v1/device/volume` ✓（那是管理接口 ✓）

    与 `/v1/board` 风格一致 ✓：失败也返回 200 + `ok:false` ✓ 网页不用 try/catch ✓
    """
    data = request.get_json(silent=True, force=True) or {}
    try:
        want = int(data.get("volume"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "volume": None, "steps": 21,
                        "error": "volume 必须是 10~21 的整数"}), 200

    v = max(10, min(21, want))
    r = device.board_volume_set(v)
    out = {"ok": bool(r.get("ok")), "volume": v, "steps": 21}
    if r.get("ok"):
        got = device.board_volume_get()          # 读回真实值 ✓（别只信自己设的 ✓）
        if got.get("ok"):
            out["volume"] = got.get("volume")
            out["steps"] = got.get("steps", 21)
    else:
        out["error"] = r.get("error", "板子没响应")
    if v != want:
        out["note"] = f"已夹到安全区间 10~21（你给的是 {want}）"     # 网页可原样显示 ✓
    # 每次改音量都留痕 ✓（现场出问题时能回看是谁在什么时候调的 ✓）
    trace.log("board_volume", want=want, set=v, ok=out["ok"])
    return jsonify(out), 200


@app.route("/v1/board", methods=["GET"])
def board_public():
    """给网页用的【只读 · 免 token · 已脱敏】的板子状态 ✓

    ⚠️ 2026-10-15 新增的原因 ✗：
      《对接说明》原来教网页同学把 token 填进前端 JS ✗ ——
      但**浏览器里存不住秘密** ✓（F12 一看就有 ✓）→ 照做等于把 token 公开 ✗
      而网页真正想显示的只是"板子在线吗 / 音量多少" ✓ → 那就给一个白名单接口 ✓
      · **免 token** ✓（纯只读 ✓）
      · **只回白名单字段** ✓：绝不回板子 IP ✗、绝不回 profile（姓名/房号）✗、绝不回原始异常 ✗
    ⚠️ 2026-10-15 同日更新 ✗：**改音量**后来也开了免 token 的 `POST /v1/board/volume` ✓
      （网页同学的诉求成立 ✓ 音量确实不敏感 ✓）—— 但**限制在 10~21** ✗，免得现场被调哑 ✓
      **写 profile、读写记忆仍然只在带 token 的接口里** ✓
    """
    st = device.board_status(timeout=1.5)
    out = {"online": bool(st.get("online"))}
    if out["online"]:
        v = device.board_volume_get()
        if v.get("ok"):
            out["volume"] = v.get("volume")
            out["steps"] = v.get("steps")
    else:
        out["error"] = "板子离线"
    return jsonify(out), 200


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


@app.route("/v1/tickets/<tid>/receipt.txt")
def ticket_receipt(tid):
    """下载一张工单的文本回执（工程债 C1 的最后一块 ✓）。

    ⚠️ 故意【免 token】✓ —— 和 /v1/tickets 保持一致 ✓
       网页只要 <a href="…/receipt.txt" download> 就能下 ✓ 不用 JS ✗
    ⚠️ 必须带 filename ✗ 否则浏览器会在页面里显示而不是下载 ✓
    """
    t = tickets.get(tid)
    if not t:
        return Response("没有这张工单：" + tid + "\n",
                        mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": "inline"}), 404
    _st = tickets.status_text(t.get("status") or "")
    lines = [
        "智小理 · 宿舍事务助手 · 报修回执",
        "=" * 40,
        "",
        "工单号：" + (t.get("id") or ""),
        "房　间：" + (t.get("room") or "（未填）"),
        "类　别：" + (t.get("category") or "（未分类）"),
        "状　态：" + _st,
        "受理时间：" + (t.get("ts") or ""),
        "最后更新：" + (t.get("updated") or ""),
        "",
        "事　由：" + (t.get("note") or "（无）"),
        "",
        "=" * 40,
        "本回执由「智小理 · 宿舍事务助手」生成",
        "生成时间：" + time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "提示：想查进度，再问一句「我的报修怎么样了」就行。",
        "",
    ]
    body = "\ufeff" + "\r\n".join(lines)      # ⭐ BOM + CRLF：Windows 记事本友好 ✓
    return Response(body, mimetype="text/plain; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="receipt_' + tid + '.txt"',
        "Cache-Control": "no-store",
    })


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


# ================= 课表与提醒（2026-10-22 新增）=================
# 设计见 code\课表与提醒_设计.md；接口结构与「给队友\网页要做的_课表与提醒.md」一致 ✓
# 这五个接口免 token —— 跟 /v1/board/volume 一个道理：网页要用、内容不敏感 ✓
# （真正敏感的是读写记忆、写 profile，那些仍然只在带 token 的接口里 ✗）

@app.route("/v1/schedule/upload", methods=["POST"])
def schedule_upload():
    """上传课表 PDF，解析后**不保存**，只把结果给网页看一眼。

    ⚠️ 为什么不保存：解析只有九成准（合并单元格会让列错位），
       必须让用户在网页上校对一遍再存 ✓
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "没收到文件"}), 400
    name = (f.filename or "").lower()
    if not name.endswith(".pdf"):
        return jsonify({"ok": False, "error": "只支持 PDF，图片和 Excel 暂不支持"}), 400

    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        f.save(tmp.name)
        tmp.close()
        items, warnings = timetable.parse_pdf(tmp.name)
    except Exception as e:
        trace.log("schedule_upload_error", error=f"{type(e).__name__}: {e}")
        return jsonify({"ok": False, "error": f"解析失败：{type(e).__name__}"}), 400
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    out_items = []
    for i, it in enumerate(items, 1):
        row = {"id": f"t{i}"}
        row.update(it)
        out_items.append(row)

    trace.log("schedule_upload", count=len(out_items), warnings=len(warnings))
    return jsonify({"ok": True, "count": len(out_items),
                    "items": out_items, "warnings": warnings}), 200


@app.route("/v1/schedule/save", methods=["POST"])
def schedule_save():
    """保存校对后的清单 + 学期参数（第 1 周日期 / 提前量 / 作息表）。"""
    data = request.get_json(silent=True, force=True) or {}
    st = reminders.set_settings(
        week1_monday=data.get("week1_monday"),
        early_minutes=data.get("early_minutes"),
        periods=data.get("periods"),
    )
    saved = reminders.save_items(data.get("items") or [])
    trace.log("schedule_save", saved=saved, week1=st["week1_monday"].isoformat(),
              early=st["early_minutes"])
    return jsonify({"ok": True, "saved": saved,
                    "week1_monday": st["week1_monday"].isoformat(),
                    "early_minutes": st["early_minutes"]}), 200


@app.route("/v1/schedule", methods=["GET"])
def schedule_get():
    """读当前课表 + 学期参数。没有课表时 items 是空数组，不报错 ✓"""
    st = reminders.get_settings()
    items = []
    for i, it in enumerate(reminders.load_items(), 1):
        row = {"id": f"t{i}"}
        row.update(it)
        items.append(row)
    return jsonify({"ok": True, "count": len(items), "items": items,
                    "week1_monday": st["week1_monday"].isoformat(),
                    "early_minutes": st["early_minutes"],
                    "periods": st["periods"]}), 200


@app.route("/v1/reminders", methods=["GET"])
def reminders_get():
    """未来 N 小时的提醒。板子每 3 秒来一次 ✓

    每条都带 in_seconds（还有多少秒）—— 板子不需要真实时间，
    拿这个换成本地 millis 截止点就行 ✓
    """
    try:
        hours = int(request.args.get("hours", 48))
    except (ValueError, TypeError):
        hours = 48
    return jsonify(reminders.upcoming(hours)), 200


@app.route("/v1/alarm", methods=["POST"])
def alarm_add():
    """直接加一个闹钟（一次性）。

    body: {"at": "2026-10-22 08:00", "say": "该吃药了"}
    语音说"十秒后提醒我喝水"的解析在 brain 里做，走这条路存进来 ✓
    """
    data = request.get_json(silent=True, force=True) or {}
    at = data.get("at") or data.get("when")
    say = str(data.get("say") or data.get("what") or "时间到了")
    nid = reminders.add_alarm(at, say)
    if nid is None:
        return jsonify({"ok": False, "error": "时间格式不对，要「YYYY-MM-DD HH:MM」"}), 400
    trace.log("alarm_add", id=nid, at=str(at), say=say[:20])
    return jsonify({"ok": True, "id": nid, "say": say}), 200

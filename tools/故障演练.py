"""故障演练（工程债 C5）：真的把每种故障注入一次，看降级行为是否符合预期。

《指南》对 C5 的验收标准是："**每种故障都有可预期的降级表现 + 日志说明原因**" ✓
所以这里不"设想"行为，而是**真的制造故障**再记录真实表现 ✓

为什么用子进程跑每个场景：`config.py` 在 **import 时**读环境变量，
一个 Python 进程只能有一种配置 → 每个场景必须独立进程 ✓（本脚本自己 re-exec 自己）

用法：
    E:\\Python\\python.exe tools\\故障演练.py            # 跑全部
    E:\\Python\\python.exe tools\\故障演练.py --case 2   # 只跑第 2 个（子进程内部用）
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PY = sys.executable
CODE = r"E:\天翼\code"
TMP = os.path.join(tempfile.gettempdir(), "故障演练")

CASES = {
    1: "平台地址不可达（云端连不上）",
    2: "平台退化（模型收到 null，只会打招呼）",
    3: "音频目录非法（配置写错）",
    4: "记忆库损坏（垃圾 记忆.db）",
    5: "板子掉线（服务端代理的失败分支）",
    6: "百度语音鉴权失败（Key 错/额度耗尽）",
}


# ==================== 子进程里执行的场景 ====================
def run_case(n: int):
    os.makedirs(TMP, exist_ok=True)
    fake = None

    if n == 1:
        os.environ["ZHIXIAOLI_CLOUD_URL"] = "http://127.0.0.1:9/v1/chat-messages"  # 死端口
        os.environ["ZHIXIAOLI_CLOUD_TIMEOUT"] = "3"
    elif n == 2:
        fake = start_fake_platform()
        os.environ["ZHIXIAOLI_CLOUD_URL"] = f"http://127.0.0.1:{fake.server_port}/v1/chat-messages"
    elif n == 3:
        blocker = os.path.join(TMP, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("我是文件，不是目录")
        os.environ["ZHIXIAOLI_AUDIO_DIR"] = os.path.join(blocker, "sub")  # 文件下面建目录 → 必然失败
    elif n == 4:
        # ⚠️ 2026-10-15 改 ✗：以前造的是"坏 JSON" ✓ —— 但记忆迁 SQLite 后，
        #    JSON 只在**首次迁移**时被读 ✓，坏掉最多"跳过迁移"✓，不致命 ✗
        #    真正致命的是【坏 记忆.db】✓：load_memory() 在 import 期就会抛 ✗
        #    → 服务端根本起不来 ✓（这条演练就该测它 ✓）
        bad_db = os.path.join(TMP, "坏记忆.db")
        with open(bad_db, "wb") as f:
            # ⚠️ bytes 字面量只能是 ASCII ✗（写中文会 SyntaxError ✓ —— 刚踩过 ✓）
            f.write(b"SQLite format 3\x00" + b"\x00" * 200 + b"not a real db at all")
        os.environ["ZHIXIAOLI_MEMORY_DB"] = bad_db
    elif n == 5:
        os.environ["ZHIXIAOLI_BOARD_HOST"] = "127.0.0.1"
        os.environ["ZHIXIAOLI_BOARD_TIMEOUT"] = "2"
    elif n == 6:
        # 坏 Key：必须在 import server 之前设好（config 在 import 时读它 ✓）
        os.environ["BAIDU_API_KEY"] = "bad-key-for-drill"
        os.environ["BAIDU_SECRET_KEY"] = "bad-secret-for-drill"

    os.environ["ZHIXIAOLI_TICKETS_DB"] = os.path.join(TMP, f"演练{n}.db")
    if n != 3:
        os.environ.setdefault("ZHIXIAOLI_AUDIO_DIR", os.path.join(TMP, "audio"))
    os.environ.setdefault("ZHIXIAOLI_MEMORY_FILE", os.path.join(TMP, f"演练{n}_记忆.json"))

    try:
        _checks(n)
    except SystemExit as e:
        msg = str(e)
        # "配置错了就 fail-fast + 说清哪里错"也是**预期行为** ✓，不是失败 ✗
        if n == 3 and "配置错误" in msg and "音频目录" in msg:
            print("RESULT|✓ 配置错误被拦住并给出清楚提示：" + msg.replace("\n", " ")[:80])
        else:
            print(f"RESULT|✗ 进程直接退出（exit={e.code}）—— 说明这种故障**没被兜住**")
    except Exception as e:
        print(f"RESULT|✗ 抛出异常 {type(e).__name__}: {str(e)[:90]} —— **没被兜住**")
    finally:
        if fake:
            fake.shutdown()


def _checks(n: int):
    sys.path.insert(0, CODE)

    if n == 1:                      # 平台不可达 → 应回退本地规则，不崩
        from server import brain
        r = brain.handle_query("我们宿舍的灯坏了，要报修")
        # 来源可能带后缀（如"本地回退·建单"）→ 取前缀比 ✓
        base_src = (r["source"] or "").split("·")[0]
        # ⚠️ 2026-10-18 补：服务端新增了「本地追问」✓（报修缺房间号时先问一句 ✓）
        #    那是【本地给出的、正确的人话】✓ 完全符合本演练的意图（平台挂了也不能崩 ✓）
        ok = base_src in ("云端平台", "本地回退", "本地规则", "本地台账", "本地追问") and bool(r["voice_text"])
        print(f"RESULT|{'✓' if ok else '✗'} 仍然给出了回答：source={r['source']} "
              f"动作={r['device_action']} 回复={r['voice_text'][:44]}")

    elif n == 2:                    # 平台退化 → 应被识别，且**本轮不能念出那句 null**
        from server import brain
        r1 = brain.handle_query("宿舍管理有什么要求")
        degraded = brain._cloud_state["degraded"]
        print(f"RESULT|{'✓' if degraded else '✗'} 识别出平台退化 degraded={degraded}")
        low = (r1["voice_text"] or "").lower()
        leaked = ("user input is" in low) or ("null" in low)
        print(f"RESULT|{'✓' if not leaked else '✗'} 本轮没有把模型的英文原话念出来"
              f"（回复={r1['voice_text'][:44]}）")
        # 用真实的 /v1/reset 路由验证"能恢复"（不再发新请求，免得又被判退化 ✓）
        from server import app as A
        A.app.test_client().post("/v1/reset")
        ok3 = brain._cloud_state["degraded"] is False
        print(f"RESULT|{'✓' if ok3 else '✗'} POST /v1/reset 后 "
              f"degraded={brain._cloud_state['degraded']}")

    elif n == 3:                    # 音频目录非法 → 期望"清楚地报错"，而不是神秘崩溃
        import importlib
        importlib.import_module("server.config")
        print("RESULT|⚠ 居然能启动（说明 AUDIO_DIR 没被真正校验）")

    elif n == 4:                    # 记忆文件损坏 → 应不崩、从零开始
        from server import memory
        facts = memory._memory.get("facts", [])
        print(f"RESULT|✓ 没崩，从零开始：已知信息 {len(facts)} 条，历史事件 "
              f"{len(memory._memory.get('events', []))} 条")

    elif n == 5:                    # 板子掉线 → 结构化错误，不抛异常
        from server import device
        s = device.board_status()
        v = device.board_volume_set(15)
        ok = ("online" in s and s["online"] is False) and ("ok" in v and v["ok"] is False)
        print(f"RESULT|{'✓' if ok else '✗'} 返回结构化失败：status={s.get('error','')[:40]} "
              f"| volume={v.get('error','')[:30]}")

    elif n == 6:                    # 百度挂了 → ASR 抛普通异常（路由会兜住）；TTS 失败不能影响回答
        from server import speech
        try:
            speech.baidu_asr(b"\x00" * 200)
            print("RESULT|✗ 拿坏 Key 居然识别成功了？")
        except Exception as e:
            print(f"RESULT|✓ ASR 抛的是普通异常（/v1/asr 路由会兜成 ok:false）："
                  f"{type(e).__name__}: {str(e)[:50]}")
        from server import brain
        r = brain.handle_query("你好呀")
        ok = bool(r.get("voice_text"))
        print(f"RESULT|{'✓' if ok else '✗'} TTS 失败**不影响回答**：source={r['source']} "
              f"audio_url={r.get('audio_url')!r} 回复={r['voice_text'][:34]}")


class _FakePlatform(BaseHTTPRequestHandler):
    """假平台：故意回一句带 'user input is' 的话 —— 服务端应据此判定"平台退化" ✓"""

    def do_POST(self):
        body = json.dumps(
            {"answer": 'The user input is "null" - essentially empty. 你好呀，有什么可以帮你的吗？'}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_fake_platform():
    srv = HTTPServer(("127.0.0.1", 0), _FakePlatform)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ==================== 主进程：逐个场景跑子进程 ====================
def main():
    if "--case" in sys.argv:
        run_case(int(sys.argv[sys.argv.index("--case") + 1]))
        return

    print("=" * 78)
    print("故障演练（每项都在独立子进程里真实注入）")
    print("=" * 78)
    passed = 0
    for n, title in CASES.items():
        print(f"\n【{n}】{title}")
        p = subprocess.run([PY, os.path.abspath(__file__), "--case", str(n)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        lines = [x for x in (p.stdout or "").splitlines() if x.startswith("RESULT|")]
        if lines:
            for x in lines:
                print("   " + x.split("|", 1)[1])
                if x.split("|", 1)[1].startswith("✓"):
                    passed += 1
        else:
            print(f"   ✗ 场景没跑起来（exit={p.returncode}）")
            for x in ((p.stderr or "").splitlines()[-4:]):
                print("      " + x[:110])
    print("\n" + "=" * 78)
    print(f"通过 {passed} 项检查")
    print("=" * 78)


main()

"""板子模拟器 —— 没有硬件时验证"到点真的会播"（2026-10-22）

板子固件将来做的事，这里用 Python 做一遍：
    每 3 秒问服务端"有什么要提醒的"
    看到"还有 N 秒"就记下截止点
    到点了就打印该播的话，并把音频拉下来（可选真放音）

为什么值得写：
    没有板子的时候，这是唯一能验证整条链路的办法。
    它能证明：课表解析对、闹钟存对、时间算对、到点真的触发、音频真的能生成。
    剩下的只有"把同样的逻辑搬进固件"。

用法：
    E:\\Python\\python.exe tools\\板子模拟器.py              # 一直跑
    E:\\Python\\python.exe tools\\板子模拟器.py --once       # 只问一次就退
    E:\\Python\\python.exe tools\\板子模拟器.py --play       # 到点真的放音
    E:\\Python\\python.exe tools\\板子模拟器.py --hours 2    # 只看未来 2 小时
"""
import argparse
import json
import os
import time
import urllib.request

BASE = os.environ.get("ZHIXIAOLI_BASE", "http://127.0.0.1:5000")


def fetch(path, timeout=6):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只问一次就退出")
    ap.add_argument("--play", action="store_true", help="到点真的放音")
    ap.add_argument("--hours", type=int, default=2)
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args()

    print("=" * 64)
    print("  板子模拟器 —— 问 " + BASE)
    print("=" * 64)

    try:
        h = fetch("/v1/health", timeout=5)
        print("  服务端版本 " + str(h.get("version")) + "  板子在线 " + str(h.get("checks", {})
              .get("board", {}).get("ok")))
    except Exception as e:
        print("  ✗ 服务端连不上：" + type(e).__name__)
        return 2

    fired = set()          # 已经播过的提醒 id
    timers = {}            # id → 截止的 monotonic 时刻

    while True:
        try:
            data = fetch("/v1/reminders?hours=" + str(args.hours))
        except Exception as e:
            print("  ⚠ 轮询失败：" + type(e).__name__)
            time.sleep(args.interval)
            continue

        items = data.get("items", [])
        now_mono = time.monotonic()

        # 第一次看到某条，就记下倒计时（模拟板子的"换成本地 millis"）
        for it in items:
            iid = it.get("id")
            if not iid or iid in fired:
                continue
            left = int(it.get("in_seconds", 0))
            if iid in timers and not timers[iid].get("url") and it.get("audio_url"):
                timers[iid]["url"] = it["audio_url"]   # 音频是后补的，补上
            if iid not in timers:
                # ⚠️ 把内容和音频一起记下来 ✗ 不能等触发时再去当前列表里找 ——
                # 服务端到点后会把这条清掉，那时就找不着了，只能播报"这条已经不在列表里"
                timers[iid] = {"due": now_mono + left, "say": it.get("say", ""),
                               "url": it.get("audio_url", "")}
                print(f"  ⏱ 记下提醒 [{iid}]  还有 {left} 秒  {it.get('say', '')[:44]}")

        # 到点的
        for iid, rec in list(timers.items()):
            if iid in fired:
                continue
            if now_mono >= rec["due"]:
                fired.add(iid)
                say = rec.get("say", "")
                url = rec.get("url", "")
                print("")
                print("  🔔 🔔 🔔  该播报了")
                print("      " + say)
                if url:
                    print("      音频 " + url)
                    if args.play:
                        play(url)
                else:
                    print("      （没有 audio_url，可能是还没到 10 分钟窗口）")
                print("")

        if args.once:
            print("")
            print("  —— 只问一次，结束 ——")
            if not items:
                print(f"  未来 {args.hours} 小时没有提醒。课表上传了吗？闹钟定了吗？")
                print("  快速造一个：POST /v1/alarm  {\"at\": \"几分钟后\", \"say\": \"试一下\"}")
            for it in items[:8]:
                print(f"    [{it['kind']}] {it['at']}  还有 {it['in_seconds']:>6} 秒  "
                      f"{it.get('say', '')[:40]}")
                if it.get("audio_url"):
                    print(f"         音频 {it['audio_url']}")
            return 0

        time.sleep(args.interval)


def play(url):
    """尽量放一下。放不出来不算错 —— 模拟器主要是验证逻辑，不是听声音。"""
    import tempfile
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = r.read()
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        f.write(data)
        f.close()
        # Windows 上直接叫系统播放器
        os.startfile(f.name)  # noqa: S606
        print("      已交给系统播放器（" + str(len(data)) + " 字节）")
    except Exception as e:
        print("      放音失败：" + type(e).__name__ + "（不影响逻辑验证）")


if __name__ == "__main__":
    import sys
    sys.exit(main())

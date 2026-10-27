"""填充语时序模拟器（2026-10-27）

板子不在身边时，唯一能验证「什么时候该说话」的办法：把这套门控逻辑在电脑上跑一遍，
输出时间线 —— 第几秒播了哪句、答案几秒到、中间最长静音多久、答案被拖后多少。

被模拟的板子逻辑（与固件要写的一致）：
  1. 松手 → 立刻播一条语气词，同时 POST /v1/voice?async=1 拿受理号
  2. 之后每 200ms 问一次 /v1/voice/result
  3. 上一句播完了、且 state=working → 播下一条填充语（最多 3 条）
  4. 三条轮完还没好 → 安静等
  5. 拿到 done → 播答案

⚠️ 一个关键规则（模拟第一版漏了，是跑出来才发现的）：
   **填充语一旦开始播就必须播完** ✗ 不能被打断。
   1.5 秒就出答案的那条路径里，若硬切填充语，用户听到的是"好的，嗯我查一…" —— 像卡带。
   所以答案要等当前这句播完（最多多等 1.3 秒），这个代价远小于被切断。

三种投放策略：
  burst  —— 上一句一完就接下一条（响应最快）
  spread —— 两条之间至少隔 2.5 秒（把三句摊开）
  final  —— 前两句 burst，第三句压到 6 秒之后（救长尾，不影响常见场景）

用法：
    E:\\Python\\python.exe tools\\填充语时序.py
    E:\\Python\\python.exe tools\\填充语时序.py --answer 9
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "code"))

from server import config as C  # noqa: E402

MANIFEST = os.path.join(C.AUDIO_DIR, "filler_manifest.json")
POLL_MS = 200          # 板子轮询间隔
FINAL_GATE = 6.0       # final 策略：第三句压到这个时刻之后


def load():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return ([x for x in data if x.get("kind") == "hi"],
            [x for x in data if x.get("kind") == "fill"])


def simulate(hi, fill, answer_at, strategy="burst", gap=2.5, seed=0):
    events = []
    t = 0.0
    end = 0.0
    if hi:
        h = hi[seed % len(hi)]
        events.append((t, h["duration"], h["text"], "语气词"))
        end = t + h["duration"]
        t = end

    used = 0
    while t < answer_at:
        idle = t >= end
        want = used < len(fill) and idle
        if want and strategy == "spread" and used > 0:
            want = (t - end) >= gap
        if want and strategy == "final" and used >= 2:
            want = t >= FINAL_GATE
        if want:
            f = fill[used]
            events.append((t, f["duration"], f["text"], "填充语"))
            end = t + f["duration"]
            used += 1
        t += POLL_MS / 1000.0

    play_at = max(answer_at, end)          # 答案等当前这句播完，不打断
    events.append((play_at, 0.0, "（答案开始播）", "答案"))

    silences, cursor = [], 0.0
    for start, dur, _x, _k in events:
        if start > cursor + 0.01:
            silences.append((cursor, start - cursor))
        cursor = max(cursor, start + dur)
    speak = sum(d for _s, d, _x, _k in events if _k in ("语气词", "填充语"))
    return events, {
        "speak": speak,
        "silences": silences,
        "longest": max([d for _s, d in silences], default=0.0),
        "fills_used": used,
        "delay": max(0.0, play_at - answer_at),
        "silent_ratio": (answer_at - speak) / answer_at if answer_at else 0.0,
    }


def show(events, st, answer_at):
    for start, dur, text, kind in events:
        if kind == "答案":
            print(f"       {start:5.2f}s  ▶ {text}")
        else:
            print(f"       {start:5.2f}s  ♪ {kind}「{text}」{dur:.2f}s")
    print(f"       说话 {st['speak']:.2f}s ｜ 最长静音 {st['longest']:.2f}s "
          f"｜ 答案被拖后 {st['delay']:.2f}s ｜ 用了 {st['fills_used']} 句")
    if st["silences"]:
        gaps = "、".join(f"{s:.1f}~{s + d:.1f}" for s, d in st["silences"] if d > 0.2)
        print(f"       静音段：{gaps or '无'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=2.5)
    ap.add_argument("--answer", type=float, default=None)
    args = ap.parse_args()

    got = load()
    if not got:
        print("  ✗ 还没有填充语音频，先跑一次 /v1/fillers 或 filler.ensure()")
        return 1
    hi, fill = got
    print(f"  语气词 {len(hi)} 条：" + "、".join(f"{x['text']}({x['duration']}s)" for x in hi))
    print(f"  填充语 {len(fill)} 条：" + "、".join(f"{x['text']}({x['duration']}s)" for x in fill))
    print("")

    scenarios = [("本地固定话术", 1.5), ("平台·快", 4.0), ("平台·中位", 7.0),
                 ("平台·长尾", 13.0)]
    if args.answer:
        scenarios = [(f"指定 {args.answer}s", args.answer)]

    for name, ans in scenarios:
        print(f"  === {name}：{ans} 秒拿到答案 ===")
        for st_name in ("burst", "spread", "final"):
            ev, st = simulate(hi, fill, ans, strategy=st_name, gap=args.gap)
            print(f"     {st_name}：")
            show(ev, st, ans)
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

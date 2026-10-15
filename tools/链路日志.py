"""链路日志查看器（工程债 B4）：一条命令串出一次请求的全链路与各段耗时。

为什么需要：
  一次语音请求跨"识别 → 平台 → 合成 → 板子播放"四段 ✗，出问题时只能靠猜 ✓
  现在服务端每个请求都有一个短 id（也回写在响应头 `X-Request-Id` ✓，板子串口也会打 ✓），
  本工具把它一次串出来 —— 这就是《指南》对 B4 的验收标准 ✓

用法：
    E:\\Python\\python.exe tools\\链路日志.py                # 最近 10 条链路
    E:\\Python\\python.exe tools\\链路日志.py a1b2c3         # 只看这个 id（支持前缀 ✓）
    E:\\Python\\python.exe tools\\链路日志.py --slow         # 按总耗时排序（找最慢的）
    E:\\Python\\python.exe tools\\链路日志.py --tail 30      # 看最近 30 条
"""
import json
import os
import sys

sys.path.insert(0, r"E:\天翼\code")

from server import config  # noqa: E402


def load(path):
    """读 JSON lines；坏行跳过（日志可能被中途打断 ✓）"""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def group(rows):
    """按 rid 归并成一次请求（保留出现顺序 ✓）"""
    order, by = [], {}
    for r in rows:
        rid = r.get("rid", "-")
        if rid not in by:
            by[rid] = []
            order.append(rid)
        by[rid].append(r)
    return [(k, by[k]) for k in order]


def brief(chain):
    """把一次请求的若干行压成一行摘要 ✓"""
    done = next((x for x in reversed(chain) if x.get("event") == "done"), None)
    chat = next((x for x in chain if x.get("event") in ("chat", "music")), None)
    err = next((x for x in chain if x.get("event") == "error"), None)
    t = (done or chat or {}).get("timings") or {}
    parts = " ".join(f"{k} {v}s" for k, v in t.items())
    return {
        "ts": (chain[0].get("ts") or "")[11:],
        "total": (done or {}).get("total", "-"),
        "stages": parts,
        "q": (chat or {}).get("query", ""),
        "a": (chat or {}).get("reply", ""),
        "src": (chat or {}).get("source", ""),
        "err": (err or {}).get("error", ""),
    }


def main():
    args = sys.argv[1:]
    want = None
    slow = "--slow" in args
    tail = 10
    if "--tail" in args:
        i = args.index("--tail")
        if i + 1 < len(args):
            tail = int(args[i + 1])
    for a in args:
        if not a.startswith("--") and a not in ("--slow",):
            want = a

    rows = load(config.LOG_FILE)
    chains = group(rows)
    if want:
        chains = [(k, v) for k, v in chains if k.startswith(want)]
    else:
        chains = chains[-tail:]

    print("=" * 92)
    print(f"链路日志：{config.LOG_FILE}")
    print(f"共 {len(rows)} 行 / {len(chains)} 条链路"
          + (f"（只看前缀 {want!r}）" if want else f"（最近 {tail} 条）"))
    print("=" * 92)
    if not chains:
        print("没有匹配的链路 —— 确认服务端写过日志（ZHIXIAOLI_LOG=1 ✓）")
        return

    infos = [(k, brief(v)) for k, v in chains]
    if slow:
        infos.sort(key=lambda x: (x[1]["total"] if isinstance(x[1]["total"], (int, float)) else -1),
                   reverse=True)

    for k, b in infos:
        flag = " ✗错误" if b["err"] else ""
        print(f"\n[{k}] {b['ts']}  总耗时 {b['total']}s{flag}")
        if b["stages"]:
            print(f"    各段: {b['stages']}")
        if b["q"]:
            print(f"    问: {b['q'][:60]}")
        if b["a"]:
            print(f"    答: {b['a'][:60]}")
        if b["src"]:
            print(f"    来源: {b['src']}")
        if b["err"]:
            print(f"    错误: {b['err'][:80]}")

    print()
    print("提示：给板子/网页看到的那 6 位环节号，就是上面的方括号里的值 ✓")
    print("      想看最慢的：加 --slow ✓；只看某一次：把那个号作为参数 ✓")


main()

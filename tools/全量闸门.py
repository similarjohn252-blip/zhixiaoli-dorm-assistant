"""全量闸门：一条命令跑完所有门禁（本地 CI）。

为什么要它：
  门禁散在 6 个脚本里 ✗ —— 提交前/演示前只能靠"我记得都跑一遍" ✗
  现在一条命令跑完，而且**一次报全**（不是第一个失败就停 ✗）
  —— 与启动自检同一个理念：把该补的一次列清楚 ✓

用法：
    E:\\Python\\python.exe tools\\全量闸门.py             # 全部（约 8~12 分钟）
    E:\\Python\\python.exe tools\\全量闸门.py --fast      # 跳过慢项（故障演练）
    E:\\Python\\python.exe tools\\全量闸门.py --deploy    # 额外跑部署自检（需服务端已在跑 ✓）
    E:\\Python\\python.exe tools\\全量闸门.py --only ruff,依赖

判成败：**既看退出码，也解析输出里的"失败 N 项"与 [FAIL]** ✓
  —— 因为个别脚本只打印汇总、退出码恒为 0 ✗（平台端测试就是 ✓）

⚠️ 它会真实调用百度与平台（耗少量额度 ✓）；故障演练会真实注入故障（在临时目录里 ✓）
"""
import os
import re
import subprocess
import sys
import time

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (键, 名称, 命令行, 超时秒, 是否慢项, 需要服务端)
STEPS = [
    ("ruff", "静态检查", [PY, "-m", "ruff", "check", "."], 180, False, False),
    ("依赖", "依赖版本 12 项", [PY, os.path.join(ROOT, "tools", "检查依赖.py")], 240, False, False),
    ("平台端", "平台端 9 项", [PY, os.path.join(ROOT, "平台端测试.py")], 420, False, False),
    ("全量", "主门禁（含固件编译）", [PY, os.path.join(ROOT, "全量测试.py")], 900, False, False),
    ("演练", "故障演练 6 场景", [PY, os.path.join(ROOT, "tools", "故障演练.py")], 600, True, False),
    ("部署", "部署自检（对外可达）", [PY, os.path.join(ROOT, "tools", "部署自检.py")], 300, False, True),
]

FAIL_PAT = re.compile(r"(失败\s*(\d+)\s*项|\[FAIL\]|✗\s|Traceback)")
SUMMARY_PAT = re.compile(r"(汇总|结论|通过 \d+ 项|全部通过|失败\s*\d+\s*项)")


def judge(rc, out):
    """返回 (是否通过, 一句话结论)"""
    n_fail = 0
    for m in re.finditer(r"失败\s*(\d+)\s*项", out):
        n_fail = max(n_fail, int(m.group(1)))
    hard = [ln for ln in out.splitlines() if "[FAIL]" in ln or ln.strip().startswith("✗")]
    if rc != 0:
        return False, f"退出码 {rc}" + (f"，失败 {n_fail} 项" if n_fail else "")
    if n_fail > 0:
        return False, f"失败 {n_fail} 项（脚本退出码却是 0 ✗ 见脚本自身的 exit）"
    if hard:
        return False, f"输出里有 {len(hard)} 条失败标记"
    return True, "通过"


def run_step(key, name, cmd, timeout, need_server):
    print("\n" + "=" * 88)
    print(f"▶ {name}   （{' '.join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))}）")
    print("=" * 88)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        rc = r.returncode
    except subprocess.TimeoutExpired:
        out, rc = f"（超时 {timeout}s ✗）", 124
    except Exception as e:
        out, rc = f"（起不来：{type(e).__name__}: {e}）", 125
    dur = time.time() - t0

    # 打尾巴（够看结论就行，别刷屏 ✓）
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-12:]
    for ln in tail:
        print("   " + ln[:150])

    ok, why = judge(rc, out)
    fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
    print(f"   → {'✓ 通过' if ok else '✗ 失败'}（{why}，{dur:.1f}s）")
    return {"key": key, "name": name, "ok": ok, "why": why, "dur": dur,
            "fails": fails[:8]}


def main():
    args = sys.argv[1:]
    fast = "--fast" in args
    deploy = "--deploy" in args
    only = None
    if "--only" in args:
        i = args.index("--only")
        if i + 1 < len(args):
            only = {x.strip() for x in args[i + 1].split(",") if x.strip()}

    print("=" * 88)
    print("全量闸门（本地 CI）—— 一次跑完全部门禁，一次报全 ✗不早停")
    print("=" * 88)
    print(f"  项目根：{ROOT}")
    print(f"  选项：{'--fast（跳过慢项）' if fast else '全部'}"
          f"{'  --deploy（含部署自检）' if deploy else ''}"
          f"{'  --only ' + ','.join(sorted(only)) if only else ''}")

    results = []
    for key, name, cmd, timeout, slow, need_server in STEPS:
        if only and key not in only:
            continue
        if slow and fast and not (only and key in only):
            print(f"\n⏭ 跳过 {name}（--fast）")
            continue
        if need_server and not deploy:
            print(f"\n⏭ 跳过 {name}（要加 --deploy，且服务端需在跑）")
            continue
        results.append(run_step(key, name, cmd, timeout, need_server))

    print("\n" + "=" * 88)
    print("闸门结果")
    print("=" * 88)
    for r in results:
        print(f"  {'✓' if r['ok'] else '✗'} {r['name']:<22} {r['dur']:6.1f}s   {r['why']}")
    bad = [r for r in results if not r["ok"]]
    if bad:
        print(f"\n✗ 有 {len(bad)} 项没过：")
        for r in bad:
            print(f"   · {r['name']}：{r['why']}")
            for f in r["fails"]:
                print(f"       {f[:130]}")
        print("\n修完再跑一次（这条命令可以放心反复跑 ✓）")
    else:
        print(f"\n✓ 全部通过（{len(results)} 项）—— 可以提交 / 可以演示 ✓")
    print("=" * 88)
    sys.exit(0 if not bad else 1)


main()

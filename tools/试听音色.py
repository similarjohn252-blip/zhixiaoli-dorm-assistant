"""试听音色：一次生成所有候选发音人的样音，用耳朵挑 ✓

用法：
    E:\\Python\\python.exe tools\\试听音色.py             # 生成全部候选 → 语音\\试听音色\\
    E:\\Python\\python.exe tools\\试听音色.py --per 3     # 只生成 3 号（内部使用 ✓）

为什么要有它：
  · 百度发音人 `per` 分**基础音库**（免费 ✓）和**精品音库**（**要另行购买** ✗）
  · 没买的 per 调过去会返回非音频 / 报错 ✓ → 本工具**逐个试，并明确告诉你哪个能用** ✓
  · ⚠️ 板子的喇叭很小 ✗ → 男声/低音容易发闷 ✓
      所以流程是：**先在电脑上听 → 挑 2~3 个 → 再上板子听一遍 → 才定** ✓

挑完怎么生效（两种都行 ✓）：
  · 临时改：在 `.env` 里加一行  ZHIXIAOLI_TTS_PER=3
  · 改默认：改 `code\\server\\config.py` 里 TTS_PER 的默认值 ✓
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "code"))

# (per, 备注) —— 前 4 个是基础音库（免费 ✓），后面是精品（没买会失败 ✗）
CANDIDATES = [
    (0, "度小美_女_基础"),
    (1, "度小宇_男_基础"),
    (3, "度逍遥_男_情感_基础"),
    (4, "度丫丫_童_基础"),
    (5003, "度逍遥_精品"),
    (5118, "度小鹿_女_精品"),
    (106, "度博文_男_精品"),
    (110, "度小童_童_精品"),
    (103, "度米朵_女_精品"),
    (5, "度小娇_女_精品"),
]

# 用真实会念到的内容来试 ✓（含中文数字 ✓ —— 音色差异在长句和数字上最明显 ✓）
DEMO_TEXT = ("宿舍事务助手，我在。三栋五零二的灯坏了，已经帮你报修，"
             "工单号是二零二六一零一五零零零一。")
OUT_DIR = os.path.join(ROOT, "语音", "试听音色")


def one(per):
    """在**子进程**里生成单个音色 ✓ —— 因为 config 是 import 时读环境变量的，
    所以必须由父进程把 ZHIXIAOLI_TTS_PER 传进来 ✓（同进程改不了 ✓）"""
    # ⚠️ 只 import speech ✓ —— 它内部会 import config ✓（config 在 import 时读环境变量，
    #    所以父进程把 ZHIXIAOLI_TTS_PER 传进来就行 ✓）。这里多写一个 config 会被 ruff 判 F401 ✗
    from server import speech  # noqa: PLC0415

    os.makedirs(OUT_DIR, exist_ok=True)
    name = dict(CANDIDATES).get(per, str(per))
    path = os.path.join(OUT_DIR, f"per{per}_{name}.wav")
    speech.baidu_tts(DEMO_TEXT, path)
    size = os.path.getsize(path)
    if size < 2000:
        raise RuntimeError(f"文件太小（{size} 字节），多半不是音频")
    print(f"OK|{per}|{name}|{size}|{path}")


def main():
    if "--per" in sys.argv:
        one(int(sys.argv[sys.argv.index("--per") + 1]))
        return 0

    print("=" * 78)
    print(f"试听音色：逐个调用百度 TTS（当前默认 per={os.environ.get('ZHIXIAOLI_TTS_PER', '0')}）")
    print(f"样音输出：{OUT_DIR}")
    print("=" * 78)
    ok, bad = [], []
    for per, name in CANDIDATES:
        env = dict(os.environ)
        env["ZHIXIAOLI_TTS_PER"] = str(per)
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--per", str(per)],
                           env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("OK|")]
        if line:
            _, p, n, size, path = line[0].split("|")
            ok.append((int(p), n, int(size), path))
            print(f"  ✓ per={p:<5} {n:<20} {int(size):>8,} B")
        else:
            err = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
            brief = next((x for x in reversed(err) if x.strip()), "未知错误")
            bad.append((per, name, brief[-110:]))
            print(f"  ✗ per={per:<5} {name:<20} 失败：{brief[-110:]}")

    print()
    print(f"可用 {len(ok)} 个 ✓（这些就是你的候选 ✓）；不可用 {len(bad)} 个 ✗")
    if bad:
        print("不可用的原因（多半是【精品音库未购买】✗ 而不是代码问题 ✓）：")
        for per, name, brief in bad:
            print(f"  per={per:<5} {name:<20} {brief}")
    print()
    print("下一步：")
    print(f"  ① 打开 {OUT_DIR}，双击 wav 用耳朵挑 ✓")
    print("  ② 挑出 2~3 个 → 我改 .env 的 ZHIXIAOLI_TTS_PER → 烧到板子上的小喇叭再听一遍 ✓")
    print("  ③ 板子上听着舒服的那个才是最终选择 ✓（电脑上好听 ≠ 小喇叭好听 ✗）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

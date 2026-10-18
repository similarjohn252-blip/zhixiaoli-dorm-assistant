"""对外地址自动纠正（2026-10-18 新增 ✓）

背景 ✗：校园网 IP **三天变了三次** ✓（10.103.7.74 → 10.203.24.251 → 10.103.51.219）
  每变一次就要手动改 `.env` + 三份文档 ✗ 漏一次就"板子/评委拿到的音频地址打不开"✗
  （2026-10-15 板子"没声音"就是这个原因 ✓）

做法 ✓：启动时探测本机地址 ✓ 配置值如果**不在本机地址里** → 自动改用探测值并大声提示 ✓
  **能手动配就优先 ✓，只在配错时才纠正 ✓**（多网卡/虚拟网卡时探测可能选错 ✓）

⚠️ 为什么用**子进程**测 ✓：`config` 是 import 时读环境变量的 ✓ 进程内改不了 ✓
   （与 test_config_paths.py 同一个套路 ✓）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.path.join(ROOT, "code")


def _probe(host):
    """用指定 PUBLIC_HOST 起一个子进程 ✓ 返回 (最终值, 本机地址列表, 全部输出)"""
    env = dict(os.environ)
    env["ZHIXIAOLI_PUBLIC_HOST"] = host
    code = ("import sys; sys.path.insert(0, r'" + CODE + "'); "
            "from server import config; "
            "print('HOST', config.PUBLIC_HOST); "
            "print('IPS', ','.join(config._local_ips()))")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    got = ""
    ips = []
    for ln in out.splitlines():
        if ln.startswith("HOST "):
            got = ln.split(" ", 1)[1].strip()
        elif ln.startswith("IPS "):
            ips = [x for x in ln.split(" ", 1)[1].strip().split(",") if x]
    return got, ips, out


def test_wrong_host_is_auto_corrected():
    """⭐ 配错的地址 → 自动改成本机真实地址 ✓ 并且【大声提示】✓"""
    got, ips, out = _probe("10.99.99.99")
    if not ips:
        return                      # 这台机器没有非回环地址 → 这条不适用 ✓
    assert got != "10.99.99.99", f"配错的值必须被纠正 ✗ 实际={got}"
    assert got in ips, f"纠正后的值必须是本机地址 ✓ 实际={got} 本机={ips}"
    assert "不在本机地址里" in out, "必须打印提示，否则用户不知道要改 .env ✗"


def test_correct_host_is_kept():
    """配置正确时【原样保留】✓ —— 别自作聪明去改 ✓"""
    _, ips, _ = _probe("127.0.0.1")
    if not ips:
        return
    got, _, out = _probe(ips[0])
    assert got == ips[0], f"配置正确时不该被改 ✗ 实际={got}"
    assert "不在本机地址里" not in out, "配置正确时不该报警 ✗"


def test_localhost_is_allowed():
    """本地开发配 127.0.0.1 是**合法**的 ✓（不该被纠正成网卡地址 ✗）"""
    got, _, out = _probe("127.0.0.1")
    assert got == "127.0.0.1", f"本机回环地址应原样保留 ✓ 实际={got}"
    assert "不在本机地址里" not in out

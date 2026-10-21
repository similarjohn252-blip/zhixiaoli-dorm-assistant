"""GPA 计算（2026-10-21）

为什么放服务端：平台那个代码节点一直段错误（连 45 行纯 Python 都崩），
               是平台沙箱的问题，不是代码的问题。放到服务端就不用管它了。

输入是一句话，比如：
  "我 GPA 3.2，已修 80 学分，还要修 40 学分，想上到 3.5"
  "现在绩点 3.2 修了 80 个学分 还剩 40 个 想上 3.5"
  "3.2 80 40 3.5"
抽出四个数：现在绩点 / 已修学分 / 剩余学分 / 目标绩点。

算三件事：
  · 未来课程平均需要多少分
  · 未来全拿 5.0 时最高能到多少
  · 达不达得到

⭐ 这是本项目少有的"确定可复现"的能力 —— 不是模型算的，是代码算的。
"""
import re

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _nums(text):
    return [float(x) for x in _NUM.findall(text or "")]


def parse(query):
    """从一句话里抽出 (cur, done, left, tgt)。抽不齐就返回 None。

    ⭐ 策略：先按"标签法"找（GPA/已修/还剩/想上 后面那个数），
      找不到再退回"按顺序取四个数"，最后按合理性修正：
      绩点在 0~5 之间，学分是正整数。
    """
    q = query or ""
    nums = _nums(q)
    if len(nums) < 4:
        return None

    cur = done = left = tgt = None

    def _after(labels, limit=30):
        """labels 里任一个词后面 limit 个字符内的第一个数"""
        for lab in labels:
            i = q.find(lab)
            if i >= 0:
                seg = q[i + len(lab): i + len(lab) + limit]
                m = _NUM.search(seg)
                if m:
                    return float(m.group(0))
        return None

    cur = _after(("GPA", "gpa", "绩点", "平均分"))
    done = _after(("已修", "修了", "已获得", "修过"))
    left = _after(("还要修", "还剩", "剩余", "还需", "再修"))
    tgt = _after(("想上", "目标", "想达到", "要到", "冲到"))

    if None in (cur, done, left, tgt):
        # 退回：按顺序取前四个，再按 0~5 判断谁是绩点
        four = nums[:4]
        gpaish = [n for n in four if 0 <= n <= 5]
        creditish = [n for n in four if n > 5]
        if len(gpaish) >= 2 and len(creditish) >= 2:
            cur, tgt = gpaish[0], gpaish[1]
            done, left = creditish[0], creditish[1]
        else:
            cur, done, left, tgt = four

    # 合理性修正：绩点必须在 0~5；学分必须 > 0
    if not (0 <= cur <= 5) or not (0 <= tgt <= 5):
        return None
    if done <= 0 or left <= 0:
        return None
    return cur, done, left, tgt


def calc(cur, done, left, tgt):
    """返回 dict：需要多少 / 最高能到多少 / 达不达得到 / 一句话"""
    total = done + left
    required = (tgt * total - cur * done) / left
    max_possible = (cur * done + 5.0 * left) / total
    achievable = required <= 5.0
    if required <= 0:
        msg = "你现在已经够高了，即使未来绩点为 0 也能到目标"
    elif achievable:
        msg = ("未来课程平均要拿到 " + str(round(required, 2))
               + " 分才能到 " + str(round(tgt, 2)) + "，努一把还行")
    else:
        msg = ("到不了：就算未来全拿 5.0，最高也只有 "
               + str(round(max_possible, 2)) + " 分，达不到 "
               + str(round(tgt, 2)))
    return {
        "required": round(max(0.0, required), 3),
        "max_possible": round(max_possible, 3),
        "achievable": achievable,
        "message": msg,
    }


def answer(query):
    """一句话进，一句话出。抽不出四个数就返回提示语（不是空）。"""
    p = parse(query)
    if not p:
        return ("告诉你四个数我就能算：现在的绩点、已修多少学分、"
                "还要修多少学分、想达到多少。比如「我 GPA 3.2，"
                "已修 80 学分，还要修 40 学分，想上到 3.5」。")
    cur, done, left, tgt = p
    r = calc(cur, done, left, tgt)
    return (f"你现在绩点 {cur}，已修 {int(done)} 学分，还要修 {int(left)} 学分，"
            f"目标是 {tgt}。" + r["message"] + "。")

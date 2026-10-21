import re


def main(text):
    q = str(text or "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", q)]

    def after(labels, limit=30):
        for lab in labels:
            i = q.find(lab)
            if i >= 0:
                seg = q[i + len(lab): i + len(lab) + limit]
                m = re.search(r"\d+(?:\.\d+)?", seg)
                if m:
                    return float(m.group(0))
        return None

    cur = after(["GPA", "gpa", "绩点", "平均分"])
    done = after(["已修", "修了", "已获得", "修过"])
    left = after(["还要修", "还剩", "剩余", "还需", "再修"])
    tgt = after(["想上", "目标", "想达到", "要到", "冲到"])

    if None in (cur, done, left, tgt):
        if len(nums) >= 4:
            gpaish = [n for n in nums if 0 <= n <= 4]
            creditish = [n for n in nums if n > 4]
            if len(gpaish) >= 2 and len(creditish) >= 2:
                cur, tgt = gpaish[0], gpaish[1]
                done, left = creditish[0], creditish[1]
            else:
                cur, done, left, tgt = nums[0], nums[1], nums[2], nums[3]
        else:
            cur = cur or 0.0
            done = done or 0.0
            left = left or 0.0
            tgt = tgt or 0.0

    if cur is None:
        cur = 0.0
    if done is None:
        done = 0.0
    if left is None:
        left = 0.0
    if tgt is None:
        tgt = 0.0

    if cur < 0:
        cur = 0.0
    if cur > 4:
        cur = 0.0
    if tgt < 0:
        tgt = 0.0
    if tgt > 4:
        tgt = 0.0
    if done < 0:
        done = 0.0
    if left < 0:
        left = 0.0

    return {
        "current_gpa": float(cur),
        "completed_credits": float(done),
        "remaining_credits": float(left),
        "target_gpa": float(tgt),
    }

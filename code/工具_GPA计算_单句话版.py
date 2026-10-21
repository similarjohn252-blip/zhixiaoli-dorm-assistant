import re


def main(text):
    q = str(text or "")

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

    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", q)]

    if cur is None or done is None or left is None or tgt is None:
        if len(nums) >= 4:
            gpaish = [n for n in nums if 0 <= n <= 5]
            creditish = [n for n in nums if n > 5]
            if len(gpaish) >= 2 and len(creditish) >= 2:
                cur = gpaish[0] if cur is None else cur
                tgt = gpaish[1] if tgt is None else tgt
                done = creditish[0] if done is None else done
                left = creditish[1] if left is None else left
            else:
                cur = nums[0] if cur is None else cur
                done = nums[1] if done is None else done
                left = nums[2] if left is None else left
                tgt = nums[3] if tgt is None else tgt

    cur = 0.0 if cur is None else float(cur)
    done = 0.0 if done is None else float(done)
    left = 0.0 if left is None else float(left)
    tgt = 0.0 if tgt is None else float(tgt)

    # ⚠️ tgt 也用 <= 0 判断：用户没说目标绩点时 tgt 会是 0，
    #    那时算出来 required 是负数，会误答"你已经够高了"——其实只是没给目标
    if left <= 0 or done <= 0 or cur <= 0 or cur > 5 or tgt <= 0 or tgt > 5:
        return {
            "required_future_gpa": 0.0,
            "max_possible_gpa": 0.0,
            "achievable": False,
            "message": ("算绩点我需要四个数：现在的绩点、已修多少学分、"
                        "还要修多少学分、想达到多少。比如"
                        "「我绩点 4.5，已修 80 学分，还要修 40 学分，想上到 5.0」"),
        }

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
        msg = ("达不到：就算未来全拿 5.0 分，最高也只有 "
               + str(round(max_possible, 2)) + " 分，到不了 "
               + str(round(tgt, 2)))

    return {
        "required_future_gpa": round(max(0.0, required), 3),
        "max_possible_gpa": round(max_possible, 3),
        "achievable": bool(achievable),
        "message": msg,
    }

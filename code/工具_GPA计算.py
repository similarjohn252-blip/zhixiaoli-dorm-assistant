def main(current_gpa, completed_credits, remaining_credits, target_gpa):
    try:
        cur = float(current_gpa)
    except Exception:
        cur = 0.0
    try:
        done = float(completed_credits)
    except Exception:
        done = 0.0
    try:
        left = float(remaining_credits)
    except Exception:
        left = 0.0
    try:
        tgt = float(target_gpa)
    except Exception:
        tgt = 0.0

    if left <= 0 or done <= 0 or cur < 0 or cur > 5 or tgt < 0 or tgt > 5:
        return {
            "required_future_gpa": 0.0,
            "max_possible_gpa": 0.0,
            "achievable": False,
            "message": "参数不对：绩点在 0~5 之间，已修和剩余学分都要大于 0",
        }

    total = done + left
    required = (tgt * total - cur * done) / left
    max_possible = (cur * done + 5.0 * left) / total
    achievable = required <= 5.0

    if required <= 0:
        msg = "即使未来课程绩点为 0，也能达到目标"
    elif achievable:
        msg = "理论上可以达到：未来课程平均需要 " + str(round(required, 2)) + " 分"
    else:
        msg = ("达不到：未来课程全拿 5.0 也最多到 "
               + str(round(max_possible, 2)) + " 分")

    return {
        "required_future_gpa": round(max(0.0, required), 3),
        "max_possible_gpa": round(max_possible, 3),
        "achievable": bool(achievable),
        "message": msg,
    }

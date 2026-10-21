def main(courses, difficulties, days, hours_per_day):
    names = [x.strip() for x in str(courses or "").replace("，", ",").split(",") if x.strip()]
    raw = [x.strip() for x in str(difficulties or "").replace("，", ",").split(",") if x.strip()]

    m = {"很简单": 1, "简单": 2, "较简单": 2, "容易": 2, "中等": 3, "一般": 3,
         "普通": 3, "较难": 4, "难": 4, "困难": 4, "很难": 5, "非常难": 5}
    w = [float(m.get(x, 3)) for x in raw]

    if not names:
        names = ["复习"]
    while len(w) < len(names):
        w.append(3.0)
    w = w[:len(names)]

    try:
        d = float(days or 0)
    except Exception:
        d = 0.0
    try:
        h = float(hours_per_day or 0)
    except Exception:
        h = 0.0
    if d <= 0:
        d = 30.0
    if h <= 0:
        h = 2.0

    total = d * h
    tw = sum(w) or 1.0

    lines = []
    for n, wt in zip(names, w):
        each = total * wt / tw
        lines.append(n + "：总共 " + str(round(each, 1)) + " 小时，每天约 "
                     + str(round(each / d, 2)) + " 小时")

    return {
        "total_hours": round(total, 1),
        "course_count": len(names),
        "daily_hours": round(h, 1),
        "plan_text": "\n".join(lines),
    }

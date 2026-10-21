import re


def _nums(text):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]


def _to_gpa(score):
    if score < 60:
        return 0.0
    g = (score - 60) / 10.0 + 1.0
    if g > 5.0:
        g = 5.0
    return g


def main(scores, credits):
    sc = _nums(scores)
    cr = _nums(credits)

    n = min(len(sc), len(cr))
    if n <= 0:
        return {
            "course_count": 0,
            "gpa": 0.0,
            "total_credits": 0.0,
            "weighted_average": 0.0,
        }

    sc = sc[:n]
    cr = cr[:n]

    total_credits = 0.0
    weighted_sum = 0.0
    gpa_sum = 0.0
    for s, c in zip(sc, cr):
        if c <= 0:
            continue
        total_credits += c
        weighted_sum += s * c
        gpa_sum += _to_gpa(s) * c

    if total_credits <= 0:
        return {
            "course_count": n,
            "gpa": 0.0,
            "total_credits": 0.0,
            "weighted_average": 0.0,
        }

    gpa = gpa_sum / total_credits
    weighted_average = weighted_sum / total_credits

    return {
        "course_count": n,
        "gpa": round(gpa, 3),
        "total_credits": round(total_credits, 2),
        "weighted_average": round(weighted_average, 2),
    }

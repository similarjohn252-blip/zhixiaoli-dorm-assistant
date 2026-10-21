# GPA 计算工具 —— 给 Dify 代码节点用的两个版本（2026-10-21）

> 用法：先跑第 1 段（隔离测试），确认沙箱是好的；再换第 2 段（正式版）。
> 两段都**不要改动函数名 `main`**，Dify 按这个名字调用。

---

## 第 1 段：先隔离测试（三行，证明沙箱本身没问题）

把代码节点整段替换成下面三行，其他都不动（输入变量先随便填个数）：

```python
def main(current_gpa, completed_credits, remaining_credits, target_gpa):
    return {"result": "ok"}
```

⚠️ **注意这里返回的是 `result`** —— 因为你这个代码节点的输出变量**现在就叫 result**。
隔离测试必须匹配当前的输出变量名，否则测不出任何东西，只会一直报"missing output parameter"。

**判断方法**：

- 如果它报的**还是** `missing output parameter: result` → 说明输出变量名也不是 result（或者你改过），
  打开代码节点的【输出变量】面板，把里面列的名字抄下来，让 `return` 的键和它一模一样。
- 如果它报**段错误** → 是平台的代码沙箱坏了。处理：删掉这个代码节点、重新加一个新的再试。
- 如果它**跑通了** → 沙箱是好的。问题在别处：要么输出键名对不上，要么原代码里有
  `raise` / 类型注解，继续看第 2 段。

⭐ 顺便记住这件事：**报"missing output parameter: X" 时，去看输出变量面板里是不是有个 X**。
Dify 是按你声明的输出变量去取值，代码里没返回那个键，它就报这个错。

---

## 第 2 段：正式版（不抛异常、不写类型注解）

原代码有三个地方容易把沙箱搞崩：

1. **`raise ValueError(...)`** —— 沙箱里未捕获的异常有时表现为段错误（而不是正常的错误信息）
2. **参数上的类型注解** `current_gpa: float` —— 有些沙箱版本处理注解会出问题
3. **返回值注解** `-> dict` —— 同上

所以正式版全部去掉，改成"算出问题了就返回一句 message"，绝不抛异常。

```python
def main(current_gpa, completed_credits, remaining_credits, target_gpa):
    # 全部先转成数字，转不了就当 0（不抛异常）
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

    # 参数不合法 → 返回提示，不抛异常
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
```

---

## 配好之后要确认的三件事

**一、代码节点的输入变量**（四个，类型 Number，名字一字不差）

`current_gpa`、`completed_credits`、`remaining_credits`、`target_gpa`

**二、代码节点的输出变量**（四个，名字一字不差，类型要对）

`required_future_gpa` Number、`max_possible_gpa` Number、
`achievable` Boolean、`message` String

**三、结束节点的输出** —— 引用上面这四个，**不要**引用"开始"的四个输入

---

## 算一遍验证（用这组数应该得到什么）

`current_gpa = 3.2`、`completed_credits = 80`、`remaining_credits = 40`、`target_gpa = 3.5`

- 总学分 120
- 需要未来平均 = (3.5 × 120 − 3.2 × 80) / 40 = (420 − 256) / 40 = **4.1**
- 未来全拿 5.0 时最高能到 = (3.2 × 80 + 4.0 × 40) / 120 = (256 + 160) / 120 = **3.467**
- 结论：**达不到**（需要 4.1，超过 4.0 上限；最高只能到 3.467）

如果算出来是这个结果，说明逻辑对。

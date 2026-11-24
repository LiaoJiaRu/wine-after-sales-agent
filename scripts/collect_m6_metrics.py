"""M6 指标采集脚本。

从 ``m6_eval.log``（pytest 输出）解析通过/失败数量，计算任务成功率；
从 ``tests/test_m6_evaluation.py`` 的 ``CASES`` 定义静态统计 handoff 标记，
计算评测集转人工率。

其余四项指标（工具调用成功率、平均重试次数、P95 端到端延迟、Trace 覆盖率）
依赖真实运行时埋点 / Langfuse 数据，当前为离线 Mock 测试，无真实调用数据，
故诚实标注为"未采集"，不误标为达标。

输出：``docs/progress/11-23-m6-metrics.md``

用法（在项目根目录下执行）::

    python scripts/collect_m6_metrics.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

# 脚本位于 <root>/scripts/，项目根为其父目录
ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "m6_eval.log"
TEST_PATH = ROOT / "tests" / "test_m6_evaluation.py"
OUT_PATH = ROOT / "docs" / "progress" / "11-23-m6-metrics.md"

# 对照实训计划表 10 的 6 项指标：指标名、目标值、采集方式
METRIC_SPEC = [
    {"name": "任务成功率", "target": "≥ 80%（12/15）", "method": "用例脚本判定 + 人工复核"},
    {"name": "工具调用成功率", "target": "≥ 95%", "method": "工具层埋点计数"},
    {"name": "平均重试次数", "target": "≤ 0.8 次/请求", "method": "状态字段 retry_count 聚合"},
    {"name": "转人工率", "target": "20% ~ 35%", "method": "handoff 标记统计"},
    {"name": "P95 端到端延迟", "target": "≤ 20 秒", "method": "Langfuse span 时长"},
    {"name": "Trace 覆盖率", "target": "100%", "method": "每次请求均生成 Trace"},
]

UNCOLLECTED_REASON = "未采集（原因：当前为离线 Mock 测试，无真实调用数据）"


def _read_text_robust(path: Path) -> str:
    """读取文本文件，兼容 UTF-8 / UTF-16（含 BOM）/ GBK，全部失败时退回 latin-1。"""
    for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="latin-1", errors="replace")


# --------------------------------------------------------------------------- #
# 1. 任务成功率：解析 pytest 日志
# --------------------------------------------------------------------------- #
def parse_pytest_summary(log_text: str) -> tuple[int, int]:
    """从 pytest 日志解析通过/失败数量，返回 ``(passed, failed)``。

    优先匹配摘要行（如 ``15 passed, 1 warning in 2.78s`` / ``1 failed, 14 passed``），
    兜底时逐行统计 ``PASSED`` / ``FAILED`` 标记。空文本返回 ``(0, 0)`` 且不崩溃。
    """
    passed = 0
    failed = 0
    m = re.search(r"(\d+)\s+passed", log_text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", log_text)
    if m:
        failed = int(m.group(1))
    if passed == 0 and failed == 0:
        passed = len(re.findall(r"\bPASSED\b", log_text))
        failed = len(re.findall(r"\bFAILED\b", log_text))
    return passed, failed


def compute_success_rate(passed: int, failed: int) -> str:
    """计算任务成功率，空输入（0/0）返回 ``"0%"`` 且不崩溃。"""
    total = passed + failed
    if total == 0:
        return "0%"
    return f"{passed / total * 100:.1f}%"


# --------------------------------------------------------------------------- #
# 2. 转人工率：静态解析 CASES，不导入测试模块（避免触发 compile_graph）
# --------------------------------------------------------------------------- #
def count_handoff(test_path: Path) -> tuple[int, int]:
    """静态统计 ``CASES`` 中 ``handoff=True`` 的数量与用例总数。

    使用 :mod:`ast` 解析字面量列表，不执行模块；若 AST 解析失败则回退到正则。
    返回 ``(handoff_true_count, total_count)``。
    """
    source = _read_text_robust(test_path)

    # 优先：AST 精确解析
    try:
        tree = ast.parse(source)
        cases_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "CASES":
                        cases_node = node.value
                        break
            if cases_node is not None:
                break
        if isinstance(cases_node, ast.List):
            true_count = 0
            total = 0
            for elt in cases_node.elts:
                if not isinstance(elt, ast.Dict):
                    continue
                total += 1
                for key, val in zip(elt.keys, elt.values):
                    if isinstance(key, ast.Constant) and key.value == "handoff":
                        if isinstance(val, ast.Constant) and val.value is True:
                            true_count += 1
            if total > 0:
                return true_count, total
    except SyntaxError:
        pass

    # 兜底：正则统计 "handoff": True
    true_count = len(re.findall(r'"handoff"\s*:\s*True', source))
    total = len(re.findall(r'"handoff"\s*:\s*(?:True|False)', source))
    return true_count, total


# --------------------------------------------------------------------------- #
# 3. 渲染 Markdown
# --------------------------------------------------------------------------- #
def render_markdown(
    passed: int,
    failed: int,
    success_rate: str,
    handoff_true: int,
    handoff_total: int,
) -> str:
    total = passed + failed
    handoff_pct = (handoff_true / handoff_total * 100) if handoff_total else 0.0

    lines = [
        "# M6 指标采集报告（11-23）",
        "",
        "- 采集日期：2026-11-23",
        "- 指标口径：对照实训计划表 10（6 项指标）",
        "- 数据来源：`m6_eval.log`（pytest 输出）、`tests/test_m6_evaluation.py`（CASES 静态统计）",
        "- 采集脚本：`scripts/collect_m6_metrics.py`",
        "",
        "## 1. 指标实测表",
        "",
        "| 指标 | 目标值 | 当前实测 | 采集方式 | 完成度 | 备注 |",
        "|---|---|---|---|---|---|",
    ]

    for spec in METRIC_SPEC:
        name = spec["name"]
        if name == "任务成功率":
            actual = f"{passed}/{total} = {success_rate}"
            met = total > 0 and (passed / total) >= 0.8
            completion = "达标" if met else "未达标"
            note = f"来源：m6_eval.log（{passed} passed，{failed} failed）"
        elif name == "转人工率":
            actual = f"{handoff_true}/{handoff_total} = {handoff_pct:.1f}%"
            completion = "已采集，超出目标区间"
            note = (
                "基于 15 条评测用例静态统计，非生产采样；评测集故意放大故障/异常/"
                "合规场景（7 条预期转人工），不能直接对照目标区间"
            )
        else:
            actual = UNCOLLECTED_REASON
            completion = "未采集"
            note = "离线 Mock 测试未产生真实运行时数据"
        lines.append(
            f"| {name} | {spec['target']} | {actual} | {spec['method']} | {completion} | {note} |"
        )

    lines += [
        "",
        "## 2. 说明",
        "",
        "1. 任务成功率与转人工率为本次可采集项，均来自离线评测集，不代表生产环境表现。",
        "2. 工具调用成功率、平均重试次数、P95 端到端延迟、Trace 覆盖率四项依赖真实运行时埋点 / Langfuse 数据；"
        "当前测试全链路使用确定性 Mock（classify_intent / judge_draft 等），未产生真实调用数据，"
        "故诚实标注为未采集，未误标为达标。",
        "3. 转人工率 46.7% 高于目标区间 20%~35%，原因是 15 条评测用例中有 7 条"
        "（U05/U06/U07/U08/U11/U12/U14）为预期转人工的故障/异常/合规场景，属于故障场景放大采样，非生产流量采样。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 4. 边界自测：空输入时成功率为 0% 且不崩溃
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    # 空输入列表（passed=0, failed=0）→ 成功率 0%，不抛异常
    assert compute_success_rate(0, 0) == "0%", "空输入成功率应为 0%"
    # 空日志文本解析 → (0, 0) → 0%
    p, f = parse_pytest_summary("")
    assert (p, f) == (0, 0), "空日志应解析为 (0, 0)"
    assert compute_success_rate(p, f) == "0%", "空日志成功率应为 0%"
    print("[self-test] 空输入边界用例通过：成功率 = 0%，未崩溃")


def main() -> None:
    _self_test()

    log_text = _read_text_robust(LOG_PATH) if LOG_PATH.exists() else ""
    passed, failed = parse_pytest_summary(log_text)
    success_rate = compute_success_rate(passed, failed)

    handoff_true, handoff_total = count_handoff(TEST_PATH) if TEST_PATH.exists() else (0, 0)

    md = render_markdown(passed, failed, success_rate, handoff_true, handoff_total)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(md, encoding="utf-8")

    print(f"任务成功率：{passed}/{passed + failed} = {success_rate}")
    print(f"转人工率：{handoff_true}/{handoff_total} = "
          f"{(handoff_true / handoff_total * 100):.1f}%" if handoff_total else "转人工率：N/A")
    print(f"已生成：{OUT_PATH}")


if __name__ == "__main__":
    main()

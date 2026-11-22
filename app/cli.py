from __future__ import annotations

import argparse
import json
import sys

from app.agents.router import route
from app.llm import flush, trace_url


def run_graph(user_text: str, order_id: str, session_id: str = "") -> dict:
    """完整 LangGraph 流程的最外层兜底。

    compile_graph().invoke() 已经有节点内 try/except + 条件边 handoff，
    这里再包一层大 try/except，保证任何 LangGraph 运行时异常（包括未被节点捕获的）
    都不会让 CLI 崩溃，而是返回 {"error": ..., "needs_human": True}。
    """
    try:
        from app.graph import RECURSION_LIMIT, compile_graph

        state = compile_graph().invoke(
            {"user_text": user_text, "order_id": order_id, "session_id": session_id},
            config={"recursion_limit": RECURSION_LIMIT},
        )
        # LangGraph invoke 异常不抛则 state 一定存在；再补一层 needs_human 同步
        return state
    except Exception as exc:  # pragma: no cover - 真正兜底
        return {
            "error": {"code": "GRAPH_RUNTIME", "message": str(exc)},
            "needs_human": True,
            "handoff": True,
        }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wine-agent", description="葡萄酒售后客服 Agent CLI")
    p.add_argument("text", nargs="*", help="用户反馈原文")
    p.add_argument("--order", default="", help="订单号（走 graph 流程时必填）")
    p.add_argument("--session", default="", help="多轮会话的 session_id")
    p.add_argument("--graph", action="store_true", help="走完整 LangGraph 链路（router → fetch → check → draft）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    text = " ".join(args.text).strip()

    if args.graph:
        order_id = (args.order or "").strip()
        session_id = (args.session or "").strip()
        state = run_graph(text, order_id, session_id)
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
    else:
        decision = route(text)
        print(json.dumps(decision.model_dump(exclude_none=True), ensure_ascii=False, indent=2))

    flush()
    url = trace_url()
    if url:
        print(f"[langfuse] {url}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

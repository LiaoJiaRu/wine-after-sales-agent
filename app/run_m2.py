"""M2 破损漏液单场景闭环演示脚本（两幕）。

第一幕（无证据）：
    python -m app.run_m2 --order SO20261116001
    → evidence_status.complete=False，reply_draft 输出【需要您补充的材料】清单。

第二幕（证据齐全）：
    python -m app.run_m2 --order SO20261116001 --with-evidence
    → evidence_status.complete=True，compensation=partial_refund 354.00 元，
      reply_draft 告知赔付金额并提示人工审核，不再要求补证。

只打印结果，不写库、不退款。
"""
from __future__ import annotations

import argparse
import json

from app.graph import compile_graph

# broken_leak 要求的完整证据三项（与 readonly.REQUIRED_EVIDENCE 对齐）
FULL_EVIDENCE = ["开箱视频", "面单照片", "破损部位照片"]

DEFAULT_ORDER_ID = "SO20261116001"
DEFAULT_USER_TEXT = "我买的酒漏了，瓶身有破损"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_m2", description="M2 破损漏液闭环演示（两幕）")
    parser.add_argument("--order", default=DEFAULT_ORDER_ID, help="订单号")
    parser.add_argument("--text", default=DEFAULT_USER_TEXT, help="用户反馈原文")
    parser.add_argument(
        "--with-evidence",
        action="store_true",
        help="传入完整证据（开箱视频/面单照片/破损部位照片），走第二幕",
    )
    args = parser.parse_args(argv)

    initial_state: dict = {
        "user_text": args.text,
        "order_id": args.order,
        "issue_type": "broken_leak",
    }
    if args.with_evidence:
        initial_state["evidence"] = list(FULL_EVIDENCE)

    state = compile_graph().invoke(initial_state)

    print("=" * 60)
    print(f"幕次: {'第二幕（证据齐全）' if args.with_evidence else '第一幕（无证据）'}")
    print(f"order_id: {args.order}  handoff: {state.get('handoff')}  needs_human: {state.get('needs_human')}")
    print("=" * 60)

    print("\n--- evidence_status ---")
    print(json.dumps(state.get("evidence_status"), ensure_ascii=False, indent=2))

    print("\n--- compensation ---")
    print(json.dumps(state.get("tool_results", {}).get("compensation"), ensure_ascii=False, indent=2))

    print("\n--- ticket_draft ---")
    print(json.dumps(state.get("ticket_draft"), ensure_ascii=False, indent=2, default=str))

    print("\n--- reply_draft ---")
    reply = state.get("reply_draft")
    if reply:
        print(json.dumps({k: v for k, v in reply.items() if k != "content"}, ensure_ascii=False, indent=2, default=str))
        print("\n[reply_draft.content]\n" + reply.get("content", ""))
    else:
        print(json.dumps(reply, ensure_ascii=False))

    if state.get("error"):
        print("\n--- error ---")
        print(json.dumps(state["error"], ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

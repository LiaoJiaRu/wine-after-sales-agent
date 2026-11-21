"""假酒/真伪专业子 Agent（M4 / 扩展阶段预留，当前未接入 LangGraph）。

M3 阶段真伪类诉求会因 `_derive_issue_type` 识别不到理赔类型而转人工（UNSUPPORTED_ISSUE_TYPE）；
待 M4 接入后，由本 Agent 负责批次溯源、防伪码核验与送检流程建议。
保留同名 `run()` 签名，便于后续平移到图节点：

    sg.add_node("fake_wine_agent", fake_wine_agent)
"""
from __future__ import annotations

from app.tools.readonly import get_batch


class FakeWineAgent:
    """占位实现：只做批次只读透传，不含真伪判定逻辑。"""

    name = "fake_wine_agent"
    issue_types = ("真伪争议",)

    def run(self, order_id: str, batch_no: str) -> dict:
        batch = get_batch(batch_no).batch
        return {
            "agent": self.name,
            "order_id": order_id,
            "batch": batch.model_dump(),
        }


fake_wine_agent = FakeWineAgent()

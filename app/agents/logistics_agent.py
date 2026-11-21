"""物流专业子 Agent（M4 / 扩展阶段预留，当前未接入 LangGraph）。

M3 阶段物流类诉求仍由 `fetch_evidence` 统一调用只读工具 `get_logistics()` 完成；
待 M4 拆分专业子 Agent 后，由本 Agent 独立负责物流时效、轨迹异常等判断。
保留同名 `run()` 签名，便于后续平移到图节点：

    sg.add_node("logistics_agent", logistics_agent)
"""
from __future__ import annotations

from app.tools.readonly import get_logistics


class LogisticsAgent:
    """占位实现：只做只读透传，不含时效判断等业务逻辑。"""

    name = "logistics_agent"
    issue_types = ("物流延迟",)

    def run(self, order_id: str) -> dict:
        shipment = get_logistics(order_id).shipment
        return {
            "agent": self.name,
            "order_id": order_id,
            "shipment": shipment.model_dump(),
        }


logistics_agent = LogisticsAgent()

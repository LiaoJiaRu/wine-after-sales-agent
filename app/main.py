"""FastAPI Agent 服务：健康检查与 LangGraph 调用入口。"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.graph import RECURSION_LIMIT, compile_graph
from app.llm import trace_url

app = FastAPI(title="Wine After-Sales Agent", version="1.0.0")
GRAPH = compile_graph()


class GraphInvokeRequest(BaseModel):
    user_text: str
    order_id: str = ""
    session_id: str = ""
    evidence: list[str] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/graph/invoke")
def invoke_graph(request: GraphInvokeRequest) -> dict[str, Any]:
    state = GRAPH.invoke(
        request.model_dump(),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    url = trace_url()
    if url:
        state["langfuse_trace_url"] = url
    return state

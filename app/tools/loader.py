from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from .errors import DataSourceError

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    p = DATA_DIR / name
    if not p.exists():
        raise DataSourceError(f"Mock 数据缺失: {p}")
    return json.loads(p.read_text(encoding="utf-8"))

def load_orders() -> list[dict]: return _load("mock_orders.json")["orders"]
def load_shipments() -> list[dict]: return _load("mock_logistics.json")["shipments"]
def load_batches() -> list[dict]: return _load("mock_batches.json")["batches"]
def load_policies() -> list[dict]: return _load("mock_policies.json")["policies"]
from __future__ import annotations

import json
import sys

from app.agents.router import route
from app.llm import flush, trace_url


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text = " ".join(args).strip()

    decision = route(text)
    print(json.dumps(decision.model_dump(exclude_none=True), ensure_ascii=False, indent=2))

    flush()
    url = trace_url()
    if url:
        print(f"[langfuse] {url}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

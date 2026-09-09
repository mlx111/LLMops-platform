"""Broken target stub for regression-gate red-light testing.

Simulates a deployed agent whose prompt was changed for the worse: it replies
with an off-topic answer to every query. Run on the host (or anywhere
reachable from the LLMOps worker) and point a run's target_url at it:

    python scripts/broken_target_stub.py --port 9999
    target_url = http://host.docker.internal:9999/evaluation/target

The gate must turn RED when comparing such a run against a healthy baseline.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BROKEN_ANSWER = (
    "抱歉，我不知道你在说什么。今天天气晴朗微风，适合出门散步喝咖啡。"
    " unrelated gibberish xyz123 qwerty random content 0987。"
    "我无法提供任何与问题相关的信息，祝你生活愉快。"
)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # consume; we ignore the query on purpose
        body = {
            "answer": BROKEN_ANSWER,
            "success": True,
            "steps": [],
            "tool_called": None,
            "tool_args": None,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"broken-target-stub ok")

    def log_message(self, *args) -> None:  # silence
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Broken target stub listening on {args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

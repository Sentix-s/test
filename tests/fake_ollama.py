"""A real HTTP server that speaks the subset of the Ollama API JARVIS uses.

Chat replies are scripted: each call to /api/chat pops the next "turn"
(a list of message dicts streamed as NDJSON lines, the last one with
done=true). Every request body is recorded for assertions.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def text_turn(*pieces: str) -> list[dict[str, Any]]:
    return [{"role": "assistant", "content": p} for p in pieces]


def tool_turn(name: str, arguments: dict[str, Any], content: str = "") -> list[dict[str, Any]]:
    return [{"role": "assistant", "content": content,
             "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}]


class FakeOllama:
    def __init__(self, models: list[str] | None = None, chunk_delay: float = 0.0) -> None:
        self.models = models if models is not None else ["qwen3:8b"]
        self.loaded: set[str] = set()
        self.turns: list[list[dict[str, Any]]] = []
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.chunk_delay = chunk_delay
        self.version = "0.12.0"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a: Any) -> None:
                pass

            def _json(self, code: int, obj: Any) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                fake.requests.append((self.path, {}))
                if self.path == "/api/version":
                    self._json(200, {"version": fake.version})
                elif self.path == "/api/tags":
                    self._json(200, {"models": [{"name": m, "size": 5_200_000_000} for m in fake.models]})
                elif self.path == "/api/ps":
                    self._json(200, {"models": [{"name": m, "size_vram": 6 * 2**30} for m in sorted(fake.loaded)]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append((self.path, body))
                model = body.get("model", "")
                if self.path in ("/api/chat", "/api/generate") and model not in fake.models:
                    self._json(404, {"error": f"model '{model}' not found"})
                    return
                if self.path == "/api/generate":
                    if body.get("keep_alive") == 0:
                        fake.loaded.discard(model)
                    else:
                        fake.loaded.add(model)
                    self._json(200, {"model": model, "response": "", "done": True})
                elif self.path == "/api/chat":
                    fake.loaded.add(model)
                    turn = fake.turns.pop(0) if fake.turns else text_turn("(no scripted reply)")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        for msg in turn:
                            self._chunk({"model": model, "message": msg, "done": False})
                            if fake.chunk_delay:
                                time.sleep(fake.chunk_delay)
                        self._chunk({"model": model, "message": {"role": "assistant", "content": ""},
                                     "done": True, "done_reason": "stop", "eval_count": 42})
                        self.wfile.write(b"0\r\n\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                elif self.path == "/api/pull":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for d in ({"status": "pulling manifest"},
                              {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 50},
                              {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 100},
                              {"status": "success"}):
                        self._chunk(d)
                    self.wfile.write(b"0\r\n\r\n")
                    fake.models.append(body["model"])
                else:
                    self._json(404, {"error": "not found"})

            def _chunk(self, obj: dict[str, Any]) -> None:
                data = (json.dumps(obj) + "\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

        class Server(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                pass  # client hung up mid-stream (cancellation tests)

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> "FakeOllama":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def chat_requests(self) -> list[dict[str, Any]]:
        return [b for p, b in self.requests if p == "/api/chat"]

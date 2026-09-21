"""HTTP 服务器与静态资源（仅标准库 ``http.server``）。

* 每个请求独立线程（``ThreadingHTTPServer``），因此「长轮询等待日志」不会阻塞其他请求。
* 渲染任务本身在后台线程 + 全局渲染锁里排队，**并发提交、串行出图**。
* 实时日志用 SSE（``text/event-stream``，分块传输）即时推送；
  客户端不支持 SSE 时可退回 ``GET /api/render/<任务>?cursor=N&wait=20`` 长轮询。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from . import __version__, routes, task_runner

SSE_PING_SECONDS = 15.0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"DrawClimateChartWeb/{__version__}"
    sys_version = ""

    # ---- 请求入口 ----
    def do_GET(self) -> None:      # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:     # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:   # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.path.startswith("/api/render/") and "/events" in self.path:
            return
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    # ---- 分发 ----
    def _handle(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        body: Optional[Any] = None
        if method in ("POST", "DELETE"):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8-sig"))
                except (ValueError, UnicodeDecodeError):
                    self._send(routes.json_response({"error": "请求体不是合法 JSON"}, status=400))
                    return

        try:
            result = routes.dispatch(method, path, query, body,
                                     dict(self.headers.items()))
        except Exception as exc:  # noqa: BLE001 - 一律转成 JSON 错误
            result = routes.to_response(exc)

        if isinstance(result, routes.EventStream):
            self._stream(result)
        else:
            self._send(result)

    # ---- 输出 ----
    def _send(self, response: routes.Response) -> None:
        try:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD" and response.body:
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _stream(self, stream: routes.EventStream) -> None:
        """以分块传输推送 SSE；客户端断开即结束。"""
        try:
            self.send_response(200)
            self.send_header("Content-Type", stream.content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            return

        task = stream.task
        cursor = stream.cursor
        last_ping = time.time()

        def emit(payload: dict[str, Any]) -> bool:
            data = ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")
            frame = f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n"
            self.wfile.write(frame)
            self.wfile.flush()
            return True

        try:
            snapshot = task.snapshot(cursor)
            emit({"type": "snapshot", **snapshot})
            cursor = snapshot["cursor"]
            while True:
                entries, cursor, status = task.wait_for_logs(cursor, timeout=SSE_PING_SECONDS)
                snapshot = task.snapshot(cursor)
                snapshot["logs"] = entries
                snapshot["cursor"] = cursor
                snapshot["status"] = status
                emit({"type": "update", **snapshot})
                if status in ("done", "error", "cancelled") and not entries:
                    break
                if not entries and time.time() - last_ping >= SSE_PING_SECONDS:
                    emit({"type": "ping", "t": round(time.time(), 3), "cursor": cursor})
                    last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            # 分块传输必须显式写终止块，否则客户端会认为响应被截断
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          port_retries: int = 10) -> int:
    """启动服务（阻塞）；返回实际使用的端口。

    端口被占用时自动顺延尝试后续端口，避免「默认端口恰好被别的程序占了」导致直接启动失败。
    """
    httpd: Optional[Server] = None
    chosen = port
    last_error: Optional[OSError] = None
    for offset in range(max(1, port_retries)):
        candidate = port + offset
        try:
            httpd = Server((host, candidate), Handler)
        except OSError as exc:
            last_error = exc
            continue
        chosen = candidate
        if offset:
            print(f"  端口 {port} 已被占用，改用 {chosen}")
        break

    if httpd is None:
        raise last_error if last_error else OSError("无法绑定端口")

    url = f"http://{host}:{chosen}/"
    print(f"DrawClimateChartWeb v{__version__}")
    print(f"  网页地址：{url}")
    print("  配置目录：config/custom（内置 config/profiles.yaml 只读）")
    print("  按 Ctrl+C 退出")
    if open_browser:
        threading.Timer(0.8, lambda: _open_browser(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        _shutdown_tasks()
    return chosen


def _shutdown_tasks() -> None:
    for task in task_runner.list_tasks():
        if task.status in ("pending", "running"):
            task.cancel_event.set()


def _open_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 打不开浏览器不影响服务
        pass

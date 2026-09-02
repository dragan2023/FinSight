import secrets
import tempfile
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .settings import Settings

# 仅附件上传路由允许受限 multipart（计划 04 §2.5）：会话/来源校验不变，
# 请求体按实际流字节计数落盘 spool（内存上限 1 MiB，其余进临时文件），
# 超过硬上限直接 413，绝不把现有全局 JSON/32 KiB 限制全部关闭。
MULTIPART_UPLOAD_PATHS = frozenset({"/api/supplements/files"})
_SPOOL_MEMORY_BYTES = 1024 * 1024
UPLOAD_REPLAY_CHUNK_BYTES = 262144


def error_response(category: str, message: str, code: int, request_id: str, retryable: bool = False):
    return JSONResponse({"error": {"category": category, "message": message,
                                  "retryable": retryable, "request_id": request_id}}, status_code=code)


class LocalSecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        headers = Headers(scope=scope)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"x-request-id", request_id.encode()), (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                ])
            await send(message)

        async def reject(category: str, message: str, code: int):
            await error_response(category, message, code, request_id)(scope, receive, secure_send)

        origin = headers.get("origin")
        if (origin and origin not in self.settings.allowed_origins) or headers.get("sec-fetch-site") == "cross-site":
            await reject("origin_denied", "此来源不允许访问本机应用。", 403)
            return
        path, method = scope["path"], scope["method"]
        if path == "/api/system/session" and headers.get("x-finsight-client") != "web":
            await reject("session_required", "请通过本机应用建立会话。", 403)
            return
        if path.startswith("/api/") and path not in {"/api/system/session", "/api/system/status"} and method != "OPTIONS":
            token = headers.get("x-finsight-session", "")
            if not secrets.compare_digest(token, scope["app"].state.session_token):
                await reject("session_expired", "本机会话已失效，请重试或刷新页面。", 403)
                return
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "multipart/form-data":
                if path not in MULTIPART_UPLOAD_PATHS:
                    await reject("json_required", "写操作只接受 application/json。", 415)
                    return
                spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES)
                length = 0
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        spool.close()
                        return
                    data = event.get("body", b"")
                    length += len(data)
                    if length > self.settings.attachment_request_cap_bytes:
                        spool.close()
                        await reject("payload_too_large", "上传内容超过单次请求上限。", 413)
                        return
                    if data:
                        spool.write(data)
                    if not event.get("more_body", False):
                        break
                spool.seek(0)

                async def spooled_receive():
                    chunk = spool.read(UPLOAD_REPLAY_CHUNK_BYTES)
                    if chunk:
                        return {"type": "http.request", "body": chunk, "more_body": True}
                    return {"type": "http.request", "body": b"", "more_body": False}

                try:
                    await self.app(scope, spooled_receive, secure_send)
                finally:
                    spool.close()
                return
            if content_type != "application/json":
                await reject("json_required", "写操作只接受 application/json。", 415)
                return
            # Bound buffering before validation so credential fields cannot be abused for huge bodies.
            chunks = []
            length = 0
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    return
                data = event.get("body", b"")
                length += len(data)
                if length > 32768:
                    await reject("payload_too_large", "请求内容过大。", 413)
                    return
                chunks.append(data)
                if not event.get("more_body", False):
                    break
            body = b"".join(chunks)
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, secure_send)
        else:
            await self.app(scope, receive, secure_send)

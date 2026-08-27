"""Bound multipart upload size before Starlette writes any temporary file."""
from starlette.responses import JSONResponse


class StudyPhotoBodyLimit:
    LIMIT = 5 * 1024 * 1024 + 64 * 1024

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "POST" or not (
            path.startswith("/api/v1/study-meetings/") and path.endswith("/evidence")
        ):
            return await self.app(scope, receive, send)
        rejected = JSONResponse({"detail": "合影不能超过5MB"}, status_code=413)
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > self.LIMIT:
                return await rejected(scope, receive, send)
        except ValueError:
            return await rejected(scope, receive, send)
        # Also bound chunked/misreported bodies, not just Content-Length.
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.LIMIT:
                return await rejected(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)

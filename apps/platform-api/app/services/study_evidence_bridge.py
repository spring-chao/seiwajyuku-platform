"""Existing server token obtains one 90-second object request from CloudBase."""
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BridgeObjectError(ValueError):
    """Retain an object HTTP status without retaining its signed URL."""

    def __init__(self, status):
        super().__init__("private object request failed")
        self.status_code = status


class StorageBridge:
    def __init__(self, url, token, bucket, region):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname !=
                "shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com"
                or parsed.path != "/study-evidence-storage" or parsed.port not in {None, 443}
                or parsed.username or parsed.password or parsed.query or parsed.fragment or len(token) < 32):
            raise ValueError("invalid storage bridge configuration")
        self.url, self.token = url, token
        self.host = f"{bucket}.cos.{region}.myqcloud.com"
        self.opener = build_opener(NoRedirect())

    def request(self, method, key, content=None, content_type=None):
        payload = {"method": method, "key": key}
        if method == "PUT":
            payload["content_type"] = content_type
        request = Request(self.url, data=json.dumps(payload).encode(), method="POST", headers={
            "Content-Type": "application/json", "X-Study-Evidence-Cleanup-Token": self.token})
        # A gateway 404 must never count as a deleted COS object. Do not retain
        # provider response bodies or URLs in storage exceptions.
        try:
            with self.opener.open(request, timeout=15) as response:
                signed = json.loads(response.read(8193))
        except Exception:
            raise ValueError("storage bridge unavailable") from None
        if not isinstance(signed, dict):
            raise ValueError("invalid object grant")
        parsed = urlsplit(signed.get("url", ""))
        if (parsed.scheme != "https" or parsed.netloc != self.host or parsed.path != "/" + key
                or parsed.username or parsed.password or parsed.fragment or signed.get("expires_in") != 90):
            raise ValueError("invalid object grant")
        headers = signed.get("headers", {})
        expected = {"content-type": content_type, "x-cos-acl": "private", "if-none-match": "*"} if method == "PUT" else {}
        if headers != expected:
            raise ValueError("invalid private object headers")
        try:
            with self.opener.open(Request(signed["url"], data=content, method=method, headers=headers), timeout=15) as response:
                return response.read(5 * 1024 * 1024 + 1) if method == "GET" else b""
        except HTTPError as error:
            raise BridgeObjectError(error.code) from None
        except Exception:
            raise ValueError("private object request unavailable") from None

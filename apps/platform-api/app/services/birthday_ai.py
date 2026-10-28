"""Bounded CloudBase copy generation using anonymous, verified context only."""
from __future__ import annotations

import json
import os
import re

import httpx


def generate_body(*, years: int | None, participation: str | None, tone: str) -> dict:
    enabled = os.getenv("BIRTHDAY_AI_ENABLED", "false").lower() == "true"
    env_id = os.getenv("CLOUDBASE_AI_ENV_ID", "")
    key = os.getenv("CLOUDBASE_AI_API_KEY", "")
    model = os.getenv("BIRTHDAY_AI_MODEL", "hy3")
    if not enabled:
        return {"status": "DISABLED"}
    if not re.fullmatch(r"[a-z0-9-]{3,100}", env_id) or not key or model not in {"deepseek-v4-flash", "hy3"}:
        return {"status": "CONFIGURATION_REQUIRED"}
    # Neither names, identifiers, exact dates, raw memories nor care notes leave
    # the platform. The caller supplies only an allowlisted participation theme.
    profile = "入塾时间未完善" if years is None else "新加入的学长" if years < 1 else "长期同行的学长"
    prompt = {
        "profile": profile,
        "verified_participation_theme": participation,
        "tone": {"warm": "温暖自然", "standard": "真诚得体", "concise": "简洁"}[tone],
    }
    try:
        response = httpx.post(
            f"https://{env_id}.api.tcloudbasegateway.com/v1/ai/cloudbase/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "max_tokens": 480, "thinking": {"type": "disabled"}, "messages": [
                {"role": "system", "content": "你为苏州盛和塾起草微信生日祝福正文。只输出正文，80至200个汉字，分2至3段。不要称呼、署名、网址、数字或markdown。根据匿名资料调整表达。仅允许泛指资料中已核实的参与主题；资料为空时只写问候。禁止编造经历、频率、成绩、职位、性别、家庭状况、困难、承诺或引文。不得评价出勤或劝学。祝愿可表达健康、从容与生活欢喜。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]}, timeout=httpx.Timeout(12.0, connect=4.0), follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
        if data["choices"][0].get("finish_reason") == "length":
            return {"status": "OUTPUT_REJECTED"}
        body = str(data["choices"][0]["message"]["content"]).strip()
        if not 30 <= len(body) <= 500 or re.search(r"[0-9]|https?://|<|>|```|——", body):
            return {"status": "OUTPUT_REJECTED"}
        usage = data.get("usage") or {}
        return {"status": "GENERATED", "body": body, "model": model,
                "total_tokens": max(0, int(usage.get("total_tokens") or 0))}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        # Provider messages can echo credentials or inputs: never persist them.
        return {"status": "PROVIDER_UNAVAILABLE"}

"""One-shot real-key verification: maximum output parameters + short reply.

计划阶段 01 验收可选项：用用户本机已保存的真实 Key，通过阶段 01 的完整
输出预算链路（documented limits -> completion_options）发送一次
「最大输出参数 + 短回复提示」请求，验证服务商真实接受目录中的最大参数。

只输出非敏感事实（服务商、策略、参数、finish、用量、回复片段）；
绝不打印 API Key、完整头部或上游原始异常。
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from backend.app.agent.model_factory import ModelFactory, generation_timeout
from backend.app.agent.provider_requests import chat_messages, completion_options
from backend.app.credentials import CredentialRoot
from backend.app.model_config.models import ChatInput
from backend.app.model_config.repository import ModelRepository
from backend.app.model_config.service import ModelService
from backend.app.settings import load_settings
from backend.app.storage import Storage


async def verify(profile_id: str | None) -> int:
    settings = load_settings(PROJECT_ROOT)
    storage = Storage(settings.db_path)
    storage.initialize()
    root = CredentialRoot(storage, settings)
    service = ModelService(ModelRepository(storage), root, settings)
    views = service.list()
    if profile_id is None:
        target = next((view for view in views if view.active), None)
    else:
        target = next((view for view in views if view.id == profile_id), None)
    if target is None:
        raise SystemExit("no matching active model profile")
    profile = service.resolve(target.id)
    data = ChatInput(prompt="请只用不超过20个字回答：1+1等于几？", confirm_external=True)
    options = completion_options(profile, chat_messages(profile, data))
    budget = options.pop("_output_budget")
    print("profile:", profile.provider, "/", profile.model, "/", profile.output_policy)
    print("request-params:", json.dumps(
        {key: value for key, value in options.items() if key != "messages"},
        ensure_ascii=False, default=str))
    print("budget:", json.dumps(dataclasses.asdict(budget), ensure_ascii=False, default=str))
    factory = ModelFactory(service, httpx.AsyncClient(timeout=None, trust_env=False))
    try:
        result = await factory.chat(data, target.id)
    finally:
        await factory.http.aclose()
    print("finish:", "truncated" if result.truncated else "complete")
    print("reply:", result.content[:120])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id", default=None, help="默认使用当前活动配置")
    try:
        return asyncio.run(verify(parser.parse_args().profile_id))
    except Exception as error:  # noqa: BLE001 - 只打印类别与安全信息
        category = getattr(error, "category", type(error).__name__)
        print("verification-failed:", category, "-", getattr(error, "message", str(error)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

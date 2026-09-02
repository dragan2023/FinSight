"""Curated official API contracts, reviewed 2026-08-31. Not an account model listing.

Limits carry the vendors' documented values. live_output_verified stays false
until the optional real-key check in the acceptance list passes for a model;
documented numbers are never presented as live-tested facts.
"""

PROVIDERS = [
    {
        "id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash-vision-exp",
        "description": "最新 Vision Exp · 图像理解、思考开关与推理强度；实验版本可能调整。",
        "docs_url": "https://api-docs.deepseek.com/guides/thinking_mode/",
        "models": [{"id": "deepseek-v4-flash-vision-exp", "name": "DeepSeek V4 Flash Vision Exp · 最新实验版",
                    "thinking": True, "efforts": ["low", "high", "max"], "budget": False,
                    "modalities": ["image"], "stream_required": False,
                    "context": 1048576, "max_output": 393216, "output_param": "max_tokens",
                    "live_output_verified": False,
                    "limits_source": "https://api-docs.deepseek.com/quick_start/pricing/"}] + [
            {"id": model, "name": label, "thinking": True, "efforts": ["low", "high", "max"],
             "budget": False, "modalities": [], "stream_required": False,
             "context": 1048576, "max_output": 393216, "output_param": "max_tokens",
             "live_output_verified": False,
             "limits_source": "https://api-docs.deepseek.com/quick_start/pricing/"}
            for model, label in [("deepseek-v4-flash", "DeepSeek V4 Flash"), ("deepseek-v4-pro", "DeepSeek V4 Pro")]
        ],
    },
    {
        "id": "glm", "name": "GLM · 智谱", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.3-flash",
        "description": "最新 GLM-5.3 Flash · 原生图像 / 视频；仅支持开启思考。通用 API，非 Coding Plan。",
        "docs_url": "https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash",
        "models": [
            {"id": "glm-5.3-flash", "name": "GLM-5.3 Flash · 最新原生多模态", "thinking": True,
             "thinking_required": True, "efforts": ["low", "high", "max"],
             "budget": False, "modalities": ["image", "video"], "stream_required": False,
             "context": None, "max_output": 131072, "output_param": "max_tokens",
             "live_output_verified": True,  # 2026-08-31 real-key check: max_tokens=131072 accepted, short reply completed.
             "limits_source": "https://docs.bigmodel.cn/api-reference/模型-api/对话补全"},
            {"id": "glm-5.3", "name": "GLM-5.3 · 旗舰文本", "thinking": True, "thinking_required": True,
             "efforts": ["low", "high", "max"], "budget": False, "modalities": [], "stream_required": False,
             "context": None, "max_output": 131072, "output_param": "max_tokens",
             "live_output_verified": False,
             "limits_source": "https://docs.bigmodel.cn/api-reference/模型-api/对话补全"},
        ],
    },
    {
        "id": "qwen", "name": "Qwen · 通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen3.8-flash",
        "description": "百炼官方 API · 默认北京地域；密钥与模型权限须匹配所选地域。",
        "docs_url": "https://help.aliyun.com/zh/model-studio/deep-thinking",
        "models": [
            {"id": "qwen3.8-flash", "name": "Qwen3.8 Flash · 最新思考 / 视觉", "thinking": True, "efforts": [],
             "budget": True, "modalities": ["image", "video"], "stream_required": False,
             "context": 1000000, "max_output": 131072, "output_param": "max_completion_tokens",
             "live_output_verified": False,
             "limits_source": "https://help.aliyun.com/zh/model-studio/qwen3-8-flash"},
            {"id": "qwen3.8-max", "name": "Qwen3.8 Max · 旗舰思考 / 视觉", "thinking": True, "efforts": [],
             "budget": True, "modalities": ["image", "video"], "stream_required": False,
             "context": 1000000, "max_output": 131072, "output_param": "max_completion_tokens",
             "live_output_verified": False,
             "limits_source": "https://help.aliyun.com/zh/model-studio/qwen3-8-max"},
            {"id": "qwen3.5-omni-plus", "name": "Qwen3.5 Omni Plus · 全模态", "thinking": False, "efforts": [],
             "budget": False, "modalities": ["image", "video", "audio"], "stream_required": True,
             "context": 262144, "input_cap": 196608, "max_output": 65536, "output_param": "max_tokens",
             "live_output_verified": False,
             "limits_source": "https://help.aliyun.com/zh/model-studio/qwen3-5-omni-plus"},
        ],
    },
]


def provider_spec(provider: str) -> dict | None:
    return next((item for item in PROVIDERS if item["id"] == provider), None)


def model_spec(provider: str, model: str) -> dict | None:
    spec = provider_spec(provider)
    return next((item for item in spec["models"] if item["id"] == model), None) if spec else None


def official_url(provider: str, region: str = "beijing", workspace_id: str = "") -> str:
    spec = provider_spec(provider)
    if provider == "qwen":
        if workspace_id:
            area = "cn-beijing" if region == "beijing" else "ap-southeast-1"
            return f"https://{workspace_id}.{area}.maas.aliyuncs.com/compatible-mode/v1"
        host = "dashscope" if region == "beijing" else "dashscope-intl"
        return f"https://{host}.aliyuncs.com/compatible-mode/v1"
    return spec["base_url"] if spec else ""

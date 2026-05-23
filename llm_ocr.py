import os
import base64
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def llm_ocr(image_path: str) -> Optional[dict]:
    """Optional LLM-based OCR. Falls back to None if no API key configured."""
    opencode_go_key = os.getenv("OPENCODE_GO_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    kimi_key = os.getenv("KIMI_API_KEY")

    if not opencode_go_key and not openai_key and not openrouter_key and not kimi_key:
        return None

    base64_image = _encode_image(image_path)
    prompt = (
        "Read the numbers from this gym machine screen. "
        "Return ONLY a JSON object: {\"duration_min\": float, \"calories\": int, \"distance\": float}. "
        "If a value is missing, omit it. Do not explain."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    },
                },
            ],
        }
    ]

    try:
        if opencode_go_key:
            import requests
            base_url = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {opencode_go_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("LLM_OCR_MODEL", "kimi-k2.6"),
                    "messages": messages,
                    "max_tokens": 4000,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        elif kimi_key:
            import requests
            resp = requests.post(
                "https://api.moonshot.cn/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {kimi_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("LLM_OCR_MODEL", "kimi-k2-6"),
                    "messages": messages,
                    "max_tokens": 4000,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        elif openrouter_key:
            import requests
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("LLM_OCR_MODEL", "moonshotai/kimi-k2.6"),
                    "messages": messages,
                    "max_tokens": 4000,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        elif openai_key:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model=os.getenv("LLM_OCR_MODEL", "gpt-4o-mini"),
                messages=messages,
                max_tokens=4000,
            )
            content = resp.choices[0].message.content
        else:
            return None

        # Strip markdown fences
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content.rsplit("\n", 1)[0]
        content = content.strip()

        data = json.loads(content)
        result = {}
        if data.get("duration_min") is not None:
            result["duration_min"] = float(data["duration_min"])
        if data.get("calories") is not None:
            result["calories"] = int(data["calories"])
        if data.get("distance") is not None:
            result["distance"] = float(data["distance"])
        logger.info("LLM OCR result: %s", result)
        return result
    except Exception as e:
        logger.error("LLM OCR failed: %s", e)
        return None

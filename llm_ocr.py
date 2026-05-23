import os
import base64
import json
import logging
import time
from typing import Optional
from PIL import Image
import io
import requests

logger = logging.getLogger(__name__)

# Reuse TCP connection across calls
_HTTP_SESSION = requests.Session()


def _encode_image(path: str, max_width: int = 384) -> str:
    img = Image.open(path)
    w, h = img.size
    # Auto-crop phone screenshots: if image is tall, crop to center 45% where the machine is
    if h > w * 1.5:
        top = int(h * 0.28)
        bottom = int(h * 0.72)
        left = int(w * 0.05)
        right = int(w * 0.95)
        img = img.crop((left, top, right, bottom))
        w, h = img.size
    if w > max_width:
        ratio = max_width / w
        img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _is_valid(result: dict) -> bool:
    if not result:
        return False
    calories = result.get("calories")
    duration = result.get("duration_min")
    distance = result.get("distance")
    has_min_fields = sum(v is not None for v in [calories, duration, distance]) >= 2
    if calories is not None and not (0 < calories <= 2000):
        return False
    if duration is not None and not (0 < duration <= 300):
        return False
    if distance is not None and not (0 < distance <= 50):
        return False
    return has_min_fields


def _post_with_retry(url, headers, json_payload, timeout=30, max_retries=1):
    resp = None
    for attempt in range(max_retries + 1):
        try:
            resp = _HTTP_SESSION.post(url, headers=headers, json=json_payload, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                logger.warning("LLM OCR timeout (attempt %d), retrying in 2s...", attempt + 1)
                time.sleep(2)
            else:
                raise
        except requests.exceptions.HTTPError as e:
            if resp is not None and resp.status_code >= 400 and resp.status_code < 500:
                raise
            if attempt < max_retries:
                logger.warning("LLM OCR HTTP error, retrying...")
                time.sleep(2)
            else:
                raise


def llm_ocr(image_path: str) -> Optional[dict]:
    """LLM-based OCR. Falls back to None if no API key configured or call fails."""
    opencode_go_key = os.getenv("OPENCODE_GO_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    kimi_key = os.getenv("KIMI_API_KEY")

    if not opencode_go_key and not openai_key and not openrouter_key and not kimi_key:
        return None

    base64_image = _encode_image(image_path)
    prompt = (
        "Read the numbers from this gym machine screen. "
        "Return ONLY a JSON object exactly like this: {\"duration_min\": 45.5, \"calories\": 420, \"distance\": 3.2}. "
        "If a value is missing, omit it. Do not explain or add markdown."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                },
            ],
        }
    ]

    try:
        if opencode_go_key:
            base_url = os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1")
            resp = _post_with_retry(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {opencode_go_key}",
                    "Content-Type": "application/json",
                },
                json_payload={
                    "model": os.getenv("LLM_OCR_MODEL", "qwen3.6-plus"),
                    "messages": messages,
                    "max_tokens": 150,
                },
                timeout=20,
            )
            content = resp.json()["choices"][0]["message"]["content"]
        elif kimi_key:
            resp = _post_with_retry(
                "https://api.moonshot.cn/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {kimi_key}",
                    "Content-Type": "application/json",
                },
                json_payload={
                    "model": os.getenv("LLM_OCR_MODEL", "kimi-k2-6"),
                    "messages": messages,
                    "max_tokens": 150,
                },
                timeout=20,
            )
            content = resp.json()["choices"][0]["message"]["content"]
        elif openrouter_key:
            resp = _post_with_retry(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                json_payload={
                    "model": os.getenv("LLM_OCR_MODEL", "moonshotai/kimi-k2.6"),
                    "messages": messages,
                    "max_tokens": 150,
                },
                timeout=20,
            )
            content = resp.json()["choices"][0]["message"]["content"]
        elif openai_key:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            resp = client.chat.completions.create(
                model=os.getenv("LLM_OCR_MODEL", "gpt-4o-mini"),
                messages=messages,
                max_tokens=150,
            )
            content = resp.choices[0].message.content
        else:
            return None

        # Strip any accidental markdown
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

        if _is_valid(result):
            logger.info("LLM OCR result: %s", result)
            return result
        else:
            logger.warning("LLM OCR rejected invalid result: %s", result)
            return None
    except Exception as e:
        logger.error("LLM OCR failed: %s", e)
        return None

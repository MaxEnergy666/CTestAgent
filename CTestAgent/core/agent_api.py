"""Agent-level API integration for configurable LLM providers.

This module provides:
- A single key-file loader for six agents.
- A resilient HTTP client for Anthropic/OpenAI-compatible endpoints.
- A JSON-first advisor interface used by each agent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY_FILE = PROJECT_ROOT / "agent_api_keys.yaml"


ROLE_PROMPTS = {
    "Coordinator": (
        "You are the Coordinator API advisor for a multi-agent C testing system. "
        "Focus on phase transition, strategy weight balancing, and function focus planning."
    ),
    "Analyzer": (
        "You are the Analyzer API advisor for static risk analysis. "
        "Focus on risk ranking, high-risk API prioritization, and parameter constraints hints."
    ),
    "Generator": (
        "You are the Generator API advisor. "
        "Generate concrete high-value test inputs and propose strategy weight hints."
    ),
    "Executor": (
        "You are the Executor API advisor. "
        "Classify crash symptoms and suggest concise triage labels from runtime stderr/stdout."
    ),
    "Reviewer": (
        "You are the Reviewer API advisor. "
        "Provide quality review hints, false-positive reduction suggestions, and coverage-gap advice."
    ),
    "Reporter": (
        "You are the Reporter API advisor. "
        "Summarize defects and provide concise vulnerability descriptions for final reports."
    ),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_agent_key_name(agent_name: str) -> str:
    return re.sub(r"\W+", "_", agent_name).upper()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_endpoint(provider: str, endpoint: str) -> str:
    ep = str(endpoint or "").strip()
    if not ep:
        return ep

    provider_lc = provider.lower().strip()
    is_openai_style = provider_lc in {"openai", "openai-compatible", "openrouter"}
    if is_openai_style:
        return ep

    # anthropic 风格：第三方中转常给 base url，这里自动补齐 /v1/messages
    if re.search(r"/v1/messages/?$", ep, flags=re.IGNORECASE):
        return ep.rstrip("/")
    if re.search(r"/v1/?$", ep, flags=re.IGNORECASE):
        return ep.rstrip("/") + "/messages"
    return ep.rstrip("/") + "/v1/messages"


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    # direct parse
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # fenced block parse
    fence_matches = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, flags=re.IGNORECASE)
    for block in fence_matches:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    # first {...} slice
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        block = raw[start : end + 1]
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                return data
        except Exception:
            return None

    return None


def _to_jsonable(value: Any) -> Any:
    """将任意对象尽量转换为可 JSON 序列化结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]

    if hasattr(value, "__dict__"):
        payload = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        return _to_jsonable(payload)

    return str(value)


def normalize_weights(weights: dict[str, Any], allowed_keys: set[str] | None = None) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in (weights or {}).items():
        if allowed_keys is not None and key not in allowed_keys:
            continue
        val = max(0.0, _safe_float(value, 0.0))
        if val > 0:
            cleaned[str(key)] = val

    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in cleaned.items()}


class _HttpModelClient:
    def __init__(
        self,
        provider: str,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        logger: logging.Logger,
    ):
        self.provider = provider
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.logger = logger

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 700, temperature: float = 0.2) -> str | None:
        payload: dict[str, Any]
        headers: dict[str, str]

        provider = self.provider.lower().strip()
        if provider in {"openai", "openai-compatible", "openrouter"}:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        else:
            # Anthropic-compatible by default
            payload = {
                "model": self.model,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }

        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(e)
            self.logger.warning("API request failed for %s: %s", self.model, detail[:600])
            return None
        except Exception as e:
            err_text = str(e).lower()
            if "timed out" in err_text or "timeout" in err_text:
                self.logger.debug("API request timeout for %s after %ss", self.model, self.timeout_seconds)
            else:
                self.logger.warning("API request exception for %s: %s", self.model, e)
            return None

        try:
            data = json.loads(raw)
        except Exception:
            self.logger.warning("API response is not valid JSON: %s", raw[:400])
            return None

        # openai-compatible format
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = (choices[0] or {}).get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content

        # anthropic format
        content = data.get("content")
        if isinstance(content, list):
            chunks: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    chunks.append(str(block.get("text", "")))
            if chunks:
                return "\n".join(chunks)

        # fallback
        if isinstance(data.get("output_text"), str):
            return str(data["output_text"])

        return None


class AgentAPIAdvisor:
    def __init__(
        self,
        agent_name: str,
        enabled: bool,
        role_prompt: str,
        client: _HttpModelClient | None,
        logger: logging.Logger,
    ):
        self.agent_name = agent_name
        self.enabled = enabled and client is not None
        self.role_prompt = role_prompt
        self.client = client
        self.logger = logger

    @classmethod
    def from_config(cls, agent_name: str, agent_config: dict[str, Any] | None, logger: logging.Logger):
        cfg = dict(agent_config or {})
        api_cfg = dict(cfg.get("api") or {})

        key_file = str(api_cfg.get("key_file") or DEFAULT_KEY_FILE)
        key_bundle = {}
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
                if isinstance(loaded, dict):
                    key_bundle = loaded
        except FileNotFoundError:
            key_bundle = {}
        except Exception as e:
            logger.warning("读取 API key 文件失败 (%s): %s", key_file, e)

        enabled = bool(api_cfg.get("enabled", True))
        provider = str(api_cfg.get("provider") or key_bundle.get("provider") or "anthropic")
        provider_lc = provider.lower().strip()
        is_openai_style = provider_lc in {"openai", "openai-compatible", "openrouter"}
        default_anthropic_endpoint = "https://api.anthropic.com/v1/messages"
        env_openai_base = os.getenv("OPENAI_BASE_URL", "").strip()
        env_anthropic_base = os.getenv("ANTHROPIC_BASE_URL", "").strip()
        file_openai_base = _first_non_empty(key_bundle.get("base_url"), key_bundle.get("openai_base_url"))
        file_anthropic_base = _first_non_empty(
            key_bundle.get("base_url"),
            key_bundle.get("anthropic_base_url"),
        )

        raw_endpoint = _first_non_empty(
            api_cfg.get("endpoint"),
            key_bundle.get("endpoint"),
        )
        if is_openai_style:
            raw_endpoint = _first_non_empty(raw_endpoint, file_openai_base, env_openai_base)
        else:
            # 若配置仍是默认官方 endpoint，允许用 ANTHROPIC_BASE_URL 覆盖到中转站。
            if file_anthropic_base and (not raw_endpoint or str(raw_endpoint).strip() == default_anthropic_endpoint):
                raw_endpoint = file_anthropic_base
            if env_anthropic_base and (not raw_endpoint or str(raw_endpoint).strip() == default_anthropic_endpoint):
                raw_endpoint = env_anthropic_base
            raw_endpoint = _first_non_empty(raw_endpoint, file_anthropic_base, env_anthropic_base)

        raw_endpoint = _first_non_empty(
            raw_endpoint,
            "https://api.openai.com/v1/chat/completions" if is_openai_style else default_anthropic_endpoint,
        )
        endpoint = _normalize_endpoint(provider, raw_endpoint)

        model = str(api_cfg.get("model") or key_bundle.get("model") or "claude-haiku-4-5")
        timeout_seconds = int(api_cfg.get("timeout_seconds") or key_bundle.get("timeout_seconds") or 10)

        key_map = key_bundle.get("keys") if isinstance(key_bundle.get("keys"), dict) else {}
        env_name = f"CTESTAGENT_{_normalize_agent_key_name(agent_name)}_API_KEY"
        shared_file_key = _first_non_empty(
            key_bundle.get("auth_token"),
            key_bundle.get("api_key"),
            key_bundle.get("anthropic_auth_token"),
            key_map.get("default"),
            key_map.get("DEFAULT"),
        )
        shared_env_key = _first_non_empty(
            os.getenv("OPENAI_API_KEY") if is_openai_style else "",
            os.getenv("ANTHROPIC_AUTH_TOKEN") if not is_openai_style else "",
            os.getenv("ANTHROPIC_API_KEY") if not is_openai_style else "",
        )
        api_key = _first_non_empty(
            api_cfg.get("api_key"),
            key_map.get(agent_name),
            shared_file_key,
            os.getenv(env_name, ""),
            shared_env_key,
        )

        if not enabled:
            return cls(agent_name, False, ROLE_PROMPTS.get(agent_name, ""), None, logger)

        if not api_key:
            logger.info(
                "%s API增强未启用：未配置 key（文件: %s, 环境变量: %s / ANTHROPIC_AUTH_TOKEN）",
                agent_name,
                key_file,
                env_name,
            )
            return cls(agent_name, False, ROLE_PROMPTS.get(agent_name, ""), None, logger)

        client = _HttpModelClient(
            provider=provider,
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            logger=logger,
        )

        return cls(agent_name, True, ROLE_PROMPTS.get(agent_name, ""), client, logger)

    def ask_json(
        self,
        task: str,
        context: dict[str, Any],
        expected_schema: dict[str, Any],
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> dict[str, Any] | None:
        if not self.enabled or self.client is None:
            return None

        safe_context = _to_jsonable(context)
        safe_schema = _to_jsonable(expected_schema)

        user_payload = {
            "task": task,
            "context": safe_context,
            "expected_schema": safe_schema,
            "rules": [
                "Return strict JSON only.",
                "Do not include markdown/code fences.",
                "Keep fields concise and deterministic.",
            ],
        }

        user_prompt = json.dumps(user_payload, ensure_ascii=False)
        text = self.client.complete(
            system_prompt=self.role_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if not text:
            return None

        parsed = _extract_first_json_object(text)
        if parsed is None:
            self.logger.debug("%s API返回非JSON，已忽略: %s", self.agent_name, text[:300])
            return None

        return parsed

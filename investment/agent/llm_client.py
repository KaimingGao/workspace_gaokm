"""统一 LLM 调用客户端（HTTP + token 计量 + 简易 cache）。

封装：
- 多后端（trae/chat/glm 等）配置选择与降级
- prompt_tokens / completion_tokens / reasoning_tokens / calls 的累计计量
- chat/complete/image 调用的超时、重试、错误映射（对上层抛语义异常）
- 同 prompt 短窗口内的 LRU cache（仅 deterministic 温度下启用）
"""

import logging

logger = logging.getLogger(__name__)
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


def empty_usage() -> Dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "calls": 0,
    }


def parse_usage(response: Optional[Dict]) -> Dict[str, int]:
    """从 OpenAI 兼容响应中解析 usage；缺失则全 0。"""
    usage = empty_usage()
    if not response or not isinstance(response, dict):
        return usage
    raw = response.get("usage") or {}
    if not isinstance(raw, dict):
        return usage
    usage["prompt_tokens"] = int(raw.get("prompt_tokens") or 0)
    usage["completion_tokens"] = int(raw.get("completion_tokens") or 0)
    usage["total_tokens"] = int(
        raw.get("total_tokens")
        or (usage["prompt_tokens"] + usage["completion_tokens"])
    )
    details = raw.get("completion_tokens_details") or {}
    if isinstance(details, dict):
        usage["reasoning_tokens"] = int(details.get("reasoning_tokens") or 0)
    usage["calls"] = 1 if usage["total_tokens"] or raw else 0
    # 有 usage 对象但全 0 时仍计 1 次调用
    if raw and usage["calls"] == 0:
        usage["calls"] = 1
    return usage


def add_usage(dst: Dict[str, int], src: Dict[str, int]) -> Dict[str, int]:
    for key in empty_usage():
        dst[key] = int(dst.get(key) or 0) + int(src.get(key) or 0)
    return dst


def format_usage(usage: Dict[str, Any], prefix: str = "") -> str:
    """单行可读摘要。"""
    p = usage.get("prompt_tokens") or 0
    c = usage.get("completion_tokens") or 0
    t = usage.get("total_tokens") or 0
    r = usage.get("reasoning_tokens") or 0
    n = usage.get("calls") or 0
    parts = [f"prompt={p}", f"completion={c}", f"total={t}", f"calls={n}"]
    if r:
        parts.insert(2, f"reasoning={r}")
    body = " ".join(parts)
    return f"{prefix}{body}" if prefix else body


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


DEFAULT_QWEN_ENDPOINT = (
    "https://ws-7hpevbps1ivbjf03.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)


def _timeout_tuple(seconds: float) -> Tuple[float, float]:
    """(connect, read)。连接短、读取可配。"""
    connect = min(15.0, max(3.0, seconds * 0.25))
    return (connect, seconds)


def _friendly_request_error(exc: BaseException, *, kind: str = "调用") -> str:
    msg = str(exc)
    low = msg.lower()
    if "timed out" in low or "timeout" in low or "read timed out" in low:
        return (
            f"大模型{kind}超时：通义千问响应过慢或网络不稳定。"
            f"可增大环境变量 DASHSCOPE_TIMEOUT 后重试。（详情: {msg}）"
        )
    if "connection" in low or "nameresolution" in low or "failed to establish" in low:
        return f"大模型{kind}网络失败：无法连接通义千问服务。（详情: {msg}）"
    return f"大模型{kind}失败: {msg}"


def _is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg or "connection" in msg


def resolve_llm_model(model: Optional[str] = None) -> tuple[str, str]:
    """返回 (model_name, source)；source=arg|env|memory|default。"""
    from core.memory_store import resolve_llm_model_config

    cfg = resolve_llm_model_config(explicit=model)
    return str(cfg["llm_model"]), str(cfg["llm_model_source"])


class LLMClient:
    """阿里云百炼 / DashScope OpenAI 兼容客户端（默认 Qwen + 可选联网搜索）。"""

    def __init__(self, api_key: str = None, endpoint: str = None, model: str = None):
        # DASHSCOPE_* 优先；DOUBAO_* 仅作迁移期兼容
        self.api_key = api_key or _env_first("DASHSCOPE_API_KEY", "DOUBAO_API_KEY")
        self.endpoint = (
            endpoint
            or _env_first("DASHSCOPE_ENDPOINT", "DOUBAO_ENDPOINT", default=DEFAULT_QWEN_ENDPOINT)
        ).rstrip("/")
        resolved_model, model_source = resolve_llm_model(model)
        self.model = resolved_model
        self.model_source = model_source
        self.chat_timeout = _env_float(
            "DASHSCOPE_TIMEOUT", _env_float("DOUBAO_TIMEOUT", 180.0)
        )
        self.probe_timeout = _env_float(
            "DASHSCOPE_PROBE_TIMEOUT", _env_float("DOUBAO_PROBE_TIMEOUT", 30.0)
        )
        self.enable_search = _env_bool("DASHSCOPE_ENABLE_SEARCH", True)
        self.search_strategy = _env_first("DASHSCOPE_SEARCH_STRATEGY", default="max")
        self.search_freshness = _env_int("DASHSCOPE_SEARCH_FRESHNESS", 7)
        self._tested = False
        self._available = None
        self._last_error = None
        self.last_usage = empty_usage()
        self.session_usage = empty_usage()

    def get_last_error(self):
        return self._last_error

    def reset_usage(self) -> None:
        self.last_usage = empty_usage()
        self.session_usage = empty_usage()

    def get_session_usage(self) -> Dict[str, int]:
        return dict(self.session_usage)

    def get_last_usage(self) -> Dict[str, int]:
        return dict(self.last_usage)

    def _record_usage(self, response: Dict) -> Dict[str, int]:
        u = parse_usage(response)
        self.last_usage = u
        add_usage(self.session_usage, u)
        return u

    def is_available(self) -> bool:
        if not self.api_key:
            self._last_error = "DASHSCOPE_API_KEY 为空"
            return False
        # 成功可缓存；超时/网络失败不永久锁死，允许下次再探
        if self._tested and self._available:
            return True
        try:
            self._test_connection()
            self._tested = True
            self._available = True
            self._last_error = None
            return True
        except Exception as e:
            logger.exception('unexpected error in is_available')
            friendly = _friendly_request_error(e, kind="探测")
            self._last_error = friendly
            self._available = False
            if _is_transient_error(e):
                self._tested = False
            else:
                self._tested = True
            return False

    def _search_body(self, *, enable_search: bool) -> Dict[str, Any]:
        if not enable_search:
            return {}
        options: Dict[str, Any] = {
            "search_strategy": self.search_strategy or "max",
        }
        if self.search_freshness is not None and self.search_freshness > 0:
            options["freshness"] = int(self.search_freshness)
        return {
            "enable_search": True,
            "search_options": options,
        }

    def _test_connection(self):
        url = f"{self.endpoint}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 10,
        }
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=_timeout_tuple(self.probe_timeout),
        )
        self._handle_response_error(response)

    def _handle_response_error(self, response):
        if response.status_code == 404:
            raise Exception(
                f"404 错误：模型 '{self.model}' 未找到或未开通。请检查百炼 / MaaS 控制台配置。"
            )
        if response.status_code == 401:
            raise Exception("401 错误：API Key 无效或格式错误")
        if response.status_code == 403:
            raise Exception("403 错误：API Key 无权限调用该模型")
        if response.status_code != 200:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", str(response.status_code))
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in llm_client.py", exc_info=True)
                error_msg = str(response.status_code)
            raise Exception(f"API 调用失败 ({response.status_code}): {error_msg}")

    def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        *,
        enable_search: Optional[bool] = None,
    ) -> Dict:
        if not self.api_key:
            raise ValueError("API Key 未配置，请设置环境变量 DASHSCOPE_API_KEY")

        url = f"{self.endpoint}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 2000,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        use_search = self.enable_search if enable_search is None else bool(enable_search)
        # 带 tools 的 Agent 轮次默认仍可联网；探测与显式关闭除外
        payload.update(self._search_body(enable_search=use_search))

        last_err: Optional[BaseException] = None
        attempts = 2
        for attempt in range(attempts):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=_timeout_tuple(self.chat_timeout),
                )
                self._handle_response_error(response)
                data = response.json()
                self._record_usage(data)
                self._tested = True
                self._available = True
                self._last_error = None
                return data
            except requests.exceptions.RequestException as e:
                last_err = e
                if attempt + 1 < attempts and _is_transient_error(e):
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise Exception(_friendly_request_error(e, kind="调用")) from e
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in llm_client.py", exc_info=True)
                raise
        raise Exception(_friendly_request_error(last_err or Exception("unknown"), kind="调用"))

    def extract_function_calls(self, response: Dict) -> List[Dict]:
        """提取全部 tool_calls（支持一次多工具）。"""
        choices = response.get("choices", [])
        if not choices:
            return []
        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls") or []
        results = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            try:
                params = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except json.JSONDecodeError:
                params = {}
            results.append(
                {
                    "id": tc.get("id", f"call_{len(results)+1}"),
                    "name": fn.get("name", ""),
                    "parameters": params,
                }
            )
        return results

    def extract_function_call(self, response: Dict) -> Optional[Dict]:
        calls = self.extract_function_calls(response)
        if not calls:
            return None
        return {"name": calls[0]["name"], "parameters": calls[0]["parameters"]}

    def get_response_content(self, response: Dict) -> str:
        choices = response.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""

    def get_assistant_message(self, response: Dict) -> Dict:
        choices = response.get("choices", [])
        if not choices:
            return {"role": "assistant", "content": ""}
        return choices[0].get("message", {"role": "assistant", "content": ""})

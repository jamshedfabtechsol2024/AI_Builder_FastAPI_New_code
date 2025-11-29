import os
import math
from typing import List, Dict, Any, Optional

import anthropic


def count_tokens(model: str, system: str, messages: List[Dict[str, Any]], api_key: Optional[str] = None) -> int:
    key = api_key or os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Missing Anthropic API key")
    client = anthropic.Anthropic(api_key=key)
    result = client.messages.count_tokens(
        model=model,
        system=system,
        messages=messages,
    )
    if hasattr(result, "input_tokens"):
        value = getattr(result, "input_tokens") or 0
        return int(value)
    if isinstance(result, dict):
        return int(result.get("input_tokens", 0) or 0)
    return 0


def credits_for_messages(
    model: str,
    system: str,
    messages: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    tokens_per_credit: int = 350,
    rounding: str = "ceil",
) -> float:
    tokens = count_tokens(model, system, messages, api_key=api_key)
    print("[DB] Tokens:", tokens)
    if tokens_per_credit <= 0:
        tokens_per_credit = 350
    value = tokens / tokens_per_credit
    print("[DB] Value:", value)
    if rounding == "ceil":
        return float(math.ceil(value))
    if rounding == "floor":
        return float(math.floor(value))
    if rounding == "round":
        return float(round(value, 4))
    return value

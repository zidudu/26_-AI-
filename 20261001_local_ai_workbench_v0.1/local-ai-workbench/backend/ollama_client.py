"""Small async client with real timing and load metrics from Ollama."""

from __future__ import annotations

import json
import asyncio
import time
from typing import Any, Callable

import httpx

from .config import EMBEDDING_MODEL, OLLAMA_URL


class OllamaError(RuntimeError):
    pass


async def installed_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            return [item["name"] for item in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise OllamaError(f"Ollama 연결 실패: {exc}") from exc


async def _processor_snapshot(client: httpx.AsyncClient, model: str) -> dict[str, Any] | None:
    try:
        response = await client.get(f"{OLLAMA_URL}/api/ps", timeout=5)
        response.raise_for_status()
        for item in response.json().get("models", []):
            if item.get("name") == model:
                size = int(item.get("size", 0))
                vram = int(item.get("size_vram", 0))
                return {
                    "vram_percent": round(vram / size * 100) if size else None,
                    "context_length": item.get("context_length"),
                }
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return None


async def chat_once(
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 256,
    context: int = 4096,
    keep_alive: str | int = 0,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": keep_alive,
        "options": {"num_ctx": context, "num_predict": max_tokens, "temperature": 0.2},
    }
    started = time.monotonic()
    first_token_seconds: float | None = None
    content: list[str] = []
    thinking: list[str] = []
    final: dict[str, Any] = {}
    processor: dict[str, Any] | None = None
    emitted_tokens = 0
    last_progress = started

    def report(stage: str, **details: Any) -> None:
        if on_event:
            on_event(stage, details)

    async def waiting_updates() -> None:
        while True:
            await asyncio.sleep(10)
            if first_token_seconds is None:
                report("waiting", elapsed_seconds=round(time.monotonic() - started))

    report("request")
    waiting_task = asyncio.create_task(waiting_updates())

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    if item.get("error"):
                        raise OllamaError(str(item["error"]))
                    message = item.get("message") or {}
                    token = message.get("content") or ""
                    thought = message.get("thinking") or ""
                    if token:
                        content.append(token)
                        emitted_tokens += 1
                    if thought:
                        thinking.append(thought)
                    if (token or thought) and first_token_seconds is None:
                        first_token_seconds = round(time.monotonic() - started, 3)
                        processor = await _processor_snapshot(client, model)
                        report("first_token", seconds=first_token_seconds, processor=processor)
                    if token and time.monotonic() - last_progress >= 5:
                        report("progress", chunks=emitted_tokens, elapsed_seconds=round(time.monotonic() - started))
                        last_progress = time.monotonic()
                    if item.get("done"):
                        final = item
    except OllamaError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise OllamaError(f"모델 실행 실패: {exc}") from exc
    finally:
        waiting_task.cancel()

    if not final:
        raise OllamaError("Ollama가 완료 응답을 보내지 않았습니다.")
    eval_count = int(final.get("eval_count") or 0)
    eval_duration = int(final.get("eval_duration") or 0)
    result = {
        "model": model,
        "content": "".join(content).strip(),
        "thinking": "".join(thinking).strip(),
        "load_seconds": round(int(final.get("load_duration") or 0) / 1e9, 3),
        "first_token_seconds": first_token_seconds,
        "total_seconds": round(time.monotonic() - started, 3),
        "input_tokens": int(final.get("prompt_eval_count") or 0),
        "output_tokens": eval_count,
        "tokens_per_second": round(eval_count / (eval_duration / 1e9), 2) if eval_duration else 0,
        "processor": processor,
        "done_reason": final.get("done_reason"),
    }
    report("complete", seconds=result["total_seconds"], output_tokens=eval_count,
           tokens_per_second=result["tokens_per_second"])
    return result


async def unload(model: str) -> None:
    """Release a retained model before loading another large model."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": model, "messages": [], "stream": False, "keep_alive": 0},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaError(f"이전 모델 메모리 해제 실패: {exc}") from exc


async def embed(texts: list[str], *, keep_alive: str | int = "5m") -> list[list[float]]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/embed",
                json={"model": EMBEDDING_MODEL, "input": texts, "keep_alive": keep_alive},
            )
            response.raise_for_status()
            vectors = response.json().get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise OllamaError("임베딩 응답의 벡터 수가 입력과 다릅니다.")
            return vectors
    except OllamaError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise OllamaError(f"문서 검색 모델 실행 실패: {exc}") from exc


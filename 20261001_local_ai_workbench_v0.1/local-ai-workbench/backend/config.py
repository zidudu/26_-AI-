"""Shared configuration for the local-only workbench."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(
    os.environ.get("LOCAL_AI_WORKBENCH_DATA")
    or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LocalAIWorkbench"
)
OLLAMA_URL = os.environ.get("LOCAL_AI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
COMPARE_MODELS = ("gemma4:26b-a4b-it-qat", "qwen3.6:27b-coding")
CHAT_MODELS = (*COMPARE_MODELS, "qwen3:8b")
EMBEDDING_MODEL = "qwen3-embedding:0.6b"


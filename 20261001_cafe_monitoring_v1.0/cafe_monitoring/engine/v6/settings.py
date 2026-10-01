from __future__ import annotations

import re
from pathlib import Path
from .common import V6Error, read_json

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=None):
    path = Path(path or ROOT / "config_v6.json").resolve()
    try:
        cfg = read_json(path)
        if not isinstance(cfg, dict):
            raise ValueError()
        expected = {"cafe_id", "input_dirs", "output_dir", "model", "reasoning_effort",
                    "default_count", "max_output_tokens", "timeout_seconds", "max_body_chars",
                    "focus_topics"}
        if set(cfg) != expected:
            raise ValueError()
        if not isinstance(cfg["cafe_id"], str) or not re.fullmatch(r"[1-9][0-9]*", cfg["cafe_id"]):
            raise ValueError()
        if not isinstance(cfg["model"], str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", cfg["model"]):
            raise ValueError()
        if cfg["reasoning_effort"] not in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise ValueError()
        for key, low, high in [("default_count", 1, 100), ("max_output_tokens", 2000, 16000),
                               ("timeout_seconds", 10, 300), ("max_body_chars", 1000, 100000)]:
            if type(cfg[key]) is not int or not low <= cfg[key] <= high:
                raise ValueError()
        for key in ("input_dirs", "focus_topics"):
            if not isinstance(cfg[key], list) or not 1 <= len(cfg[key]) <= 30:
                raise ValueError()
            if any(not isinstance(x, str) or not x.strip() or len(x) > 250 for x in cfg[key]):
                raise ValueError()
        if not isinstance(cfg["output_dir"], str) or not cfg["output_dir"].strip():
            raise ValueError()
        cfg["output_dir"] = (path.parent / cfg["output_dir"]).resolve()
        cfg["input_dirs"] = list(dict.fromkeys((path.parent / s).resolve() for s in cfg["input_dirs"]))
        out = cfg["output_dir"]
        for source in cfg["input_dirs"]:
            if source == out or source in out.parents or out in source.parents:
                raise V6Error("CONFIG_ERROR", "input_dirs와 output_dir는 서로 겹치지 않는 폴더로 설정하세요.")
        return cfg
    except V6Error:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise V6Error("CONFIG_ERROR", "config_v6.json의 항목·자료형·범위를 확인하세요. README_V6.md에 설명이 있습니다.") from None

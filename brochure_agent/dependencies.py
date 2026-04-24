from __future__ import annotations

import os

from openai import OpenAI


def require_dependency(module_name: str, package_name: str):
    try:
        return __import__(module_name, fromlist=["*"])
    except ImportError as exc:
        raise RuntimeError(
            f"Missing dependency '{package_name}'. Install the packages from requirements.txt first."
        ) from exc


def build_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


"""List the models of OpenAI-compatible endpoints.

    QWEN38_LOCAL_API_KEY_VAR=<env var holding the key> python scripts/llm_endpoint_probe.py http://host:port/v1 ...

The key is read from the named variable and sent only as the Authorization
header; nothing secret is printed."""
import json
import os
import sys

import httpx


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from novel_manga.model_client import endpoint_key
    key = endpoint_key()
    headers = {"Authorization": "Bearer " + key} if key else {}
    print("key:", "present" if key else "none")
    for base in sys.argv[1:]:
        try:
            response = httpx.get(f"{base.rstrip('/')}/models", headers=headers, timeout=8, trust_env=False)
            try:
                data = response.json()
            except ValueError:
                data = {}
            models = [(m.get("id"), m.get("max_model_len")) for m in data.get("data", [])] if isinstance(data, dict) else []
            print(f"{base} -> HTTP {response.status_code} {models or data if response.status_code != 200 else models}")
        except httpx.HTTPError as error:
            print(f"{base} -> {type(error).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

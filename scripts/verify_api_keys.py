from __future__ import annotations

import re

from app.config import ModelSettings, get_settings
from app.llm.openai_client import chat


def check(label: str, model: ModelSettings) -> bool:
    try:
        answer = chat(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            model,
            "This is a connection test. Reply with exactly OK.",
            role="connection_test",
        )
    except Exception as exc:
        message = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", str(exc))
        message = re.sub(r"AIza[A-Za-z0-9_-]{8,}", "[REDACTED]", message)
        if getattr(exc, "status_code", None) == 429 and any(
            phrase in message.casefold() for phrase in ("credit", "quota", "resource_exhausted")
        ):
            print(f"{label}: AUTHENTICATED, BUT NO CREDITS/QUOTA")
        else:
            print(f"{label}: FAILED ({type(exc).__name__}: {message[:300]})")
        return False
    print(f"{label}: OK (response characters: {len(answer)})")
    return True


def main() -> int:
    settings = get_settings()
    results = [
        check("Gemini", settings.chat),
        check("OpenAI", settings.review),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

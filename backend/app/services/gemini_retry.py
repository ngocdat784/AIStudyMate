from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from google.genai import errors


T = TypeVar("T")

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def call_gemini_with_retry(
    call: Callable[[], T],
    *,
    max_attempts: int = 3,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts phải lớn hơn hoặc bằng 1.")

    for attempt in range(max_attempts):
        try:
            return call()
        except errors.APIError as exc:
            if (
                exc.code not in RETRYABLE_STATUS_CODES
                or attempt == max_attempts - 1
            ):
                raise

            delay = min(2**attempt, 8) + random.uniform(0, 0.5)
            time.sleep(delay)
        except (TimeoutError, ConnectionError):
            if attempt == max_attempts - 1:
                raise

            delay = min(2**attempt, 8) + random.uniform(0, 0.5)
            time.sleep(delay)

    raise RuntimeError("Gemini call retry loop ended unexpectedly.")

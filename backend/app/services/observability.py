from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import Lock
from typing import Any


request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        for field in (
            "event",
            "method",
            "route",
            "status_code",
            "duration_ms",
            "exception_type",
        ):
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["exception_type"] = record.exc_info[0].__name__
            entry["stack"] = [
                {
                    "file": frame.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_application_logging() -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(JsonLogFormatter())
    if root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)


class RequestMetrics:
    _LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self._lock = Lock()
        self._counts: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency: dict[tuple[str, str, int], list[float]] = {}
        self._recent_requests: dict[int, int] = defaultdict(int)

    def record(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        key = (method, route, status_code)
        with self._lock:
            self._counts[key] += 1
            histogram = self._latency.setdefault(
                key,
                [0.0] * (len(self._LATENCY_BUCKETS) + 3),
            )
            histogram[0] += 1
            histogram[1] += duration_seconds
            for index, boundary in enumerate(self._LATENCY_BUCKETS, start=2):
                if duration_seconds <= boundary:
                    histogram[index] += 1
            histogram[-1] += 1
            self._recent_requests[int(time.monotonic())] += 1

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            routes = [
                {
                    "method": method,
                    "route": route,
                    "status_code": status_code,
                    "requests": count,
                    "errors": count if status_code >= 500 else 0,
                    "average_latency_ms": round(
                        self._latency[(method, route, status_code)][1]
                        / count
                        * 1000,
                        2,
                    ),
                    "latency_buckets": {
                        str(boundary): self._latency[
                            (method, route, status_code)
                        ][index + 2]
                        for index, boundary in enumerate(self._LATENCY_BUCKETS)
                    },
                    "latency_over_10_seconds": self._latency[
                        (method, route, status_code)
                    ][-1]
                    - self._latency[(method, route, status_code)][-2],
                }
                for (method, route, status_code), count in sorted(
                    self._counts.items()
                )
            ]
            total_requests = sum(self._counts.values())
            total_errors = sum(
                count
                for (_method, _route, status_code), count in self._counts.items()
                if status_code >= 500
            )
            current_second = int(now)
            for second in tuple(self._recent_requests):
                if second < current_second - 59:
                    del self._recent_requests[second]
            recent = sum(self._recent_requests.values())
            process_uptime = max(now - self.started_at, 0.001)
        return {
            "process_uptime_seconds": round(process_uptime, 2),
            "requests_total": total_requests,
            "errors_5xx_total": total_errors,
            "throughput_requests_per_second_1m": round(recent / 60, 3),
            "routes": routes,
            "scope": "This process only; aggregate across application workers.",
        }


request_metrics = RequestMetrics()

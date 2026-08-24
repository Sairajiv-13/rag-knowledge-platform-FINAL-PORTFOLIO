"""Prometheus metrics (pure ASGI middleware + counters used by services).

Route labels use the matched route TEMPLATE (/v1/documents/{document_id}),
never the raw path — raw paths (uuids, probe scans) would explode label
cardinality and eat Prometheus memory. Unmatched requests share one label.

NOTE: counters live in this process; running multiple workers behind gunicorn
would need prometheus_client's multiprocess mode (PROMETHEUS_MULTIPROC_DIR).
Documented, not implemented — compose runs a single uvicorn process.
"""

import time

from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    # request mix spans <10ms probes to multi-second LLM answers
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
LLM_TOKENS = Counter(
    "rag_llm_tokens_total", "LLM tokens by direction", ["model", "direction"]
)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"status": "500"}  # a crash before response.start counts as 500

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = str(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, capture)
        finally:
            route_label = _route_template(scope)
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route_label, status_holder["status"]).inc()
            HTTP_LATENCY.labels(method, route_label).observe(time.perf_counter() - start)


def _route_template(scope: Scope) -> str:
    """Templated path for the metric label, e.g. /v1/documents/{document_id}.

    We can't just use scope["route"].path: recent FastAPI keeps included
    routers as wrapper objects, so the matched route's .path lacks the /v1
    prefix. Instead, reconstruct the template from the concrete request path
    (mutated into scope by the router) by substituting matched path-param
    VALUES back into {name} placeholders — correct for our UUID params, and
    documented as the assumption it is.
    """
    if "route" not in scope:  # never matched a route: one shared label,
        return "unmatched"  # or 404-scanning bots explode cardinality
    path: str = scope["path"]
    for name, value in (scope.get("path_params") or {}).items():
        path = path.replace(str(value), "{" + name + "}")
    return path

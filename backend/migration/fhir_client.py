"""Small FHIR R4 HTTP client: paged search, grouped POST search, counts, retries.

Deliberately independent from Django so it can be tested and reused on its own.
"""

import logging
import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

logger = logging.getLogger(__name__)

Resource = dict[str, Any]
Params = Mapping[str, str | int]

USER_AGENT = "credo-fhir-migration/0.1 (+https://github.com/Meranhor/credo-fhir-migration)"
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 60.0  # never let a server park the import for longer than this per attempt


class FhirClientError(Exception):
    """A request failed for good: non-retryable status, invalid payload or retries exhausted."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, MAX_RETRY_AFTER)
        # Exponential backoff (0.5s, 1s, 2s, ... capped) with jitter so that
        # concurrent clients do not retry in lockstep.
        backoff = min(self.max_delay, self.base_delay * 2.0 ** (attempt - 1))
        return backoff * random.uniform(0.5, 1.0)


@dataclass
class ClientStats:
    requests: int = 0
    retries: int = 0
    bytes_received: int = 0  # on the wire, i.e. compressed


class FhirClient:
    def __init__(
        self,
        base_url: str,
        *,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retry = retry or RetryPolicy()
        self.stats = ClientStats()
        self._sleep = sleep
        # One client = one keep-alive connection pool; httpx asks for gzip by default.
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout or httpx.Timeout(60.0, connect=10.0),
            headers={"Accept": "application/fhir+json", "User-Agent": USER_AGENT},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def search(
        self, resource_type: str, params: Params, *, post: bool = False
    ) -> Iterator[list[Resource]]:
        """Yield the matching resources page by page, following `link[next]`.

        `post=True` sends the parameters as a form body to `<type>/_search`,
        which keeps grouped searches (hundreds of references) under URL limits.
        """
        if post:
            bundle = self._request("POST", f"{resource_type}/_search", data=_form(params))
        else:
            bundle = self._request("GET", resource_type, params=params)
        while True:
            yield _matches(bundle)
            next_url = _next_link(bundle)
            if next_url is None:
                return
            bundle = self._request("GET", self._checked(next_url))

    def count(self, resource_type: str, params: Params) -> int:
        """Number of resources the server holds for this search (`_summary=count`)."""
        bundle = self._request(
            "POST", f"{resource_type}/_search", data=_form({**params, "_summary": "count"})
        )
        total = bundle.get("total")
        if not isinstance(total, int):
            raise FhirClientError(f"{resource_type} count: bundle has no total")
        return total

    def _checked(self, url: str) -> str:
        # Only follow pagination links that stay on the configured server.
        if url == self.base_url or url.startswith((f"{self.base_url}?", f"{self.base_url}/")):
            return url
        raise FhirClientError(f"Refusing to follow a next link outside {self.base_url}")

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Params | None = None,
        data: dict[str, str] | None = None,
    ) -> Resource:
        attempt = 0
        while True:
            attempt += 1
            self.stats.requests += 1
            retry_after: float | None = None
            try:
                response = self._http.request(method, url, params=params, data=data)
            except httpx.TransportError as exc:  # timeouts, connection resets, DNS...
                reason = type(exc).__name__
            else:
                self.stats.bytes_received += response.num_bytes_downloaded
                if response.is_success:
                    return _bundle(response)
                if response.status_code not in RETRYABLE_STATUSES:
                    raise FhirClientError(_describe(response))
                reason = f"HTTP {response.status_code}"
                retry_after = _retry_after(response)

            # Logs carry the path only: query strings and bodies may hold identifiers.
            path = httpx.URL(url).path or "/"
            if attempt >= self.retry.max_attempts:
                raise FhirClientError(f"{method} {path}: {reason} after {attempt} attempts")
            delay = self.retry.delay(attempt, retry_after)
            logger.warning(
                "%s %s: %s, retrying in %.1fs (attempt %d/%d)",
                method,
                path,
                reason,
                delay,
                attempt + 1,
                self.retry.max_attempts,
            )
            self.stats.retries += 1
            self._sleep(delay)


def _form(params: Params) -> dict[str, str]:
    return {key: str(value) for key, value in params.items()}


def _bundle(response: httpx.Response) -> Resource:
    try:
        payload = response.json()
    except ValueError as exc:
        raise FhirClientError(f"{response.request.url.path}: response is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("resourceType") != "Bundle":
        raise FhirClientError(f"{response.request.url.path}: expected a Bundle")
    return payload


def _matches(bundle: Resource) -> list[Resource]:
    # Skip `include` and `outcome` entries: only the resources the search matched.
    return [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if "resource" in entry and entry.get("search", {}).get("mode", "match") == "match"
    ]


def _next_link(bundle: Resource) -> str | None:
    for link in bundle.get("link", []):
        if link.get("relation") == "next":
            url = link.get("url")
            return url if isinstance(url, str) else None
    return None


def _retry_after(response: httpx.Response) -> float | None:
    # Only the delay-seconds form; an HTTP-date falls back to our own backoff.
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdigit() else None


def _describe(response: httpx.Response) -> str:
    message = f"{response.request.method} {response.request.url.path}: HTTP {response.status_code}"
    try:
        issues = response.json().get("issue", [])
        diagnostics = issues[0].get("diagnostics") if issues else None
    except (ValueError, AttributeError):
        diagnostics = None
    return f"{message} ({diagnostics[:200]})" if isinstance(diagnostics, str) else message

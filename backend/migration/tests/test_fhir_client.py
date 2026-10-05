import gzip
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from migration.fhir_client import FhirClient, FhirClientError

BASE = "https://fhir.test/baseR4"


def bundle(*ids: str, next_url: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "link": [{"relation": "next", "url": next_url}] if next_url else [],
        "entry": [{"resource": {"resourceType": "Patient", "id": i}} for i in ids],
        **extra,
    }


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps: list[float]) -> Iterator[FhirClient]:
    # Injected sleep: retry tests run instantly and can assert on the delays.
    with FhirClient(BASE, sleep=sleeps.append) as fhir_client:
        yield fhir_client


def ids(pages: list[list[dict[str, Any]]]) -> list[list[str]]:
    return [[resource["id"] for resource in page] for page in pages]


def test_search_follows_next_links_until_the_last_page(
    client: FhirClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{BASE}/Patient", params={"_count": "2"}).respond(
        json=bundle("a", "b", next_url=f"{BASE}?_getpages=xyz&_getpagesoffset=2")
    )
    respx_mock.get(BASE, params={"_getpages": "xyz", "_getpagesoffset": "2"}).respond(
        json=bundle("c")
    )

    pages = list(client.search("Patient", {"_count": 2}))

    assert ids(pages) == [["a", "b"], ["c"]]
    assert client.stats.requests == 2


def test_post_search_sends_a_form_body_then_follows_next_with_get(
    client: FhirClient, respx_mock: respx.MockRouter
) -> None:
    first = respx_mock.post(f"{BASE}/Observation/_search").respond(
        json=bundle("o1", next_url=f"{BASE}?_getpages=xyz")
    )
    respx_mock.get(BASE, params={"_getpages": "xyz"}).respond(json=bundle("o2"))

    pages = list(client.search("Observation", {"subject": "Patient/1,Patient/2"}, post=True))

    assert ids(pages) == [["o1"], ["o2"]]
    assert first.calls.last.request.content == b"subject=Patient%2F1%2CPatient%2F2"


def test_search_yields_only_matched_resources(
    client: FhirClient, respx_mock: respx.MockRouter
) -> None:
    page = bundle("a")
    page["entry"] += [
        {"resource": {"resourceType": "Patient", "id": "inc"}, "search": {"mode": "include"}},
        {"resource": {"resourceType": "OperationOutcome"}, "search": {"mode": "outcome"}},
    ]
    respx_mock.get(f"{BASE}/Patient").respond(json=page)

    assert ids(list(client.search("Patient", {}))) == [["a"]]


def test_next_link_to_another_server_is_refused(
    client: FhirClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{BASE}/Patient").respond(
        json=bundle("a", next_url="https://elsewhere.test/baseR4?_getpages=xyz")
    )

    with pytest.raises(FhirClientError, match="outside"):
        list(client.search("Patient", {}))


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_is_retried(
    client: FhirClient, respx_mock: respx.MockRouter, sleeps: list[float], status: int
) -> None:
    respx_mock.get(f"{BASE}/Patient").mock(
        side_effect=[httpx.Response(status), httpx.Response(200, json=bundle("a"))]
    )

    assert ids(list(client.search("Patient", {}))) == [["a"]]
    assert (client.stats.requests, client.stats.retries) == (2, 1)
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 0.5


def test_timeout_is_retried(client: FhirClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{BASE}/Patient").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=bundle("a"))]
    )

    assert ids(list(client.search("Patient", {}))) == [["a"]]
    assert client.stats.retries == 1


def test_retry_after_header_is_honoured(
    client: FhirClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    respx_mock.get(f"{BASE}/Patient").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=bundle("a")),
        ]
    )

    list(client.search("Patient", {}))

    assert sleeps == [7.0]


def test_gives_up_after_max_attempts_with_growing_delays(
    client: FhirClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    respx_mock.get(f"{BASE}/Patient").respond(503)

    with pytest.raises(FhirClientError, match="HTTP 503 after 5 attempts"):
        list(client.search("Patient", {}))

    assert client.stats.requests == 5
    # Exponential backoff with jitter: each delay sits in [backoff / 2, backoff].
    for delay, backoff in zip(sleeps, [0.5, 1.0, 2.0, 4.0], strict=True):
        assert backoff / 2 <= delay <= backoff


def test_client_error_fails_fast_without_retry(
    client: FhirClient, respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    outcome = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": "Unknown param"}]}
    respx_mock.get(f"{BASE}/Patient").respond(400, json=outcome)

    with pytest.raises(FhirClientError, match=r"HTTP 400 \(Unknown param\)"):
        list(client.search("Patient", {"bogus": 1}))

    assert client.stats.requests == 1
    assert sleeps == []


def test_count_posts_summary_count_and_returns_total(
    client: FhirClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{BASE}/Observation/_search").respond(json=bundle(total=42))

    assert client.count("Observation", {"subject": "Patient/1"}) == 42
    assert b"_summary=count" in route.calls.last.request.content


def test_asks_for_gzip_and_counts_compressed_bytes(
    client: FhirClient, respx_mock: respx.MockRouter
) -> None:
    body = gzip.compress(json.dumps(bundle(*(f"p{i}" for i in range(200)))).encode())
    route = respx_mock.get(f"{BASE}/Patient").respond(
        content=body, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"}
    )

    pages = list(client.search("Patient", {}))

    request = route.calls.last.request
    assert "gzip" in request.headers["Accept-Encoding"]
    assert request.headers["User-Agent"].startswith("credo-fhir-migration/")
    assert len(pages[0]) == 200
    assert client.stats.bytes_received == len(body)

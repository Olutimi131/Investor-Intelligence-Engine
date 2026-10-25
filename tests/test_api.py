"""
FastAPI Endpoint Integration Tests
==================================
Tests health check, list investors, filter query params, inspect entity, and ad-hoc extract.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "investor-intelligence-engine"
    assert data["entities_loaded"] >= 6


def test_list_investors(client):
    response = client.get("/api/investors")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6


def test_search_and_filter(client):
    # Keyword search
    res_kw = client.get("/api/investors?q=Apex")
    assert res_kw.status_code == 200
    data_kw = res_kw.json()
    assert len(data_kw) == 1
    assert "Apex Horizon" in data_kw[0]["canonical_name"]

    # Conflicts only filter
    res_conflicts = client.get("/api/investors?conflicts_only=true")
    assert res_conflicts.status_code == 200
    data_conf = res_conflicts.json()
    assert len(data_conf) == 2
    for item in data_conf:
        assert len(item["conflicts"]) > 0


def test_get_investor_by_id(client):
    response = client.get("/api/investors/inv-001")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "inv-001"
    assert len(data["sources"]) == 3

    # Non-existent ID
    res_404 = client.get("/api/investors/inv-999")
    assert res_404.status_code == 404


def test_stats_endpoint(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_raw_records"] == 10
    assert data["total_canonical_entities"] == 6
    assert data["deduplication_rate_percent"] == 40.0


def test_extract_endpoint(client):
    payload = {
        "raw_text": "Nordic Seed Ventures invests $1M to $3M in Finnish B2B SaaS and ClimateTech.",
        "raw_name": "Nordic Seed Ventures"
    }
    response = client.post("/api/extract", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "B2B SaaS" in data["sectors"]
    assert "ClimateTech" in data["sectors"]
    assert data["ticket_size"]["formatted_range"] == "$1M - $3M"

"""Unit tests for Sample Application."""

import pytest
from app import app

 
@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_endpoint(client):
    """Test the root endpoint returns 200 and expected payload."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["application"] == "sample-app"
    assert "version" in data


def test_healthz_endpoint(client):
    """Test the health check endpoint returns 200 and healthy status."""
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "healthy"

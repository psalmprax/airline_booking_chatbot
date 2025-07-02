import pytest
from unittest.mock import MagicMock
from requests.exceptions import RequestException

from actions.api_client import AeroDataApiClient

# Sample raw response from the fictional AeroData API
MOCK_AERODATA_RESPONSE = {
    "flights": [
        {
            "carrier_name": "AeroData Air",
            "departure_time_local": "2025-03-10T09:00:00",
            "price_usd": 450.50,
            "flight_number": "AD123"
        },
        {
            "carrier_name": "SkyHopper",
            "departure_time_local": "2025-03-10T14:30:00",
            "price_usd": 425.00,
            "flight_number": "SH456"
        }
    ]
}

# The expected transformed data that the bot's actions will use
EXPECTED_TRANSFORMED_DATA = [
    {
        "airline": "AeroData Air",
        "time": "09:00",
        "price": 450.50,
        "flight_id": "AD123"
    },
    {
        "airline": "SkyHopper",
        "time": "14:30",
        "price": 425.00,
        "flight_id": "SH456"
    }
]

def test_aerodata_client_init_with_key(monkeypatch):
    """Tests that the client initializes correctly when the API key is set."""
    monkeypatch.setenv("AERODATA_API_KEY", "test-key-123")
    client = AeroDataApiClient()
    assert client.api_key == "test-key-123"

def test_aerodata_client_init_without_key(monkeypatch, caplog):
    """Tests that the client logs a warning if the API key is not set."""
    # Ensure the env var is not set for this test
    monkeypatch.delenv("AERODATA_API_KEY", raising=False)
    client = AeroDataApiClient()
    assert client.api_key is None
    assert "AERODATA_API_KEY" in caplog.text
    assert "not configured" in caplog.text

def test_aerodata_transform_response():
    """Tests the response transformation logic with a typical response."""
    client = AeroDataApiClient()
    transformed = client._transform_response(MOCK_AERODATA_RESPONSE)
    assert transformed == EXPECTED_TRANSFORMED_DATA

def test_aerodata_transform_response_empty():
    """Tests the transformation with an empty or malformed response."""
    client = AeroDataApiClient()
    transformed = client._transform_response({"flights": []})
    assert transformed == []

def test_aerodata_search_success(monkeypatch, mocker):
    """Tests a successful search call, mocking the requests library."""
    # Arrange
    monkeypatch.setenv("AERODATA_API_KEY", "test-key-123")
    
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_AERODATA_RESPONSE
    
    mock_requests_get = mocker.patch("requests.get", return_value=mock_response)
    
    client = AeroDataApiClient()
    
    # Act
    results = client.search(
        departure_city="LHR",
        destination_city="JFK",
        departure_date="2025-03-10"
    )
    
    # Assert
    mock_requests_get.assert_called_once()
    call_args, call_kwargs = mock_requests_get.call_args
    assert call_kwargs["params"] == {
        "from": "LHR",
        "to": "JFK",
        "date": "2025-03-10"
    }
    assert call_kwargs["headers"] == {"X-API-Key": "test-key-123"}
    assert results == EXPECTED_TRANSFORMED_DATA

def test_aerodata_search_api_failure(monkeypatch, mocker):
    """Tests the search call when the API request fails (e.g., network error)."""
    # Arrange
    monkeypatch.setenv("AERODATA_API_KEY", "test-key-123")
    
    mocker.patch("requests.get", side_effect=RequestException("API is down"))
    
    client = AeroDataApiClient()
    
    # Act
    results = client.search(departure_city="LHR", destination_city="JFK", departure_date="2025-03-10")
    
    # Assert
    assert results is None

def test_aerodata_search_no_api_key(monkeypatch):
    """Tests that search returns None immediately if the API key is not configured."""
    # Arrange
    monkeypatch.delenv("AERODATA_API_KEY", raising=False)
    client = AeroDataApiClient()
    
    # Act
    results = client.search(departure_city="LHR", destination_city="JFK", departure_date="2025-03-10")
    
    # Assert
    assert results is None
import datetime
import json
import pytest
import redis
from unittest.mock import MagicMock
from requests.exceptions import RequestException

from actions.api_client import AeroDataApiClient, SkyscannerApiClient, RedisCache, SabreApiClient, BaseFlightApiClient
from actions.car_rental_api_client import HertzApiClient, AvisApiClient, EnterpriseApiClient

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

# --- Tests for SabreApiClient ---

MOCK_SABRE_TOKEN_RESPONSE = {
    "access_token": "mock-sabre-token",
    "token_type": "Bearer",
    "expires_in": 1800
}

MOCK_SABRE_RESPONSE = {
    "PricedItineraries": [
        {
            "AirItinerary": {
                "OriginDestinationOptions": {
                    "OriginDestinationOption": [
                        {
                            "FlightSegment": [
                                {
                                    "DepartureDateTime": "2025-09-15T08:30:00",
                                    "OperatingAirline": {
                                        "Code": "SB",
                                        "CompanyShortName": "SabreAir"
                                    },
                                    "FlightNumber": "987"
                                }
                            ]
                        }
                    ]
                }
            },
            "AirItineraryPricingInfo": {
                "ItinTotalFare": {
                    "TotalFare": {
                        "Amount": "550.75"
                    }
                }
            }
        }
    ]
}

EXPECTED_SABRE_TRANSFORMED_DATA = [
    {
        "airline": "SabreAir",
        "time": "08:30",
        "price": 550.75,
        "flight_id": "SB987"
    }
]

def test_sabre_client_init_without_keys(monkeypatch, caplog):
    """Tests that the Sabre client logs a warning if API keys are not set."""
    monkeypatch.delenv("SABRE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SABRE_CLIENT_SECRET", raising=False)
    client = SabreApiClient()
    assert client.client_id is None
    assert "SABRE_CLIENT_ID" in caplog.text
    assert "not configured" in caplog.text

def test_sabre_transform_response():
    """Tests the Sabre response transformation logic."""
    client = SabreApiClient()
    transformed = client._transform_response(MOCK_SABRE_RESPONSE)
    assert transformed == EXPECTED_SABRE_TRANSFORMED_DATA

def test_sabre_get_access_token_success(monkeypatch, mocker):
    """Tests successful retrieval of a Sabre access token."""
    # Arrange
    monkeypatch.setenv("SABRE_CLIENT_ID", "test-id")
    monkeypatch.setenv("SABRE_CLIENT_SECRET", "test-secret")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_SABRE_TOKEN_RESPONSE
    mock_post = mocker.patch("requests.post", return_value=mock_response)

    client = SabreApiClient()

    # Act
    token = client._get_access_token()

    # Assert
    assert token == "mock-sabre-token"
    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert "Authorization" in call_kwargs["headers"]
    assert call_kwargs["headers"]["Authorization"].startswith("Basic ")

def test_sabre_search_token_failure(monkeypatch, mocker):
    """Tests that search fails if it cannot get an access token."""
    # Arrange
    monkeypatch.setenv("SABRE_CLIENT_ID", "test-id")
    monkeypatch.setenv("SABRE_CLIENT_SECRET", "test-secret")
    mocker.patch("requests.post", side_effect=RequestException("Auth failed"))
    client = SabreApiClient()

    # Act
    results = client.search(departure_city="DFW", destination_city="LAX", departure_date="2025-09-15")

    # Assert
    assert results is None

# --- Tests for SkyscannerApiClient ---

# Fictional Skyscanner response
MOCK_SKYSCANNER_RESPONSE = {
    "itineraries": [
        {
            "pricingOptions": [{"price": {"amount": "350.00"}}],
            "legs": [
                {
                    "id": "leg_1",
                    "operatingCarrier": {"name": "Sky High"},
                    "departure": "2025-05-20T10:00:00"
                }
            ]
        },
        {
            "pricingOptions": [{"price": {"amount": "375.50"}}],
            "legs": [
                {
                    "id": "leg_2",
                    "operatingCarrier": {"name": "JetAway"},
                    "departure": "2025-05-20T12:30:00"
                }
            ]
        }
    ]
}

# Expected transformed data
EXPECTED_SKYSCANNER_TRANSFORMED_DATA = [
    {
        "airline": "Sky High",
        "time": "10:00",
        "price": 350.00,
        "flight_id": "leg_1"
    },
    {
        "airline": "JetAway",
        "time": "12:30",
        "price": 375.50,
        "flight_id": "leg_2"
    }
]

def test_skyscanner_client_init_with_key(monkeypatch):
    """Tests that the Skyscanner client initializes correctly when the API key is set."""
    monkeypatch.setenv("SKYSCRANNER_API_KEY", "test-sky-key")
    client = SkyscannerApiClient()
    assert client.api_key == "test-sky-key"

def test_skyscanner_client_init_without_key(monkeypatch, caplog):
    """Tests that the Skyscanner client logs a warning if the API key is not set."""
    monkeypatch.delenv("SKYSCRANNER_API_KEY", raising=False)
    client = SkyscannerApiClient()
    assert client.api_key is None
    assert "SKYSCRANNER_API_KEY" in caplog.text
    assert "not configured" in caplog.text

def test_skyscanner_transform_response():
    """Tests the Skyscanner response transformation logic."""
    client = SkyscannerApiClient()
    transformed = client._transform_response(MOCK_SKYSCANNER_RESPONSE)
    assert transformed == EXPECTED_SKYSCANNER_TRANSFORMED_DATA

def test_skyscanner_search_success(monkeypatch, mocker):
    """Tests a successful Skyscanner search call."""
    # Arrange
    monkeypatch.setenv("SKYSCRANNER_API_KEY", "test-sky-key")
    
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_SKYSCANNER_RESPONSE
    
    mock_requests_post = mocker.patch("requests.post", return_value=mock_response)
    
    client = SkyscannerApiClient()
    
    # Act
    results = client.search(
        departure_city="JFK",
        destination_city="LHR",
        departure_date="2025-05-20",
        passengers=2,
        travel_class="business"
    )
    
    # Assert
    mock_requests_post.assert_called_once()
    call_args, call_kwargs = mock_requests_post.call_args
    
    assert call_kwargs["json"]["query"]["adults"] == 2
    assert call_kwargs["json"]["query"]["cabinClass"] == "business"
    assert call_kwargs["headers"] == {"x-api-key": "test-sky-key"}
    assert results == EXPECTED_SKYSCANNER_TRANSFORMED_DATA

def test_skyscanner_search_api_failure(monkeypatch, mocker):
    """Tests the Skyscanner search call when the API request fails."""
    monkeypatch.setenv("SKYSCRANNER_API_KEY", "test-sky-key")
    mocker.patch("requests.post", side_effect=RequestException("Network Error"))
    client = SkyscannerApiClient()
    results = client.search(departure_city="JFK", destination_city="LHR", departure_date="2025-05-20")
    assert results is None

# --- Tests for HertzApiClient ---

MOCK_HERTZ_RESPONSE = {
    "available_vehicles": [
        {"vehicle_name": "Tesla Model 3", "daily_rate_usd": 95.00, "vehicle_id": "HTZ-T3-01"},
        {"vehicle_name": "Ford Mustang", "daily_rate_usd": 80.50, "vehicle_id": "HTZ-M5-02"}
    ]
}

EXPECTED_HERTZ_TRANSFORMED_DATA = [
    {"provider": "Hertz", "model": "Tesla Model 3", "price_per_day": 95.0, "id": "HTZ-T3-01"},
    {"provider": "Hertz", "model": "Ford Mustang", "price_per_day": 80.5, "id": "HTZ-M5-02"}
]

def test_hertz_client_init_with_key(monkeypatch):
    """Tests that the Hertz client initializes correctly when the API key is set."""
    monkeypatch.setenv("HERTZ_API_KEY", "test-hertz-key")
    client = HertzApiClient()
    assert client.api_key == "test-hertz-key"

def test_hertz_client_init_without_key(monkeypatch, caplog):
    """Tests that the Hertz client logs a warning if the API key is not set."""
    monkeypatch.delenv("HERTZ_API_KEY", raising=False)
    client = HertzApiClient()
    assert client.api_key is None
    assert "HERTZ_API_KEY" in caplog.text
    assert "not configured" in caplog.text

def test_hertz_transform_response():
    """Tests the Hertz response transformation logic."""
    client = HertzApiClient()
    transformed = client._transform_response(MOCK_HERTZ_RESPONSE)
    assert transformed == EXPECTED_HERTZ_TRANSFORMED_DATA

def test_hertz_search_success(monkeypatch, mocker):
    """Tests a successful Hertz search call."""
    # Arrange
    monkeypatch.setenv("HERTZ_API_KEY", "test-hertz-key")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_HERTZ_RESPONSE

    mock_requests_get = mocker.patch("requests.get", return_value=mock_response)

    client = HertzApiClient()
    client.cache = {} # Use a simple dict to isolate cache testing

    # Act
    results = client.search(location="LAX", pickup_date="2025-07-01", dropoff_date="2025-07-05", car_type="suv")

    # Assert
    mock_requests_get.assert_called_once()
    call_args, call_kwargs = mock_requests_get.call_args
    assert call_kwargs["params"]["pickup_location"] == "LAX"
    assert call_kwargs["params"]["vehicle_class"] == "suv"
    assert call_kwargs["headers"] == {"Authorization": "Bearer test-hertz-key"}
    assert results == EXPECTED_HERTZ_TRANSFORMED_DATA

def test_hertz_search_api_failure(monkeypatch, mocker):
    """Tests the Hertz search call when the API request fails."""
    monkeypatch.setenv("HERTZ_API_KEY", "test-hertz-key")
    mocker.patch("requests.get", side_effect=RequestException("Network Error"))
    client = HertzApiClient()
    results = client.search(location="LAX", pickup_date="2025-07-01", dropoff_date="2025-07-05", car_type="suv")
    assert results is None

def test_hertz_search_uses_cache(monkeypatch, mocker):
    """Tests that a successful search result is cached and reused."""
    # Arrange
    monkeypatch.setenv("HERTZ_API_KEY", "test-hertz-key")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_HERTZ_RESPONSE

    mock_requests_get = mocker.patch("requests.get", return_value=mock_response)

    client = HertzApiClient()
    client.cache = {} # Use a simple dict as a mock cache for this test

    # Act: First call, should call the API
    first_results = client.search(location="LAX", pickup_date="2025-07-01", dropoff_date="2025-07-05", car_type="suv")

    # Act: Second call with same params, should use cache
    second_results = client.search(location="LAX", pickup_date="2025-07-01", dropoff_date="2025-07-05", car_type="suv")

    # Assert
    mock_requests_get.assert_called_once() # API should only be called once
    assert first_results == EXPECTED_HERTZ_TRANSFORMED_DATA
    assert second_results == first_results

# --- Tests for AvisApiClient ---

MOCK_AVIS_RESPONSE = {
    "cars": [
        {"car_model": "Chevrolet Malibu", "rate": 62.00, "rental_id": "AVS-CM-45"},
        {"car_model": "Jeep Wrangler", "rate": 88.75, "rental_id": "AVS-JW-91"}
    ]
}

EXPECTED_AVIS_TRANSFORMED_DATA = [
    {"provider": "Avis", "model": "Chevrolet Malibu", "price_per_day": 62.0, "id": "AVS-CM-45"},
    {"provider": "Avis", "model": "Jeep Wrangler", "price_per_day": 88.75, "id": "AVS-JW-91"}
]

def test_avis_client_init_with_key(monkeypatch):
    """Tests that the Avis client initializes correctly when the API key is set."""
    monkeypatch.setenv("AVIS_API_KEY", "test-avis-key")
    client = AvisApiClient()
    assert client.api_key == "test-avis-key"

def test_avis_client_init_without_key(monkeypatch, caplog):
    """Tests that the Avis client logs a warning if the API key is not set."""
    monkeypatch.delenv("AVIS_API_KEY", raising=False)
    client = AvisApiClient()
    assert client.api_key is None
    assert "AVIS_API_KEY" in caplog.text
    assert "not configured" in caplog.text

def test_avis_transform_response():
    """Tests the Avis response transformation logic."""
    client = AvisApiClient()
    transformed = client._transform_response(MOCK_AVIS_RESPONSE)
    assert transformed == EXPECTED_AVIS_TRANSFORMED_DATA

def test_avis_search_success(monkeypatch, mocker):
    """Tests a successful Avis search call."""
    # Arrange
    monkeypatch.setenv("AVIS_API_KEY", "test-avis-key")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_AVIS_RESPONSE

    mock_requests_get = mocker.patch("requests.get", return_value=mock_response)

    client = AvisApiClient()
    client.cache = {}

    # Act
    results = client.search(location="SFO", pickup_date="2025-08-10", dropoff_date="2025-08-15", car_type="full-size")

    # Assert
    mock_requests_get.assert_called_once()
    call_args, call_kwargs = mock_requests_get.call_args
    assert call_kwargs["params"]["pickup_loc"] == "SFO"
    assert call_kwargs["params"]["category"] == "full-size"
    assert call_kwargs["headers"] == {"X-Api-Key": "test-avis-key"}
    assert results == EXPECTED_AVIS_TRANSFORMED_DATA

def test_avis_search_api_failure(monkeypatch, mocker):
    """Tests the Avis search call when the API request fails."""
    monkeypatch.setenv("AVIS_API_KEY", "test-avis-key")
    mocker.patch("requests.get", side_effect=RequestException("Network Error"))
    client = AvisApiClient()
    results = client.search(location="SFO", pickup_date="2025-08-10", dropoff_date="2025-08-15", car_type="full-size")
    assert results is None

# --- Tests for EnterpriseApiClient ---

MOCK_ENTERPRISE_RESPONSE = {
    "rental_options": [
        {
            "option_id": "ENT-VWJ-01",
            "vehicle_info": {"make": "Volkswagen", "model": "Jetta"},
            "cost": {"per_day": 52.50}
        },
        {
            "option_id": "ENT-CRA-02",
            "vehicle_info": {"make": "Chrysler", "model": "Pacifica"},
            "cost": {"per_day": 95.00}
        }
    ]
}

EXPECTED_ENTERPRISE_TRANSFORMED_DATA = [
    {"provider": "Enterprise", "model": "Volkswagen Jetta", "price_per_day": 52.5, "id": "ENT-VWJ-01"},
    {"provider": "Enterprise", "model": "Chrysler Pacifica", "price_per_day": 95.0, "id": "ENT-CRA-02"}
]

def test_enterprise_client_init_with_key(monkeypatch):
    """Tests that the Enterprise client initializes correctly when the API key is set."""
    monkeypatch.setenv("ENTERPRISE_API_KEY", "test-enterprise-key")
    client = EnterpriseApiClient()
    assert client.api_key == "test-enterprise-key"

def test_enterprise_client_init_without_key(monkeypatch, caplog):
    """Tests that the Enterprise client logs a warning if the API key is not set."""
    monkeypatch.delenv("ENTERPRISE_API_KEY", raising=False)
    client = EnterpriseApiClient()
    assert client.api_key is None
    assert "ENTERPRISE_API_KEY" in caplog.text
    assert "not configured" in caplog.text

def test_enterprise_transform_response():
    """Tests the Enterprise response transformation logic."""
    client = EnterpriseApiClient()
    transformed = client._transform_response(MOCK_ENTERPRISE_RESPONSE)
    assert transformed == EXPECTED_ENTERPRISE_TRANSFORMED_DATA

def test_enterprise_search_success(monkeypatch, mocker):
    """Tests a successful Enterprise search call."""
    monkeypatch.setenv("ENTERPRISE_API_KEY", "test-enterprise-key")
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = MOCK_ENTERPRISE_RESPONSE
    mock_requests_get = mocker.patch("requests.get", return_value=mock_response)
    client = EnterpriseApiClient()
    client.cache = {}
    results = client.search(location="MIA", pickup_date="2025-10-01", dropoff_date="2025-10-05", car_type="standard")
    mock_requests_get.assert_called_once()
    call_args, call_kwargs = mock_requests_get.call_args
    assert call_kwargs["params"]["location_code"] == "MIA"
    assert call_kwargs["params"]["car_group"] == "STANDARD"
    assert call_kwargs["headers"] == {"Api-Token": "test-enterprise-key"}
    assert results == EXPECTED_ENTERPRISE_TRANSFORMED_DATA

def test_enterprise_search_api_failure(monkeypatch, mocker):
    """Tests the Enterprise search call when the API request fails."""
    monkeypatch.setenv("ENTERPRISE_API_KEY", "test-enterprise-key")
    mocker.patch("requests.get", side_effect=RequestException("Network Error"))
    client = EnterpriseApiClient()
    results = client.search(location="MIA", pickup_date="2025-10-01", dropoff_date="2025-10-05", car_type="standard")
    assert results is None

# --- Tests for RedisCache Error Handling ---

@pytest.fixture
def mock_redis_client(mocker):
    """Mocks the redis.Redis client instance used by RedisCache."""
    mock_redis = mocker.MagicMock(spec=redis.Redis)
    # We mock the redis.Redis class to return our mock instance.
    mocker.patch('redis.Redis', return_value=mock_redis)
    return mock_redis

def test_redis_cache_init_connection_error(mocker, caplog):
    """Tests that RedisCache handles an initial connection error gracefully."""
    # Arrange: Force the connection pool or ping to fail
    mocker.patch('redis.Redis.ping', side_effect=redis.exceptions.ConnectionError("Can't connect"))

    # Act
    cache = RedisCache()

    # Assert
    assert cache.redis is None
    assert "Could not connect to Redis" in caplog.text
    assert "Caching will be disabled" in caplog.text

def test_redis_cache_contains_redis_error(mock_redis_client, caplog):
    """Tests that __contains__ handles a runtime RedisError."""
    # Arrange
    mock_redis_client.exists.side_effect = redis.exceptions.RedisError("EXISTS failed")
    cache = RedisCache()

    # Act
    result = 'some_key' in cache

    # Assert
    assert result is False
    assert "Redis cache 'exists' check failed" in caplog.text

def test_redis_cache_getitem_redis_error(mock_redis_client, caplog):
    """Tests that __getitem__ handles a runtime RedisError."""
    # Arrange
    mock_redis_client.get.side_effect = redis.exceptions.RedisError("GET failed")
    cache = RedisCache()

    # Act
    result = cache['some_key']

    # Assert
    assert result is None
    assert "Redis cache 'get' operation failed" in caplog.text

def test_redis_cache_getitem_json_decode_error(mock_redis_client, caplog):
    """Tests that __getitem__ handles corrupt data from Redis."""
    # Arrange
    mock_redis_client.get.return_value = '{"key": "value", "malformed"}' # Invalid JSON
    cache = RedisCache()

    # Act
    result = cache['some_key']

    # Assert
    assert result is None
    assert "Failed to decode JSON from Redis cache" in caplog.text

def test_redis_cache_setitem_redis_error(mock_redis_client, caplog):
    """Tests that __setitem__ handles a runtime RedisError."""
    # Arrange
    mock_redis_client.setex.side_effect = redis.exceptions.RedisError("SET failed")
    cache = RedisCache()

    # Act
    cache['some_key'] = {"data": "good"}

    # Assert
    assert "Redis cache 'set' operation failed" in caplog.text

def test_redis_cache_setitem_type_error(mock_redis_client, caplog):
    """Tests that __setitem__ handles non-serializable data."""
    # Arrange
    cache = RedisCache()
    # A datetime object is not directly JSON serializable without a custom encoder
    non_serializable_object = datetime.datetime.now()

    # Act
    cache['some_key'] = non_serializable_object

    # Assert
    assert "Failed to serialize value to JSON" in caplog.text
    # Ensure we didn't even try to send the corrupt data to Redis
    mock_redis_client.setex.assert_not_called()

def test_aerodata_search_no_api_key(monkeypatch):
    """Tests that search returns None immediately if the API key is not configured."""
    # Arrange
    monkeypatch.delenv("AERODATA_API_KEY", raising=False)
    client = AeroDataApiClient()
    
    # Act
    results = client.search(departure_city="LHR", destination_city="JFK", departure_date="2025-03-10")
    
    # Assert
    assert results is None
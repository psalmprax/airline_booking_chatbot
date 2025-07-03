import base64
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import redis
import dateparser
import requests
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

class RedisCache:
    """
    A Redis-based cache that mimics the interface of cachetools.TTLCache with robust error handling.
    It handles serialization/deserialization and gracefully degrades if Redis is unavailable.
    """
    def __init__(self, host='localhost', port=6379, db=0, ttl=60):
        try:
            # Add a connection timeout to prevent the action server from hanging.
            self.redis_pool = redis.ConnectionPool(
                host=host, port=port, db=db, decode_responses=True, socket_connect_timeout=2
            )
            self.redis = redis.Redis(connection_pool=self.redis_pool)
            self.ttl = ttl
            self.redis.ping() # Check connection
            logger.info(f"Successfully connected to Redis cache at {host}:{port}")
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            logger.error(f"Could not connect to Redis at {host}:{port}. Caching will be disabled. Error: {e}")
            self.redis = None

    def __contains__(self, key: Any) -> bool:
        if not self.redis: return False
        try:
            return self.redis.exists(str(key))
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis cache 'exists' check failed. Caching for this request will be skipped. Error: {e}")
            return False

    def __getitem__(self, key: Any) -> Any:
        if not self.redis: return None
        try:
            cached_value = self.redis.get(str(key))
            if not cached_value:
                return None
            return json.loads(cached_value)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from Redis cache for key '{key}'. The cache entry may be corrupt. Error: {e}")
            return None
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis cache 'get' operation failed. Caching for this request will be skipped. Error: {e}")
            return None

    def __setitem__(self, key: Any, value: Any):
        if not self.redis: return
        try:
            serialized_value = json.dumps(value)
            self.redis.setex(str(key), self.ttl, serialized_value)
        except TypeError as e:
            logger.error(f"Failed to serialize value to JSON for Redis cache key '{key}'. The value will not be cached. Error: {e}")
        except redis.exceptions.RedisError as e:
            logger.error(f"Redis cache 'set' operation failed. The value will not be cached. Error: {e}")

class BaseFlightApiClient(ABC):
    """
    Abstract base class for all flight API clients.
    Defines the common interface for searching flights.
    """
    def __init__(self):
        # Initialize a single cache instance for all clients.
        # Connection details are pulled from environment variables.
        self.cache = RedisCache(
            host=os.environ.get("REDIS_HOST", "redis"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            db=int(os.environ.get("REDIS_DB", 1)), # Use a different DB than the tracker store
            ttl=int(os.environ.get("CACHE_TTL_SECONDS", 60))
        )

    def _get_env_var(self, var_name: str, default: Optional[str] = None) -> Optional[str]:
        """
        Helper function to get an environment variable with an optional default value.
        """
        value = os.environ.get(var_name, default)
        if value is None:
            logger.warning(f"Environment variable '{var_name}' not set and no default provided.")
        return value

    @abstractmethod
    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Searches for flights based on the provided criteria.
        This method must be implemented by all concrete subclasses.
        Returns a list of flight options, or None if the API call fails.
        """
        raise NotImplementedError


class MockApiClient(BaseFlightApiClient):
    """A mock client that returns static data for development and testing."""

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> List[Dict[str, Any]]:
        logger.info(f"MockApiClient searching with parameters: { {
            "departure_city": departure_city,
            "departure_date": departure_date,
            "destination_city": destination_city,
            "return_date": return_date,
            "passengers": passengers,
            "seat_preference": seat_preference,
            "preferred_airline": preferred_airline,
            "frequent_flyer_number": frequent_flyer_number,
            "destinations": destinations,
            "travel_class": travel_class,
        } }")
        logger.warning("No real API provider configured. Returning mock flight data.")

        return [
            {"airline": "AwesomeAirlines", "time": "08:00", "price": 350, "flight_id": "AA123"},
            {"airline": "FlyHigh", "time": "11:30", "price": 320, "flight_id": "FH456"},
            {"airline": "SkyJet", "time": "15:00", "price": 380, "flight_id": "SJ789"},
        ]


class AmadeusApiClient(BaseFlightApiClient):
    """
    A client for the Amadeus for Self-Service API.
    Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET environment variables.
    """

    def __init__(self):
        super().__init__() # Initialize the base class to get the cache
        self.client_id = self._get_env_var("AMADEUS_CLIENT_ID")
        self.client_secret = self._get_env_var("AMADEUS_CLIENT_SECRET")
        self.base_url = self._get_env_var("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
        self._access_token = None
        self._token_expiry_time = 0

        if not self.client_id or not self.client_secret:
            logger.warning(
                "Amadeus API client is not configured. Please set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET."
            )

    def _get_access_token(self) -> Optional[str]:
        """Fetches a new OAuth2 access token from Amadeus if needed."""
        if self._access_token and time.time() < self._token_expiry_time:
            return self._access_token

        auth_url = f"{self.base_url}/v1/security/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        try:
            response = requests.post(auth_url, data=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._token_expiry_time = time.time() + data["expires_in"] - 300
            logger.info("Successfully retrieved new Amadeus access token.")
            return self._access_token
        except RequestException as e:
            logger.error(f"Failed to get Amadeus access token: {e}")
            return None

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the complex Amadeus response into the simple format our bot expects."""
        transformed_results = []
        carriers = response.get("dictionaries", {}).get("carriers", {})

        for offer in response.get("data", []):
            first_segment = offer["itineraries"][0]["segments"][0]
            transformed_results.append({
                "airline": carriers.get(first_segment["carrierCode"], "Unknown Airline"),
                "time": dateparser.parse(first_segment["departure"]["at"]).strftime("%H:%M"),
                "price": float(offer["price"]["total"]),
                "flight_id": f"{first_segment['carrierCode']}{first_segment['number']}"
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Searches for flights using the Amadeus API.
        NOTE: This example assumes city names are valid IATA codes (e.g., 'LHR', 'NYC').
        """
        access_token = self._get_access_token()
        if not access_token or not self.client_id or not self.client_secret:
            logger.error("Amadeus client not configured or could not get token. Cannot search.")
            return None

        # 1. Build the dictionary of parameters that will be sent to the API.
        params = {
            "originLocationCode": departure_city,
            "destinationLocationCode": destination_city,
            "departureDate": departure_date,
            "adults": passengers,
            "nonStop": "true",
            "currencyCode": "USD",
            "travelClass": travel_class.upper() if travel_class else "ECONOMY",
            "max": 5,
        }
        if return_date:
            params["returnDate"] = return_date

        # 2. Create a stable, order-independent cache key from the API parameters.
        #    A sorted tuple of the dictionary's items is a perfect hashable key.
        cache_key = tuple(sorted(params.items()))

        # 3. Check the cache using the new, robust key.
        if cache_key in self.cache:
            logger.info("Returning cached Amadeus results.")
            return self.cache[cache_key]

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        search_url = f"{self.base_url}/v2/shopping/flight-offers"
        logger.info(f"Searching Amadeus for flights with params: {params}")

        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            api_response = response.json()
            logger.info(f"Successfully received {len(api_response.get('data', []))} flight offers from Amadeus.")

            # Transform the response and store in the cache
            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response

            return transformed_response
        except RequestException as e:
            logger.error(f"API request failed: {e}")
            return None


class DuffelApiClient(BaseFlightApiClient):
    """
    A client for the Duffel API.
    Requires DUFFEL_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("DUFFEL_API_KEY")
        self.base_url = self._get_env_var("DUFFEL_BASE_URL", "https://api.duffel.com")
        self.api_version = self._get_env_var("DUFFEL_API_VERSION", "v1")

        if not self.api_key:
            logger.warning("Duffel API client is not configured. Please set DUFFEL_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the Duffel response into our standard format."""
        transformed_results = []
        for offer in response.get("data", {}).get("offers", []):
            first_segment = offer["slices"][0]["segments"][0]
            transformed_results.append({
                "airline": first_segment["operating_carrier"]["name"],
                "time": dateparser.parse(first_segment["departing_at"]).strftime("%H:%M"),
                "price": float(offer["total_amount"]),
                "flight_id": f"{first_segment['operating_carrier']['iata_code']}{first_segment['operating_carrier_flight_number']}"
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("DUFFEL_API_KEY not set. Cannot search with Duffel.")
            return None

        # 1. Build a dictionary of the core parameters for the cache key.
        #    This is more robust than using the complex final payload.
        cache_params = {
            "departure_city": departure_city,
            "destination_city": destination_city,
            "departure_date": departure_date,
            "return_date": return_date,
            "passengers": passengers,
            "travel_class": travel_class,
        }
        # Filter out None values to ensure consistency
        cache_params = {k: v for k, v in cache_params.items() if v is not None}
        cache_key = tuple(sorted(cache_params.items()))

        # 2. Check the cache.
        if cache_key in self.cache:
            logger.info("Returning cached Duffel results.")
            return self.cache[cache_key]

        search_url = f"{self.base_url}/air/offer_requests"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Duffel-Version": self.api_version,
        }

        slices = [{
            "origin": departure_city,
            "destination": destination_city,
            "departure_date": departure_date
        }]
        if return_date:
            slices.append({
                "origin": destination_city,
                "destination": departure_city,
                "departure_date": return_date,
            })

        payload = {
            "data": {
                "passengers": [{"type": "adult"} for _ in range(passengers)],
                "slices": slices,
                "cabin_class": travel_class if travel_class else "economy", # Use new parameter
            }
        }

        logger.info(f"Searching Duffel for flights with payload: {payload}")
        try:
            response = requests.post(search_url, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()
            logger.info(f"Successfully received {len(api_response.get('data', {}).get('offers',[]))} flight offers from Duffel.")

            # 3. Transform the response and store in the cache
            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response

            return transformed_response
        except RequestException as e:
            logger.error(f"Duffel API request failed: {e}")
            return None


class KiwiApiClient(BaseFlightApiClient):
    """
    A client for the Kiwi.com (Tequila) API.
    Requires KIWI_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("KIWI_API_KEY")
        self.base_url = self._get_env_var("KIWI_BASE_URL", "https://api.tequila.kiwi.com")
        self.partner_id = self._get_env_var("KIWI_PARTNER_ID", "picky")

        if not self.api_key:
            logger.warning("Kiwi.com API client is not configured. Please set KIWI_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the Kiwi.com response into our standard format."""
        transformed_results = []
        for route in response.get("data", []):
            first_segment = route["route"][0]
            transformed_results.append({
                "airline": route["airlines"][0], # Kiwi returns airline codes
                "time": dateparser.parse(first_segment["local_departure"]).strftime("%H:%M"),
                "price": float(route["price"]),
                "flight_id": f"{first_segment['airline']}{first_segment['flight_no']}"
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("KIWI_API_KEY not set. Cannot search with Kiwi.com.")
            return None

        search_url = f"{self.base_url}/v2/search"
        headers = {"apikey": self.api_key}
        
        # Kiwi uses a specific date format
        parsed_dep_date = dateparser.parse(departure_date).strftime("%d/%m/%Y")

        params = {
            "fly_from": departure_city,
            "fly_to": destination_city,
            "date_from": parsed_dep_date,
            "date_to": parsed_dep_date,
            "partner": self.partner_id,
            "cabin_class": travel_class if travel_class else "M", # 'M' is Economy for Kiwi
            "limit": 5,
        }
        if return_date:
            parsed_ret_date = dateparser.parse(return_date).strftime("%d/%m/%Y")
            params["return_from"] = parsed_ret_date
            params["return_to"] = parsed_ret_date

        # Create a stable cache key from the API parameters.
        cache_key = tuple(sorted(params.items()))

        # Check the cache.
        if cache_key in self.cache:
            logger.info("Returning cached Kiwi.com results.")
            return self.cache[cache_key]

        logger.info(f"Searching Kiwi.com for flights with params: {params}")
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()
            logger.info(f"Successfully received {len(api_response.get('data', []))} flight offers from Kiwi.com.")

            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response
            return transformed_response
        except RequestException as e:
            logger.error(f"Kiwi.com API request failed: {e}")
            return None


class SabreApiClient(BaseFlightApiClient):
    """
    A client for the Sabre Bargain Finder Max API.
    NOTE: Sabre's API is complex and this is a highly simplified representation.
    """
    def __init__(self):
        super().__init__()
        self.client_id = self._get_env_var("SABRE_CLIENT_ID")
        self.client_secret = self._get_env_var("SABRE_CLIENT_SECRET")
        self.base_url = self._get_env_var("SABRE_BASE_URL", "https://api.sabre.com") # Fictional URL
        self._access_token: Optional[str] = None
        self._token_expiry_time: float = 0
        if not self.client_id or not self.client_secret:
            logger.warning("Sabre API client is not configured. Please set SABRE_CLIENT_ID and SABRE_CLIENT_SECRET.")

    def _get_access_token(self) -> Optional[str]:
        """Fetches a new OAuth2 access token from Sabre if needed."""
        if self._access_token and time.time() < self._token_expiry_time:
            return self._access_token

        # Fictional token endpoint
        auth_url = f"{self.base_url}/v2/auth/token"
        # Sabre uses a different auth method, typically base64 encoded credentials
        encoded_creds = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded_creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {"grant_type": "client_credentials"}

        try:
            response = requests.post(auth_url, headers=headers, data=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._token_expiry_time = time.time() + data["expires_in"] - 300
            logger.info("Successfully retrieved new Sabre access token.")
            return self._access_token
        except RequestException as e:
            logger.error(f"Failed to get Sabre access token: {e}")
            return None

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the fictional Sabre Bargain Finder Max response."""
        transformed_results = []
        for itinerary in response.get("PricedItineraries", []):
            air_itinerary = itinerary.get("AirItinerary", {})
            first_segment = air_itinerary.get("OriginDestinationOptions", {}).get("OriginDestinationOption", [{}])[0].get("FlightSegment", [{}])[0]
            
            transformed_results.append({
                "airline": first_segment.get("OperatingAirline", {}).get("CompanyShortName", "Unknown Airline"),
                "time": dateparser.parse(first_segment.get("DepartureDateTime", "")).strftime("%H:%M"),
                "price": float(itinerary.get("AirItineraryPricingInfo", {}).get("ItinTotalFare", {}).get("TotalFare", {}).get("Amount", 0)),
                "flight_id": f"{first_segment.get('OperatingAirline', {}).get('Code', 'XX')}{first_segment.get('FlightNumber', '000')}"
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        access_token = self._get_access_token()
        if not access_token:
            logger.error("Sabre client not configured or could not get token. Cannot search.")
            return None

        search_url = f"{self.base_url}/v4/offers/shop" # Fictional endpoint
        headers = {"Authorization": f"Bearer {access_token}"}
        
        payload = {
            "OTA_AirLowFareSearchRQ": {
                "OriginDestinationInformation": [
                    {
                        "DepartureDateTime": f"{departure_date}T00:00:00",
                        "OriginLocation": {"LocationCode": departure_city},
                        "DestinationLocation": {"LocationCode": destination_city}
                    }
                ],
                "TravelerInfoSummary": {
                    "AirTravelerAvail": [{"PassengerTypeQuantity": [{"Code": "ADT", "Quantity": passengers}]}]
                }
            }
        }
        if return_date:
            payload["OTA_AirLowFareSearchRQ"]["OriginDestinationInformation"].append({
                "DepartureDateTime": f"{return_date}T00:00:00",
                "OriginLocation": {"LocationCode": destination_city},
                "DestinationLocation": {"LocationCode": departure_city}
            })

        cache_key = ("sabre", json.dumps(payload, sort_keys=True))

        if cache_key in self.cache:
            logger.info("Returning cached Sabre results.")
            return self.cache[cache_key]

        logger.info(f"Searching Sabre for flights with payload: {payload}")
        try:
            response = requests.post(search_url, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            api_response = response.json()

            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response
            return transformed_response
        except RequestException as e:
            logger.error(f"Sabre API request failed: {e}")
            return None

class AeroDataApiClient(BaseFlightApiClient):
    """
    Example client for a fictional 'AeroData' API.
    Requires AERODATA_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("AERODATA_API_KEY")
        self.base_url = self._get_env_var("AERODATA_BASE_URL", "https://api.aerodata.com")

        if not self.api_key:
            logger.warning("AeroData API client is not configured. Please set AERODATA_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the AeroData response into our standard format."""
        transformed_results = []
        for flight in response.get("flights", []):
            transformed_results.append({
                "airline": flight.get("carrier_name"),
                "time": dateparser.parse(flight.get("departure_time_local")).strftime("%H:%M"),
                "price": float(flight.get("price_usd")),
                "flight_id": flight.get("flight_number")
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("AERODATA_API_KEY not set. Cannot search.")
            return None

        search_url = f"{self.base_url}/v1/search"
        headers = {"X-API-Key": self.api_key}
        params = {
            "from": departure_city,
            "to": destination_city,
            "date": departure_date,
        }

        logger.info(f"Searching AeroData for flights with params: {params}")
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            return self._transform_response(response.json())
        except RequestException as e:
            logger.error(f"AeroData API request failed: {e}")
            return None


class FlightStatsApiClient(BaseFlightApiClient):
    """
    Client for the fictional 'FlightStats' API.
    Requires FLIGHTSTATS_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        # Use the helper method from the base class to safely get API keys
        self.api_key = self._get_env_var("FLIGHTSTATS_API_KEY")
        self.base_url = self._get_env_var("FLIGHTSTATS_BASE_URL", "https://api.flightstats.com")

        if not self.api_key:
            logger.warning("FlightStats API client is not configured. Please set FLIGHTSTATS_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the FlightStats response into our standard format."""
        # This is where you would write the logic to convert the specific
        # response structure of the FlightStats API into the format your
        # bot expects: a list of dictionaries with 'airline', 'time', 'price', 'flight_id'.
        # This is a crucial step for each new API provider.
        transformed_results = []
        for flight in response.get("scheduledFlights", []):
             transformed_results.append({
                "airline": flight.get("carrierFsCode"),
                "time": dateparser.parse(flight.get("departureTime")).strftime("%H:%M"),
                "price": 150.00, # Fictional price
                "flight_id": f"{flight.get('carrierFsCode')}{flight.get('flightNumber')}"
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None, # New parameter
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("FLIGHTSTATS_API_KEY not set. Cannot search.")
            return None

        # This is a fictional but plausible endpoint structure for demonstration.
        # The actual FlightStats API might have a different structure.
        search_url = f"{self.base_url}/v1/schedules/search"

        # Construct parameters for the API call, including the API key
        params = {
            "apiKey": self.api_key,
            "from": departure_city,
            "to": destination_city,
            "date": departure_date,
            "passengers": passengers,
            "class": travel_class if travel_class else "economy",
        }
        if return_date:
            params["returnDate"] = return_date

        # Create a stable cache key from the API parameters.
        cache_key = tuple(sorted(params.items()))

        # Check the cache.
        if cache_key in self.cache:
            logger.info("Returning cached FlightStats results.")
            return self.cache[cache_key]

        logger.info(f"Searching FlightStats for flights with params: {params}")
        try:
            response = requests.get(search_url, params=params, timeout=15)
            response.raise_for_status()
            api_response = response.json()

            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response
            return transformed_response
        except RequestException as e:
            logger.error(f"FlightStats API request failed: {e}")
            return None


class SkyscannerApiClient(BaseFlightApiClient):
    """
    A client for the Skyscanner API.
    Requires SKYSCRANNER_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("SKYSCRANNER_API_KEY")
        self.base_url = self._get_env_var("SKYSCRANNER_BASE_URL", "https://partners.api.skyscanner.net")

        if not self.api_key:
            logger.warning("Skyscanner API client is not configured. Please set SKYSCRANNER_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the Skyscanner response into our standard format."""
        transformed_results = []
        # This structure is fictional and needs to be adapted to the real Skyscanner API response
        for itinerary in response.get("itineraries", []):
            pricing_option = itinerary.get("pricingOptions", [{}])[0]
            first_leg = itinerary.get("legs", [{}])[0]
            
            transformed_results.append({
                "airline": first_leg.get("operatingCarrier", {}).get("name", "Unknown Airline"),
                "time": dateparser.parse(first_leg.get("departure", "")).strftime("%H:%M"),
                "price": float(pricing_option.get("price", {}).get("amount", 0)),
                "flight_id": first_leg.get("id", "SK-UNKNOWN")
            })
        return transformed_results

    def search(
        self,
        departure_city: str,
        departure_date: str,
        destination_city: Optional[str] = None,
        return_date: Optional[str] = None,
        passengers: int = 1,
        seat_preference: Optional[str] = None,
        preferred_airline: Optional[str] = None,
        frequent_flyer_number: Optional[str] = None,
        destinations: Optional[List[str]] = None,
        travel_class: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("SKYSCRANNER_API_KEY not set. Cannot search.")
            return None

        search_url = f"{self.base_url}/apiservices/v3/flights/live/search/create"
        headers = {"x-api-key": self.api_key}

        # Skyscanner API requires a specific payload structure
        payload = {
            "query": {
                "market": "US",
                "locale": "en-US",
                "currency": "USD",
                "queryLegs": [
                    {
                        "originPlaceId": {"iata": departure_city},
                        "destinationPlaceId": {"iata": destination_city},
                        "date": {"year": int(departure_date[:4]), "month": int(departure_date[5:7]), "day": int(departure_date[8:10])}
                    }
                ],
                "adults": passengers,
                "cabinClass": travel_class if travel_class else "CABIN_CLASS_ECONOMY"
            }
        }

        logger.info(f"Searching Skyscanner for flights with payload: {payload}")
        try:
            response = requests.post(search_url, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            api_response = response.json()
            return self._transform_response(api_response)
        except RequestException as e:
            logger.error(f"Skyscanner API request failed: {e}")
            return None

API_CLIENTS = {
    "amadeus": AmadeusApiClient,
    "duffel": DuffelApiClient,
    "kiwi": KiwiApiClient,
    "sabre": SabreApiClient,
    "aerodata": AeroDataApiClient, # Register the new client here
    "flightstats": FlightStatsApiClient,
    "skyscanner": SkyscannerApiClient,
    "mock": MockApiClient,
}

def get_api_client() -> BaseFlightApiClient:
    """
    Factory function to get the appropriate API client based on environment configuration.
    This is the single entry point for actions to get a flight client.
    """
    provider = os.environ.get("FLIGHT_API_PROVIDER", "mock").lower()
    client_class = API_CLIENTS.get(provider)

    if client_class:
        logger.info(f"Using {provider.capitalize()} API client.")
        return client_class()

    # If the provider is not in our dictionary, log a warning and default to Mock.
    logger.warning(
        f"Unknown FLIGHT_API_PROVIDER '{provider}'. "
        "Defaulting to Mock API client."
    )
    return MockApiClient()
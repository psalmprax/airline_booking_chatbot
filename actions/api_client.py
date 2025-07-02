import logging
import os
import time
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import dateparser
import requests
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)


class BaseFlightApiClient(ABC):
    """
    Abstract base class for all flight API clients.
    Defines the common interface for searching flights.
    """

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
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Searches for flights using the Amadeus API.
        NOTE: This example assumes city names are valid IATA codes (e.g., 'LHR', 'NYC').
        """
        access_token = self._get_access_token()
        if not access_token or not self.client_id or not self.client_secret:
            logger.error("Amadeus client not configured or could not get token. Cannot search.")
            return None

        params = {
            "originLocationCode": departure_city,
            "destinationLocationCode": destination_city,
            "departureDate": departure_date,
            "adults": passengers,
            "nonStop": "true",
            "currencyCode": "USD",
            "max": 5,
        }
        if return_date:
            params["returnDate"] = return_date

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
            return self._transform_response(api_response)
        except RequestException as e:
            logger.error(f"API request failed: {e}")
            return None


class DuffelApiClient(BaseFlightApiClient):
    """
    A client for the Duffel API.
    Requires DUFFEL_API_KEY environment variable.
    """
    def __init__(self):
        self.api_key = self._get_env_var("DUFFEL_API_KEY")
        self.base_url = self._get_env_var("DUFFEL_BASE_URL", "https://api.duffel.com")
        self.api_version = self._get_env_var("DUFFEL_API_VERSION", "v1")

        if not self.api_key:
            logger.warning("Duffel API client is not configured. Please set DUFFEL_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("DUFFEL_API_KEY not set. Cannot search with Duffel.")
            return None

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
                "cabin_class": "economy",
            }
        }

        logger.info(f"Searching Duffel for flights with payload: {payload}")
        try:
            response = requests.post(search_url, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()
            logger.info(f"Successfully received {len(api_response.get('data', {}).get('offers',[]))} flight offers from Duffel.")
            return self._transform_response(api_response)
        except RequestException as e:
            logger.error(f"Duffel API request failed: {e}")
            return None


class KiwiApiClient(BaseFlightApiClient):
    """
    A client for the Kiwi.com (Tequila) API.
    Requires KIWI_API_KEY environment variable.
    """
    def __init__(self):
        self.api_key = self._get_env_var("KIWI_API_KEY")
        self.base_url = self._get_env_var("KIWI_BASE_URL", "https://api.tequila.kiwi.com")
        self.partner_id = self._get_env_var("KIWI_PARTNER_ID", "picky")

        if not self.api_key:
            logger.warning("Kiwi.com API client is not configured. Please set KIWI_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("KIWI_API_KEY not set. Cannot search with Kiwi.com.")
            return None

        search_url = f"{self.base_url}/v2/search"  # noqa
        headers = {"apikey": self.api_key}
        
        # Kiwi uses date ranges, so we'll search for the whole day
        parsed_dep_date = dateparser.parse(departure_date).strftime("%d/%m/%Y")

        params = {
            "fly_from": departure_city,
            "fly_to": destination_city,
            "date_from": parsed_dep_date,
            "date_to": parsed_dep_date,
            "partner": self.partner_id,
            "limit": 5,
        }
        if return_date:
            parsed_ret_date = dateparser.parse(return_date).strftime("%d/%m/%Y")
            params["return_from"] = parsed_ret_date
            params["return_to"] = parsed_ret_date

        logger.info(f"Searching Kiwi.com for flights with params: {params}")
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()
            logger.info(f"Successfully received {len(api_response.get('data', []))} flight offers from Kiwi.com.")
            return self._transform_response(api_response)
        except RequestException as e:
            logger.error(f"Kiwi.com API request failed: {e}")
            return None


class SabreApiClient(BaseFlightApiClient):
    """
    Placeholder for a Sabre API client.
    NOTE: Sabre's API is complex and this is a highly simplified representation.
    """
    def __init__(self):
        self.client_id = self._get_env_var("SABRE_CLIENT_ID")
        self.client_secret = self._get_env_var("SABRE_CLIENT_SECRET")
        self.base_url = self._get_env_var("SABRE_BASE_URL")
        # ... other Sabre-specific initializations ...
        if not self.client_id or not self.client_secret:
            logger.warning("Sabre API client is not configured. Please set SABRE_CLIENT_ID and SABRE_CLIENT_SECRET.")

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
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.client_id or not self.client_secret:
            logger.error("SABRE_CLIENT_ID or SABRE_CLIENT_SECRET not set. Cannot search.")
            return None

        logger.warning("SabreApiClient is not fully implemented. This is a placeholder.")
        # In a real implementation, you would have logic here for:
        # 1. Getting a Sabre OAuth2 token.
        # 2. Building a complex JSON request for the Bargain Finder Max API.
        # 3. Making the POST request.
        # 4. Transforming the complex response.
        return []

class AeroDataApiClient(BaseFlightApiClient):
    """
    Example client for a fictional 'AeroData' API.
    Requires AERODATA_API_KEY environment variable.
    """
    def __init__(self):
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

API_CLIENTS = {
    "amadeus": AmadeusApiClient,
    "duffel": DuffelApiClient,
    "kiwi": KiwiApiClient,
    "sabre": SabreApiClient,
    "aerodata": AeroDataApiClient, # Register the new client here
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
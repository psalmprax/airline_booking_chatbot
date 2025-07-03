import logging
import os
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import requests
from requests.exceptions import RequestException

# Reuse the robust RedisCache from the flight API client
from .api_client import RedisCache

logger = logging.getLogger(__name__)


class BaseCarRentalApiClient(ABC):
    """
    Abstract base class for all car rental API clients.
    """
    def __init__(self):
        # Reuse the same Redis cache instance logic as the flight clients
        # We use a different database (db=2) to keep car and flight caches separate.
        self.cache = RedisCache(
            host=os.environ.get("REDIS_HOST", "redis"),
            port=int(os.environ.get("REDIS_PORT", 6379)),
            db=int(os.environ.get("REDIS_DB_CAR", 2)),
            ttl=int(os.environ.get("CACHE_TTL_SECONDS", 120))
        )

    def _get_env_var(self, var_name: str, default: Optional[str] = None) -> Optional[str]:
        """Helper function to get an environment variable."""
        return os.environ.get(var_name, default)

    @abstractmethod
    def search(
        self,
        location: str,
        pickup_date: str,
        dropoff_date: str,
        car_type: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Searches for rental cars based on the provided criteria.
        Must be implemented by all concrete subclasses.
        """
        raise NotImplementedError


class MockCarRentalApiClient(BaseCarRentalApiClient):
    """A mock client that returns static data for development and testing."""

    def search(
        self,
        location: str,
        pickup_date: str,
        dropoff_date: str,
        car_type: str
    ) -> List[Dict[str, Any]]:
        logger.warning("No real car rental API provider configured. Returning mock car data.")
        return [
            {"provider": "Hertz", "model": "Toyota Camry", "price_per_day": 55, "id": "HERTZ001"},
            {"provider": "Avis", "model": "Ford Explorer", "price_per_day": 75, "id": "AVIS002"},
            {"provider": "Enterprise", "model": "Nissan Versa", "price_per_day": 48, "id": "ENT003"},
        ]


class HertzApiClient(BaseCarRentalApiClient):
    """
    A fictional client for a Hertz Car Rental API.
    Requires HERTZ_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("HERTZ_API_KEY")
        self.base_url = self._get_env_var("HERTZ_BASE_URL", "https://api.hertz.com")

        if not self.api_key:
            logger.warning("Hertz API client is not configured. Please set HERTZ_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the fictional Hertz response into our standard format."""
        transformed_results = []
        for car in response.get("available_vehicles", []):
            transformed_results.append({
                "provider": "Hertz",
                "model": car.get("vehicle_name"),
                "price_per_day": float(car.get("daily_rate_usd")),
                "id": car.get("vehicle_id")
            })
        return transformed_results

    def search(
        self,
        location: str,
        pickup_date: str,
        dropoff_date: str,
        car_type: str
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("HERTZ_API_KEY not set. Cannot search.")
            return None

        search_url = f"{self.base_url}/v1/vehicles/search"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {
            "pickup_location": location,
            "pickup_date": pickup_date,
            "dropoff_date": dropoff_date,
            "vehicle_class": car_type,
        }

        cache_key = tuple(sorted(params.items()))
        if cache_key in self.cache:
            logger.info("Returning cached Hertz car rental results.")
            return self.cache[cache_key]

        logger.info(f"Searching Hertz for cars with params: {params}")
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()

            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response
            return transformed_response
        except RequestException as e:
            logger.error(f"Hertz API request failed: {e}")
            return None


class AvisApiClient(BaseCarRentalApiClient):
    """
    A fictional client for an Avis Car Rental API.
    Requires AVIS_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("AVIS_API_KEY")
        self.base_url = self._get_env_var("AVIS_BASE_URL", "https://api.avis.com")

        if not self.api_key:
            logger.warning("Avis API client is not configured. Please set AVIS_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the fictional Avis response into our standard format."""
        transformed_results = []
        for car in response.get("cars", []):
            transformed_results.append({
                "provider": "Avis",
                "model": car.get("car_model"),
                "price_per_day": float(car.get("rate")),
                "id": car.get("rental_id")
            })
        return transformed_results

    def search(
        self,
        location: str,
        pickup_date: str,
        dropoff_date: str,
        car_type: str
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("AVIS_API_KEY not set. Cannot search.")
            return None

        search_url = f"{self.base_url}/rentals/v2/search"
        headers = {"X-Api-Key": self.api_key} # Avis might use a different auth header
        params = {
            "pickup_loc": location,
            "start_date": pickup_date,
            "end_date": dropoff_date,
            "category": car_type,
        }

        cache_key = tuple(sorted(params.items()))
        if cache_key in self.cache:
            logger.info("Returning cached Avis car rental results.")
            return self.cache[cache_key]

        logger.info(f"Searching Avis for cars with params: {params}")
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()

            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response
            return transformed_response
        except RequestException as e:
            logger.error(f"Avis API request failed: {e}")
            return None


class EnterpriseApiClient(BaseCarRentalApiClient):
    """
    A fictional client for an Enterprise Rent-A-Car API.
    Requires ENTERPRISE_API_KEY environment variable.
    """
    def __init__(self):
        super().__init__()
        self.api_key = self._get_env_var("ENTERPRISE_API_KEY")
        self.base_url = self._get_env_var("ENTERPRISE_BASE_URL", "https://api.ehi.com")

        if not self.api_key:
            logger.warning("Enterprise API client is not configured. Please set ENTERPRISE_API_KEY.")

    def _transform_response(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transforms the fictional Enterprise response into our standard format."""
        transformed_results = []
        for option in response.get("rental_options", []):
            vehicle_info = option.get("vehicle_info", {})
            cost_info = option.get("cost", {})
            transformed_results.append({
                "provider": "Enterprise",
                "model": f"{vehicle_info.get('make')} {vehicle_info.get('model')}",
                "price_per_day": float(cost_info.get("per_day")),
                "id": option.get("option_id")
            })
        return transformed_results

    def search(
        self,
        location: str,
        pickup_date: str,
        dropoff_date: str,
        car_type: str
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            logger.error("ENTERPRISE_API_KEY not set. Cannot search.")
            return None

        search_url = f"{self.base_url}/v1/cars/availability"
        headers = {"Api-Token": self.api_key} # Enterprise might use a different auth header
        params = {
            "location_code": location,
            "pickup": pickup_date,
            "dropoff": dropoff_date,
            "car_group": car_type.upper(),
        }

        cache_key = tuple(sorted(params.items()))
        if cache_key in self.cache:
            logger.info("Returning cached Enterprise car rental results.")
            return self.cache[cache_key]

        logger.info(f"Searching Enterprise for cars with params: {params}")
        try:
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            api_response = response.json()

            transformed_response = self._transform_response(api_response)
            self.cache[cache_key] = transformed_response
            return transformed_response
        except RequestException as e:
            logger.error(f"Enterprise API request failed: {e}")
            return None


# --- Factory Function ---

CAR_RENTAL_API_CLIENTS = {
    "hertz": HertzApiClient,
    "avis": AvisApiClient,
    "enterprise": EnterpriseApiClient,
    "mock": MockCarRentalApiClient,
}

def get_car_rental_api_client() -> BaseCarRentalApiClient:
    """
    Factory function to get the appropriate car rental API client.
    """
    provider = os.environ.get("CAR_RENTAL_API_PROVIDER", "mock").lower()
    client_class = CAR_RENTAL_API_CLIENTS.get(provider)

    if client_class:
        logger.info(f"Using {provider.capitalize()} car rental API client.")
        return client_class()

    logger.warning(
        f"Unknown CAR_RENTAL_API_PROVIDER '{provider}'. "
        "Defaulting to Mock car rental API client."
    )
    return MockCarRentalApiClient()
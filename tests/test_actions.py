import datetime
from unittest.mock import MagicMock

import pytest
from rasa_sdk import Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, ActiveLoop, AllSlotsReset, FollowupAction

from actions.actions import ValidateFlightBookingForm, ActionSearchFlights, ActionSetAirportFromClarification, ActionStorePreference, ActionDeletePreference, ActionFlexibleSearch, ActionFlightStatus, ActionSetFlightAndAskConfirm, ActionConfirmBooking, ActionCancelBooking, ActionAskConfirmCancellation, ActionResumeBooking, ActionReviewAndConfirm, ActionHandleCorrection
from actions.api_client import BaseFlightApiClient
from actions.db_client import DatabaseClient


@pytest.fixture
def flight_booking_validator():
    """Provides a clean instance of the form validator for each test."""
    return ValidateFlightBookingForm()

@pytest.fixture
def mock_db_client(mocker):
    """Mocks the db_client used in actions.py."""
    mock_client = mocker.MagicMock(spec=DatabaseClient)
    mocker.patch('actions.actions.db_client', mock_client)
    return mock_client

@pytest.fixture
def mock_api_client(mocker):
    """Mocks the get_api_client factory function."""
    mock_client_instance = mocker.MagicMock(spec=BaseFlightApiClient)
    mocker.patch('actions.actions.get_api_client', return_value=mock_client_instance)
    return mock_client_instance

def test_validate_departure_date_valid_future(flight_booking_validator, monkeypatch):
    """
    Tests that a valid future date string is parsed and set correctly.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})

    # We mock `datetime.date.today()` to make the test deterministic.
    # Let's pretend today is Jan 15, 2025.
    class MockDate(datetime.date):
        """Mock date class for deterministic testing."""
        @classmethod
        def today(cls):
            return cls(2025, 1, 15)

    monkeypatch.setattr(datetime, 'date', MockDate)

    # Act: Validate "tomorrow"
    result = flight_booking_validator.validate_departure_date("tomorrow", dispatcher, tracker, {})

    # Assert: The result should be the next day in the correct format.
    assert result == {"departure_date": "2025-01-16"}
    assert len(dispatcher.messages) == 0  # No error messages should be sent

def test_validate_departure_date_correction(flight_booking_validator, monkeypatch):
    """
    Tests that correcting a departure date is acknowledged.
    """
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": { "departure_date": "2025-01-20" }
    })

    class MockDate(datetime.date):
        """Mock date class for deterministic testing."""
        @classmethod
        def today(cls):
            return cls(2025, 1, 15)

    monkeypatch.setattr(datetime, 'date', MockDate)

    # Act: Validate a new date
    result = flight_booking_validator.validate_departure_date("January 22nd 2025", dispatcher, tracker, {})

    # Assert: The slot is updated, and an acknowledgment message is sent.
    assert result == {"departure_date": "2025-01-22"}
    assert len(dispatcher.messages) == 1
    assert "Okay, I've updated the departure date to 2025-01-22." in dispatcher.messages[0]["text"]

def test_validate_departure_date_past_date(flight_booking_validator, monkeypatch):
    """
    Tests that a date in the past is rejected.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})

    # Pretend today is Jan 15, 2025.
    class MockDate(datetime.date):
        """Mock date class for deterministic testing."""
        @classmethod
        def today(cls):
            return cls(2025, 1, 15)

    monkeypatch.setattr(datetime, 'date', MockDate)

    # Act: Validate "yesterday"
    result = flight_booking_validator.validate_departure_date("yesterday", dispatcher, tracker, {})

    # Assert: The slot should be rejected (set to None) and an error message sent.
    assert result == {"departure_date": None}
    assert len(dispatcher.messages) == 1
    assert "You can't book a flight in the past!" in dispatcher.messages[0]["text"]


def test_validate_departure_date_invalid_string(flight_booking_validator):
    """
    Tests that a nonsensical date string is rejected.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})
    # Act
    result = flight_booking_validator.validate_departure_date("a week from whenever", dispatcher, tracker, {})
    # Assert
    assert result == {"departure_date": None}
    assert len(dispatcher.messages) == 1
    assert "couldn't understand" in dispatcher.messages[0]["text"]


def test_validate_return_date_valid(flight_booking_validator):
    """
    Tests that a return date after the departure date is validated correctly.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    # Simulate a tracker where the departure_date has already been set.
    tracker = Tracker.from_dict({
        "slots": {
            "departure_date": "2025-01-20"
        }
    })

    # Act
    result = flight_booking_validator.validate_return_date("January 25th 2025", dispatcher, tracker, {})

    # Assert
    assert result == {"return_date": "2025-01-25"}
    assert len(dispatcher.messages) == 0


def test_validate_return_date_before_departure(flight_booking_validator):
    """
    Tests that a return date on or before the departure date is rejected.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "departure_date": "2025-01-20"
        }
    })

    # Act
    result = flight_booking_validator.validate_return_date("January 19th 2025", dispatcher, tracker, {})

    # Assert
    assert result == {"return_date": None}
    assert len(dispatcher.messages) == 1
    assert "must be after the departure date" in dispatcher.messages[0]["text"]


def test_validate_return_date_without_departure_date(flight_booking_validator):
    """
    Tests the safeguard that rejects a return date if departure_date is not set.
    """
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"slots": {}})  # No departure_date
    result = flight_booking_validator.validate_return_date("any date", dispatcher, tracker, {})
    assert result == {"return_date": None}
    assert "I need to know the departure date first" in dispatcher.messages[0]["text"]

def test_action_store_preference_success(mocker):
    """
    Tests ActionStorePreference when the database operation is successful.
    """
    # Arrange
    action = ActionStorePreference()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "sender_id": "test_user_123",
        "latest_message": {
            "entities": [{"entity": "seat_preference", "value": "window"}]
        }
    })

    # Mock the DatabaseClient to simulate a successful write
    mock_db_client = mocker.MagicMock(spec=DatabaseClient)
    mock_db_client.pool = True # Simulate that a pool exists
    mock_db_client.store_user_preference.return_value = True
    mocker.patch('actions.actions.db_client', mock_db_client)

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    mock_db_client.store_user_preference.assert_called_once_with("test_user_123", "window")
    assert "I've saved your preference" in dispatcher.messages[0]["text"]


def test_action_store_preference_db_error(mocker):
    """
    Tests ActionStorePreference when the database throws an error.
    """
    # Arrange
    action = ActionStorePreference()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "sender_id": "test_user_456",
        "latest_message": {
            "entities": [{"entity": "seat_preference", "value": "aisle"}]
        }
    })

    # Mock the DatabaseClient to simulate a failed write
    mock_db_client = mocker.MagicMock(spec=DatabaseClient)
    mock_db_client.pool = True # Simulate that a pool exists
    mock_db_client.store_user_preference.return_value = False
    mocker.patch('actions.actions.db_client', mock_db_client)

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    mock_db_client.store_user_preference.assert_called_once_with("test_user_456", "aisle")
    assert "couldn't save your preference due to a technical issue" in dispatcher.messages[0]["text"]


def test_action_store_preference_no_entity(mocker):
    """
    Tests ActionStorePreference when no seat_preference entity is found.
    """
    # Arrange
    action = ActionStorePreference()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "sender_id": "test_user_789",
        "latest_message": {
            "entities": []  # No entities extracted
        }
    })

    # Mock the db_client to ensure its methods are not called.
    mock_db_client = mocker.MagicMock(spec=DatabaseClient)
    mocker.patch('actions.actions.db_client', mock_db_client)

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    assert "I didn't catch that preference" in dispatcher.messages[0]["text"]
    mock_db_client.store_user_preference.assert_not_called()


def test_action_flexible_search_beach_with_budget():
    """
    Tests ActionFlexibleSearch when user provides 'beach' trip_type and a budget.
    """
    # Arrange
    action = ActionFlexibleSearch()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "latest_message": {
            "entities": [
                {"entity": "trip_type", "value": "beach"},
                {"entity": "budget", "value": "500"}
            ]
        }
    })

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    assert len(dispatcher.messages) == 2
    assert dispatcher.messages[0]["text"] == "I am searching for a beach trip with a budget of $500..."
    assert "Cancun" in dispatcher.messages[1]["text"]


def test_action_flexible_search_adventure_no_budget():
    """
    Tests ActionFlexibleSearch when user provides 'adventure' trip_type without a budget.
    """
    action = ActionFlexibleSearch()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "latest_message": {"entities": [{"entity": "trip_type", "value": "adventure"}]}
    })
    action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 2
    assert dispatcher.messages[0]["text"] == "I am searching for a adventure trip with a budget of $any..."
    assert "Costa Rica" in dispatcher.messages[1]["text"]


def test_action_flexible_search_default_no_entities():
    """
    Tests ActionFlexibleSearch when user provides no entities.
    """
    action = ActionFlexibleSearch()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"entities": []}}) # No entities
    action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 2
    assert dispatcher.messages[0]["text"] == "I am searching for a any trip with a budget of $any..."
    assert "Prague" in dispatcher.messages[1]["text"]


def test_action_flight_status_with_id():
    """
    Tests the 'happy path' where a flight_id is present in the tracker.
    """
    action = ActionFlightStatus()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "latest_message": {
            "entities": [{"entity": "flight_id", "value": "UA456"}]
        }
    })
    action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 2
    assert dispatcher.messages[0]["text"] == "Checking status for flight UA456..."
    assert dispatcher.messages[1]["text"] == "Flight UA456 is on time. It will depart at 11:30."


def test_action_flight_status_without_id():
    """
    Tests the 'unhappy path' where no flight_id is provided.
    """
    action = ActionFlightStatus()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"entities": []}})
    action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 1
    assert dispatcher.messages[0]["text"] == "I need a flight ID to check the status."


def test_validate_city_unambiguous(flight_booking_validator, mock_db_client):
    """Tests city validation when the DB returns a single, unambiguous airport."""
    dispatcher = CollectingDispatcher()
    mock_db_client.get_airports_for_city.return_value = [{"name": "Paris Charles de Gaulle", "iata": "CDG"}]

    result = flight_booking_validator.validate_departure_city("Paris", dispatcher, Tracker.from_dict({}), {})

    assert result == {"departure_city": "Paris", "departure_city_iata": "CDG"}
    mock_db_client.get_airports_for_city.assert_called_once_with("Paris")
    assert len(dispatcher.messages) == 0

def test_validate_city_ambiguous(flight_booking_validator, mock_db_client):
    """Tests city validation when the DB returns multiple airports, triggering clarification."""
    dispatcher = CollectingDispatcher()
    mock_db_client.get_airports_for_city.return_value = [
        {"name": "John F. Kennedy Intl.", "iata": "JFK"},
        {"name": "LaGuardia Airport", "iata": "LGA"}
    ]

    result = flight_booking_validator.validate_departure_city("New York", dispatcher, Tracker.from_dict({}), {})

    # The form should pause and ask for clarification
    assert result == {
        "departure_city": None,
        "departure_city_iata": None,
        "ambiguous_city_name": "New York",
        "ambiguous_city_slot": "departure_city"
    }
    # Check that a message with buttons was sent
    assert len(dispatcher.messages) == 1
    assert "multiple airports for New York" in dispatcher.messages[0]["text"]
    assert len(dispatcher.messages[0]["buttons"]) == 2
    assert dispatcher.messages[0]["buttons"][0]["payload"] == '/select_airport{"selected_iata_code": "JFK"}'

def test_validate_city_invalid(flight_booking_validator, mock_db_client):
    """Tests city validation for an unknown city."""
    dispatcher = CollectingDispatcher()
    mock_db_client.get_airports_for_city.return_value = []

    result = flight_booking_validator.validate_departure_city("Atlantis", dispatcher, Tracker.from_dict({}), {})

    assert result == {"departure_city": None, "departure_city_iata": None}
    assert len(dispatcher.messages) == 1
    assert "I don't recognize 'Atlantis'" in dispatcher.messages[0]["text"]

def test_action_search_flights_success(mock_api_client):
    """Tests ActionSearchFlights for a successful search."""
    action = ActionSearchFlights()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "departure_city_iata": "JFK", "destination_city_iata": "LHR",
            "departure_date": "2025-02-10", "number_of_passengers": 1
        }
    })
    mock_api_client.search.return_value = [
        {"airline": "TestAir", "time": "10:00", "price": 500, "flight_id": "TA100"}
    ]

    action.run(dispatcher, tracker, {})

    mock_api_client.search.assert_called_once()
    assert "Here are some flights I found:" in dispatcher.messages[1]["text"]
    assert len(dispatcher.messages[1]["buttons"]) == 1

def test_action_search_flights_no_results(mock_api_client):
    """Tests ActionSearchFlights when the API returns no results."""
    action = ActionSearchFlights()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "departure_city_iata": "JFK", "destination_city_iata": "LHR",
            "departure_date": "2025-02-10", "number_of_passengers": 1
        }
    })
    mock_api_client.search.return_value = [] # API call succeeded but no flights found

    action.run(dispatcher, tracker, {})

    mock_api_client.search.assert_called_once()
    assert "I'm sorry, I couldn't find any flights" in dispatcher.messages[1]["text"]

def test_action_search_flights_api_failure(mock_api_client):
    """Tests ActionSearchFlights when the API call fails."""
    action = ActionSearchFlights()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "departure_city_iata": "JFK", "destination_city_iata": "LHR",
            "departure_date": "2025-02-10", "number_of_passengers": 1
        }
    })
    mock_api_client.search.return_value = None # API call failed

    action.run(dispatcher, tracker, {})

    mock_api_client.search.assert_called_once()
    assert dispatcher.messages[1]["response"] == "utter_api_failure"


def test_required_slots_no_trip_type(flight_booking_validator):
    """
    Tests that 'booking_trip_type' is the only required slot initially.
    """
    tracker = Tracker.from_dict({"slots": {}})
    required = flight_booking_validator.required_slots(tracker)
    assert required == ["booking_trip_type"]


def test_required_slots_one_way(flight_booking_validator):
    """
    Tests the required slots for a 'one-way' trip.
    """
    tracker = Tracker.from_dict({"slots": {"booking_trip_type": "one-way"}})
    required = flight_booking_validator.required_slots(tracker)
    expected = [
        "booking_trip_type",
        "departure_city",
        "destination_city",
        "departure_date",
        "number_of_passengers",
        "preferred_airline",
        "frequent_flyer_number",
    ]
    assert required == expected


def test_validate_number_of_passengers_valid(flight_booking_validator):
    """
    Tests that a valid number of passengers is accepted and converted to an integer.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})

    # Act
    result = flight_booking_validator.validate_number_of_passengers("3", dispatcher, tracker, {})

    # Assert
    assert result == {"number_of_passengers": 3}
    assert len(dispatcher.messages) == 0

def test_validate_number_of_passengers_correction(flight_booking_validator):
    """
    Tests that correcting the number of passengers is acknowledged.
    """
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": { "number_of_passengers": 1 }
    })
    result = flight_booking_validator.validate_number_of_passengers("2", dispatcher, tracker, {})
    assert result == {"number_of_passengers": 2}
    assert len(dispatcher.messages) == 1
    assert "Okay, I've updated the number of passengers to 2." in dispatcher.messages[0]["text"]


def test_validate_number_of_passengers_invalid_string(flight_booking_validator):
    """
    Tests that a non-numeric string is rejected.
    """
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})
    result = flight_booking_validator.validate_number_of_passengers("a couple", dispatcher, tracker, {})
    assert result == {"number_of_passengers": None}
    assert "don't understand 'a couple' as a number" in dispatcher.messages[0]["text"]


def test_validate_number_of_passengers_zero(flight_booking_validator):
    """
    Tests that zero or a negative number of passengers is rejected.
    """
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})
    result = flight_booking_validator.validate_number_of_passengers("0", dispatcher, tracker, {})
    assert result == {"number_of_passengers": None}
    assert "must be at least 1" in dispatcher.messages[0]["text"]


def test_required_slots_round_trip(flight_booking_validator):
    """
    Tests the required slots for a 'round trip', ensuring 'return_date' is included.
    """
    tracker = Tracker.from_dict({"slots": {"booking_trip_type": "round trip"}})
    required = flight_booking_validator.required_slots(tracker)
    expected = [
        "booking_trip_type",
        "departure_city",
        "destination_city",
        "departure_date",
        "return_date",
        "number_of_passengers",
        "preferred_airline",
        "frequent_flyer_number",
    ]
    assert required == expected


def test_required_slots_multi_city_adding_more(flight_booking_validator):
    """
    Tests required slots for 'multi-city' when the user is still adding destinations.
    """
    tracker = Tracker.from_dict({"slots": {"booking_trip_type": "multi-city", "add_more_destinations": True}})
    required = flight_booking_validator.required_slots(tracker)
    expected = [
        "booking_trip_type",
        "departure_city",
        "next_destination",
        "add_more_destinations",
        "departure_date",
        "number_of_passengers",
        "preferred_airline",
        "frequent_flyer_number",
    ]
    assert required == expected

def test_required_slots_multi_city_done(flight_booking_validator):
    """
    Tests required slots for 'multi-city' after the user has finished adding destinations.
    """
    tracker = Tracker.from_dict({"slots": {"booking_trip_type": "multi-city", "add_more_destinations": False}})
    required = flight_booking_validator.required_slots(tracker)
    expected = [
        "booking_trip_type",
        "departure_city",
        "departure_date",
        "number_of_passengers",
        "preferred_airline",
        "frequent_flyer_number",
    ]
    assert required == expected


def test_validate_preferred_airline_valid(flight_booking_validator):
    """Tests that a valid airline is accepted."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"intent": {"name": "inform"}}})
    result = flight_booking_validator.validate_preferred_airline("AwesomeAirlines", dispatcher, tracker, {})
    assert result == {"preferred_airline": "AwesomeAirlines"}
    assert "Okay, I'll look for flights on AwesomeAirlines." in dispatcher.messages[0]["text"]


def test_validate_preferred_airline_deny(flight_booking_validator):
    """Tests that the user can deny having a preferred airline."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"intent": {"name": "deny"}}})
    result = flight_booking_validator.validate_preferred_airline("no", dispatcher, tracker, {})
    assert result == {"preferred_airline": None}
    assert "Okay, I'll search all available airlines." in dispatcher.messages[0]["text"]


def test_validate_preferred_airline_no_entity(flight_booking_validator):
    """Tests that the form moves on if no airline is extracted and it's not a deny."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"intent": {"name": "inform"}}})
    result = flight_booking_validator.validate_preferred_airline(None, dispatcher, tracker, {})
    assert result == {"preferred_airline": None}
    assert len(dispatcher.messages) == 0


def test_validate_frequent_flyer_number_deny(flight_booking_validator):
    """Tests that the user can deny providing a frequent flyer number."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"intent": {"name": "deny"}}})
    result = flight_booking_validator.validate_frequent_flyer_number("no", dispatcher, tracker, {})
    assert result == {"frequent_flyer_number": None}
    assert "Okay, no problem." in dispatcher.messages[0]["text"]


def test_validate_frequent_flyer_number_no_airline(flight_booking_validator):
    """Tests validation fails if preferred_airline is not set."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {"preferred_airline": None},
        "latest_message": {"intent": {"name": "inform"}}
    })
    result = flight_booking_validator.validate_frequent_flyer_number("12345", dispatcher, tracker, {})
    assert result == {"frequent_flyer_number": None}
    assert "I need to know your preferred airline" in dispatcher.messages[0]["text"]


def test_validate_frequent_flyer_number_no_value(flight_booking_validator):
    """Tests validation when no frequent flyer number is provided by the user."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {"preferred_airline": "AwesomeAirlines"},
        "latest_message": {"intent": {"name": "inform"}}
    })
    result = flight_booking_validator.validate_frequent_flyer_number(None, dispatcher, tracker, {})
    assert result == {"frequent_flyer_number": None}
    assert len(dispatcher.messages) == 0


def test_validate_frequent_flyer_number_unknown_airline(flight_booking_validator):
    """Tests that any number is accepted for an airline with no defined format."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {"preferred_airline": "UnknownAir"},
        "latest_message": {"intent": {"name": "inform"}}
    })
    result = flight_booking_validator.validate_frequent_flyer_number("any-format-123", dispatcher, tracker, {})
    assert result == {"frequent_flyer_number": "any-format-123"}
    assert "Great, I've added your frequent flyer number" in dispatcher.messages[0]["text"]


@pytest.mark.parametrize("airline, number, is_valid", [
    ("AwesomeAirlines", "AA12345678", True),
    ("AwesomeAirlines", "aa12345678", False),
    ("AwesomeAirlines", "AA1234567", False),
    ("AwesomeAirlines", "1234567890", False),
    ("FlyHigh", "F-1234567", True),
    ("FlyHigh", "F-123456", False),
    ("FlyHigh", "f-1234567", False),
])
def test_validate_frequent_flyer_number_specific_formats(flight_booking_validator, airline, number, is_valid):
    """Tests validation against specific airline formats."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {"preferred_airline": airline},
        "latest_message": {"intent": {"name": "inform"}}
    })
    result = flight_booking_validator.validate_frequent_flyer_number(number, dispatcher, tracker, {})

    if is_valid:
        assert result == {"frequent_flyer_number": number}
        assert "Great, I've added your frequent flyer number" in dispatcher.messages[0]["text"]
    else:
        assert result == {"frequent_flyer_number": None}
        assert "That doesn't look like a valid frequent flyer number" in dispatcher.messages[0]["text"]
        assert f"for {airline}" in dispatcher.messages[0]["text"]


@pytest.mark.parametrize("user_input, expected_result", [
    ("one-way", {"booking_trip_type": "one-way"}),
    ("oneway please", {"booking_trip_type": "one-way"}),
    ("a round trip", {"booking_trip_type": "round trip"}),
    ("multi-city", {
        "booking_trip_type": "multi-city",
        "add_more_destinations": True,
        "destinations": [],
        "destinations_iata": []
    }),
])
def test_validate_booking_trip_type_valid(flight_booking_validator, user_input, expected_result):
    """
    Tests that various valid trip type inputs are normalized correctly.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})

    # Act
    result = flight_booking_validator.validate_booking_trip_type(user_input, dispatcher, tracker, {})

    # Assert
    assert result == expected_result
    assert len(dispatcher.messages) == 0


def test_validate_booking_trip_type_invalid(flight_booking_validator):
    """Tests that an invalid trip type is rejected."""
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})
    result = flight_booking_validator.validate_booking_trip_type("a return ticket", dispatcher, tracker, {})
    assert result == {"booking_trip_type": None}
    assert "I didn't understand the trip type" in dispatcher.messages[0]["text"]


@pytest.mark.parametrize("slot_value, expected_bool", [
    (True, True),   # Corresponds to 'affirm' intent
    (False, False), # Corresponds to 'deny' intent
    (None, False)   # Corresponds to any other intent or no input
])
def test_validate_add_more_destinations(flight_booking_validator, slot_value, expected_bool):
    """
    Tests that the add_more_destinations validation correctly handles boolean inputs
    from the from_intent mapping.
    """
    # Arrange
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})

    # Act
    result = flight_booking_validator.validate_add_more_destinations(
        slot_value, dispatcher, tracker, {}
    )

    # Assert
    assert result == {"add_more_destinations": expected_bool}
    assert len(dispatcher.messages) == 0


def test_action_confirm_booking():
    """
    Tests that the ActionConfirmBooking sends the correct confirmation message.
    """
    # Arrange
    action = ActionConfirmBooking()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})  # State doesn't matter for this simple action

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    assert len(dispatcher.messages) == 1
    assert dispatcher.messages[0]["response"] == "utter_booking_confirmed"


def test_action_set_flight_and_ask_confirm_with_id():
    """
    Tests that the confirmation action works correctly when a flight_id is present.
    """
    # Arrange
    action = ActionSetFlightAndAskConfirm()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "latest_message": {
            "entities": [{"entity": "flight_id", "value": "FH456"}]
        }
    })

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    assert len(dispatcher.messages) == 1
    message = dispatcher.messages[0]
    assert message["text"] == "You've selected flight FH456. Shall I go ahead and book it?"
    assert len(message["buttons"]) == 2
    assert message["buttons"][0]["title"] == "Yes, confirm booking"
    assert message["buttons"][0]["payload"] == "/confirm_booking"


def test_action_set_flight_and_ask_confirm_without_id():
    """
    Tests that the confirmation action handles the case where no flight_id is found.
    """
    # Arrange
    action = ActionSetFlightAndAskConfirm()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"latest_message": {"entities": []}})

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    assert len(dispatcher.messages) == 1
    assert "something went wrong" in dispatcher.messages[0]["text"]


def test_action_delete_preference_success(mocker):
    """
    Tests that ActionDeletePreference works when a preference is successfully deleted.
    """
    # Arrange
    action = ActionDeletePreference()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"sender_id": "test_user"})

    mock_db_client = mocker.MagicMock(spec=DatabaseClient)
    mock_db_client.pool = True
    mock_db_client.delete_user_preference.return_value = True # Simulate successful deletion
    mocker.patch('actions.actions.db_client', mock_db_client)

    # Act
    action.run(dispatcher, tracker, {})

    # Assert
    mock_db_client.delete_user_preference.assert_called_once_with("test_user")
    assert dispatcher.messages[0]["response"] == "utter_preference_deleted"


def test_action_delete_preference_not_found(mocker):
    """
    Tests that ActionDeletePreference works when there is no preference to delete.
    """
    action = ActionDeletePreference()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({"sender_id": "test_user"})

    mock_db_client = mocker.MagicMock(spec=DatabaseClient)
    mock_db_client.pool = True
    mock_db_client.delete_user_preference.return_value = False # Simulate no record was found/deleted
    mocker.patch('actions.actions.db_client', mock_db_client)

    action.run(dispatcher, tracker, {})

    mock_db_client.delete_user_preference.assert_called_once_with("test_user")
    assert dispatcher.messages[0]["response"] == "utter_no_preference_to_delete"
    
def test_action_ask_confirm_cancellation():
    """Tests that the ask confirmation action sends a message and sets a slot."""
    action = ActionAskConfirmCancellation()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})
    events = action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 1
    assert dispatcher.messages[0]["response"] == "utter_ask_confirm_cancellation"
    assert events == [SlotSet("cancellation_pending", True)]


def test_action_resume_booking():
    """Tests that the resume action sends a generic message if no slots are filled."""
    action = ActionResumeBooking()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": { "cancellation_pending": True }
    })
    events = action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 1
    assert dispatcher.messages[0]["response"] == "utter_resume_booking"
    assert events == [SlotSet("cancellation_pending", None)]


def test_action_resume_booking_with_filled_slots():
    """Tests that the resume action summarizes filled slots in a sentence."""
    action = ActionResumeBooking()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "booking_trip_type": "one-way",
            "departure_city": "London",
            "number_of_passengers": 2,
            "cancellation_pending": True
        }
    })
    events = action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 1
    message = dispatcher.messages[0]
    assert message["response"] == "utter_form_summary_sentence"
    summary = message["template_vars"]["summary"]
    assert summary == "a one-way trip for 2 passenger(s) from London"
    assert events == [SlotSet("cancellation_pending", None)]


def test_action_cancel_booking():
    """Tests that the cancellation action deactivates the loop and resets slots."""
    action = ActionCancelBooking()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({})
    events = action.run(dispatcher, tracker, {})
    assert len(dispatcher.messages) == 1
    assert dispatcher.messages[0]["response"] == "utter_ok_cancelled"
    assert any(isinstance(e, ActiveLoop) and e.name is None for e in events)
    assert any(isinstance(e, AllSlotsReset) for e in events)


def test_action_review_and_confirm(mocker):
    """Tests that the review action summarizes details and provides confirmation buttons."""
    action = ActionReviewAndConfirm()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "booking_trip_type": "one-way",
            "departure_city": "London",
            "destination_city": "Paris",
            "number_of_passengers": 1,
            "departure_date": "2025-03-10"
        }
    })

    action.run(dispatcher, tracker, {})

    assert len(dispatcher.messages) == 1
    message = dispatcher.messages[0]
    assert message["response"] == "utter_ask_confirm_details"
    summary = message["template_vars"]["summary"]
    assert summary == "a one-way trip for 1 passenger(s) from London to Paris departing on 2025-03-10"
    assert len(message["buttons"]) == 2
    assert message["buttons"][0]["payload"] == "/confirm_details"
    assert message["buttons"][1]["payload"] == "/deny_details"
    assert message["buttons"][1]["title"] == "No, something's wrong"

def test_action_handle_correction(mocker):
    """Tests that the correction action validates and sets the new slot value."""
    action = ActionHandleCorrection()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "departure_city": "London",
            "destination_city": "Paris"
        },
        "latest_message": {
            "intent": {"name": "correct_info"},
            "entities": [{"entity": "city", "value": "Berlin", "role": "destination"}]
        }
    })

    # Mock the validator and its method
    mock_validator = mocker.patch('actions.actions.ValidateFlightBookingForm', autospec=True).return_value
    mock_validator.validate_destination_city.return_value = {
        "destination_city": "Berlin",
        "destination_city_iata": "BER"
    }

    events = action.run(dispatcher, tracker, {})

    assert SlotSet("destination_city", "Berlin") in events
    assert SlotSet("destination_city_iata", "BER") in events
    assert FollowupAction("action_review_and_confirm") in events
    mock_validator.validate_destination_city.assert_called_once()

def test_action_handle_correction_multi_city(mocker):
    """Tests correcting the last destination of a multi-city trip."""
    action = ActionHandleCorrection()
    dispatcher = CollectingDispatcher()
    tracker = Tracker.from_dict({
        "slots": {
            "booking_trip_type": "multi-city",
            "departure_city": "London",
            "destinations": ["Paris", "Rome"],
            "destinations_iata": ["CDG", "FCO"]
        },
        "latest_message": {
            "intent": {"name": "correct_info"},
            "entities": [{"entity": "city", "value": "Berlin", "role": "destination"}]
        }
    })

    # Mock the validator and its _validate_city helper method
    mock_validator = mocker.patch('actions.actions.ValidateFlightBookingForm', autospec=True).return_value
    mock_validator._validate_city.return_value = {
        "next_destination": "Berlin",
        "next_destination_iata": "BER"
    }

    events = action.run(dispatcher, tracker, {})

    mock_validator._validate_city.assert_called_once_with("next_destination", "Berlin", dispatcher)
    assert SlotSet("destinations", ["Paris", "Berlin"]) in events
    assert SlotSet("destinations_iata", ["CDG", "BER"]) in events
    assert FollowupAction("action_review_and_confirm") in events

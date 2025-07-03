import logging
import os
import re
from datetime import date
import dateparser
from typing import Any, Text, Dict, List, Optional

import psycopg2
from psycopg2 import pool
from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import SlotSet, ActiveLoop, AllSlotsReset, FollowupAction
from rasa_sdk.executor import CollectingDispatcher
from thefuzz import process

from .api_client import get_api_client
from .car_rental_api_client import get_car_rental_api_client
from .db_client import DatabaseClient
from rasa_sdk.types import DomainDict

logger = logging.getLogger(__name__)

# --- Airline-Specific Configurations ---
# This dictionary holds the validation rules for frequent flyer numbers for different airlines.
# It can be easily extended with new airlines and their formats.
FREQUENT_FLYER_FORMATS = {
    "awesomeairlines": {
        "regex": r"^[A-Z]{2}\d{8}$",
        "example": "AA12345678"
    },
    "flyhigh": {
        "regex": r"^[A-Z]-\d{7}$",
        "example": "F-1234567"
    },
    # Add other airlines here as needed
}

# In a real app, this would be a call to a database or an API
db_pool = None
try:
    # Initialize the connection pool when the action server starts.
    # minconn=1 ensures at least one connection is ready.
    # maxconn=10 limits the number of concurrent connections.
    db_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host="db",
        database=os.environ.get("POSTGRES_DB"),
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )
    logger.info("Database connection pool created successfully.")
except psycopg2.OperationalError as e:
    logger.error(f"Could not create the database connection pool: {e}")
    # The action server will continue to run, but DB actions will fail.

# Initialize clients and load data at startup
db_client = DatabaseClient(db_pool)
if db_pool:
    # In a real app, schema migrations would be handled by a tool like Alembic.
    # This is for demonstration purposes.
    db_client.initialize_schema()

def _build_summary_sentence(tracker: Tracker) -> str:
    """Builds a natural language summary of the booking details from the tracker."""
    # Get all the relevant slots
    trip_type = tracker.get_slot("booking_trip_type")
    dep_city = tracker.get_slot("departure_city")
    dest_city = tracker.get_slot("destination_city")
    destinations = tracker.get_slot("destinations")
    dep_date = tracker.get_slot("departure_date")
    ret_date = tracker.get_slot("return_date")
    passengers = tracker.get_slot("number_of_passengers")
    airline = tracker.get_slot("preferred_airline")
    travel_class = tracker.get_slot("travel_class")

    # Build a list of phrases based on filled slots
    phrases = []
    if trip_type:
        phrases.append(f"a {trip_type} trip")
    if passengers is not None:
        phrases.append(f"for {passengers} passenger(s)")
    if dep_city:
        route = f"from {dep_city}"
        if destinations: # For multi-city trips
            route += " to " + " -> ".join(destinations)
        elif dest_city: # For one-way or round-trip
            route += f" to {dest_city}"
        phrases.append(route)
    if dep_date:
        phrases.append(f"departing on {dep_date}")
    if ret_date:
        phrases.append(f"and returning on {ret_date}")
    if travel_class:
        phrases.append(f"in {travel_class} class")
    if airline:
        phrases.append(f"on {airline}")

    return " ".join(phrases)


def _validate_date(
    date_string: Any,
    slot_name: str,
    dispatcher: CollectingDispatcher,
) -> Optional[Text]:
    """
    Parses a date string, validates it's in the future, and returns it in YYYY-MM-DD format.
    Returns None if validation fails.
    """
    if not date_string:
        return None

    parsed_date = dateparser.parse(str(date_string), settings={'PREFER_DATES_FROM': 'future'})

    if not parsed_date:
        dispatcher.utter_message(text=f"I'm sorry, I couldn't understand '{date_string}' as a date. Could you be more specific?")
        return None

    if parsed_date.date() < date.today():
        dispatcher.utter_message(text=f"The {slot_name.replace('_', ' ')} can't be in the past! Please provide a future date.")
        return None

    return parsed_date.strftime("%Y-%m-%d")


def _validate_end_date(
    end_date_string: Any,
    start_date_string: Optional[Text],
    slot_name: str,
    start_date_slot_name: str,
    dispatcher: CollectingDispatcher,
) -> Optional[Text]:
    """
    Validates an end date, ensuring it's after the start date.
    Uses _validate_date for initial validation. Returns None if validation fails.
    """
    if not start_date_string:
        dispatcher.utter_message(text=f"I need to know the {start_date_slot_name.replace('_', ' ')} first.")
        return None

    validated_end_date = _validate_date(end_date_string, slot_name, dispatcher)
    if not validated_end_date:
        return None

    start_date_obj = dateparser.parse(str(start_date_string))
    end_date_obj = dateparser.parse(validated_end_date)

    if end_date_obj.date() <= start_date_obj.date():
        dispatcher.utter_message(text=f"The {slot_name.replace('_', ' ')} must be after the {start_date_slot_name.replace('_', ' ')}.")
        return None

    return validated_end_date

class ActionStorePreference(Action):
    """Saves a user's preference to the 'database'."""

    def name(self) -> Text:
        return "action_store_preference"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        seat_pref = next(tracker.get_latest_entity_values("seat_preference"), None)

        if not seat_pref:
            dispatcher.utter_message(text="I didn't catch that preference. You can say things like 'I prefer a window seat'.")
            return []

        # Use the conversation ID as the user identifier
        user_id = tracker.sender_id

        if not db_client.pool:
            dispatcher.utter_message(text="I'm sorry, I'm having trouble accessing my memory right now. Please try again later.")
            return []
        success = db_client.store_user_preference(user_id, "seat_preference", seat_pref)
        if success:
            dispatcher.utter_message(text=f"Great! I've saved your preference for a {seat_pref} seat for future bookings.")
        else:
            dispatcher.utter_message(text="I couldn't save your preference due to a technical issue.")

        return []

class ActionDeletePreference(Action):
    """Deletes a user's stored preference from the database."""

    def name(self) -> Text:
        return "action_delete_preference"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        user_id = tracker.sender_id

        if not db_client.pool:
            dispatcher.utter_message(text="I'm sorry, I'm having trouble accessing my memory right now. Please try again later.")
            return []

        # For now, this action only deletes the seat preference.
        # A more advanced version could ask the user which preference to delete.
        was_deleted = db_client.delete_user_preference(user_id, "seat_preference")

        if was_deleted:
            dispatcher.utter_message(response="utter_preference_deleted")
        else:
            dispatcher.utter_message(response="utter_no_preference_to_delete")

        return []


class ActionFlexibleSearch(Action):
    """Handles the logic for a flexible, constraint-based search."""

    def name(self) -> Text:
        return "action_flexible_search"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        trip_type = next(tracker.get_latest_entity_values("trip_type"), "any")
        budget = next(tracker.get_latest_entity_values("budget"), "any")

        # In a real bot, this would trigger a complex query to a flight API
        # or a database of flight options.
        # e.g., api.search(type=trip_type, max_price=budget)

        dispatcher.utter_message(
            text=f"I am searching for a {trip_type} trip with a budget of ${budget}..."
        )

        # Mocked results
        if trip_type == "beach":
            results = "How about a trip to Cancun for $450 or Mallorca for $600?"
        elif trip_type == "adventure":
            results = "I found an amazing hiking trip to Costa Rica for $750!"
        else:
            results = "I can recommend a city break to Prague for $550."

        dispatcher.utter_message(text=results)
        return []

class ActionFlightStatus(Action):
    """Checks the status of a flight."""

    def name(self) -> Text:
        return "action_flight_status"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        flight_id = next(tracker.get_latest_entity_values("flight_id"), None)

        if not flight_id:
            dispatcher.utter_message(text="I need a flight ID to check the status.")
            return []

        # In a real bot, you would query an API with the flight_id
        dispatcher.utter_message(text=f"Checking status for flight {flight_id}...")
        dispatcher.utter_message(text=f"Flight {flight_id} is on time. It will depart at 11:30.")

        return []

class ActionSetFlightAndAskConfirm(Action):
    """Sets the selected flight_id and asks the user for final confirmation."""

    def name(self) -> Text:
        return "action_set_flight_and_ask_confirm"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        flight_id = next(tracker.get_latest_entity_values("flight_id"), None)

        if not flight_id:
            dispatcher.utter_message(text="I'm sorry, something went wrong. I didn't get the flight selection.")
            return []

        dispatcher.utter_message(
            text=f"You've selected flight {flight_id}. Shall I go ahead and book it?",
            buttons=[
                {"title": "Yes, confirm booking", "payload": "/confirm_booking"},
                {"title": "No, cancel", "payload": "/stop"}
            ]
        )
        return []

class ActionConfirmBooking(Action):
    """Confirms a flight booking."""

    def name(self) -> Text:
        return "action_confirm_booking"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        # In a real bot, this would trigger a booking API call
        # and handle success or failure.
        dispatcher.utter_message(response="utter_booking_confirmed")

        # --- NEW: Upsell for hotel booking ---
        destination_city = tracker.get_slot("destination_city")

        # For multi-city, we could offer a hotel at the final destination
        if not destination_city and tracker.get_slot("booking_trip_type") == "multi-city":
            destinations = tracker.get_slot("destinations")
            if destinations:
                destination_city = destinations[-1]

        if destination_city:
            # Pre-fill the hotel_name entity for a smoother experience
            payload = f'/book_hotel{{"hotel_name": "{destination_city}"}}'
            dispatcher.utter_message(
                text=f"Do you also need a hotel in {destination_city}?",
                buttons=[
                    {"title": "Yes, find a hotel", "payload": payload},
                    {"title": "No, thanks", "payload": "/stop"} # Use /stop for a generic "anything else?" response
                ]
            )
        # If we can't determine a destination, the conversation ends gracefully.
        else:
            dispatcher.utter_message(text="Is there anything else I can help you with?") # Graceful exit if destination is unknown
        return []

class ActionCancelBooking(Action):
    """A custom action to handle the cancellation of the flight booking form."""

    def name(self) -> Text:
        return "action_cancel_booking"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        logger.info("User cancelled the flight booking form.")
        dispatcher.utter_message(response="utter_ok_cancelled")

        # This will deactivate the form and reset all slots to prevent
        # unexpected behavior in subsequent conversations.
        return [ActiveLoop(None), AllSlotsReset()]

class ActionAskConfirmCancellation(Action):
    """Asks the user to confirm they want to cancel the booking process."""

    def name(self) -> Text:
        return "action_ask_confirm_cancellation"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(response="utter_ask_confirm_cancellation")
        return [SlotSet("cancellation_pending", True)]

class ActionResumeBooking(Action):
    """Resumes the booking process after a user decides not to cancel, summarizing collected info."""

    def name(self) -> Text:
        return "action_resume_booking"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        summary_sentence = _build_summary_sentence(tracker)

        if summary_sentence:
            dispatcher.utter_message(response="utter_form_summary_sentence", summary=summary_sentence)
        else:
            # If no slots are filled, just say we're continuing.
            dispatcher.utter_message(response="utter_resume_booking")

        # Reset the cancellation flag
        return [SlotSet("cancellation_pending", None)]

class ActionSetAirportFromClarification(Action):
    """
    Handles the user's selection from an airport clarification prompt.
    This action is triggered by a rule when the user selects an airport button.
    """
    def name(self) -> Text:
        return "action_set_airport_from_clarification"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        # Retrieve the details from the slots set during the ambiguity prompt
        city_slot_to_fill = tracker.get_slot("ambiguous_city_slot")
        city_name = tracker.get_slot("ambiguous_city_name")
        selected_iata = tracker.get_slot("selected_iata_code")

        if not all([city_slot_to_fill, city_name, selected_iata]):
            logger.error("Could not set airport from clarification, missing slots.")
            return []

        logger.info(f"Clarified '{city_name}' to '{selected_iata}' for slot '{city_slot_to_fill}'.")

        # Return the events to set the correct city and IATA slots, and clear the ambiguity slots.
        # This will fill the slot the form was waiting for, allowing it to proceed.
        return [
            SlotSet(city_slot_to_fill, city_name),
            SlotSet(f"{city_slot_to_fill}_iata", selected_iata),
            SlotSet("ambiguous_city_name", None),
            SlotSet("ambiguous_city_slot", None),
            SlotSet("selected_iata_code", None),
            SlotSet("requested_slot", None), # Clear requested_slot to allow form to proceed
        ]

class ActionHandleCorrection(Action):
    """
    Handles a user's request to correct a piece of information after the review step.
    This action is designed to be modular, delegating validation logic to helper methods
    and the `ValidateFlightBookingForm` class.
    """

    def name(self) -> Text:
        return "action_handle_correction"

    def __init__(self):
        """Initializes the action with a validator instance and a mapping."""
        super().__init__()
        self.validator = ValidateFlightBookingForm()
        # Map entities to their corresponding validation method names in the validator class.
        self.entity_to_validator_map = {
            "departure_date": "validate_departure_date", # type: ignore
            "return_date": "validate_return_date",
            "number_of_passengers": "validate_number_of_passengers",
            "preferred_airline": "validate_preferred_airline",
            "frequent_flyer_number": "validate_frequent_flyer_number",
            "booking_trip_type": "validate_booking_trip_type",
            "travel_class": "validate_travel_class",
        }

    def _ordinal_to_int(self, ordinal_string: str) -> Optional[int]:
        """Converts an ordinal string like 'first', 'second' to a zero-based integer index."""
        ordinal_map = {
            "first": 0, "1st": 0,
            "second": 1, "2nd": 1,
            "third": 2, "3rd": 2,
            "fourth": 3, "4th": 3,
            "last": -1,
        }
        return ordinal_map.get(str(ordinal_string).lower())

    def _handle_city_correction(
        self,
        entity: Dict[Text, Any],
        all_entities: List[Dict[Text, Any]],
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Handles the specific logic for correcting a city."""
        entity_value = entity.get("value")
        role = entity.get("role")
        trip_type = tracker.get_slot("booking_trip_type")

        if role == "departure":
            return self.validator.validate_departure_city(entity_value, dispatcher, tracker, domain)

        if role == "destination":
            if trip_type == "multi-city":
                # For multi-city, assume correction applies to the last destination.
                destinations = tracker.get_slot("destinations") or []
                destinations_iata = tracker.get_slot("destinations_iata") or []
                if not destinations:
                    return {}  # Cannot correct if no destinations are set.

                # Find the ordinal entity from the user's message to target a specific index
                ordinal_entity = next((e for e in all_entities if e.get("entity") == "ordinal"), None)
                target_index = -1  # Default to the last element

                if ordinal_entity:
                    index = self._ordinal_to_int(ordinal_entity.get("value"))
                    # Check if the requested index is valid for the current trip
                    if index is not None and abs(index) < len(destinations):
                        target_index = index
                    else:
                        dispatcher.utter_message(text=f"I'm sorry, I can't correct the '{ordinal_entity.get('value')}' destination as there are only {len(destinations)} destinations in your trip.")
                        return {}

                validation_result = self.validator._validate_city("next_destination", entity_value, dispatcher)
                if validation_result.get("next_destination"):
                    destinations[target_index] = validation_result["next_destination"]
                    destinations_iata[target_index] = validation_result["next_destination_iata"]
                    return {"destinations": destinations, "destinations_iata": destinations_iata}
            else:
                # For one-way or round-trip.
                return self.validator.validate_destination_city(entity_value, dispatcher, tracker, domain)
        
        return {}

    def _handle_generic_correction(
        self,
        entity: Dict[Text, Any],
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Handles corrections for entities with a direct validator mapping."""
        entity_name = entity.get("entity")
        entity_value = entity.get("value")

        validator_method_name = self.entity_to_validator_map.get(entity_name)
        if validator_method_name:
            # Get the validation method from the validator instance and call it.
            validation_method = getattr(self.validator, validator_method_name)
            return validation_method(entity_value, dispatcher, tracker, domain)
        
        return {}

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: "Tracker",
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        
        all_entities = tracker.latest_message.get("entities", [])
        corrected_slots = {}

        # Prioritize city corrections as they might have associated ordinals
        city_entity = next((e for e in all_entities if e.get("entity") == "city"), None)

        if city_entity:
            correction = self._handle_city_correction(city_entity, all_entities, dispatcher, tracker, domain)
            if correction:
                corrected_slots.update(correction)

        # Handle other, non-city corrections
        for entity in all_entities:
            # Don't re-process city or ordinal entities
            if entity.get("entity") not in ["city", "ordinal"]:
                correction = self._handle_generic_correction(entity, dispatcher, tracker, domain)
                if correction:
                    corrected_slots.update(correction)
        
        if not corrected_slots or all(value is None for value in corrected_slots.values()):
            dispatcher.utter_message(text="I'm sorry, I didn't understand the correction. Let's review again.")
            return [FollowupAction("action_review_and_confirm")]

        events = [SlotSet(slot, value) for slot, value in corrected_slots.items()]
        logger.info(f"Applying corrections: {corrected_slots}")
        events.append(FollowupAction("action_review_and_confirm"))
        
        return events

class ActionHandleCitySuggestion(Action):
    """Handles the user's response to a city typo suggestion."""

    def name(self) -> Text:
        return "action_handle_city_suggestion"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        intent = tracker.latest_message['intent'].get('name')
        suggested_city = tracker.get_slot("suggested_city")
        city_slot_to_fill = tracker.get_slot("ambiguous_city_slot")
        active_form = tracker.active_loop.get('name')

        if not city_slot_to_fill or not active_form:
            logger.error("ActionHandleCitySuggestion called without 'ambiguous_city_slot' or an active form.")
            return [FollowupAction("action_listen")]

        events = [SlotSet("suggested_city", None), SlotSet("ambiguous_city_slot", None)]

        if intent == 'affirm':
            # User confirmed the suggestion. We set the slot with the corrected value.
            # The form will re-run its validation on this corrected value.
            events.append(SlotSet(city_slot_to_fill, suggested_city))
        else: # Deny or anything else
            dispatcher.utter_message(text="My mistake. Could you please spell out the city name for me?")
            # We clear the slot that was being validated to force the form to re-ask for it.
            events.append(SlotSet(city_slot_to_fill, None))
            # Also clear any associated IATA slot if it exists (for flights)
            if f"{city_slot_to_fill}_iata" in tracker.slots:
                 events.append(SlotSet(f"{city_slot_to_fill}_iata", None))

        # After handling, we want the form to continue its logic.
        events.append(FollowupAction(active_form))
        return events

class ActionOfferFlightToDestination(Action):
    """
    Handles the upsell from hotel booking to flight booking.
    It pre-fills the destination city and then activates the flight booking form.
    """
    def name(self) -> Text:
        return "action_offer_flight_to_destination"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        city = next(tracker.get_latest_entity_values("city"), None)

        if not city:
            logger.warning("ActionOfferFlightToDestination triggered without a 'city' entity.")
            # Fallback: just start the form without pre-filling anything.
            return [FollowupAction("flight_booking_form")]

        # We need to validate the city to get its IATA code and handle ambiguity.
        # We can reuse the validation logic from the flight booking form.
        validator = ValidateFlightBookingForm()
        validation_result = validator._validate_city("destination_city", city, dispatcher)

        events = []
        # The _validate_city helper returns a dictionary of slots to be set.
        # We can just iterate over it and create SlotSet events.
        for slot, value in validation_result.items():
            events.append(SlotSet(slot, value))

        # After setting the slots, we hand over control to the flight booking form.
        events.append(FollowupAction("flight_booking_form"))
        return events

class ActionReviewAndConfirm(Action):
    """Summarizes the collected booking information and asks for user confirmation."""

    def name(self) -> Text:
        return "action_review_and_confirm"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        summary_sentence = _build_summary_sentence(tracker)
        buttons = [
            {"title": "Yes, looks good", "payload": "/confirm_details"},
            {"title": "No, something's wrong", "payload": "/deny_details"},
        ]
        dispatcher.utter_message(response="utter_ask_confirm_details", summary=summary_sentence, buttons=buttons)

        return []

class ActionSearchFlights(Action):
    """Takes the collected information from the form and searches for flights."""

    def name(self) -> Text:
        return "action_search_flights"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: DomainDict) -> List[Dict[Text, Any]]:

        # Get user-facing names for messages
        dep_city_name = tracker.get_slot("departure_city")
        dest_city_name = tracker.get_slot("destination_city")
        destinations_names = tracker.get_slot("destinations")

        # Get IATA codes for API call
        dep_city_iata = tracker.get_slot("departure_city_iata")
        dest_city_iata = tracker.get_slot("destination_city_iata")
        destinations_iata = tracker.get_slot("destinations_iata")

        dep_date = tracker.get_slot("departure_date")
        return_date = tracker.get_slot("return_date")
        passengers = tracker.get_slot("number_of_passengers")
        preferred_airline = tracker.get_slot("preferred_airline") # From current conversation
        frequent_flyer_number = tracker.get_slot("frequent_flyer_number")
        user_id = tracker.sender_id
        seat_pref = None

        # --- Handle multi-city which is not supported by API clients directly ---
        if tracker.get_slot("booking_trip_type") == "multi-city":
            full_path = " -> ".join([dep_city_name] + destinations_names)
            dispatcher.utter_message(text=f"Okay! Searching for a multi-city trip for {passengers} passenger(s) along the route: {full_path}, starting on {dep_date}.")
            dispatcher.utter_message(text="Multi-city searches are complex. For this demo, I can't show you the results directly, but I have all the details for the search!")
            return []

        # --- Retrieve user preference from DB ---
        # This will supplement information not gathered in the current form.
        if db_client.pool:
            seat_pref = db_client.get_user_preference(user_id, "seat_preference")
            # If an airline wasn't specified in this conversation, check for a saved one.
            if not preferred_airline:
                saved_airline = db_client.get_user_preference(user_id, "preferred_airline")
                if saved_airline:
                    preferred_airline = saved_airline # Use the saved preference for the API call
                    dispatcher.utter_message(text=f"Just so you know, I'm using your saved preference to search for flights on {saved_airline}.")

        if return_date:
            search_message = f"Okay! Searching for round trip flights for {passengers} passenger(s) from {dep_city_name} to {dest_city_name}, departing on {dep_date} and returning on {return_date}."
        else:
            search_message = f"Okay! Searching for one-way flights for {passengers} passenger(s) from {dep_city_name} to {dest_city_name} for {dep_date}."

        if seat_pref:
            search_message += f" I'll keep in mind you prefer a {seat_pref} seat."

        dispatcher.utter_message(
            text=search_message
        )

        # --- API CALL LOGIC IS NOW IN THE CLIENT ---
        flight_api = get_api_client()
        flight_options = flight_api.search(
            departure_city=dep_city_iata,
            destination_city=dest_city_iata,
            departure_date=dep_date,
            return_date=return_date,
            passengers=passengers,
            preferred_airline=preferred_airline,
            frequent_flyer_number=frequent_flyer_number,
            seat_preference=seat_pref,
            travel_class=tracker.get_slot("travel_class"), # Pass the new slot
            destinations=destinations_iata,
        )
        
        # Handle the case where the API call failed
        if flight_options is None:
            dispatcher.utter_message(response="utter_api_failure")
            return []

        # Handle the case where the API call succeeded but found no flights
        if not flight_options:
            dispatcher.utter_message(text="I'm sorry, I couldn't find any flights for that route and date.")
            return []

        # If flights were found, create buttons for them
        buttons = []
        for flight in flight_options:
            title = f"{flight['airline']} at {flight['time']} for ${flight['price']}"
            # The payload is what Rasa receives when the button is clicked
            payload = f"/select_flight{{\"flight_id\": \"{flight['flight_id']}\"}}"
            buttons.append({"title": title, "payload": payload})
        dispatcher.utter_message(text="Here are some flights I found:", buttons=buttons)

        return []

class ValidateFlightBookingForm(FormValidationAction):
    """Validates the input for the flight booking form."""

    def name(self) -> Text:
        return "validate_flight_booking_form"

    @staticmethod
    def required_slots(tracker: Tracker) -> List[Text]:
        """
        A list of required slots that the form has to fill, in a logical order.
        The method returns the complete list of slots that are needed for the form
        to be considered complete. Rasa will ask for the first unfilled slot from this list.
        """

        trip_type = tracker.get_slot("booking_trip_type")

        # Start with the trip type, as it determines the rest of the flow.
        if not trip_type:
            return ["booking_trip_type"]

        # Base slots required for all trip types
        slots = ["booking_trip_type", "departure_city"]

        # Add slots based on the trip type
        if trip_type == "one-way":
            slots.append("destination_city")
        elif trip_type == "round trip":
            slots.append("destination_city")
        elif trip_type == "multi-city":
            # Loop until the user says they don't want to add more destinations
            if tracker.get_slot("add_more_destinations") is not False: # Loop continues
                slots.append("next_destination")
                slots.append("add_more_destinations")

        # Add remaining common slots in a logical order
        slots.append("departure_date")

        if trip_type == "round trip":
            slots.append("return_date")
        slots.append("number_of_passengers")

        # Conditionally ask for travel class for "premium" bookings, defined as
        # multi-city trips OR bookings for more than 4 passengers.
        num_passengers = tracker.get_slot("number_of_passengers")
        if trip_type == "multi-city" or (num_passengers and num_passengers > 4):
            slots.append("travel_class")

        slots.append("preferred_airline")
        slots.append("frequent_flyer_number")

        return slots

    def _validate_city(
        self,
        slot_name: str,
        slot_value: str,
        dispatcher: "CollectingDispatcher"
    ) -> Dict[Text, Any]:
        """
        A helper function to validate a city, check for ambiguity, and set slots.
        Returns a dictionary of slots to set.
        """
        airports = db_client.get_airports_for_city(slot_value)
        
        if not airports:
            # --- NEW: Typo handling logic ---
            all_cities = db_client.get_all_city_names()
            if all_cities:
                # Find the best match for the user's input
                best_match, score = process.extractOne(slot_value, all_cities)
                # We use a threshold to avoid suggesting something completely unrelated.
                if score > 80:
                    dispatcher.utter_message(
                        response="utter_clarify_city_typo",
                        typed_city=slot_value,
                        suggested_city=best_match,
                        buttons=[
                            {"title": f"Yes, I meant {best_match}", "payload": "/affirm"},
                            {"title": "No, that's not it", "payload": "/deny"}
                        ]
                    )
                    # Pause the form and store context for the suggestion handler
                    return {
                        slot_name: None,
                        f"{slot_name}_iata": None,
                        "suggested_city": best_match,
                        "ambiguous_city_slot": slot_name # Remember which slot we are filling
                    }
            # --- End of typo handling ---

            dispatcher.utter_message(text=f"I'm sorry, I don't recognize '{slot_value}' as a valid city.")
            return {slot_name: None, f"{slot_name}_iata": None}

        if len(airports) == 1:
            # Unambiguous case: only one airport found.
            iata_code = airports[0]['iata']
            logger.info(f"Found unique IATA code '{iata_code}' for city '{slot_value}'.")
            return {slot_name: slot_value, f"{slot_name}_iata": iata_code}

        # Ambiguous case: multiple airports found.
        logger.info(f"Found multiple airports for '{slot_value}'. Asking for clarification.")
        buttons = []
        for airport in airports:
            title = f"{airport['name']} ({airport['iata']})"
            payload = f"/select_airport{{\"selected_iata_code\": \"{airport['iata']}\"}}"
            buttons.append({"title": title, "payload": payload})

        # Ask the user to clarify which airport they mean.
        dispatcher.utter_message(
            response="utter_clarify_airport",
            ambiguous_city_name=slot_value,
            buttons=buttons
        )

        # Pause the form by returning the city slot as None.
        # Store the context needed for clarification in the ambiguity slots.
        return {
            slot_name: None,
            f"{slot_name}_iata": None,
            "ambiguous_city_name": slot_value,
            "ambiguous_city_slot": slot_name
        }

    def validate_departure_city(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `departure_city` value and find its IATA code."""
        return self._validate_city("departure_city", slot_value, dispatcher)

    def validate_destination_city(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `destination_city` value and find its IATA code."""

        departure_city = tracker.get_slot("departure_city")
        if departure_city and slot_value.lower() == departure_city.lower():
            dispatcher.utter_message(text="Departure and destination cities cannot be the same.")
            return {"destination_city": None, "destination_city_iata": None}

        return self._validate_city("destination_city", slot_value, dispatcher)

    def validate_departure_date(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `departure_date` value using the shared helper."""
        current_date = tracker.get_slot("departure_date")
        validated_date = _validate_date(slot_value, "departure_date", dispatcher)

        if validated_date and current_date and current_date != validated_date:
            dispatcher.utter_message(text=f"Okay, I've updated the departure date to {validated_date}.")

        return {"departure_date": validated_date}

    def validate_next_destination(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `next_destination` and handle airport ambiguity."""

        # Also ensure it's not the same as the previous destination
        destinations = tracker.get_slot("destinations") or []
        previous_dest = destinations[-1] if destinations else tracker.get_slot("departure_city")
        if previous_dest and slot_value.lower() == previous_dest.lower():
            dispatcher.utter_message(text="The next destination cannot be the same as the previous one.")
            return {"next_destination": None}

        city_validation_result = self._validate_city("next_destination", slot_value, dispatcher)
        if city_validation_result.get("next_destination"): # If valid and unambiguous:
            # For multi-city, we need to APPEND to the destinations lists
            current_destinations = tracker.get_slot("destinations") or []
            current_destinations.append(city_validation_result.get("next_destination"))

            current_destinations_iata = tracker.get_slot("destinations_iata") or []
            current_destinations_iata.append(city_validation_result.get("next_destination_iata"))

            return {
                "destinations": current_destinations,
                "destinations_iata": current_destinations_iata,
                "next_destination": None # Clear temp slot
            }
        else:
            # In case of invalid or ambiguous city, the _validate_city already
            # produced the correct error message. We simply return the result.
            return city_validation_result

    def validate_add_more_destinations(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate the user's response to adding another destination."""
        if slot_value is True:
            # If user wants to add more, we keep the slot True to continue the loop
            return {"add_more_destinations": True}
        else:
            # If user says no, we set it to False to break the loop
            return {"add_more_destinations": False}

    def validate_booking_trip_type(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `booking_trip_type` value."""
        value = slot_value.lower()
        # Normalize user input
        if "one" in value or "oneway" in value:
            return {"booking_trip_type": "one-way"}
        if "round" in value:
            return {"booking_trip_type": "round trip"}
        if "multi" in value:
            # When multi-city is chosen, initialize the loop control slot
            return {
                "booking_trip_type": "multi-city",
                "add_more_destinations": True,
                "destinations": [], # Clear any previous destinations
                "destinations_iata": []
            }

        dispatcher.utter_message(text="I didn't understand the trip type. Please say 'one-way', 'round trip', or 'multi-city'.")
        return {"booking_trip_type": None}

    def validate_number_of_passengers(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `number_of_passengers` value."""
        current_passengers = tracker.get_slot("number_of_passengers")

        try:
            # Try to convert the value to an integer
            num_passengers = int(slot_value)
            # Check if the number is positive
            if num_passengers <= 0:
                dispatcher.utter_message(text="The number of passengers must be at least 1.")
                return {"number_of_passengers": None}
        except (ValueError, TypeError):
            # If conversion fails, it's not a valid number
            dispatcher.utter_message(text=f"I'm sorry, I don't understand '{slot_value}' as a number of passengers. Please provide a number like '2' or '3'.")
            return {"number_of_passengers": None}

        # Acknowledge the correction if the value has changed
        if current_passengers is not None and current_passengers != num_passengers:
            dispatcher.utter_message(text=f"Okay, I've updated the number of passengers to {num_passengers}.")

        # Return the validated integer value
        return {"number_of_passengers": num_passengers}

    def validate_preferred_airline(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `preferred_airline` value."""
        # If the user denies, we assume no preference.
        if tracker.latest_message['intent'].get('name') == 'deny':
            dispatcher.utter_message(text="Okay, I'll search all available airlines.")
            return {"preferred_airline": None}

        # If an airline was extracted, we accept it.
        if slot_value:
            dispatcher.utter_message(text=f"Okay, I'll look for flights on {slot_value}.")

        # If no entity was extracted, and it wasn't a deny, we assume no preference and move on.
        return {"preferred_airline": slot_value}

    def validate_frequent_flyer_number(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `frequent_flyer_number` against airline-specific formats."""
        if tracker.latest_message['intent'].get('name') == 'deny':
            dispatcher.utter_message(text="Okay, no problem.")
            return {"frequent_flyer_number": None}

        if not slot_value:
            # This can happen if the user says something that doesn't contain the entity
            # but isn't a 'deny' intent. We just move on.
            return {"frequent_flyer_number": None}

        airline = tracker.get_slot("preferred_airline")
        if not airline:
            # This shouldn't happen based on slot order, but it's a good safeguard.
            dispatcher.utter_message(text="I need to know your preferred airline before I can validate your frequent flyer number.")
            return {"frequent_flyer_number": None}

        airline_format = FREQUENT_FLYER_FORMATS.get(airline.lower())

        if not airline_format:
            # We don't have a specific format for this airline, so we accept it as is.
            logger.info(f"No specific frequent flyer format for '{airline}'. Accepting '{slot_value}' without validation.")
            dispatcher.utter_message(text=f"Great, I've added your frequent flyer number {slot_value}.")
            return {"frequent_flyer_number": slot_value}

        # We have a format, let's validate using regex.
        if re.match(airline_format["regex"], slot_value):
            dispatcher.utter_message(text=f"Great, I've added your frequent flyer number {slot_value}.")
            return {"frequent_flyer_number": slot_value}
        else:
            # The number is invalid for the specified airline.
            example = airline_format["example"]
            dispatcher.utter_message(
                text=f"That doesn't look like a valid frequent flyer number for {airline}. "
                     f"It should look something like this: {example}. Let's skip it for now."
            )
            return {"frequent_flyer_number": None}

    def validate_return_date(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `return_date` value using the shared helper."""
        departure_date_str = tracker.get_slot("departure_date")
        validated_date = _validate_end_date(
            slot_value,
            departure_date_str,
            "return_date",
            "departure_date",
            dispatcher
        )
        return {"return_date": validated_date}

    def validate_travel_class(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `travel_class` value."""
        # Get the allowed values from the domain.yml slot definition
        allowed_classes = self.domain_slots["travel_class"]["values"]

        # Normalize the input for comparison
        normalized_value = str(slot_value).lower()

        if normalized_value in allowed_classes:
            dispatcher.utter_message(text=f"Okay, searching for flights in {normalized_value} class.")
            return {"travel_class": normalized_value}
        else:
            dispatcher.utter_message(
                text=f"I'm sorry, '{slot_value}' is not a valid travel class. "
                     f"Please choose from: {', '.join(allowed_classes)}."
            )
            return {"travel_class": None}


class ActionSearchHotels(Action):
    """Takes the collected information and 'searches' for hotels."""

    def name(self) -> Text:
        return "action_search_hotels"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        hotel_name = tracker.get_slot("hotel_name")
        check_in = tracker.get_slot("check_in_date")
        check_out = tracker.get_slot("check_out_date")
        guests = tracker.get_slot("number_of_guests")

        dispatcher.utter_message(
            text=f"Okay! Searching for hotels in {hotel_name} for {guests} guests, "
                 f"checking in on {check_in} and checking out on {check_out}."
        )
        # In a real bot, you would call a hotel API here.
        dispatcher.utter_message(text="I've found a great deal at the 'Grand Hotel' for $200/night. Would you like to book it?")

        # --- NEW: Upsell for flight booking ---
        # We assume the hotel_name is the destination city for the flight.
        if hotel_name:
            # This payload triggers a custom action to pre-fill slots before starting the form.
            payload = f'/offer_flight_to_destination{{"city": "{hotel_name}"}}'
            dispatcher.utter_message(
                text=f"Do you also need a flight to {hotel_name}?",
                buttons=[
                    {"title": f"Yes, find a flight", "payload": payload},
                    {"title": "No, thanks", "payload": "/stop"}
                ]
            )
        else:
            dispatcher.utter_message(text="Is there anything else I can help you with?") # Graceful exit if hotel name is unknown
        return []


class ValidateHotelBookingForm(FormValidationAction):
    """Validates the input for the hotel booking form."""

    def name(self) -> Text:
        return "validate_hotel_booking_form"

    def validate_hotel_name(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `hotel_name` value, which can be a city or a specific hotel."""
        if not slot_value:
            return {"hotel_name": None}

        return {"hotel_name": slot_value}
    def validate_check_in_date(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `check_in_date` value using the shared helper."""
        validated_date = _validate_date(slot_value, "check_in_date", dispatcher)
        return {"check_in_date": validated_date}

    def validate_check_out_date(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `check_out_date` value using the shared helper."""
        check_in_str = tracker.get_slot("check_in_date")
        validated_date = _validate_end_date(
            slot_value, check_in_str, "check_out_date", "check_in_date", dispatcher
        )
        return {"check_out_date": validated_date}

    def validate_number_of_guests(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `number_of_guests` value."""
        try:
            num_guests = int(slot_value)
            if num_guests <= 0:
                dispatcher.utter_message(text="You must book for at least 1 guest.")
                return {"number_of_guests": None}
        except (ValueError, TypeError):
            dispatcher.utter_message(text=f"I don't understand '{slot_value}' as a number. Please provide a number like '2'.")
            return {"number_of_guests": None}

        return {"number_of_guests": num_guests}


class ActionSearchCars(Action):
    """Takes the collected information and 'searches' for rental cars."""

    def name(self) -> Text:
        return "action_search_cars"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        pickup_location = tracker.get_slot("pickup_location")
        pickup_date = tracker.get_slot("pickup_date")
        dropoff_date = tracker.get_slot("dropoff_date")
        car_type = tracker.get_slot("car_type")

        dispatcher.utter_message(
            text=f"Okay! Searching for a {car_type} to rent in {pickup_location} from {pickup_date} to {dropoff_date}."
        )

        car_rental_api = get_car_rental_api_client()
        car_options = car_rental_api.search(
            location=pickup_location,
            pickup_date=pickup_date,
            dropoff_date=dropoff_date,
            car_type=car_type
        )

        if car_options is None:
            dispatcher.utter_message(text="I'm sorry, I'm having trouble searching for cars right now. Please try again later.")
            return []

        if not car_options:
            dispatcher.utter_message(text="I couldn't find any available cars for your search. You might want to try different dates or a different location.")
            return []

        buttons = []
        for car in car_options:
            title = f"{car['provider']} {car['model']} - ${car['price_per_day']}/day"
            # A real implementation would need a 'select_car' intent and action
            payload = f"/inform{{\"selected_car_id\": \"{car['id']}\"}}"
            buttons.append({"title": title, "payload": payload})
        dispatcher.utter_message(text="Here are some rental cars I found:", buttons=buttons)

        # --- NEW: Upsell for flight booking ---
        if pickup_location:
            # This payload triggers a custom action to pre-fill slots before starting the form.
            payload = f'/offer_flight_to_destination{{"city": "{pickup_location}"}}'
            dispatcher.utter_message(
                text=f"Do you also need a flight to {pickup_location}?",
                buttons=[
                    {"title": f"Yes, find a flight", "payload": payload},
                    {"title": "No, thanks", "payload": "/stop"}
                ]
            )
        return []


class ValidateCarBookingForm(FormValidationAction):
    """Validates the input for the car booking form."""

    def name(self) -> Text:
        return "validate_car_booking_form"

    def validate_pickup_location(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `pickup_location` value, with typo correction."""
        if not slot_value:
            return {"pickup_location": None} # Keep the typo handling logic consistent by returning None immediately

        all_cities = db_client.get_all_city_names()
        if not all_cities:
            logger.warning("No cities found in the database for location validation.")
            # Fallback to original behavior: accept any input
            return {"pickup_location": slot_value}

        # Check for an exact match first (case-insensitive)
        for city in all_cities:
            if city.lower() == str(slot_value).lower():
                return {"pickup_location": city} # Return the canonical name

        # If no exact match, try fuzzy matching
        best_match, score = process.extractOne(slot_value, all_cities)
        if score > 80:
            dispatcher.utter_message(
                response="utter_clarify_city_typo",
                typed_city=slot_value,
                suggested_city=best_match,
                buttons=[
                    {"title": f"Yes, I meant {best_match}", "payload": "/affirm"},
                    {"title": "No, that's not it", "payload": "/deny"}
                ]
            )
            return {
                "pickup_location": None,
                "suggested_city": best_match,
                "ambiguous_city_slot": "pickup_location"
            }

        dispatcher.utter_message(text=f"I'm sorry, I don't recognize '{slot_value}' as a valid location.")
        return {"pickup_location": None}

    def validate_pickup_date(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `pickup_date` value using the shared helper."""
        validated_date = _validate_date(slot_value, "pickup_date", dispatcher)
        return {"pickup_date": validated_date}

    def validate_dropoff_date(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `dropoff_date` value using the shared helper."""
        pickup_date_str = tracker.get_slot("pickup_date")
        validated_date = _validate_end_date(
            slot_value, pickup_date_str, "dropoff_date", "pickup_date", dispatcher
        )
        return {"dropoff_date": validated_date}

    def validate_car_type(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        """Validate `car_type` value."""
        allowed_types = self.domain_slots["car_type"]["values"]
        normalized_value = str(slot_value).lower().replace('-', ' ')

        # Simple matching to find a valid category
        for car_type in allowed_types:
            if car_type.replace('-', ' ') in normalized_value:
                return {"car_type": car_type}

        dispatcher.utter_message(
            text=f"I'm sorry, '{slot_value}' is not a valid car type. "
                 f"Please choose from: {', '.join(allowed_types)}."
        )
        return {"car_type": None}
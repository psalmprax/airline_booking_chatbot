import logging
import os
import re
import datetime
import dateparser
from typing import Any, Text, Dict, List

import psycopg2
from psycopg2 import pool
from rasa_sdk import Action, Tracker, FormValidationAction
from rasa_sdk.events import SlotSet, ActiveLoop, AllSlotsReset, FollowupAction
from rasa_sdk.executor import CollectingDispatcher

from .api_client import get_api_client
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
    if airline:
        phrases.append(f"on {airline}")

    return " ".join(phrases)

class ActionStorePreference(Action):
    """Saves a user's preference to the 'database'."""

    def name(self) -> Text:
        return "action_store_preference"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        seat_pref = next(tracker.get_latest_entity_values("seat_preference"), None)

        if not seat_pref:
            dispatcher.utter_message(text="I didn't catch that preference. You can say things like 'I prefer a window seat'.")
            return []

        # Use the conversation ID as the user identifier
        user_id = tracker.sender_id

        if not db_client.pool:
            dispatcher.utter_message(text="I'm sorry, I'm having trouble accessing my memory right now. Please try again later.")
            return []

        success = db_client.store_user_preference(user_id, seat_pref)
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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        user_id = tracker.sender_id

        if not db_client.pool:
            dispatcher.utter_message(text="I'm sorry, I'm having trouble accessing my memory right now. Please try again later.")
            return []

        was_deleted = db_client.delete_user_preference(user_id)

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        # In a real bot, this would trigger a booking API call
        # and handle success or failure.
        dispatcher.utter_message(response="utter_booking_confirmed")

        return []

class ActionCancelBooking(Action):
    """A custom action to handle the cancellation of the flight booking form."""

    def name(self) -> Text:
        return "action_cancel_booking"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(response="utter_ask_confirm_cancellation")
        return [SlotSet("cancellation_pending", True)]

class ActionResumeBooking(Action):
    """Resumes the booking process after a user decides not to cancel, summarizing collected info."""

    def name(self) -> Text:
        return "action_resume_booking"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
    """Handles a user's request to correct a piece of information after the review step."""

    def name(self) -> Text:
        return "action_handle_correction"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        # Instantiate the validator to reuse its validation methods
        validator = ValidateFlightBookingForm()
        
        entities = tracker.latest_message.get("entities", [])
        events = []
        
        # A map from entity name to the validation method that should be called.
        entity_to_validation_map = {
            "departure_date": validator.validate_departure_date,
            "return_date": validator.validate_return_date,
            "number_of_passengers": validator.validate_number_of_passengers,
            "preferred_airline": validator.validate_preferred_airline,
            "frequent_flyer_number": validator.validate_frequent_flyer_number,
            "booking_trip_type": validator.validate_booking_trip_type,
        }

        corrected_slots = {}

        for entity in entities:
            entity_name = entity.get("entity")
            entity_value = entity.get("value")
            
            if entity_name == "city":
                role = entity.get("role")
                trip_type = tracker.get_slot("booking_trip_type")

                if role == "departure":
                    validation_result = validator.validate_departure_city(entity_value, dispatcher, tracker, domain)
                    corrected_slots.update(validation_result)
                elif role == "destination":
                    # If it's a multi-city trip, a generic "destination" correction is ambiguous.
                    # A simple strategy is to assume it corrects the *last* destination.
                    if trip_type == "multi-city":
                        destinations = tracker.get_slot("destinations") or []
                        destinations_iata = tracker.get_slot("destinations_iata") or []
                        if destinations:
                            # Validate the new city value using the generic helper
                            validation_result = validator._validate_city("next_destination", entity_value, dispatcher)
                            if validation_result.get("next_destination"):
                                # Replace the last item in the lists
                                destinations[-1] = validation_result.get("next_destination")
                                destinations_iata[-1] = validation_result.get("next_destination_iata")
                                corrected_slots["destinations"] = destinations
                                corrected_slots["destinations_iata"] = destinations_iata
                    else: # For one-way or round-trip
                        validation_result = validator.validate_destination_city(entity_value, dispatcher, tracker, domain)
                        corrected_slots.update(validation_result)
            elif entity_name in entity_to_validation_map:
                validation_method = entity_to_validation_map[entity_name]
                validation_result = validation_method(entity_value, dispatcher, tracker, domain)
                corrected_slots.update(validation_result)
        
        if not corrected_slots or all(value is None for value in corrected_slots.values()):
            dispatcher.utter_message(text="I'm sorry, I didn't understand the correction. Let's review again.")
            return [FollowupAction("action_review_and_confirm")]

        for slot, value in corrected_slots.items():
            events.append(SlotSet(slot, value))

        logger.info(f"Applying corrections: {corrected_slots}")
        events.append(FollowupAction("action_review_and_confirm"))
        return events

class ActionReviewAndConfirm(Action):
    """Summarizes the collected booking information and asks for user confirmation."""

    def name(self) -> Text:
        return "action_review_and_confirm"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

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
        preferred_airline = tracker.get_slot("preferred_airline")
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
        if db_client.pool:
            seat_pref = db_client.get_user_preference(user_id)

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
    def required_slots(tracker: "Tracker") -> List[Text]:
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
        slots.append("preferred_airline")
        slots.append("frequent_flyer_number")

        return slots

    def _validate_city(
        self,
        slot_name: str,
        slot_value: str,
        dispatcher: CollectingDispatcher
    ) -> Dict[Text, Any]:
        """
        A helper function to validate a city, check for ambiguity, and set slots.
        Returns a dictionary of slots to set.
        """
        airports = db_client.get_airports_for_city(slot_value)

        if not airports:
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
        """Validate `departure_date` value."""

        # Check if this is a correction by looking for an existing value
        current_date = tracker.get_slot("departure_date")

        # Use dateparser to understand natural language dates (e.g., "tomorrow")
        # The 'future' setting helps resolve ambiguities like "Saturday" to the upcoming one.
        parsed_date = dateparser.parse(slot_value, settings={'PREFER_DATES_FROM': 'future'})

        if not parsed_date:
            dispatcher.utter_message(text=f"I'm sorry, I couldn't understand '{slot_value}' as a date. Could you be more specific?")
            return {"departure_date": None}

        # Check if the parsed date is in the past (ignoring time)
        if parsed_date.date() < datetime.date.today():
            dispatcher.utter_message(text="You can't book a flight in the past! Please provide a future date.")
            return {"departure_date": None}

        new_date_str = parsed_date.strftime("%Y-%m-%d")

        # If the slot was already filled and the new value is different, acknowledge the change.
        if current_date and current_date != new_date_str:
            dispatcher.utter_message(text=f"Okay, I've updated the departure date to {new_date_str}.")

        # For consistency, we return the date in a standard YYYY-MM-DD format.
        return {"departure_date": new_date_str}

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
        """Validate `return_date` value."""
        departure_date_str = tracker.get_slot("departure_date")
        if not departure_date_str:
            # This should not happen if the form order is correct, but as a safeguard:
            dispatcher.utter_message(text="I need to know the departure date first.")
            return {"return_date": None}

        # Parse both dates to compare them
        departure_date = dateparser.parse(departure_date_str)
        return_date = dateparser.parse(slot_value, settings={'PREFER_DATES_FROM': 'future'})

        if not return_date:
            dispatcher.utter_message(text=f"I couldn't understand '{slot_value}' as a return date. Could you be more specific?")
            return {"return_date": None}

        if return_date.date() <= departure_date.date():
            dispatcher.utter_message(text="The return date must be after the departure date.")
            return {"return_date": None}

        return {"return_date": return_date.strftime("%Y-%m-%d")}
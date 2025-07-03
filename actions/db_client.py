import logging
from typing import Optional, Set, List, Dict

import psycopg2
from psycopg2.pool import SimpleConnectionPool

logger = logging.getLogger(__name__)

class DatabaseClient:
    """
    A client to interact with the PostgreSQL database.
    It encapsulates all database-related logic.
    """

    def __init__(self, pool: Optional[SimpleConnectionPool]):
        self.pool = pool

    def initialize_schema(self):
        """
        Creates necessary tables if they don't exist.
        In a production environment, this should be handled by a dedicated migration tool.
        """
        if not self.pool:
            logger.error("Database pool not available, cannot initialize schema.")
            return

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cities (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) UNIQUE NOT NULL
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        user_id VARCHAR(255) PRIMARY KEY,
                        preference_key VARCHAR(50) NOT NULL,
                        preference_value VARCHAR(255) NOT NULL,
                        PRIMARY KEY (user_id, preference_key)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS airports (
                        id SERIAL PRIMARY KEY,
                        city_id INTEGER NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
                        airport_name VARCHAR(100) NOT NULL,
                        iata_code VARCHAR(3) UNIQUE NOT NULL
                    );
                """)
                # Add an index on the city name for faster lookups
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cities_name_lower ON cities (LOWER(name));")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_airports_city_id ON airports (city_id);")

                # Separate data loading from schema creation for better maintainability
                cities_to_add = ['London', 'Paris', 'New York', 'Tokyo', 'Berlin', 'San Francisco']
                cur.execute("""
                    INSERT INTO cities (name)
                    SELECT unnest(%s::text[])
                    ON CONFLICT (name) DO NOTHING;
                """, (cities_to_add,))

                cur.execute("""
                    INSERT INTO airports (city_id, airport_name, iata_code) VALUES
                    ((SELECT id FROM cities WHERE name = 'London'), 'Heathrow Airport', 'LHR'),
                    ((SELECT id FROM cities WHERE name = 'London'), 'Gatwick Airport', 'LGW'),
                    ((SELECT id FROM cities WHERE name = 'Paris'), 'Charles de Gaulle Airport', 'CDG'),
                    ((SELECT id FROM cities WHERE name = 'New York'), 'John F. Kennedy Intl.', 'JFK'),
                    ((SELECT id FROM cities WHERE name = 'New York'), 'LaGuardia Airport', 'LGA'),
                    ((SELECT id FROM cities WHERE name = 'Tokyo'), 'Haneda Airport', 'HND'),
                    ((SELECT id FROM cities WHERE name = 'Berlin'), 'Berlin Brandenburg Airport', 'BER'),
                    ((SELECT id FROM cities WHERE name = 'San Francisco'), 'San Francisco Intl.', 'SFO')
                    ON CONFLICT (iata_code) DO NOTHING;
                """)

                conn.commit()
                logger.info("Database schema initialized or already exists.")
        except psycopg2.Error as e:
            logger.error(f"Failed to initialize database schema: {e}")
        finally:
            if conn:
                self.pool.putconn(conn)

    def store_user_preference(self, user_id: str, key: str, value: str) -> bool:
        """Stores or updates a user's preference for a given key (e.g., 'seat')."""
        if not self.pool:
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_preferences (user_id, preference_key, preference_value) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, preference_key) DO UPDATE SET preference_value = EXCLUDED.preference_value;
                """, (user_id, key, value))
                conn.commit()
                logger.info(f"Stored preference for user '{user_id}': {key}={value}")
                return True
        except psycopg2.Error as e:
            logger.error(f"Database error in store_user_preference: {e}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_user_preference(self, user_id: str, key: str) -> Optional[str]:
        """Retrieves a user's preference for a given key."""
        if not self.pool:
            return None

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT preference_value FROM user_preferences WHERE user_id = %s AND preference_key = %s", (user_id, key))
                result = cur.fetchone()
                return result[0] if result else None
        except psycopg2.Error as e:
            logger.error(f"Database error in get_user_preference: {e}")
            return None
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_airports_for_city(self, city_name: str) -> List[Dict[str, str]]:
        """Retrieves all airports for a given city name (case-insensitive)."""
        if not self.pool:
            return []

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                # Join with the cities table to search by name
                cur.execute("""
                    SELECT ap.airport_name, ap.iata_code
                    FROM airports ap
                    JOIN cities c ON ap.city_id = c.id
                    WHERE LOWER(c.name) = LOWER(%s)""", (city_name,))
                results = cur.fetchall()
                return [{"name": row[0], "iata": row[1]} for row in results]
        except psycopg2.Error as e:
            logger.error(f"Database error in get_airports_for_city: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_all_city_names(self) -> List[str]:
        """Retrieves a list of all unique city names from the database."""
        if not self.pool:
            return []

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM cities;")
                results = cur.fetchall()
                return [row[0] for row in results]
        except psycopg2.Error as e:
            logger.error(f"Database error in get_all_city_names: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def delete_user_preference(self, user_id: str, key: str) -> bool:
        """Deletes a specific preference for a user."""
        if not self.pool:
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_preferences WHERE user_id = %s AND preference_key = %s", (user_id, key))
                # Check if any row was actually deleted
                deleted_rows = cur.rowcount
                conn.commit()
                logger.info(f"Deleted {deleted_rows} preference record(s) for user '{user_id}'.")
                return deleted_rows > 0
        except psycopg2.Error as e:
            logger.error(f"Database error in delete_user_preference: {e}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)
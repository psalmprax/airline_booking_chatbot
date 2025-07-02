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
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        user_id VARCHAR(255) PRIMARY KEY,
                        seat_preference VARCHAR(50) NOT NULL
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS airports (
                        id SERIAL PRIMARY KEY,
                        city_name VARCHAR(100) NOT NULL,
                        airport_name VARCHAR(100) NOT NULL,
                        iata_code VARCHAR(3) UNIQUE NOT NULL
                    );
                """)
                cur.execute("""
                    INSERT INTO airports (city_name, airport_name, iata_code) VALUES
                    ('London', 'Heathrow Airport', 'LHR'),
                    ('London', 'Gatwick Airport', 'LGW'),
                    ('Paris', 'Charles de Gaulle Airport', 'CDG'),
                    ('New York', 'John F. Kennedy Intl.', 'JFK'),
                    ('New York', 'LaGuardia Airport', 'LGA'),
                    ('Tokyo', 'Haneda Airport', 'HND'),
                    ('Berlin', 'Berlin Brandenburg Airport', 'BER'),
                    ('San Francisco', 'San Francisco Intl.', 'SFO')
                    ON CONFLICT (iata_code) DO NOTHING;
                """)
                conn.commit()
                logger.info("Database schema initialized or already exists.")
        except psycopg2.Error as e:
            logger.error(f"Failed to initialize database schema: {e}")
        finally:
            if conn:
                self.pool.putconn(conn)

    def store_user_preference(self, user_id: str, preference: str) -> bool:
        """Stores or updates a user's seat preference."""
        if not self.pool:
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_preferences (user_id, seat_preference) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET seat_preference = EXCLUDED.seat_preference;
                """, (user_id, preference))
                conn.commit()
                logger.info(f"Stored preference for user '{user_id}': seat_preference={preference}")
                return True
        except psycopg2.Error as e:
            logger.error(f"Database error in store_user_preference: {e}")
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_user_preference(self, user_id: str) -> Optional[str]:
        """Retrieves a user's seat preference."""
        if not self.pool:
            return None

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT seat_preference FROM user_preferences WHERE user_id = %s", (user_id,))
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
                # Use ILIKE for case-insensitive matching
                cur.execute("SELECT airport_name, iata_code FROM airports WHERE city_name ILIKE %s", (city_name,))
                results = cur.fetchall()
                return [{"name": row[0], "iata": row[1]} for row in results]
        except psycopg2.Error as e:
            logger.error(f"Database error in get_airports_for_city: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def delete_user_preference(self, user_id: str) -> bool:
        """Deletes a user's seat preference."""
        if not self.pool:
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_preferences WHERE user_id = %s", (user_id,))
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
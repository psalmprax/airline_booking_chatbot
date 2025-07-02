import pytest
import psycopg2

from actions.db_client import DatabaseClient


@pytest.fixture
def mock_pool(mocker):
    """A fixture that provides a mocked database connection pool, connection, and cursor."""
    pool = mocker.MagicMock()
    conn = mocker.MagicMock()
    cursor = mocker.MagicMock()
    pool.getconn.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    return pool, conn, cursor


def test_initialize_schema_success(mock_pool, mocker):
    """Tests successful schema initialization."""
    pool, conn, cursor = mock_pool
    client = DatabaseClient(pool)

    client.initialize_schema()

    assert cursor.execute.call_count == 3
    cursor.execute.assert_any_call(mocker.string_matching("CREATE TABLE IF NOT EXISTS user_preferences"))
    cursor.execute.assert_any_call(mocker.string_matching("CREATE TABLE IF NOT EXISTS airports"))
    cursor.execute.assert_any_call(mocker.string_matching("INSERT INTO airports"))
    conn.commit.assert_called_once()
    pool.putconn.assert_called_once_with(conn)


def test_initialize_schema_db_error(mock_pool):
    """Tests schema initialization when a database error occurs."""
    pool, conn, cursor = mock_pool
    cursor.execute.side_effect = psycopg2.Error("Test DB Error")
    client = DatabaseClient(pool)

    client.initialize_schema()

    conn.commit.assert_not_called()
    pool.putconn.assert_called_once_with(conn)


def test_store_user_preference_success(mock_pool, mocker):
    """Tests storing a user preference successfully."""
    pool, conn, cursor = mock_pool
    client = DatabaseClient(pool)

    result = client.store_user_preference("test_user", "window")

    assert result is True
    cursor.execute.assert_called_once_with(
        mocker.string_matching("INSERT INTO user_preferences"),
        ("test_user", "window")
    )
    conn.commit.assert_called_once()
    pool.putconn.assert_called_once_with(conn)


def test_store_user_preference_db_error(mock_pool):
    """Tests storing a user preference when a database error occurs."""
    pool, conn, cursor = mock_pool
    cursor.execute.side_effect = psycopg2.Error("Test DB Error")
    client = DatabaseClient(pool)

    result = client.store_user_preference("test_user", "window")

    assert result is False
    conn.commit.assert_not_called()
    pool.putconn.assert_called_once_with(conn)


def test_get_user_preference_found(mock_pool):
    """Tests getting a user preference when it exists."""
    pool, conn, cursor = mock_pool
    cursor.fetchone.return_value = ("window",)
    client = DatabaseClient(pool)

    result = client.get_user_preference("test_user")

    assert result == "window"
    cursor.execute.assert_called_once_with(
        "SELECT seat_preference FROM user_preferences WHERE user_id = %s",
        ("test_user",)
    )
    pool.putconn.assert_called_once_with(conn)


def test_get_user_preference_not_found(mock_pool):
    """Tests getting a user preference when it does not exist."""
    pool, conn, cursor = mock_pool
    cursor.fetchone.return_value = None
    client = DatabaseClient(pool)

    result = client.get_user_preference("test_user")

    assert result is None
    pool.putconn.assert_called_once_with(conn)

def test_delete_user_preference_success(mock_pool, mocker):
    """Tests deleting a user preference successfully."""
    pool, conn, cursor = mock_pool
    cursor.rowcount = 1  # Simulate one row was deleted
    client = DatabaseClient(pool)

    result = client.delete_user_preference("test_user")

    assert result is True
    cursor.execute.assert_called_once_with(
        "DELETE FROM user_preferences WHERE user_id = %s",
        ("test_user",)
    )
    conn.commit.assert_called_once()
    pool.putconn.assert_called_once_with(conn)


def test_delete_user_preference_user_not_found(mock_pool, mocker):
    """Tests deleting a preference for a user that does not exist."""
    pool, conn, cursor = mock_pool
    cursor.rowcount = 0  # Simulate no rows were deleted
    client = DatabaseClient(pool)

    result = client.delete_user_preference("non_existent_user")

    assert result is False
    conn.commit.assert_called_once()


def test_delete_user_preference_db_error(mock_pool):
    """Tests deleting a user preference when a database error occurs."""
    pool, conn, cursor = mock_pool
    cursor.execute.side_effect = psycopg2.Error("Test DB Error")
    client = DatabaseClient(pool)

    result = client.delete_user_preference("test_user")

    assert result is False
    conn.commit.assert_not_called()
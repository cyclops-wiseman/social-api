import time
import jwt
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Provide a test client with an isolated test database.

    Creates a temporary SQLite database, overrides the application's
    database path and JWT secret, and seeds the database with test users.
    Yields a FastAPI test client for use in API tests.

    Args:
        tmp_path: A pytest fixture providing a temporary directory.
        monkeypatch: A pytest fixture for temporarily modifying
            application attributes.

    Yields:
        TestClient: A FastAPI test client connected to the application
        configured with the test database and JWT secret.
    """
    db_path = tmp_path / "test.db"
    secret = "pytest-only-secret-key-32-bytes-minimum!!"

    monkeypatch.setattr(main, "DB_PATH", str(db_path))
    monkeypatch.setattr(main, "JWT_SECRET", secret)

    with TestClient(main.app) as test_client:
        import sqlite3

        with sqlite3.connect(db_path) as db:
            db.execute("INSERT INTO users(id, username) VALUES (1, 'demo')")
            db.execute("INSERT INTO users(id, username) VALUES (2, 'customer2')")
        yield test_client


def token(user_id="1", username="demo", secret=None, **extra):
    """Generate a signed JWT for testing authentication.

    Creates a JWT containing the specified user ID, username, issued-at
    timestamp, and expiration timestamp. Additional claims can be supplied
    through keyword arguments. The token is signed using HS256 and either
    the provided secret or the application's configured JWT secret.

    Args:
        user_id: The user ID to include in the token's "sub" claim.
            Defaults to "1".
        username: The username to include in the token payload.
            Defaults to "demo".
        secret: The secret key used to sign the token. Defaults to the
            application's configured JWT secret if not provided.
        **extra: Additional claims to include in the JWT payload.
            These override existing claims if their keys overlap.

    Returns:
        str: The encoded JWT string.
    """
    payload = {
        "sub": user_id,
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        **extra,
    }
    return jwt.encode(
        payload,
        secret or main.JWT_SECRET,
        algorithm="HS256",
    )


def auth(user_id="1", username="demo"):
    """Create authorization headers containing a JWT bearer token.

    Generates a JWT for the specified user and formats it as an
    Authorization header for authenticated API test requests.

    Args:
        user_id: The user ID to include in the token. Defaults to "1".
        username: The username to include in the token. Defaults to
            "demo".

    Returns:
        dict[str, str]: A dictionary containing the Authorization header
        with the generated bearer token.
    """
    return {"Authorization": f"Bearer {token(user_id, username)}"}


def test_health(client):
    """Test that the health endpoint reports a healthy application.

    Sends a GET request to the health endpoint and verifies that the
    response indicates a successful status, a healthy database, and
    an integer uptime value.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The response status code is 200.
        The application status is "ok".
        The database status is "ok".
        The uptime value is an integer.
    """
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["db"] == "ok"
    assert isinstance(response.json()["uptime"], int)


def test_feed_empty(client):
    response = client.get("/feed")
    assert response.status_code == 200
    assert response.json() == {"posts": []}


def test_create_and_read_post(client):
    """Test creating a post and retrieving it by ID.

    Creates a post using an authenticated request and verifies that the
    response contains the expected body, author, initial like count, and
    UTC creation timestamp. Then retrieves the post by its ID and checks
    that the stored content matches the original post.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The post creation response has status code 201.
        The post body is stripped of leading and trailing whitespace.
        The author matches the authenticated username.
        The initial like count is zero.
        The creation timestamp ends with "Z", indicating UTC.
        The post retrieval response has status code 200.
        The retrieved post body matches the created post.
    """
    response = client.post(
        "/posts",
        headers=auth(),
        json={"body": " hello homelab "},
    )
    assert response.status_code == 201
    post = response.json()["post"]
    assert post["body"] == "hello homelab"
    assert post["author"] == "demo"
    assert post["like_count"] == 0
    assert post["created_at"].endswith("Z")

    read = client.get(f"/posts/{post['id']}")
    assert read.status_code == 200
    assert read.json()["post"]["body"] == "hello homelab"


def test_authentication_errors(client):
    """Test authentication failures when creating a post.

    Sends POST requests to the posts endpoint without an authorization
    header and with an invalid bearer token. Verifies that both requests
    are rejected with HTTP 401 responses and the expected error messages.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        A request without a bearer token returns status code 401 and
        the error message "missing bearer token".
        A request with an invalid bearer token returns status code 401
        and the error message "invalid or expired token".
    """
    response = client.post("/posts", json={"body": "hello"})
    assert response.status_code == 401
    assert response.json() == {"error": "missing bearer token"}

    response = client.post(
        "/posts",
        headers={"Authorization": "Bearer invalid"},
        json={"body": "hello"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid or expired token"}


@pytest.mark.parametrize(
    "body,message",
    [
        ({}, "body is required"),
        ({"body": 123}, "body is required"),
        ({"body": "  "}, "body is required"),
        ({"body": "x" * 501}, "body must be at most 500 characters"),
    ],
)
def test_invalid_post_body(client, body, message):
    """Test that invalid post bodies are rejected.

    Sends authenticated POST requests with missing, non-string, empty,
    whitespace-only, or excessively long post bodies.

    Args:
        client: The pytest fixture providing the FastAPI test client.
        body: The JSON payload to submit.
        message: The expected error message.

    Asserts:
        The response status code is 400.
        The response contains the expected error message.
    """
    response = client.post("/posts", headers=auth(), json=body)
    assert response.status_code == 400
    assert response.json() == {"error": message}


def test_malformed_json(client):
    """Test that malformed JSON request bodies are rejected.

    Sends an authenticated POST request containing invalid JSON and
    verifies that the API returns a client error.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The response status code is 400.
        The response contains the error message "malformed JSON body".
    """
    response = client.post(
        "/posts",
        headers={
            **auth(),
            "Content-Type": "application/json",
        },
        content="{broken",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "malformed JSON body"}


@pytest.mark.parametrize("post_id", ["0", "-1", "abc", "1.5"])
def test_invalid_post_ids(client, post_id):
    """Test that invalid post IDs are rejected.

    Sends GET requests using post IDs that are zero, negative,
    non-numeric, or decimal values.

    Args:
        client: The pytest fixture providing the FastAPI test client.
        post_id: The invalid post ID to test.

    Asserts:
        The response status code is 400.
        The response contains the error message "invalid post id".
    """
    response = client.get(f"/posts/{post_id}")
    assert response.status_code == 400
    assert response.json() == {"error": "invalid post id"}


def test_unknown_post(client):
    """Test that retrieving a nonexistent post returns a not-found error.

    Requests a post with an ID that does not exist in the database.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The response status code is 404.
        The response contains the error message "post not found".
    """
    response = client.get("/posts/999")
    assert response.status_code == 404
    assert response.json() == {"error": "post not found"}


def test_like_is_idempotent(client):
    """Test that liking the same post multiple times creates one like.

    Creates a post and submits two like requests from the same user.
    Verifies that the first request creates a like and the second
    request does not create a duplicate.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The first like request returns status code 201 and indicates
        that the post was not previously liked.
        The second like request returns status code 200 and indicates
        that the post was already liked.
        The post's like count remains one.
    """
    created = client.post("/posts", headers=auth(), json={"body": "like me"})
    post_id = created.json()["post"]["id"]

    first = client.post(f"/posts/{post_id}/like", headers=auth())
    second = client.post(f"/posts/{post_id}/like", headers=auth())

    assert first.status_code == 201
    assert first.json() == {
        "liked": True,
        "already_liked": False,
        "post_id": post_id,
    }

    assert second.status_code == 200
    assert second.json() == {
        "liked": True,
        "already_liked": True,
        "post_id": post_id,
    }

    post = client.get(f"/posts/{post_id}").json()["post"]
    assert post["like_count"] == 1


def test_different_users_can_like(client):
    """Test that different users can like the same post.

    Creates a post as the default user and submits a like request
    as a second authenticated user.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The second user's like request returns status code 201.
        The post's like count is one after the request.
    """
    created = client.post("/posts", headers=auth(), json={"body": "multiple likes"})
    post_id = created.json()["post"]["id"]

    response = client.post(
        f"/posts/{post_id}/like",
        headers=auth("2", "customer2"),
    )
    assert response.status_code == 201

    assert client.get(f"/posts/{post_id}").json()["post"]["like_count"] == 1


def test_authentication_precedes_like_id_validation(client):
    """Test that authentication occurs before post ID validation.

    Sends like requests with an invalid post ID, first without
    authentication and then with a valid bearer token.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The unauthenticated request returns status code 401.
        The authenticated request returns status code 400.
    """
    response = client.post("/posts/abc/like")
    assert response.status_code == 401

    response = client.post("/posts/abc/like", headers=auth())
    assert response.status_code == 400


def test_expired_token(client):
    """Test that an expired JWT is rejected.

    Generates an expired bearer token and uses it to submit a request
    to the post creation endpoint.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The response status code is 401.
        The response contains the error message
        "invalid or expired token".
    """
    expired = token(exp=int(time.time()) - 60)
    response = client.post(
        "/posts",
        headers={"Authorization": f"Bearer {expired}"},
        json={"body": "hello"},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "invalid or expired token"}


def test_feed_is_limited_to_20(client):
    """Test that the feed returns no more than 20 posts.

    Creates 25 posts using authenticated requests and retrieves the
    feed to verify that the endpoint enforces its result limit.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        Every post creation request returns status code 201.
        The feed request returns status code 200.
        The feed contains exactly 20 posts.
    """
    for i in range(25):
        response = client.post(
            "/posts",
            headers=auth(),
            json={"body": f"post {i}"},
        )
        assert response.status_code == 201

    response = client.get("/feed")
    assert response.status_code == 200
    assert len(response.json()["posts"]) == 20


def test_unknown_route(client):
    """Test that requests to undefined routes return a not-found error.

    Sends a GET request to a path that is not registered with the
    application.

    Args:
        client: The pytest fixture providing the FastAPI test client.

    Asserts:
        The response status code is 404.
        The response contains the error message "not found".
    """
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}

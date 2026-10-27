import json
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import (
    DB_PATH,
    JWT_SECRET,
    HOST,
    PORT,
    ENVIRONMENT,
    LOG_LEVEL,
)

START_TIME = time.monotonic()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("feed-api")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 500),
    created_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS likes (
    user_id INTEGER NOT NULL REFERENCES users(id),
    post_id INTEGER NOT NULL REFERENCES posts(id),
    created_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (user_id, post_id)
);

CREATE INDEX IF NOT EXISTS posts_created_at_id_idx
    ON posts(created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS likes_post_id_idx ON likes(post_id);
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown events.

    Creates the database directory, validates the JWT secret in production,
    initializes the SQLite database schema, and logs application lifecycle
    events.

    Args:
        app: The FastAPI application instance.

    Raises:
        RuntimeError: If JWT_SECRET is not configured in production.
        sqlite3.Error: If database initialization fails.
    """
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    # In prod, configure a real secret in the service environment.
    if not JWT_SECRET and os.getenv("ENVIRONMENT") == "production":
        raise RuntimeError("JWT_SECRET must be configured!")

    with sqlite3.connect(DB_PATH, timeout=5) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)

    log.info("startup complete")
    yield
    log.info("shutdown complete")


app = FastAPI(title="Homelab Feed API", lifespan=lifespan)


def error(message: str, status: int):
    """Create a standardized JSON error response.

    Args:
        message: The error message to include in the response body.
        status: The HTTP status code for the response.

    Returns:
        JSONResponse: A response containing the error message and status
        code.
    """
    return JSONResponse(status_code=status, content={"error": message})


def connect():
    """Create and configure a SQLite database connection.

    Opens the database specified by DB_PATH, configures rows to be
    accessible by column name, and enables foreign key enforcement.

    Returns:
        sqlite3.Connection: The configured database connection.
    """
    db = sqlite3.connect(DB_PATH, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions and return standardized JSON errors.

    Returns a 404 response for not-found errors and a generic 500 response
    for all other HTTP exceptions.

    Args:
        request: The incoming HTTP request.
        exc: The HTTP exception raised by the application.

    Returns:
        JSONResponse: A standardized JSON error response.
    """
    if exc.status_code == 404:
        return error("not found", 404)
    return error("internal server error", 500)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Handle request validation errors.

    Returns a generic internal server error response when request
    validation fails.

    Args:
        request: The incoming HTTP request.
        exc: The request validation exception.

    Returns:
        JSONResponse: A JSON response with HTTP status code 500.
    """
    return error("internal server error", 500)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """Handle unexpected exceptions raised during request processing.

    Logs the exception with its traceback and returns a generic internal
    server error response.

    Args:
        request: The incoming HTTP request.
        exc: The unexpected exception that occurred.

    Returns:
        JSONResponse: A JSON response with HTTP status code 500.
    """
    log.exception("unhandled request error")
    return error("internal server error", 500)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    """Log HTTP request details and processing duration.

    Measures the time taken to process a request and logs its HTTP method,
    URL path, response status code, and duration in milliseconds.

    Args:
        request: The incoming HTTP request.
        call_next: The callable that forwards the request to the next
            middleware or application handler.

    Returns:
        Response: The HTTP response produced by the downstream handler.
    """
    started = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - started) * 1000
    log.info(
        "method=%s path=%s status=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


def authenticate(request: Request):
    """Authenticate a request using a JWT bearer token.

    Extracts the bearer token from the Authorization header, validates
    the token using HS256, and checks that the required claims have
    acceptable values.

    Args:
        request: The incoming HTTP request containing the Authorization
            header.

    Returns:
        tuple[dict | None, JSONResponse | None]: A tuple containing the
        authenticated user's data and None on success, or None and an
        error response on failure. The user data contains the integer
        user ID under "id" and the username under "username".
    """
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return None, error("missing bearer token", 401)

    token = authorization[len("Bearer ") :]
    try:
        claims = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None, error("invalid or expired token", 401)

    sub = claims.get("sub")
    username = claims.get("username")

    if (
        not isinstance(sub, str)
        or not re.fullmatch(r"[1-9][0-9]*", sub)
        or not isinstance(username, str)
    ):
        return None, error("invalid token payload", 401)

    return {"id": int(sub), "username": username}, None


def valid_id(value: str) -> bool:
    """Check whether a string represents a positive integer ID.

    The value must contain only decimal digits and represent an integer
    greater than zero.

    Args:
        value: The string to validate.

    Returns:
        bool: True if the value is a valid positive integer ID, otherwise
        False.
    """
    return bool(re.fullmatch(r"[0-9]+", value)) and int(value) > 0


def post_query():
    """Build the base SQL query for retrieving posts.

    Selects post details, the author's username, and the number of likes
    associated with each post. The query joins posts with users and can
    be extended with additional SQL clauses by the caller.

    Returns:
        str: The SQL SELECT statement for retrieving post information.
    """
    return """
        SELECT p.id, p.body, p.created_at,
               u.username AS author,
               (SELECT COUNT(*) FROM likes l
                WHERE l.post_id = p.id) AS like_count
        FROM posts p
        JOIN users u ON u.id = p.user_id
    """


def get_post(db, post_id: int):
    """Retrieve a post by its ID.

    Executes the base post query filtered by the specified post ID and
    converts the resulting database row into a dictionary.

    Args:
        db: An active SQLite database connection.
        post_id: The ID of the post to retrieve.

    Returns:
        dict | None: A dictionary containing the post's fields, author,
        and like count, or None if no matching post exists.
    """
    row = db.execute(
        post_query() + " WHERE p.id = ?",
        (post_id,),
    ).fetchone()
    return dict(row) if row else None


@app.get("/health")
def health():
    """Check application and database health.

    Executes a simple database query to verify connectivity and returns
    the current application uptime. If a SQLite error occurs, returns
    a degraded health status with details of the database error.

    Returns:
        dict | JSONResponse: A dictionary containing the application
        status, database status, and uptime in seconds when healthy,
        or a JSON response with HTTP status code 503 when the database
        is unavailable.
    """
    try:
        with connect() as db:
            db.execute("SELECT 1").fetchone()

        return {
            "status": "ok",
            "db": "ok",
            "uptime": int(time.monotonic() - START_TIME),
        }
    except sqlite3.Error as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "db": "unreachable",
                "error": str(exc),
            },
        )


@app.post("/auth/register")
async def register(request: Request):
    """Register a new user and issue an authentication token.

    Parses and validates the JSON request body, checks the username
    format, and creates a new user in the database. If registration
    succeeds, generates a signed JWT containing the user's ID and
    username, valid for 24 hours.

    Args:
        request: The incoming HTTP request containing a JSON body
            with the desired username.

    Returns:
        JSONResponse: A response with status code 201 containing the
        registered user's ID and username, an authentication token,
        the token type, and its expiration duration in seconds.
        Returns an error response with status code 400 for malformed
        JSON or an invalid username, or 409 if the username already
        exists.
    """
    try: 
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return error("malformed JSON body", 400)

    username = payload.get("username") if isinstance(payload, dict) else None
    if not isinstance(username, str):
        return error("username is required", 400)

    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        return error(
            "username must be 3-32 characters using letters, numbers or underscores",
            400,
        )

    try: 
        with connect() as db:
            cursor = db.execute(
                "INSERT INTO users(username) VALUES (?)",
                (username,),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return error("username already exists", 409)

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": str(user_id),
            "username": username,
            "iat": now,
            "exp": now + 86400,
        },
        JWT_SECRET,
        algorithm="H256"
    )

    return JSONResponse(
        status_code=201,
        content={
            "user": {"id": user_id, "username": username},
            "token": token,
            "token_type": "bearer",
            "expires_in": 86400,
        },
    )


@app.get("/feed")
def feed():
    """Retrieve the latest posts for the feed.

    Fetches up to 20 posts from the database, ordered by creation time
    in descending order, with post ID as a secondary sort key. Each post
    includes its author and like count.

    Returns:
        dict: A dictionary containing a list of posts under the "posts"
        key. Each post is represented as a dictionary.
    """
    with connect() as db:
        rows = db.execute(
            post_query() + " ORDER BY p.created_at DESC, p.id DESC LIMIT 20"
        ).fetchall()
    return {"posts": [dict(row) for row in rows]}


@app.get("/posts/{post_id}")
def read_post(post_id: str):
    """Retrieve a post by its ID.

    Validates the supplied post ID, retrieves the corresponding post from
    the database, and returns the post details if found.

    Args:
        post_id: The ID of the post to retrieve, provided as a string.

    Returns:
        dict | JSONResponse: A dictionary containing the post under the
        "post" key if found, or an error response with status code 400
        for an invalid ID or 404 if the post does not exist.
    """
    if not valid_id(post_id):
        return error("invalid post id", 400)

    with connect() as db:
        post = get_post(db, int(post_id))

    if post is None:
        return error("post not found", 404)

    return {"post": post}


@app.post("/posts")
async def create_post(request: Request):
    """Create a new post for the authenticated user.

    Authenticates the request, parses and validates the JSON payload,
    and inserts the post into the database. The post body must be a
    non-empty string of at most 500 characters after trimming whitespace.

    Args:
        request: The incoming HTTP request containing a bearer token
            and a JSON body with the post content.

    Returns:
        JSONResponse: A response with status code 201 containing the
        created post's ID, body, creation timestamp, author username,
        and initial like count. Returns an error response with status
        code 401 for authentication failures or 400 for malformed JSON
        or invalid post content.
    """
    user, auth_error = authenticate(request)
    if auth_error:
        return auth_error

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return error("malformed JSON body", 400)

    if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
        return error("body is required", 400)

    body = payload["body"].strip()

    if not body:
        return error("body is required", 400)

    if len(body) > 500:
        return error("body must be at most 500 characters", 400)

    with connect() as db:
        cursor = db.execute(
            "INSERT INTO posts(user_id, body) VALUES (?, ?)",
            (user["id"], body),
        )
        post_id = cursor.lastrowid
        created_at = db.execute(
            "SELECT created_at FROM posts WHERE id = ?", (post_id,)
        ).fetchone()["created_at"]

    # The contract uses the token username in this response.
    return JSONResponse(
        status_code=201,
        content={
            "post": {
                "id": post_id,
                "body": body,
                "created_at": created_at,
                "author": user["username"],
                "like_count": 0,
            }
        },
    )


@app.post("/posts/{post_id}/like")
def like_post(post_id: str, request: Request):
    """Like a post on behalf of the authenticated user.

    Authenticates the request before validating the post ID, verifies
    that the post exists, and inserts a like into the database.
    Duplicate likes are ignored so that a user can like a post only
    once.

    Args:
        post_id: The ID of the post to like, provided as a string.
        request: The incoming HTTP request containing the user's
            bearer token.

    Returns:
        JSONResponse: A response containing the like status, whether
        the post was already liked, and the post ID. Returns status
        code 201 when a new like is created or 200 when the user has
        already liked the post. Returns an error response with status
        code 401 for authentication failures, 400 for an invalid post
        ID, or 404 if the post does not exist.
    """
    # Auth must run before post ID validation
    user, auth_error = authenticate(request)
    if auth_error:
        return auth_error

    if not valid_id(post_id):
        return error("invalid post id", 400)

    post_id = int(post_id)

    with connect() as db:
        exists = db.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()

        if exists is None:
            return error("post not found", 404)

        cursor = db.execute(
            """
            INSERT INTO LIKES(user_id, post_id)
            VALUES (?,?)
            ON CONFLICT(user_id, post_id) DO NOTHING
            """,
            (user["id"], post_id),
        )

        already_liked = cursor.rowcount == 0

    return JSONResponse(
        status_code=200 if already_liked else 201,
        content={
            "liked": True,
            "already_liked": already_liked,
            "post_id": post_id,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
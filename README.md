# Homelab Feed API

A lightweight FastAPI service for a social-style feed with JWT-based authentication, SQLite persistence, post creation, feed retrieval, and likes.

## Overview

This project exposes a small API for:
- creating and reading posts
- retrieving the latest feed
- liking a post once per user
- authenticating requests with a bearer JWT
- storing data in SQLite

The application is implemented in [app/main.py](app/main.py), with environment/configuration in [app/config.py](app/config.py). API behavior is covered by [tests/test_api.py](tests/test_api.py).

## Tech Stack

- Python 3
- FastAPI
- SQLite
- PyJWT
- Pytest

## Project Structure

```text
.
├── app/
│   ├── __init__.py
│   ├── config.py
│   └── main.py
├── data/
│   └── feed.db
├── loadtest/
│   └── tokens.json
├── scripts/
│   └── seed_users.py
├── tests/
│   └── test_api.py
├── .env.example
├── pytest.ini
├── requirements.txt
└── README.md
```

## Prerequisites

- Python 3.10+
- pip
- A local `.env` file for runtime settings

## Setup

1. Create and activate a virtual environment:

```bash
python -m venv env
```

On Windows PowerShell:

```powershell
.\env\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source env/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy the example environment file and update it:

```bash
copy .env.example .env
```

or on Linux/macOS:

```bash
cp .env.example .env
```

Edit `.env` with a real secret:

```env
ENVIRONMENT=development
SQLITE_PATH=./data/feed.db
JWT_SECRET=replace-this-with-a-local-development-secret
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
```

Generate a strong development secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Run the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The app will initialize the SQLite schema automatically on startup.

Open the interactive API docs here:

- http://localhost:8000/docs
- http://localhost:8000/redoc

## API Endpoints

### Health check

```http
GET /health
```

Returns application status and database health.

Example response:

```json
{
  "status": "ok",
  "db": "ok",
  "uptime": 42
}
```

---

### Feed

```http
GET /feed
```

Returns the latest posts, sorted newest first.

Example response:

```json
{
  "posts": [
    {
      "id": 1,
      "body": "hello from the feed",
      "created_at": "2026-10-26T12:00:00Z",
      "author": "demo",
      "like_count": 2
    }
  ]
}
```

---

### Read one post

```http
GET /posts/{post_id}
```

Example:

```bash
curl http://localhost:8000/posts/1
```

Response:

```json
{
  "post": {
    "id": 1,
    "body": "hello from the feed",
    "created_at": "2026-10-26T12:00:00Z",
    "author": "demo",
    "like_count": 2
  }
}
```

---

### Create a post

```http
POST /posts
Authorization: Bearer <jwt>
```

Request body:

```json
{
  "body": "hello from the feed"
}
```

Example:

```bash
curl -X POST http://localhost:8000/posts \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{"body":"hello from the feed"}'
```

Response:

```json
{
  "post": {
    "id": 7,
    "body": "hello from the feed",
    "created_at": "2026-10-26T12:00:00Z",
    "author": "demo",
    "like_count": 0
  }
}
```

Notes:
- `body` is required
- it must be a non-empty string
- max length is 500 characters
- leading/trailing whitespace is trimmed

---

### Like a post

```http
POST /posts/{post_id}/like
Authorization: Bearer <jwt>
```

Example:

```bash
curl -X POST http://localhost:8000/posts/7/like \
  -H "Authorization: Bearer <jwt>"
```

Example response:

```json
{
  "liked": true,
  "already_liked": false,
  "post_id": 7
}
```

If the same user likes the same post again, the API returns:

```json
{
  "liked": true,
  "already_liked": true,
  "post_id": 7
}
```

---

## Authentication

Protected endpoints require a JWT bearer token in the `Authorization` header:

```http
Authorization: Bearer <token>
```

The token must include:
- `sub`: user id as a string
- `username`
- `exp`: expiration time

The app validates the token signature using the configured `JWT_SECRET`.

## Load Testing Helpers

The repository includes a helper script to generate synthetic users and JWTs:

```bash
python scripts/seed_users.py --count 100 --output loadtest/tokens.json
```

This creates a JSON file with user ids, usernames, and bearer tokens for load testing. It checks for a valid `JWT_SECRET` and requires the API database to already exist.

## Database

The app uses SQLite and stores the database at the path configured in `SQLITE_PATH`, defaulting to:

```text
data/feed.db
```

The schema includes:
- `users`
- `posts`
- `likes`

It also creates supporting indexes for feed ordering and like lookups.

## Testing

Run the project test suite with:

```bash
pytest
```

The tests in `test_api.py` cover:
- health endpoint
- empty feed
- post creation and retrieval
- authentication failure cases
- malformed JSON handling
- invalid IDs
- like behavior and validation

## Notes

- In production, set `JWT_SECRET` to a secure secret and do not leave it as a placeholder.
- The app is intentionally simple and suitable for a homelab or small internal feed service.
- SQLite is used for ease of setup and local deployment; it is not intended as a high-scale production data layer.
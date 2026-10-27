import argparse
import json
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from app.config import DB_PATH, JWT_SECRET
from app.main import SCHEMA

WORDS = (
    "server database request latency cache index query benchmark droplet cpu "
    "memory disk network throughput coffee morning weekend project deploy "
    "release bug feature test rewrite refactor commit branch merge review "
    "thinking building shipping learning reading writing debugging measuring "
    "watching waiting running walking fast slow small big cheap simple boring "
    "clever weird surprising honest careful lucky tired ready done broken "
    "nginx linux systemd kernel socket keepalive pool"
).split()

OPENERS = (
    "Hot take:",
    "TIL",
    "Unpopular opinion:",
    "Note to self:",
    "PSA:",
    "Update:",
    "Question:",
)


def timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_post_body(rng: random.Random) -> str:
    word_count = rng.randint(5,24)
    words = [rng.choice(WORDS) for _ in range(word_count)]
    body = " ".join(words)
    body = body[0].upper() + body[1:] + rng.choice((".", ",", "!", "?"))

    if rng.random() < 0.25:
        body = f"{rng.choice(OPENERS)} {body}"

    return body[:500]


def insert_in_batches(
    db: sqlite3.Connection,
    sql: str,
    rows, 
    batch_size: int = 5000,      
) -> int:
    batch = []
    inserted = 0

    for row in rows:
        batch.append(row)
        if len(batch) == batch_size:
            db.executemany(sql, batch)
            inserted += len(batch)
            batch.clear()

    if batch: 
        db.executemany(sql, batch)
        inserted += len(batch)

    return inserted


def main():
    parser = argparse.ArgumentParser(
        description="Create synthetic feed API data for load testing"
    )
    parser.add_argument("--users", type=int, default=1_000)
    parser.add_argument("--posts", type=int, default=10_000)
    parser.add_argument(
        "--likes",
        type=int,
        default=40_000,
        help="Approximate number of likes; distribution is intentionally uneven",
    )
    parser.add_argument("--tokens", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="loadtest/tokens.json")
    args = parser.parse_args()

    if min(args.users, args.posts, args.likes, args.tokens) < 1:
        parser.error("--users, --posts, --likes and --tokens must be positive")
    if args.tokens > args.users:
        parser.error("--tokens cannot exceed --users")
    if len(JWT_SECRET.encode("utf-8")) < 32:
        parser.error("Set JWT_SECRET to a secret of atleast 32 bytes")

    rng = random.Random(args.seed)
    end = datetime(2026,1,1, tzinfo=timezone.utc)
    start_ms = int((end - timedelta(days=365)).timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    time_span_ms = end_ms - start_ms

    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path, timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(SCHEMA)

        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "posts", "likes")
        }
        if any(counts.values()):
            raise SystemExit(
                f"Database is not emtpy: {counts}."
                "Use a separate, empty SQLITE_PATH for generated test data."
            )

        usernames = [
            f"loaduser{number:06d}"
            for number in range(1, args.users + 1)
        ]

        def user_rows():
            for user_id, username in enumerate(usernames, start=1):
                created = end - timedelta(
                    days=730 - rng.random() * 365
                )
                yield user_id, username, timestamp(created)

        insert_in_batches(
            db, 
            "INSERT INTO users(id, username, created_at) VALUES (?,?,?)",
            user_rows(),
        )

        post_times_ms = []

        def post_rows():
            for post_id in range(1, args.posts+1):
                user_id = 1 + int((rng.random() ** 2) * args.users)
                created_ms = start_ms + int(rng.random() * time_span_ms)
                post_times_ms.append(created_ms)
                yield (
                    post_id, user_id,
                    make_post_body(rng),
                    timestamp(datetime.fromtimestamp(
                        created_ms / 1000,
                        tz=timezone.utc
                    )),
                )

        insert_in_batches(
            db, 
            "INSERT INTO posts(id, user_id, body, created_at) VALUES (?,?,?,?)",
            post_rows(),
        )
        mean_likes = args.likes / args.posts
        popular_mean = mean_likes * 20
        normal_mean = (mean_likes - 0.01 * popular_mean) / 0.99
        normal_mean = max(0.05, normal_mean)
        like_cap = min( 
            args.users,
            max(50, round(popular_mean * 10)),
        )

        def like_rows():
            for post_id, post_created_ms in enumerate(post_times_ms, start=1):
                is_popular = rng.random() < 0.01
                mean = popular_mean if is_popular else normal_mean
                like_count = min(
                    like_cap,
                    int(rng.expovariate(1/mean)),
                )

                for user_id in rng.sample(
                    range(1, args.users + 1),
                    like_count,
                ):
                    liked_ms = post_created_ms + rng.randint(
                        0,
                        7*24*60*60*1000,
                    )
                    yield(
                        user_id, 
                        post_id,
                        timestamp(datetime.fromtimestamp(
                            liked_ms / 1000,
                            tz=timezone.utc,
                        )),
                    )

        likes_created = insert_in_batches(
            db, "INSERT INTO likes(user_id, post_id, created_at) "
            "VALUES (?,?,?)",
            like_rows(),
        )

    now = int(time.time())
    tokens = [
        {                                 
            "id": user_id,
            "username": usernames[user_id - 1],
            "token": jwt.encode(
                {
                    "sub": str(user_id),
                    "username": usernames[user_id - 1],
                    "iat": now,
                    "exp": now + 365 * 24 * 60 * 60, 
                },
                JWT_SECRET,
                algorithm="HS256",
            ),
        }
        for user_id in range(1, args.tokens + 1)
    ]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

    try:
        os.chmod(output, 0o600)
    except OSError:
        pass

    print(f"Database: {db_path}")
    print(f"Users: {args.users:,}")
    print(f"Posts: {args.posts:,}")
    print(f"Likes: {likes_created:,} (approximate target: {args.likes:,})")
    print(f"Tokens: {len(tokens):,} (expire in 365 days)")
    print(f"Token file: {output.resolve()}")


if __name__ == "__main__":
    main()
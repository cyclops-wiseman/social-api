import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import jwt
from app.config import DB_PATH, JWT_SECRET


def main():
    parser = argparse.ArgumentParser(
        description="Create synthetic feed API users and JWTs"
    )
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--output", default="loadtest/tokens.json")
    args = parser.parse_args()

    if args.count < 1 or args.count > 100_000:
        parser.error("--count must be between 1 and 100000")

    if len(JWT_SECRET.encode("utf-8")) < 32:
        parser.error("Set JWT_SECRET to a secret of atleast 32 bytes")

    db_path = Path(DB_PATH)
    if not db_path.exists():
        raise SystemExit(f'DB not found: {db_path}. Start the API first.')

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    users = []
    now = int(time.time())

    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("PRAGMA foreign_keys=ON")

        # reuse existing synthetic users when rerunning the script. 
        for number in range(1, args.count + 1):
            username = f"loaduser{number:04d}"

            db.execute(
                "INSERT OR IGNORE INTO users(username) VALUES (?)",
                (username,),
            )

            row = db.execute(
                "SELECT id FROM users WHERE username = ?", 
                (username,),
            ).fetchone()

            user_id = row[0]

            token = jwt.encode(
                {
                    "sub": str(user_id),
                    "username": username,
                    "iat": now, 
                    "exp": now + 86400,
                },
                JWT_SECRET,
                algorithm="HS256",
            )

            users.append({
                "id": user_id,
                "username": username,
                "token": token, 
            })

    output.write_text(
        json.dumps(users, indent=2),
        encoding="utf-8",
    )

    try: 
        os.chmod(output, 0o600)
    except OSError:
        # Windows permissions are managed differently
        pass

    print(f"Prepared {len(users)} synthetic users.")
    print(f"Token file: {output.resolve()}")
    print("Tokens expire in 24 hours.")


if __name__ == "__main__":
    main()
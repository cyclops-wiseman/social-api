import os
from pathlib import Path

from dotenv import load_dotenv


# Project Root is the parent directory of app/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load the project's .env regardless of current working directory
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)


def project_path(value: str) -> Path:
    """Resolve relative paths from project root."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DB_PATH = project_path(
    os.getenv("SQLITE_PATH", "data/feed.db")
)

JWT_SECRET = os.getenv("JWT_SECRET", "")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
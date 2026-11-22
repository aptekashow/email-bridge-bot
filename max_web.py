import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("MAX_WEB_DATA_DIR", str(BASE_DIR.parent / "email-bridge-data")))
PROFILE_DIR = DATA_DIR / "max-profile"
SCREENSHOT_PATH = DATA_DIR / "max-login.png"
STATUS_PATH = DATA_DIR / "max-web-status.json"
COMMAND_PATH = DATA_DIR / "max-web-command.json"
MAX_WEB_URL = "https://web.max.ru/"


def _atomic_json_write(path: Path, payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(DATA_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def queue_browser_start() -> None:
    """Ask the separate MAX browser worker to open MAX Web."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FileLock(str(COMMAND_PATH) + ".lock", timeout=5):
        _atomic_json_write(COMMAND_PATH, {
            "action": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })


def read_browser_status() -> dict:
    try:
        with STATUS_PATH.open("r", encoding="utf-8") as stream:
            result = json.load(stream)
        if isinstance(result, dict):
            return result
    except (OSError, ValueError):
        pass
    return {
        "running": False,
        "message": "Браузер ещё не запускался",
        "updated_at": None,
        "error": None,
    }


def get_screenshot_path() -> Path:
    return SCREENSHOT_PATH


def get_data_dir() -> Path:
    return DATA_DIR

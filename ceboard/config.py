import os
from pathlib import Path
from datetime import timezone, timedelta

DATA_DIR = os.getenv("DATA_DIR", str(Path("./data").resolve()))
IMAGE_DIR = os.getenv("IMAGE_DIR", str(Path("./images").resolve()))
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
Path(IMAGE_DIR).mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{Path(DATA_DIR) / 'ctf_scoring.db'}")
SESSION_SECRET = os.getenv("SESSION_SECRET", "CloudEver-Team")
TZ = timezone(timedelta(hours=9))  # 亚洲/东京（UTC+9）

MAX_AVATAR_SIZE = 1 * 1024 * 1024  # 1MB

CATEGORIES = ["web", "pwn", "crypto", "rev", "misc", "others"]

VERSION = "1.3.0"

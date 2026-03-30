# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Evolution status tracking and study/implement logic."""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from .config import PROJECT_DIR

log = logging.getLogger("admin")


def _update_evolution_status(pid: str, status: str):
    """Update evolution database entry status."""
    db_path = os.path.join(PROJECT_DIR, "evolution_database.json")
    try:
        hkt = timezone(timedelta(hours=8))
        with open(db_path) as f:
            db = json.load(f)
        if not isinstance(db, list):
            return
        for entry in db:
            if isinstance(entry, dict) and entry.get("id") == pid:
                entry["status"] = status
                entry["updated_at"] = datetime.now(hkt).isoformat()
                break
        with open(db_path, "w") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

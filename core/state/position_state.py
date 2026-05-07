"""Position state persisted in SQLite."""
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional


class PositionState:
    def __init__(self, db_path: str = "memory/trades.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                )
                """
            )

    def save(self, position: Optional[Any], symbol: Optional[str] = None) -> None:
        with self._connect() as conn:
            if not position:
                if symbol:
                    conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
                else:
                    conn.execute("DELETE FROM positions")
                return
            conn.execute(
                "INSERT OR REPLACE INTO positions(symbol, payload) VALUES (?, ?)",
                (position.symbol, json.dumps(position.to_dict(), ensure_ascii=True)),
            )

    def load(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return json.loads(row[0]) if row else None


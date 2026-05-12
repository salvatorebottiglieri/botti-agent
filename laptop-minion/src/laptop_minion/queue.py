"""SQLite offline queue for events.

When disconnected from MQTT, events are stored in SQLite and flushed
when connection is restored.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cortex_protocol.schemas import MinionEvent

from laptop_minion.config import get_state_dir


class OfflineQueue:
    """SQLite-backed offline queue for minion events.

    Thread-safe queue that stores events when disconnected and flushes
    them when connection is restored.
    """

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = get_state_dir() / "offline_queue.db"
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the SQLite database schema."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_queue (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    flushed_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_flushed
                ON event_queue(flushed_at)
            """)
            conn.commit()
            conn.close()

    def enqueue(self, event: MinionEvent, batch_id: UUID, sequence: int) -> str:
        """Add an event to the queue.

        Returns the event's queue ID.
        """
        queue_id = str(uuid4())
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """
                INSERT INTO event_queue (id, batch_id, sequence, event_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    queue_id,
                    str(batch_id),
                    sequence,
                    event.model_dump_json(),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        return queue_id

    def enqueue_batch(
        self, events: list[MinionEvent], batch_id: UUID, start_sequence: int
    ) -> list[str]:
        """Add multiple events to the queue.

        Returns list of queue IDs.
        """
        queue_ids = []
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            now = datetime.utcnow().isoformat()
            for i, event in enumerate(events):
                queue_id = str(uuid4())
                queue_ids.append(queue_id)
                conn.execute(
                    """
                    INSERT INTO event_queue (id, batch_id, sequence, event_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        queue_id,
                        str(batch_id),
                        start_sequence + i,
                        event.model_dump_json(),
                        now,
                    ),
                )
            conn.commit()
            conn.close()
        return queue_ids

    def flush(self) -> tuple[list[tuple[str, MinionEvent, int]], UUID]:
        """Get all unflushed events and mark them as flushed.

        Returns:
            Tuple of (list of (queue_id, event, sequence), batch_id)
            A new batch_id is generated for this flush.
        """
        batch_id = uuid4()
        events: list[tuple[str, MinionEvent, int]] = []

        with self._lock:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                """
                SELECT id, event_json, sequence
                FROM event_queue
                WHERE flushed_at IS NULL
                ORDER BY sequence ASC
                """,
            )
            rows = cursor.fetchall()
            cursor.close()

            if rows:
                now = datetime.utcnow().isoformat()
                ids = [row[0] for row in rows]
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"""
                    UPDATE event_queue
                    SET flushed_at = ?, batch_id = ?
                    WHERE id IN ({placeholders})
                    """,
                    [now, str(batch_id), *ids],
                )
                conn.commit()

            conn.close()

        for queue_id, event_json, sequence in rows:
            event_dict = json.loads(event_json)
            # Reconstruct the event with discriminator
            event = _parse_event(event_dict)
            events.append((queue_id, event, sequence))

        return events, batch_id

    def mark_flushed(self, queue_ids: list[str]) -> None:
        """Mark specific events as flushed."""
        if not queue_ids:
            return
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            now = datetime.utcnow().isoformat()
            placeholders = ",".join("?" * len(queue_ids))
            conn.execute(
                f"""
                UPDATE event_queue
                SET flushed_at = ?
                WHERE id IN ({placeholders})
                """,
                [now, *queue_ids],
            )
            conn.commit()
            conn.close()

    def delete_flushed(self, before_date: datetime | None = None) -> int:
        """Delete flushed events older than the given date.

        Returns the number of deleted rows.
        """
        if before_date is None:
            before_date = datetime.utcnow()
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                """
                DELETE FROM event_queue
                WHERE flushed_at IS NOT NULL
                AND flushed_at < ?
                """,
                [before_date.isoformat()],
            )
            conn.commit()
            conn.close()
            return cursor.rowcount

    def size(self) -> int:
        """Get the number of unflushed events in the queue."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                "SELECT COUNT(*) FROM event_queue WHERE flushed_at IS NULL"
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count

    def total_count(self) -> int:
        """Get the total number of events ever queued."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM event_queue")
            count = cursor.fetchone()[0]
            conn.close()
            return count

    def stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN flushed_at IS NULL THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN flushed_at IS NOT NULL THEN 1 ELSE 0 END) as flushed
                FROM event_queue
                """
            )
            row = cursor.fetchone()
            conn.close()
            return {
                "total_events": row[0] or 0,
                "pending_events": row[1] or 0,
                "flushed_events": row[2] or 0,
                "queue_size_bytes": self._db_path.stat().st_size if self._db_path.exists() else 0,
            }


def _parse_event(event_dict: dict[str, Any]) -> MinionEvent:
    """Parse a JSON dict back into the correct MinionEvent type."""
    from cortex_protocol.schemas.events import (
        ActivityEvent,
        AppUsageEvent,
        ApplicationFocusEvent,
        BatteryEvent,
        CalendarEvent,
        CallLogEvent,
        KeyboardActivityEvent,
        LocationEvent,
        NetworkStatusEvent,
        PaymentEvent,
        RefundEvent,
        ScreenActivityEvent,
    )

    event_type = event_dict.get("type")
    parsers = {
        "location": LocationEvent,
        "activity": ActivityEvent,
        "calendar": CalendarEvent,
        "app_usage": AppUsageEvent,
        "call_log": CallLogEvent,
        "payment": PaymentEvent,
        "refund": RefundEvent,
        "screen_activity": ScreenActivityEvent,
        "application_focus": ApplicationFocusEvent,
        "keyboard_activity": KeyboardActivityEvent,
        "battery": BatteryEvent,
        "network_status": NetworkStatusEvent,
    }

    parser = parsers.get(event_type, ScreenActivityEvent)
    return parser.model_validate(event_dict)

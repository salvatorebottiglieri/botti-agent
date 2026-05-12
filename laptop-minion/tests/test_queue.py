"""Tests for queue module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from cortex_protocol.schemas import (
    ScreenActivityEvent,
    ScreenActivityPayload,
    BatteryEvent,
    BatteryPayload,
)

from laptop_minion.queue import OfflineQueue


class TestOfflineQueue:
    """Tests for OfflineQueue."""

    @pytest.fixture
    def queue(self, tmp_path):
        """Create a test queue with temporary database."""
        db_path = tmp_path / "test_queue.db"
        return OfflineQueue(db_path=db_path)

    @pytest.fixture
    def sample_events(self):
        """Create sample events for testing."""
        screen_event = ScreenActivityEvent(
            occurred_at=datetime.utcnow(),
            payload=ScreenActivityPayload(
                event_type="active_window_changed",
                window_title="test.py",
                application_name="Code",
                session_id="test-session",
            )
        )
        
        battery_event = BatteryEvent(
            occurred_at=datetime.utcnow(),
            payload=BatteryPayload(
                level=0.75,
                is_charging=True,
            )
        )
        
        return [screen_event, battery_event]

    def test_queue_initial_size(self, queue):
        """Test that new queue has size 0."""
        assert queue.size() == 0
        assert queue.total_count() == 0

    def test_enqueue_single_event(self, queue, sample_events):
        """Test enqueueing a single event."""
        batch_id = uuid4()
        event = sample_events[0]
        
        queue_id = queue.enqueue(event, batch_id, sequence=1)
        
        assert queue_id is not None
        assert queue.size() == 1
        assert queue.total_count() == 1

    def test_enqueue_batch(self, queue, sample_events):
        """Test enqueueing multiple events."""
        batch_id = uuid4()
        
        queue_ids = queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        
        assert len(queue_ids) == 2
        assert queue.size() == 2
        assert queue.total_count() == 2

    def test_flush_returns_events(self, queue, sample_events):
        """Test that flush returns enqueued events."""
        batch_id = uuid4()
        queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        
        events, new_batch_id = queue.flush()
        
        assert len(events) == 2
        assert new_batch_id is not None
        # Events should be marked as flushed
        assert queue.size() == 0

    def test_flush_preserves_event_order(self, queue, sample_events):
        """Test that flush preserves sequence order."""
        batch_id = uuid4()
        queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        
        events, _ = queue.flush()
        
        # Check sequence order
        sequences = [seq for _, _, seq in events]
        assert sequences == [1, 2] or sequences == sorted(sequences)

    def test_stats(self, queue, sample_events):
        """Test queue statistics."""
        batch_id = uuid4()
        queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        
        stats = queue.stats()
        
        assert stats["total_events"] == 2
        assert stats["pending_events"] == 2
        assert stats["flushed_events"] == 0

    def test_stats_after_flush(self, queue, sample_events):
        """Test stats after flushing."""
        batch_id = uuid4()
        queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        
        queue.flush()
        
        stats = queue.stats()
        
        assert stats["total_events"] == 2
        assert stats["pending_events"] == 0
        assert stats["flushed_events"] == 2

    def test_delete_old_flushed_events(self, queue, sample_events):
        """Test deleting old flushed events."""
        batch_id = uuid4()
        queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        queue.flush()
        
        # Delete events older than 1 day from now - recent flush should be deleted
        # since flushed_at (now) < before_date (now + 1 day)
        # Actually this will delete them because flushed_at < before_date
        deleted = queue.delete_flushed(before_date=datetime.utcnow() + timedelta(days=1))
        assert deleted == 2
        
        # After previous deletion, queue should be empty
        assert queue.total_count() == 0

    def test_mark_flushed_specific_events(self, queue, sample_events):
        """Test marking specific events as flushed."""
        batch_id = uuid4()
        queue_ids = queue.enqueue_batch(sample_events, batch_id, start_sequence=1)
        
        # Mark only first event as flushed
        queue.mark_flushed([queue_ids[0]])
        
        stats = queue.stats()
        assert stats["pending_events"] == 1
        assert stats["flushed_events"] == 1

    def test_multiple_batches(self, queue, sample_events):
        """Test handling multiple batches."""
        batch1 = uuid4()
        batch2 = uuid4()
        
        queue.enqueue_batch(sample_events, batch1, start_sequence=1)
        queue.enqueue_batch(sample_events, batch2, start_sequence=3)
        
        assert queue.size() == 4
        assert queue.total_count() == 4

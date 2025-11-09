"""Job model and data structures"""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
import json


class JobState:
    """Job state constants"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


@dataclass
class Job:
    """Job data structure"""
    id: str
    command: str
    state: str = JobState.PENDING
    attempts: int = 0
    max_retries: int = 3
    created_at: str = None
    updated_at: str = None
    next_retry_at: Optional[str] = None
    error_message: Optional[str] = None

    def __post_init__(self):
        """Initialize timestamps if not provided"""
        now = datetime.utcnow().isoformat() + "Z"
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now

    def to_dict(self) -> dict:
        """Convert job to dictionary"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert job to JSON string"""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> 'Job':
        """Create job from dictionary"""
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> 'Job':
        """Create job from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def can_retry(self) -> bool:
        """Check if job can be retried"""
        return self.attempts < self.max_retries and self.state == JobState.FAILED

    def should_move_to_dlq(self) -> bool:
        """Check if job should be moved to DLQ"""
        return self.attempts >= self.max_retries and self.state == JobState.FAILED

    def get_next_retry_delay(self) -> Optional[int]:
        """Get the delay in seconds until the next retry"""
        if not self.next_retry_at or self.state != JobState.FAILED:
            return None
        
        try:
            next_retry = datetime.fromisoformat(self.next_retry_at.replace('Z', '+00:00'))
            now = datetime.utcnow()
            delay = (next_retry - now).total_seconds()
            return max(0, int(delay))
        except (ValueError, TypeError):
            return None


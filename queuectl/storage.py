"""Persistent storage layer using SQLite"""

import sqlite3
import json
import os
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from queuectl.models import Job, JobState


class Storage:
    """SQLite-based job storage"""

    def __init__(self, db_path: str = None):
        """Initialize storage"""
        if db_path is None:
            home = Path.home()
            db_dir = home / ".queuectl"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "jobs.db")
        
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_retry_at TEXT,
                error_message TEXT
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_state ON jobs(state)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_next_retry ON jobs(next_retry_at)
        """)
        
        conn.commit()
        conn.close()

    def _job_from_row(self, row: tuple) -> Job:
        """Convert database row to Job object"""
        return Job(
            id=row[0],
            command=row[1],
            state=row[2],
            attempts=row[3],
            max_retries=row[4],
            created_at=row[5],
            updated_at=row[6],
            next_retry_at=row[7],
            error_message=row[8]
        )

    def add_job(self, job: Job) -> bool:
        """Add a new job"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO jobs (id, command, state, attempts, max_retries,
                                created_at, updated_at, next_retry_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.id, job.command, job.state, job.attempts, job.max_retries,
                job.created_at, job.updated_at, job.next_retry_at, job.error_message
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Job ID already exists
            return False
        finally:
            conn.close()

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return self._job_from_row(row)
        return None

    def update_job(self, job: Job) -> bool:
        """Update existing job"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        job.updated_at = datetime.utcnow().isoformat() + "Z"
        
        cursor.execute("""
            UPDATE jobs SET
                command = ?, state = ?, attempts = ?, max_retries = ?,
                updated_at = ?, next_retry_at = ?, error_message = ?
            WHERE id = ?
        """, (
            job.command, job.state, job.attempts, job.max_retries,
            job.updated_at, job.next_retry_at, job.error_message, job.id
        ))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return success

    def get_jobs_by_state(self, state: str) -> List[Job]:
        """Get all jobs with a specific state"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM jobs WHERE state = ? ORDER BY created_at", (state,))
        rows = cursor.fetchall()
        conn.close()
        
        return [self._job_from_row(row) for row in rows]

    def get_pending_jobs(self, limit: int = 1) -> List[Job]:
        """Get pending jobs that are ready to be processed"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get pending jobs (never processed, so next_retry_at should be NULL)
        cursor.execute("""
            SELECT * FROM jobs 
            WHERE state = ? AND next_retry_at IS NULL
            ORDER BY created_at
            LIMIT ?
        """, (JobState.PENDING, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._job_from_row(row) for row in rows]

    def get_failed_jobs_ready_for_retry(self, limit: int = 1) -> List[Job]:
        """Get failed jobs that are ready for retry"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat() + "Z"
        
        cursor.execute("""
            SELECT * FROM jobs 
            WHERE state = ? AND next_retry_at <= ? AND attempts < max_retries
            ORDER BY created_at
            LIMIT ?
        """, (JobState.FAILED, now, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._job_from_row(row) for row in rows]

    def get_all_jobs(self) -> List[Job]:
        """Get all jobs"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM jobs ORDER BY created_at")
        rows = cursor.fetchall()
        conn.close()
        
        return [self._job_from_row(row) for row in rows]

    def get_stats(self) -> dict:
        """Get job statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        stats = {}
        for state in [JobState.PENDING, JobState.PROCESSING, JobState.COMPLETED, 
                     JobState.FAILED, JobState.DEAD]:
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE state = ?", (state,))
            stats[state] = cursor.fetchone()[0]
        
        conn.close()
        return stats

    def lock_job(self, job_id: str) -> bool:
        """Try to lock a job for processing (prevent duplicate processing)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Use a transaction to atomically check and update
        cursor.execute("BEGIN IMMEDIATE")
        
        try:
            # Check if job is in a processable state
            now = datetime.utcnow().isoformat() + "Z"
            cursor.execute("""
                SELECT state, next_retry_at, attempts, max_retries FROM jobs WHERE id = ?
            """, (job_id,))
            row = cursor.fetchone()
            
            if not row:
                conn.rollback()
                conn.close()
                return False
            
            current_state, next_retry_at, attempts, max_retries = row
            
            # Only lock if:
            # - Pending (never processed)
            # - Failed and ready for retry (next_retry_at <= now and attempts < max_retries)
            if current_state == JobState.PENDING:
                # Pending jobs are always ready
                pass
            elif current_state == JobState.FAILED:
                # Failed jobs must be ready for retry
                if next_retry_at is None or next_retry_at > now or attempts >= max_retries:
                    conn.rollback()
                    conn.close()
                    return False
            else:
                # Not in a processable state
                conn.rollback()
                conn.close()
                return False
            
            # Update to processing state
            cursor.execute("""
                UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?
            """, (JobState.PROCESSING, now, job_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception:
            conn.rollback()
            conn.close()
            return False


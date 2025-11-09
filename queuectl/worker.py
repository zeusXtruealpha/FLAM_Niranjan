"""Worker manager for processing jobs"""

import subprocess
import signal
import sys
import time
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from queuectl.storage import Storage
from queuectl.models import Job, JobState
from queuectl.config import Config


class WorkerManager:
    """Manages worker processes"""
    
    def __init__(self, storage: Storage, config: Config):
        """Initialize worker manager"""
        self.storage = storage
        self.config = config
        self.workers = []
        self.running = False
        self.pid_file = Path.home() / ".queuectl" / "workers.pid"
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.stop()

    def start_workers(self, count: int = None):
        """Start worker processes"""
        if count is None:
            count = self.config.get("worker_count", 1)
        
        if self.running:
            print(f"Workers are already running. Use 'queuectl worker stop' to stop them first.")
            return
        
        self.running = True
        self.workers = []
        
        # Save PID file
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.pid_file, 'w') as f:
            f.write(str(os.getpid()))
        
        print(f"Starting {count} worker(s)...")
        
        for i in range(count):
            worker = Worker(self.storage, self.config, worker_id=i+1)
            self.workers.append(worker)
            worker.start()
        
        print(f"Started {count} worker(s). PID: {os.getpid()}")
        print("Workers are processing jobs. Press Ctrl+C to stop gracefully.")
        
        # Keep main process alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop_workers(self):
        """Stop all worker processes gracefully"""
        if not self.running:
            # Try to stop workers from PID file
            if self.pid_file.exists():
                try:
                    with open(self.pid_file, 'r') as f:
                        pid = int(f.read().strip())
                    
                    # Check if process is still running
                    try:
                        os.kill(pid, signal.SIGTERM)
                        print(f"Sent stop signal to worker process {pid}")
                        # Wait a bit for graceful shutdown
                        time.sleep(2)
                    except ProcessLookupError:
                        print("Worker process not found")
                    
                    self.pid_file.unlink()
                except (ValueError, IOError) as e:
                    print(f"Error stopping workers: {e}")
            else:
                print("No workers are running")
            return
        
        print("Stopping workers gracefully...")
        self.running = False
        
        # Wait for workers to finish current jobs
        for worker in self.workers:
            worker.stop()
        
        # Wait for workers to complete
        for worker in self.workers:
            worker.join(timeout=10)
        
        # Remove PID file
        if self.pid_file.exists():
            self.pid_file.unlink()
        
        print("All workers stopped")

    def stop(self):
        """Stop workers (alias for stop_workers)"""
        self.stop_workers()

    def is_running(self) -> bool:
        """Check if workers are running"""
        if self.pid_file.exists():
            try:
                with open(self.pid_file, 'r') as f:
                    pid = int(f.read().strip())
                # Check if process exists
                os.kill(pid, 0)
                return True
            except (ValueError, IOError, ProcessLookupError):
                return False
        return False


class Worker:
    """Individual worker process"""
    
    def __init__(self, storage: Storage, config: Config, worker_id: int = 1):
        """Initialize worker"""
        self.storage = storage
        self.config = config
        self.worker_id = worker_id
        self.running = False
        self.current_job: Optional[Job] = None
        self.process: Optional[subprocess.Popen] = None

    def start(self):
        """Start worker in a separate thread"""
        import threading
        self.running = True
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        """Main worker loop"""
        while self.running:
            try:
                # Try to get a job
                job = self._get_next_job()
                
                if job:
                    self._process_job(job)
                else:
                    # No jobs available, wait a bit
                    time.sleep(1)
            except Exception as e:
                print(f"Worker {self.worker_id} error: {e}", file=sys.stderr)
                time.sleep(1)

    def _get_next_job(self) -> Optional[Job]:
        """Get next job to process (with locking)"""
        # First try pending jobs
        jobs = self.storage.get_pending_jobs(limit=10)
        
        for job in jobs:
            if self.storage.lock_job(job.id):
                # Successfully locked, refresh from DB
                return self.storage.get_job(job.id)
        
        # Then try failed jobs ready for retry
        jobs = self.storage.get_failed_jobs_ready_for_retry(limit=10)
        
        for job in jobs:
            if self.storage.lock_job(job.id):
                # Successfully locked, refresh from DB
                return self.storage.get_job(job.id)
        
        # Finally, handle failed jobs that have exhausted retries and should move to DLQ
        self._process_exhausted_retry_jobs()
        
        return None

    def _process_exhausted_retry_jobs(self):
        """Process failed jobs that have exhausted retries and should move to DLQ"""
        failed_jobs = self.storage.get_jobs_by_state(JobState.FAILED)
        
        for job in failed_jobs:
            if job.should_move_to_dlq():
                # Move to DLQ
                job.state = JobState.DEAD
                job.next_retry_at = None
                job.updated_at = datetime.utcnow().isoformat() + "Z"
                self.storage.update_job(job)
                print(f"Worker {self.worker_id}: Job {job.id} moved to DLQ after exhausting {job.max_retries} retries")

    def _process_job(self, job: Job):
        """Process a single job"""
        self.current_job = job
        print(f"Worker {self.worker_id}: Processing job {job.id} - {job.command}")
        
        try:
            # Execute command
            result = self._execute_command(job.command)
            
            if result['success']:
                # Job completed successfully
                job.state = JobState.COMPLETED
                job.error_message = None
                self.storage.update_job(job)
                print(f"Worker {self.worker_id}: Job {job.id} completed successfully")
            else:
                # Job failed
                job.attempts += 1
                job.error_message = result.get('error', 'Command failed')
                
                if job.should_move_to_dlq():
                    # Move to DLQ
                    job.state = JobState.DEAD
                    job.next_retry_at = None
                    self.storage.update_job(job)
                    print(f"Worker {self.worker_id}: Job {job.id} moved to DLQ after {job.attempts} attempts")
                else:
                    # Schedule retry with exponential backoff
                    backoff_base = self.config.get("backoff_base", 2)
                    delay_seconds = backoff_base ** job.attempts
                    next_retry = datetime.utcnow() + timedelta(seconds=delay_seconds)
                    job.next_retry_at = next_retry.isoformat() + "Z"
                    job.state = JobState.FAILED
                    self.storage.update_job(job)
                    print(f"Worker {self.worker_id}: Job {job.id} failed, will retry in {delay_seconds}s (attempt {job.attempts}/{job.max_retries})")
        
        except Exception as e:
            # Unexpected error
            job.attempts += 1
            job.error_message = str(e)
            
            if job.should_move_to_dlq():
                job.state = JobState.DEAD
                job.next_retry_at = None
            else:
                backoff_base = self.config.get("backoff_base", 2)
                delay_seconds = backoff_base ** job.attempts
                next_retry = datetime.utcnow() + timedelta(seconds=delay_seconds)
                job.next_retry_at = next_retry.isoformat() + "Z"
                job.state = JobState.FAILED
            
            self.storage.update_job(job)
            print(f"Worker {self.worker_id}: Error processing job {job.id}: {e}", file=sys.stderr)
        
        finally:
            self.current_job = None

    def _execute_command(self, command: str) -> dict:
        """Execute a shell command"""
        try:
            # Use shell=True to support complex commands
            # timeout can be added as a bonus feature
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self.process = process
            stdout, stderr = process.communicate()
            exit_code = process.returncode
            
            self.process = None
            
            if exit_code == 0:
                return {
                    'success': True,
                    'stdout': stdout,
                    'stderr': stderr
                }
            else:
                error_msg = stderr.strip() if stderr else f"Command exited with code {exit_code}"
                return {
                    'success': False,
                    'error': error_msg,
                    'stdout': stdout,
                    'stderr': stderr,
                    'exit_code': exit_code
                }
        
        except FileNotFoundError:
            return {
                'success': False,
                'error': f"Command not found: {command}"
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def stop(self):
        """Stop worker gracefully"""
        self.running = False
        
        # If processing a job, wait for it to complete
        if self.current_job and self.process:
            try:
                # Wait up to 30 seconds for job to complete
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # Force kill if timeout
                self.process.kill()
                self.process.wait()

    def join(self, timeout: Optional[float] = None):
        """Wait for worker to finish (thread join)"""
        # This is handled by the thread, but we can add a small delay
        time.sleep(0.1)


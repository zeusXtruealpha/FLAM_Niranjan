"""CLI interface for queuectl"""

import click
import json
import sys
from queuectl.storage import Storage
from queuectl.models import Job, JobState
from queuectl.config import Config
from queuectl.worker import WorkerManager
from queuectl.dashboard import run_dashboard


@click.group()
@click.version_option(version="1.0.0")
def main():
    """QueueCTL - CLI-based background job queue system"""
    pass


@main.command()
@click.argument('job_data', type=str)
def enqueue(job_data):
    """Enqueue a new job to the queue
    
    JOB_DATA: JSON string with job details (id, command, max_retries optional)
    
    Example: queuectl enqueue '{"id":"job1","command":"sleep 2"}'
    """
    try:
        data = json.loads(job_data)
        
        # Validate required fields
        if 'id' not in data or 'command' not in data:
            click.echo("Error: Job must have 'id' and 'command' fields", err=True)
            sys.exit(1)
        
        # Create job
        job = Job(
            id=data['id'],
            command=data['command'],
            max_retries=data.get('max_retries', 3)
        )
        
        # Add to storage
        storage = Storage()
        if storage.add_job(job):
            click.echo(f"Job '{job.id}' enqueued successfully")
            click.echo(f"Command: {job.command}")
            click.echo(f"State: {job.state}")
        else:
            click.echo(f"Error: Job with ID '{job.id}' already exists", err=True)
            sys.exit(1)
    
    except json.JSONDecodeError:
        click.echo("Error: Invalid JSON format", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.group()
def worker():
    """Worker management commands"""
    pass


@worker.command('start')
@click.option('--count', '-c', type=int, default=None, help='Number of workers to start')
def worker_start(count):
    """Start one or more worker processes"""
    storage = Storage()
    config = Config()
    
    if count is not None:
        config.set("worker_count", count)
    
    manager = WorkerManager(storage, config)
    manager.start_workers(count)


@worker.command('stop')
def worker_stop():
    """Stop all running workers gracefully"""
    storage = Storage()
    config = Config()
    manager = WorkerManager(storage, config)
    manager.stop_workers()


@main.command()
def status():
    """Show summary of all job states and active workers"""
    storage = Storage()
    config = Config()
    manager = WorkerManager(storage, config)
    
    stats = storage.get_stats()
    
    click.echo("=" * 50)
    click.echo("QueueCTL Status")
    click.echo("=" * 50)
    click.echo()
    click.echo("Job Statistics:")
    click.echo(f"  Pending:   {stats.get(JobState.PENDING, 0)}")
    click.echo(f"  Processing: {stats.get(JobState.PROCESSING, 0)}")
    click.echo(f"  Completed: {stats.get(JobState.COMPLETED, 0)}")
    click.echo(f"  Failed:    {stats.get(JobState.FAILED, 0)}")
    click.echo(f"  Dead (DLQ): {stats.get(JobState.DEAD, 0)}")
    click.echo()
    
    total = sum(stats.values())
    click.echo(f"Total Jobs: {total}")
    click.echo()
    
    # Check worker status
    if manager.is_running():
        click.echo("Workers: Running")
    else:
        click.echo("Workers: Stopped")
    click.echo()


@main.command('list')
@click.option('--state', '-s', type=click.Choice(['pending', 'processing', 'completed', 'failed', 'dead']), 
              help='Filter jobs by state')
@click.option('--format', '-f', type=click.Choice(['table', 'json']), default='table',
              help='Output format')
def list_jobs(state, format):
    """List jobs, optionally filtered by state"""
    storage = Storage()
    
    if state:
        jobs = storage.get_jobs_by_state(state)
    else:
        jobs = storage.get_all_jobs()
    
    if format == 'json':
        jobs_data = [job.to_dict() for job in jobs]
        click.echo(json.dumps(jobs_data, indent=2))
    else:
        if not jobs:
            click.echo("No jobs found")
            return
        
        click.echo(f"{'ID':<20} {'State':<12} {'Command':<30} {'Attempts':<10} {'Created At':<20}")
        click.echo("-" * 100)
        
        for job in jobs:
            created = job.created_at[:19] if job.created_at else "N/A"
            click.echo(f"{job.id:<20} {job.state:<12} {job.command[:28]:<30} {job.attempts}/{job.max_retries:<10} {created:<20}")


@main.group()
def dlq():
    """Dead Letter Queue commands"""
    pass


@dlq.command('list')
@click.option('--format', '-f', type=click.Choice(['table', 'json']), default='table',
              help='Output format')
def dlq_list(format):
    """List all jobs in the Dead Letter Queue"""
    storage = Storage()
    jobs = storage.get_jobs_by_state(JobState.DEAD)
    
    if format == 'json':
        jobs_data = [job.to_dict() for job in jobs]
        click.echo(json.dumps(jobs_data, indent=2))
    else:
        if not jobs:
            click.echo("Dead Letter Queue is empty")
            return
        
        click.echo(f"Dead Letter Queue ({len(jobs)} jobs):")
        click.echo()
        click.echo(f"{'ID':<20} {'Command':<40} {'Attempts':<15} {'Error':<30}")
        click.echo("-" * 110)
        
        for job in jobs:
            error = (job.error_message or "N/A")[:28] if job.error_message else "N/A"
            click.echo(f"{job.id:<20} {job.command[:38]:<40} {job.attempts}/{job.max_retries:<15} {error:<30}")


@dlq.command('retry')
@click.argument('job_id', type=str)
def dlq_retry(job_id):
    """Retry a job from the Dead Letter Queue"""
    storage = Storage()
    job = storage.get_job(job_id)
    
    if not job:
        click.echo(f"Error: Job '{job_id}' not found", err=True)
        sys.exit(1)
    
    if job.state != JobState.DEAD:
        click.echo(f"Error: Job '{job_id}' is not in the Dead Letter Queue (current state: {job.state})", err=True)
        sys.exit(1)
    
    # Reset job to pending state
    job.state = JobState.PENDING
    job.attempts = 0
    job.error_message = None
    job.next_retry_at = None
    
    if storage.update_job(job):
        click.echo(f"Job '{job_id}' moved from DLQ to pending queue")
        click.echo(f"Command: {job.command}")
    else:
        click.echo(f"Error: Failed to update job '{job_id}'", err=True)
        sys.exit(1)


@main.group()
def config():
    """Configuration management commands"""
    pass


@config.command('get')
@click.argument('key', type=str, required=False)
def config_get(key):
    """Get configuration value(s)"""
    config = Config()
    
    if key:
        # Convert hyphen to underscore for internal keys
        internal_key = key.replace('-', '_')
        value = config.get(internal_key)
        if value is None:
            click.echo(f"Error: Unknown config key '{key}'", err=True)
            sys.exit(1)
        click.echo(f"{key} = {value}")
    else:
        # Show all config
        all_config = config.get_all()
        click.echo("Configuration:")
        for k, v in all_config.items():
            # Convert underscore to hyphen for display
            display_key = k.replace('_', '-')
            click.echo(f"  {display_key} = {v}")


@config.command('set')
@click.argument('key', type=str)
@click.argument('value', type=str)
def config_set(key, value):
    """Set a configuration value
    
    Available keys:
      max-retries: Maximum retry attempts (integer)
      backoff-base: Base for exponential backoff (float)
      worker-count: Default number of workers (integer)
    """
    config = Config()
    
    try:
        # Convert hyphen to underscore for internal keys
        internal_key = key.replace('-', '_')
        config.set(internal_key, value)
        click.echo(f"Set {key} = {value}")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command('dashboard')
@click.option('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
@click.option('--port', default=5000, type=int, help='Port to bind to (default: 5000)')
@click.option('--debug', is_flag=True, help='Enable debug mode')
def dashboard(host, port, debug):
    """Start the web dashboard
    
    Opens a web interface to monitor jobs, workers, and system status.
    Access the dashboard at http://host:port
    """
    try:
        run_dashboard(host=host, port=port, debug=debug)
    except KeyboardInterrupt:
        click.echo("\nDashboard stopped")
    except Exception as e:
        click.echo(f"Error starting dashboard: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()


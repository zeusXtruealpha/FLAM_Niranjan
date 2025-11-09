# QueueCTL - CLI Background Job Queue System

`queuectl` is a CLI-based background job queue system built in Python. It is designed to be a minimal, production-grade service that manages background jobs, supports concurrent workers, handles automatic retries with exponential backoff, and includes a Dead Letter Queue (DLQ) for failed jobs.

This project was built to fulfill the "Backend Developer Internship Assignment" requirements.

## 🚀 Features

* **Persistent Storage**: Job data persists across restarts using a **SQLite** database.
* **Concurrent Workers**: Run multiple workers in parallel to process jobs concurrently.
* **Automatic Retries**: Failed jobs are automatically retried with an **exponential backoff** delay.
* **Dead Letter Queue (DLQ)**: Jobs that fail all retry attempts are moved to the DLQ for manual inspection.
* **Configuration Management**: CLI-based commands to view and set configuration like retry counts.
* **Web Dashboard**: A (bonus) built-in web dashboard provides real-time monitoring of job states.
* **Clean CLI Interface**: A full-featured CLI for enqueuing, listing, and managing jobs.

---

## ⚙️ How It Works: "What Happens"

This system uses a central database (SQLite) as the single source of truth, allowing multiple processes to coordinate.

1.  **Enqueueing (The "To-Do" List)**
    * When you run `queuectl enqueue ...`, you are creating a new row in the `jobs.db` database.
    * This new job is given the state **`pending`**. It's now in the "to-do" list, waiting for a worker.

2.  **Working (Doing the Job)**
    * You start one or more workers using `queuectl worker start`.
    * Each worker is a separate process that constantly polls the database, looking for jobs.
    * When a worker finds a `pending` job, it "locks" it by immediately changing its state to **`processing`**. This prevents any other worker from grabbing the same job.
    * The worker then executes the job's `command` (e.g., `timeout /t 10`) using a subprocess.

3.  **Finishing (The Outcome)**
    * **If the command succeeds** (exits with code 0): The worker updates the job's state to **`completed`**. The job is now finished.
    * **If the command fails** (exits with a non-zero code, like `nonexistent-command`): The worker updates the state to **`failed`** and calculates the next retry time using exponential backoff (e.g., $2^{\text{attempts}}$ seconds).
    * The job will remain in the `failed` state until its `next_retry_at` time is in the past. Then, a worker is allowed to pick it up, lock it (set to `processing`), and try again.

4.  **Permanent Failure (The "Too-Hard" Pile)**
    * If a job fails more than its `max_retries` (e.g., 3 times), the worker will give up.
    * Instead of retrying, it sets the job's state to **`dead`**. This moves the job into the Dead Letter Queue (DLQ), removing it from the main workflow so it can be manually reviewed.

---

## 🔧 Setup and Installation

1.  **Clone Repository:**
    ```bash
    git clone <your-repository-url>
    cd <your-project-directory>
    ```

2.  **Install Dependencies:**
    (A virtual environment is recommended)
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run:**
    All commands are run from your project's root directory using the `python -m queuectl.cli` module.

---

## 💻 CLI Command Reference

All examples are for the **Windows Command Prompt (cmd.exe)**.

### 1. Enqueue a Job

Adds a new job to the queue. The JSON must be wrapped in `"` and all internal quotes must be escaped with `\`.

**Syntax:**
`python -m queuectl.cli enqueue "{\"id\":\"<job-id>\", ...}"`

**Examples:**

* **Enqueue a simple job:**
    ```cmd
    python -m queuectl.cli enqueue "{\"id\":\"job-1\",\"command\":\"echo Hello World\"}"
    ```

* **Enqueue a long-running job (sleeps for 10 sec):**
    ```cmd
    python -m queuectl.cli enqueue "{\"id\":\"long-job-1\",\"command\":\"timeout /t 10 /nobreak\"}"
    ```

* **Enqueue a job that will fail:**
    ```cmd
    python -m queuectl.cli enqueue "{\"id\":\"fail-job-1\",\"command\":\"nonexistent-command\"}"
    ```

* **Enqueue a job with custom max retries:**
    ```cmd
    python -m queuectl.cli enqueue "{\"id\":\"custom-retry-job\",\"command\":\"echo Test\",\"max_retries\":5}"
    ```

### 2. Manage Workers

Workers are the processes that run the jobs. You must run this in its own terminal.

* **Start 3 workers:**
    ```cmd
    python -m queuectl.cli worker start --count 3
    ```

* **Start 1 worker:**
    ```cmd
    python -m queuectl.cli worker start --count 1
    ```
    *(This command blocks the terminal. Press `Ctrl+C` to stop.)*

* **Stop workers gracefully:**
    (Run this from a *different* terminal)
    ```cmd
    python -m queuectl.cli worker stop
    ```

### 3. Check Status

Get a quick summary of all job states.

```cmd
python -m queuectl.cli status
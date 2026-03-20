# PrioMon Experiments — Simulation & Research Mode

This directory is the **research arm** of PrioMon. It runs a tightly controlled, automated simulation to benchmark the gossip protocol — measuring convergence time, bandwidth savings from priority filtering, and resilience under node failures.

The experiment mode uses a **central orchestrator** (`monitoring.py`) that lives outside the nodes. It spawns them, configures them, observes them, and records everything into a SQLite database for later analysis.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  monitoring.py (host)                │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Experiment│  │  Run     │  │  Flask Server    │  │
│  │ state    │  │  state   │  │  :4000           │  │
│  └──────────┘  └──────────┘  └──────────────────┘  │
│        │              │              │               │
│  spawn Docker containers via Docker SDK              │
└─────────────────────────────────────────────────────┘
          │           │           │
    ┌─────▼──┐  ┌─────▼──┐  ┌────▼───┐
    │ node1  │  │ node2  │  │ node3  │
    │  :5001 │  │  :5002 │  │  :5003 │
    └────────┘  └────────┘  └────────┘
       gossip ←──────────────────→
       data reports → monitoring.py → PrioMonDB.db
```

**Key difference from standalone mode:** The orchestrator *tells* each node its full peer list, gossip rate, and target count at startup via a `POST /start_node` call. Nodes do not discover each other — they are pre-configured.

---

## Files in This Directory

### `monitoring.py` — The Orchestrator

This is the brain of the simulation. It's a Flask server that does two jobs at once:

**1. Orchestration (before/during the experiment)**
- Reads `config.ini` to build an `Experiment` object with all parameter ranges
- Calls `spawn_multiple_nodes()` which uses the Docker SDK (`docker.DockerClient`) to spin up `N` containers from the pre-built `priomonv1` image
- Each container gets a free port via `get_free_port()` (OS-assigned when a socket binds to port 0)
- After containers are up, calls `start_run()` which POSTs to `/start_node` on each container, handing it: `node_list`, `target_count`, `gossip_rate`, `monitoring_address`, etc.
- Uses `concurrent.futures.ThreadPoolExecutor` for parallel starts

**2. Monitoring (receiving data from nodes)**
- **`/receive_node_data`** — main data collection endpoint. Each node POSTs its full gossip state here after every round. The server checks for convergence: if every node's state contains data about every other node, the run has converged.
- **`/receive_ic`** — convergence confirmation endpoint. Nodes ping this when they believe they've converged.
- **`/push_data_to_database`** — used in "push mode" where nodes periodically dump their accumulated data here.

**3. Batch DB Writer (performance trick)**
Instead of writing to SQLite on every incoming request (which would torch the disk), all inserts go into a `queue.Queue`. A dedicated background thread (`execute_queries_from_queue`) drains this queue in batches of 50, committing them together. This dramatically reduces disk write amplification.

**Convergence detection logic:**
```python
# convergence = every node has seen data from every other node
if len(data_entries_per_ip) < node_count:
    return False
for ip in data_entries_per_ip:
    if len(data_entries_per_ip[ip]) < node_count:
        return False
```
The run also auto-converges at round 80 as a safety cutoff.

**Query test phase:**
After convergence, the orchestrator optionally:
1. Stops a random percentage of nodes (`stop_node_percentage`) to simulate failures
2. Runs 100 quorum queries against the surviving nodes via `query_client.query()`
3. Records success/failure + latency per query to the database

---

### `connector_db.py` — Database Layer

Manages the `PrioMonDB.db` SQLite database that records everything about each experiment run.

**Database tables:**
- `experiments` — one row per experiment (timestamp, param ranges)
- `runs` — one row per run (node count, gossip rate, target count)
- `converged_runs` — convergence time, message count, convergence round
- `round_of_node` — per-round gossip stats per node (nd, fd, rm, bytes_of_data)
- `round_metrics_stats` — how many metrics were sent vs. filtered per round
- `metric_transmissions` — per-metric-type transmission log with actual values

**SSD Safety (WAL Mode):**
The DB is opened with:
```python
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```
WAL (Write-Ahead Logging) lets reads and writes happen concurrently, and `NORMAL` sync means the OS doesn't `fsync()` on every commit — critical when you're writing ~50 rows/second during a simulation. Without this, a long experiment would hammer SSD write cycles unnecessarily.

---

### `config.ini` — Experiment Parameters

```ini
[PriomonParam]
node_range = [3]          # how many nodes to test (can be a list: [3, 5, 10])
gossip_rate_range = [0.01] # seconds between gossip rounds
target_count_range = [2]   # how many peers each node gossips to each round
runs = 1                   # how many repetitions per parameter combo
continue_after_convergence = 0  # 0=stop at convergence, 1=keep going to round 80
push_mode = 0              # 0=nodes push per-round, 1=nodes batch-push periodically
client_port = 4000         # port the monitoring Flask server listens on

[system_setting]
query_logic = 1            # 1=run query tests after convergence
failure_rate = 0.0         # fraction of nodes to kill before query test (e.g. 0.3 = 30%)
docker_ip = 127.0.0.1      # host IP Docker containers can reach back to monitoring.py
is_send_data_back = 1      # 1=nodes POST their state to monitoring.py each round

[database]
db_file = PrioMonDB.db
```

**Running a multi-parameter sweep:** set list values to iterate combinations:
```ini
node_range = [3, 5, 10]
gossip_rate_range = [0.01, 0.1, 0.5]
target_count_range = [1, 2]
runs = 3
```
This generates `3 × 3 × 2 × 3 = 54` runs automatically.

---

### `plot.py` — Visualization

After an experiment completes, `plot.py` reads `PrioMonDB.db` and generates charts:

- **Bandwidth savings over time** — line graph: metrics sent vs. filtered per round. Shows how the priority engine kicks in after the first few rounds and starts dropping low-priority metrics.
- **Total bandwidth saved** — pie chart: overall % of metric transmissions that were skipped.
- **Transmissions by metric type** — bar chart: which metric (CPU/memory/network/storage) was sent how many times. Confirms that CPU (priority=1) is sent far more often than storage (priority=10).
- **Query success vs. failure rate** — scatter/line plot: as you increase `failure_rate`, does the quorum query still succeed? Shows the resilience curve.

Output images are saved as `.png` files in the `experiments/` directory.

---

## Step-by-Step Running Guide

### Prerequisites
- Python 3.9+
- Docker Desktop (or Docker Engine on Linux)
- ~2 GB of RAM for a 3-node simulation

### Step 1 — Build the node Docker image

The experiment needs a pre-built Docker image named `priomonv1`. This is what each simulated node runs.

```powershell
# from the repo root
docker-compose up --build -d
```

This uses `docker-compose.yml` which builds `src/app/Dockerfile` and tags it `priomonv1`. The `priomon-builder` service immediately exits after the image is built — the image is what we need.

Verify the image exists:
```powershell
docker images priomonv1
```

### Step 2 — Install orchestrator dependencies

The orchestrator itself runs on your host (not in Docker). It needs a few extra packages:

```powershell
pip install -r requirements.txt
```

Root `requirements.txt` includes `docker`, `joblib`, `flask`, `requests`, and `sqlite3` wrapper tools.

### Step 3 — Configure the experiment

Edit `experiments/config.ini` to set your parameters. Keep `node_range = [3]` for a first run — it's fast and produces clear charts.

### Step 4 — Start the monitoring server

```powershell
python experiments/monitoring.py
```

This starts the Flask server on port 4000. You'll see something like:
```
 * Running on http://0.0.0.0:4000
```

The server is now waiting for you to trigger an experiment.

### Step 5 — Trigger the experiment

In a separate terminal (or browser):
```powershell
curl http://localhost:4000/start
```

What happens next (automated):
1. Orchestrator creates `Experiment` and `Run` objects based on `config.ini`
2. Docker containers are spawned in parallel (`spawn_multiple_nodes`)
3. Orchestrator waits until all containers are healthy (`nodes_are_ready` polls Docker status)
4. `start_run` POSTs `/start_node` to each container with the full peer list + gossip config
5. Nodes start gossiping and reporting per-round data back to `/receive_node_data`
6. Orchestrator watches for convergence
7. If `query_logic=1`: stops a % of nodes, runs 100 queries, records results
8. `reset_run_sync` resets all nodes for the next run
9. Repeats for all parameter combinations
10. Returns `"OK - Experiment finished"` when done

Live progress is printed to the monitoring.py terminal.

### Step 6 — Visualize results

```powershell
python experiments/plot.py
```

Generates PNG charts in `experiments/`. Open them and look at:
- How quickly did the cluster converge? (convergence time vs. node count)
- What % of metric transmissions were filtered? (priority engine effectiveness)
- Did queries succeed even with 30% node failures? (resilience)

---

## What the Experiment Measures

| Metric | What it tells you |
|---|---|
| `convergence_time` | Time (seconds) from first gossip to full cluster agreement |
| `convergence_round` | Number of gossip rounds until convergence |
| `convergence_message_count` | Total messages exchanged to reach convergence |
| `nd` (new data / round) | How many new entries arrived at a node per round |
| `fd` (fresh data / round) | How many entries were updated (nd ⊆ fd) |
| `rm` (received messages / round) | Total inbound gossip messages per round |
| `metrics_sent` | Metric values included in gossip payloads |
| `metrics_filtered` | Metric values dropped by priority filter |
| `bytes_of_data` | Bytes of gossip payload received per round |
| Query `success` / `time_to_query` | Quorum query reliability under node failures |

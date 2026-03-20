# PrioMon: Priority-Based Distributed Monitoring

PrioMon is a high-performance distributed monitoring system that utilizes a gossip protocol to propagate system metrics (CPU, Memory, Network, Storage) across a cluster of nodes. It features a unique **priority-based bandwidth-saving mechanism** that intelligently filters metrics based on their importance and the magnitude of data changes.

##  Key Features

- **Gossip Protocol Propagation**: Efficient data dissemination without a single point of failure.
- **Priority Filtering**: Reduces bandwidth usage by up to 100x by prioritizing critical status updates and skipping redundant ones.
- **Node Resilience**: Automatic failure detection and handling of dead nodes.
- **SSD Optimized**: Uses SQLite WAL (Write-Ahead Logging) to protect your hardware during intensive high-frequency simulations.
- **Integrated Analytics**: Built-in plotting tools to visualize convergence time, success rates, and bandwidth savings.
- **Standalone Mode**: Run nodes independently on real machines — no orchestrator needed. Nodes discover each other via seed addresses.
- **Peer Persistence**: Nodes save their peer list to disk so they survive restarts without losing cluster membership.
- **Cluster Metrics API**: Clean JSON endpoints to query any node's view of the cluster (`/cluster/metrics`).
- **Quorum Queries**: Verify a node's data against multiple peers with consensus-based verification (`/cluster/query/<target>`).
- **Metric History**: Each node persists a rolling window of cluster metrics to local SQLite for historical queries (`/cluster/history`).

##  Project Structure

```text
PrioMon/
├── src/                    # Core Gossip Engine
│   ├── app/                # Dockerized node logic
│   │   ├── priomon.py      # Flask server (gossip + cluster API + standalone startup)
│   │   ├── node.py         # Node state, metric collection, priority filtering
│   │   ├── discovery.py    # Seed-based peer discovery + state persistence
│   │   ├── metric_store.py # Local SQLite metric history (rolling window)
│   │   ├── standalone.py   # CLI entry point for standalone mode
│   │   ├── node_config.yaml # Template config for standalone nodes
│   │   └── query.py        # Quorum-based query logic
│   └── query_client.py     # Client-side query bridge
├── experiments/            # Simulation Orchestration & Analysis
│   ├── monitoring.py       # Central experiment runner & monitoring server
│   ├── plot.py             # Analytics visualization tool
│   └── config.ini          # Simulation parameters
├── standalone_configs/     # Example configs for a 3-node standalone cluster
├── docker-compose.yml              # Orchestrated mode (monitoring.py spawns nodes)
├── docker-compose.standalone.yml   # Standalone mode (nodes self-organize)
└── requirements.txt        # Orchestrator dependencies (Python)
```

##  Quick Start

### Option A: Orchestrated Simulation (for benchmarking / research)

1.  **Prerequisites**: Python 3.9+, Docker & Docker Compose
2.  **Build the Node Image**:
    ```powershell
    docker-compose up --build -d
    ```
3.  **Install orchestrator deps & start**:
    ```powershell
    pip install -r requirements.txt
    python experiments/monitoring.py
    ```
4.  **Trigger the Experiment**: Open `http://localhost:4000/start`

### Option B: Standalone Cluster (for real-world use)

No orchestrator needed — each node runs independently and finds peers via seeds.

1.  **Build the image**:
    ```powershell
    docker-compose -f docker-compose.standalone.yml build
    ```
2.  **Start the cluster**:
    ```powershell
    docker-compose -f docker-compose.standalone.yml up
    ```
    Node 1 starts as the seed. Nodes 2 & 3 join by contacting it.

3.  **Check health**: `curl http://localhost:5001/health`
4.  **View cluster metrics**: `curl http://localhost:5001/cluster/metrics`
5.  **Query a specific node (verified)**: `curl http://localhost:5001/cluster/query/172.18.0.2:5000`
6.  **View metric history**: `curl http://localhost:5001/cluster/history?minutes=5`

### Running on bare metal (no Docker)

```bash
cd src/app
pip install -r requirements.txt
python standalone.py --config /path/to/your_config.yaml
```

### Visualizing Results (Orchestrated mode)
After the simulation finishes:
```powershell
python experiments/plot.py
```

##  Configuration

### Orchestrated mode
Tweak parameters in `experiments/config.ini`.

### Standalone mode
Copy `src/app/node_config.yaml` and customize:
- **seeds**: IP:port of nodes to contact on startup
- **metric_priorities**: how often each metric type gets sent (1=every round, 10=every 10th round)
- **metric_deltas**: minimum % change to force an early update
- **state_file**: where to persist the peer list across restarts
- **metric_history.snapshot_interval**: how often to save metrics to SQLite (seconds)
- **metric_history.retention_seconds**: how long to keep historical data

##  API Endpoints

### Cluster Operations
| Endpoint | Method | Description |
|---|---|---|
| `/cluster/metrics` | GET | Clean JSON of all nodes' current metrics |
| `/cluster/metrics/<ip:port>` | GET | Metrics for a specific node |
| `/cluster/query/<ip:port>` | GET | Quorum-verified query (consensus check across peers) |
| `/cluster/history` | GET | Historical metrics from local SQLite (`?node=...&minutes=10&limit=200`) |
| `/cluster/history/latest` | GET | Most recent persisted metrics for each node |

### Node Management
| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Liveness check (status, peer count, cycle) |
| `/peers` | GET | Returns the current peer list |
| `/join` | POST | Register a new peer `{"ip": "...", "port": "..."}` |
| `/metrics_priority_stats` | GET | Priority filtering statistics |

### Raw Gossip Data
| Endpoint | Method | Description |
|---|---|---|
| `/get_recent_data_from_node` | GET | Latest raw gossip snapshot |
| `/get_data_from_node` | GET | Full historical gossip data |
| `/metadata` | GET | Counter + digest per node (for quorum) |

## Example: Querying Cluster Metrics

```bash
# clean cluster view from node 1
curl http://localhost:5001/cluster/metrics | python -m json.tool

# response:
{
  "timestamp": 1710912345.123,
  "reporting_node": "172.18.0.2:5000",
  "total_nodes": 3,
  "cycle": 42,
  "nodes": {
    "172.18.0.2:5000": {
      "cpu": 23.5,
      "memory": 67.8,
      "network": 1234567890.0,
      "storage": 50000000000.0,
      "alive": true,
      "cycle": "42"
    },
    "172.18.0.3:5000": { ... },
    "172.18.0.4:5000": { ... }
  }
}
```

```bash
# quorum-verified query about a specific node
curl "http://localhost:5001/cluster/query/172.18.0.3:5000?quorum_size=2"

# response:
{
  "status": "verified",
  "target": "172.18.0.3:5000",
  "quorum_size": 2,
  "attempts": 1,
  "consensus": { "counter": "42", "digest": "abc123..." },
  "metrics": { "cpu": 15.2, "memory": 45.1, ... },
  "health": { "alive": true, "failure_count": 0 },
  "verified_by": ["172.18.0.2:5000", "172.18.0.4:5000"]
}
```
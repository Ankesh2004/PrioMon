# PrioMon — Priority-Based Distributed Monitoring

PrioMon is a distributed monitoring system built around a **gossip protocol** with a novel **priority-based bandwidth-saving engine**. It propagates system metrics (CPU, Memory, Network, Storage) across a cluster of nodes — without a central broker, without polling, and without wasting bandwidth on updates nobody cares about.

This repo contains two distinct systems that share the same gossip core:

| Mode | Location | Audience |
|---|---|---|
| **🧪 Experiment / Simulation** | `experiments/` | Researchers — benchmark convergence, bandwidth, resilience |
| **🌍 Real-World / Standalone** | `src/app/` | Engineers — deploy actual nodes on machines/containers |

---

## How the Core Works (Both Modes)

### Gossip Protocol

Every node runs a timed gossip loop. On each cycle, a node:
1. Reads its own system metrics (CPU, memory, network, storage via `psutil`)
2. Applies **priority filtering** to decide which metrics to include this round
3. Picks `N` random peers from its peer list
4. Does a **two-phase gossip exchange** with each peer:
   - Phase 1: Sends its own data + metadata (counter + digest) for all other nodes it knows about → receives back what the peer needs
   - Phase 2: Sends the requested data; receives the peer's updates

This is a hybrid **push-pull gossip** — more efficient than pure push because data flows in both directions per connection.

### Priority Filtering (The Key Innovation)

Metrics are not all equal. CPU changing by 0.1% is noise; a spike from 10% to 90% matters. PrioMon applies two complementary filters before a metric gets included in a gossip payload:

**1. Round-Based Priority (`METRIC_PRIORITIES`)**
```
cpu:     priority = 1   → sent every single round
memory:  priority = 5   → sent at most every 5 rounds
network: priority = 5   → sent at most every 5 rounds
storage: priority = 10  → sent at most every 10 rounds
```

**2. Delta Threshold (`METRIC_DELTAS`)**
Even if a metric isn't due by schedule, it gets sent immediately if it changes by more than:
```
cpu:     5%   change → immediate send
memory:  7%   change → immediate send
network: 15%  change → immediate send
storage: 10%  change → immediate send
```

If a metric is filtered out, it's **dropped entirely** from the gossip payload (not sent as `null`). The receiving node's merge logic preserves its last known value for that metric. This avoids false overwriting with stale data.

### Failure Detection (SWIM-style)

When a node can't reach a peer, it:
1. Adds itself to that peer's `failureList` in its local state
2. Gossips this suspicion to others → state spreads
3. If **3 or more distinct nodes** report the same peer as unreachable → it's declared **DEAD**
4. Dead nodes are removed from the active peer list but their tombstone stays in gossip data so the status propagates

---

## Project Structure

```text
PrioMon/
│
├── src/app/                          ← REAL-WORLD NODE (standalone mode)
│   ├── standalone.py                 # CLI entry point: python standalone.py --config ...
│   ├── priomon.py                    # Flask server — gossip API + cluster API + startup
│   ├── node.py                       # Node state machine, metric collection, priority engine
│   ├── discovery.py                  # Seed-based peer discovery + disk persistence
│   ├── metric_store.py               # Rolling SQLite history of cluster metrics
│   ├── tls_utils.py                  # mTLS config — cert loading, SSL context, session setup
│   ├── node_config.yaml              # Template config for a standalone node
│   ├── singleton.py                  # Singleton pattern for Node instance
│   ├── utility.py                    # mk_digest() — SHA-256 gossip digest
│   ├── query.py                      # Quorum query logic
│   └── Dockerfile                    # Container image for a node
│
├── experiments/                      ← SIMULATION / RESEARCH MODE
│   ├── monitoring.py                 # Experiment orchestrator + monitoring Flask server
│   ├── connector_db.py               # Database layer for experiment data
│   ├── plot.py                       # Matplotlib analytics visualizations
│   ├── config.ini                    # Experiment parameters
│   └── README.md                     # Detailed experiment guide (this dir)
│
├── standalone_configs/               ← Example configs for a 2-node standalone cluster
│   ├── node1.yaml                    # Seed node config (no seeds, IS the seed)
│   └── node2.yaml                    # Joiner node config (seeds: [node1:5000])
│
├── scripts/
│   └── generate_certs.py             # One-shot mTLS certificate generator
│
├── certs/                            # Pre-generated certs for the 2-node example
│   ├── ca.crt / ca.key
│   ├── node1.crt / node1.key
│   └── node2.crt / node2.key
│
├── docker-compose.yml                # Orchestrated experiment mode (builds priomonv1 image)
├── docker-compose.standalone.yml     # Standalone 2-node cluster with mTLS
├── requirements.txt                  # Python deps for the experiment orchestrator
└── notes.md                          # Real-world roadmap and future plans
```

---

## 🧪 Mode 1: Experiment / Simulation

> **→ Full guide: [`experiments/README.md`](experiments/README.md)**

This mode runs a **controlled, automated simulation** where a central `monitoring.py` script:
- Spawns Docker containers as nodes using the Docker SDK
- Feeds each node its complete peer list and parameters
- Collects per-round gossip data from every node
- Saves everything to `PrioMonDB.db` for later analysis

**Quick start:**
```powershell
# 1. Build the node Docker image
docker-compose up --build -d

# 2. Install orchestrator dependencies
pip install -r requirements.txt

# 3. Start the monitoring server
python experiments/monitoring.py

# 4. Trigger the experiment via HTTP
curl http://localhost:4000/start

# 5. After it finishes, visualize results
python experiments/plot.py
```

---

## 🌍 Mode 2: Real-World / Standalone

> **→ Full guide: [`src/app/README.md`](src/app/README.md)**

This mode runs **actual autonomous nodes** that discover each other, persist their state, encrypt their gossip with mTLS, and expose clean observability APIs. No orchestrator. No central controller. Just nodes.

**Quick start (Docker, 2-node cluster with mTLS):**
```powershell
docker-compose -f docker-compose.standalone.yml up --build
```

**Quick start (bare metal, no Docker):**
```powershell
# Node 1 (seed)
cd src/app
pip install -r requirements.txt
python standalone.py --config ../../standalone_configs/node1.yaml

# Node 2 (on another terminal / machine)
python standalone.py --config ../../standalone_configs/node2.yaml
```

**Check that it's working:**
```powershell
# Health check
curl http://localhost:5001/health

# See what every node thinks the cluster looks like
curl http://localhost:5001/cluster/metrics
```

---

## Key Differences at a Glance

| Aspect | 🧪 Experiment Mode | 🌍 Standalone Mode |
|---|---|---|
| **Startup** | `monitoring.py` bootstraps everything | Each node starts itself via `standalone.py` |
| **Peer Discovery** | Static list pushed by orchestrator | Dynamic — seeds → `/peers` → `/join` |
| **Identity** | IP:port only, ephemeral | Persistent UUID (`.node_id` file survives restart) |
| **Peer Persistence** | None — orchestrator controls everything | `peer_state.json` saved to disk every 30s |
| **Security** | Plaintext HTTP (local Docker bridge) | mTLS — all gossip encrypted + authenticated |
| **Metric History** | Central `PrioMonDB.db` per experiment | Per-node `metric_history.db` with rolling retention |
| **Failure Model** | Containers stopped by orchestrator | SWIM-style suspect → dead via gossip |
| **Observability API** | Raw data endpoints only | `/cluster/metrics`, `/health`, `/cluster/history` |
| **Use Case** | Benchmarking, academic research | IoT, Kubernetes sidecars, mesh networks |

---

## API Reference (Both Modes)

### Gossip Endpoints (Internal — node-to-node)
| Endpoint | Method | Description |
|---|---|---|
| `/receive_metadata` | POST | Phase 1 of gossip exchange — metadata comparison |
| `/receive_message` | GET | Phase 2 of gossip exchange — data transfer |
| `/metadata` | GET | Returns counter + digest per node (for quorum queries) |

### Node Management
| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Liveness check: `{status, nodeAlive, node_id, peers, cycle}` |
| `/peers` | GET | Returns this node's current peer list |
| `/join` | POST | Register a new peer: `{"ip": "...", "port": "..."}` |
| `/metrics_priority_stats` | GET | Priority filtering stats + bandwidth savings |

### Cluster Observability (Standalone mode)
| Endpoint | Method | Description |
|---|---|---|
| `/cluster/metrics` | GET | Clean JSON view of all nodes' latest metrics |
| `/cluster/metrics/<ip:port>` | GET | Metrics for one specific node |
| `/cluster/query/<ip:port>` | GET | Quorum-verified consensus query for a target node |
| `/cluster/history` | GET | Historical metrics from SQLite (`?node=&minutes=&limit=`) |
| `/cluster/history/latest` | GET | Most recent persisted snapshot per node |

### Raw Data (Mostly for debugging)
| Endpoint | Method | Description |
|---|---|---|
| `/get_recent_data_from_node` | GET | Latest raw gossip snapshot (all peers' data) |
| `/get_data_from_node` | GET | Full multi-round gossip history |
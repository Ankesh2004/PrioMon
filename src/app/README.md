# PrioMon Standalone Node — Real-World Mode

This directory contains the **real-world, production-ready incarnation** of PrioMon. Each node in this mode is a fully autonomous agent. It discovers peers, gossips metrics, detects failures, persists state, and serves a clean observability API — all without any central orchestrator.

---

## Architecture

```
  Machine A                       Machine B                       Machine C
┌───────────────────┐           ┌───────────────────┐           ┌───────────────────┐
│  standalone.py    │           │  standalone.py    │           │  standalone.py    │
│  priomon.py :5000 │◄─ mTLS ──►│  priomon.py :5000 │◄─ mTLS ──►│  priomon.py :5000 │
│  node.py          │           │  node.py          │           │  node.py          │
│  ───────────────  │           │  ───────────────  │           │  ───────────────  │
│  peer_state.json  │           │  peer_state.json  │           │  peer_state.json  │
│  metric_history.db│           │  metric_history.db│           │  metric_history.db│
│  .node_id         │           │  .node_id         │           │  .node_id         │
└───────────────────┘           └───────────────────┘           └───────────────────┘
        │
        │ Seeds from config
        ▼
  [seed: machineA:5000]
```

Every node is identical — there's no master, no leader, no broker. Any node can be a seed for new joiners.

---

## Files in `src/app/`

| File | Role |
|---|---|
| `standalone.py` | CLI entry point — parses args, calls `start_standalone()` |
| `priomon.py` | Flask HTTP server — all API endpoints + startup `start_standalone()` |
| `node.py` | Node state, metric collection engine, priority filter, gossip transmit loop |
| `discovery.py` | Peer discovery via seeds + disk persistence of peer list |
| `metric_store.py` | SQLite rolling history of cluster metrics (per node) |
| `tls_utils.py` | mTLS config — SSL context builder, session configurator |
| `node_config.yaml` | Template config file — copy this for each node |
| `singleton.py` | `@Singleton` decorator used by `Node` class |
| `utility.py` | `mk_digest()` — SHA-256 hash of gossip data for consistency checks |
| `query.py` | Quorum query logic shared with experiment mode |
| `Dockerfile` | Container image for a node |

---

## How Standalone Startup Works (Technical)

When you run `python standalone.py --config node_config.yaml`, this is the sequence:

```
standalone.py:main()
  └─► priomon.py:start_standalone(config_path)
        ├─ 1. Load YAML config
        ├─ 2. Read metric_priorities + metric_deltas → update METRIC_PRIORITIES, METRIC_DELTAS
        ├─ 3. Load TLS certs → create SSL context (tls_utils.load_tls_from_config)
        ├─ 4. Discover peers (discovery.discover_peers)
        │       ├─ Contact each seed → GET /peers → POST /join (announce ourselves)
        │       └─ Fallback: load peer_state.json from disk
        ├─ 5. Initialize Node singleton (node.Node.instance())
        ├─ 6. node.set_params(...) — inject IP, port, peer_list, gossip config
        ├─ 7. node.configure_tls(tls_config) — attach certs to HTTP sessions
        ├─ 8. node.load_or_create_node_id() — load UUID from .node_id or generate+save
        ├─ 9. announce_to_peers() — POST /join to every known peer
        ├─ 10. Start Flask server (with SSL context if mTLS enabled)
        └─ 11. (background thread, 3s delay)
              ├─ client_thread: node.start_gossiping(target_count, gossip_rate)
              ├─ counter_thread: node.start_gossip_counter()
              ├─ start_metric_snapshotter(node, interval) — SQLite history writer
              └─ node.start_state_saver(interval=30) — periodic peer_state.json save
```

The 3-second delay before starting gossip gives Flask time to bind its port. Without it, a node might try to gossip before it can receive gossip back.

---

## Core Components Deep Dive

### `node.py` — The Gossip Engine

**Priority Filtering (`should_send_metric` + `get_filtered_data_by_priority`)**

There are two layers of priority filtering, and they work at different levels:

- `should_send_metric()` runs during **metric collection** (`get_new_data()`). It decides whether to even generate a metric value for this round based on `METRIC_PRIORITIES` (round schedule) and `METRIC_DELTAS` (minimum % change). If filtered at this stage, the metric just doesn't get measured this round.

- `get_filtered_data_by_priority()` runs during **gossip transmission** (`prepare_metadata_and_own_fresh_data()`). It filters a node's own data before sending it to peers. If a metric isn't due this round, it's `pop()`-ed from the `appState` dict entirely.

The receiving node (`compare_and_update_node_data` in `priomon.py`) handles missing metrics by inheriting the last known value:
```python
for metric in existing_metrics - incoming_metrics:
    existing_val = node.data[latest_entry][key]['appState'][metric]
    if existing_val != "not_updated":
        new_data[key]['appState'][metric] = existing_val
```

**Gossip Transmit Loop (`transmit()`)**
```python
def transmit(self, target_count):
    # 1. snapshot current gossip state
    latest_data = self.data[latest_entry].copy()
    # 2. add our own fresh data (with priority filter applied)
    latest_data[f"{self.ip}:{self.port}"] = get_new_data()
    self.data[new_time_key] = latest_data
    # 3. pick random peers
    random_nodes = self.get_random_nodes(self.node_list, target_count)
    # 4. gossip to each
    for node in random_nodes:
        self.send_to_node(node, new_time_key)
```

**Two-Phase Gossip (`send_to_node()`)**

Phase 1 — Metadata exchange:
```
→ POST /receive_metadata  {metadata: {ip: counter, ...}, own_key: own_data, _peers: [...]}
← {requested_keys: [...], updates: {...}}
```
The peer compares counters. If our counter for a node is newer → we provide data. If theirs is newer → we request it.

Phase 2 — Data transfer:
```
→ GET /receive_message    {requested_keys' data}
← "OK" or 500 if peer is dead
```

Peer lists are **piggybacked** on every gossip message (`_peers` field in the payload). This is how new peers propagate through the network without needing a dedicated "membership protocol" — if node3 tells node1 about node5, node1 learns about node5 for free on the next gossip cycle.

**SWIM-style Failure Detection (`update_failure_data()`)**

When a gossip call to a peer fails:
1. Our own key is appended to that peer's `failureList` in our local state
2. This state gets gossiped to others naturally
3. The `failureList` is merged across gossip (union of failure reports)
4. When `failureCount >= 3` → status changes `"suspect"` → `"dead"`, and the peer is removed from `node_list`
5. The dead entry remains in `data` as a tombstone so the "dead" status can keep propagating

---

### `discovery.py` — Peer Discovery

```python
def discover_peers(seeds, own_ip, own_port, state_file):
    for seed in seeds:
        # ask the seed for its peer list
        resp = requests.get(f"{scheme}://{seed_ip}:{seed_port}/peers")
        peer_list = resp.json()
        # announce ourselves to the seed
        requests.post(f"{scheme}://{seed_ip}:{seed_port}/join",
                      json={"ip": own_ip, "port": str(own_port)})
        break  # one seed is enough

    if not peers:
        # no seed reachable — try loading last known state from disk
        peers = load_state(state_file)

    save_state(peers, state_file)
    return peers
```

After discovery, `announce_to_peers()` POSTs `/join` to every known peer so they add us immediately rather than waiting for gossip to spread our existence.

**State persistence format (`peer_state.json`):**
```json
{
  "peers": [
    {"ip": "192.168.1.10", "port": "5000"},
    {"ip": "192.168.1.11", "port": "5000"}
  ],
  "saved_at": 1710912345.0
}
```

---

### `tls_utils.py` — mTLS

**What mTLS does:** Every node has its own certificate signed by a shared CA. When node A connects to node B:
- B verifies A's cert is signed by the trusted CA (authentication)
- A verifies B's cert is signed by the trusted CA (mutual)
- The connection is encrypted with TLS 1.2+

This means a random machine on the network can't inject itself into the gossip cluster — it would need a cert signed by your CA.

**Configuration in code:**
```python
# Server side — Flask gets an SSL context that requires client certs
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(certfile=node_cert, keyfile=node_key)
ctx.load_verify_locations(cafile=ca_cert)
ctx.verify_mode = ssl.CERT_REQUIRED   # <-- this is what makes it MUTUAL TLS
```

```python
# Client side — requests.Session presents our cert on every outgoing call
session.cert = (node_cert, node_key)
session.verify = ca_cert
```

The global TLS config (`_global_tls_config`) is set once during startup via `set_global_tls()` and then read by `discovery.py`, `priomon.py`, and `node.py` through `get_global_tls()`.

---

### `metric_store.py` — Local Metric History

Each node maintains its own rolling SQLite database (`metric_history.db`) with cluster metrics as it observes them through gossip. This is a per-node view — not a central store.

**Table: `metric_snapshots`**
```sql
CREATE TABLE metric_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       REAL NOT NULL,       -- Unix timestamp
    node_key        TEXT NOT NULL,       -- "ip:port"
    cpu             REAL,
    memory          REAL,
    network         REAL,
    storage         REAL,
    status          TEXT DEFAULT 'alive',
    cycle           INTEGER,
    gossip_counter  TEXT
)
```

A background thread (`start_metric_snapshotter`) reads the Node's in-memory gossip state every `snapshot_interval` seconds and inserts rows. Old rows are purged based on `retention_seconds`.

This is also WAL-mode: concurrent reads don't block writes, critical since the snapshot thread writes while Flask threads read for `/cluster/history`.

---

## Configuration Reference (`node_config.yaml`)

```yaml
node:
  host: "0.0.0.0"   # bind address — leave as 0.0.0.0 to accept from all interfaces
  port: 5000         # port this node listens on

gossip:
  target_count: 2   # how many peers to gossip with per cycle (higher = faster convergence, more bandwidth)
  rate: 0.5          # seconds between gossip rounds (lower = faster, but more CPU/network)

# Seeds are known nodes to contact on first startup.
# At least one must be reachable for a node to join an existing cluster.
# If no seed is reachable, the node starts alone and waits for others to join it.
seeds:
  - "192.168.1.10:5000"

# Controls how often each metric type is sent in gossip.
# Priority = 1 means every round. Priority = 10 means at most every 10th round.
metric_priorities:
  cpu: 1        # CPU is critical — send every single round
  memory: 5     # Memory changes slowly — every 5 rounds
  network: 5    # Network I/O — every 5 rounds
  storage: 10   # Free disk — barely changes, every 10 rounds

# Minimum % change that forces an immediate send, overriding the priority schedule.
# Prevents missing sudden spikes just because "it's not time yet".
metric_deltas:
  cpu: 5.0       # 5% CPU change → send immediately
  memory: 7.0    # 7% memory change → send immediately
  network: 15.0  # 15% network change → send immediately
  storage: 10.0  # 10% storage change → send immediately

# Where to persist the peer list across restarts.
state_file: "peer_state.json"

# Local metric history settings.
metric_history:
  snapshot_interval: 5      # take a snapshot of cluster metrics every 5 seconds
  retention_seconds: 3600   # keep 1 hour of history

# mTLS — set enabled: true to encrypt all gossip.
# All cert paths must be readable by this node's process.
tls:
  enabled: false
  ca_cert: "/path/to/ca.crt"
  node_cert: "/path/to/node.crt"
  node_key: "/path/to/node.key"

# Optional — send data to a central monitoring.py server.
# Leave blank for fully decentralized operation.
monitoring:
  address: ""
  port: 4000
  send_data_back: false

push_mode: false   # if true, node batches old rounds and pushes them to monitoring.py
```

**Tuning tips:**
- Increase `target_count` from 2 to 3+ for faster convergence in large clusters (at the cost of higher bandwidth)
- Decrease `gossip.rate` below 0.5 for more aggressive propagation in low-latency environments
- Increase `metric_priorities.storage` to 30+ if storage metrics are completely irrelevant to you (saves bandwidth)
- Keep `cpu: 1` — if a node is spiking, you want to know immediately, not on a schedule

---

## Running Guide

### Option A: Docker Compose (Easiest — 2-node cluster with mTLS)

The `docker-compose.standalone.yml` at the repo root starts a 2-node cluster where node1 is the seed and node2 joins it. mTLS is enabled using the pre-generated certs in `certs/`.

```powershell
# from repo root
docker-compose -f docker-compose.standalone.yml up --build
```

What this does:
- Builds the `priomonv1` image from `src/app/Dockerfile`
- Starts `node1` on host port `5001` (bound to Docker port 5000), with `node1.yaml` config
- Waits for `node1` to be healthy (healthcheck polls `https://localhost:5000/health`)
- Starts `node2` on host port `5002`, with `node2.yaml` config (seeds: `["node1:5000"]`)
- Nodes gossip over the `priomon_net` bridge via mTLS

```powershell
# Check node1's health
curl http://localhost:5001/health

# See the full cluster as node1 sees it
curl http://localhost:5001/cluster/metrics | python -m json.tool

# See the cluster from node2's perspective (might differ during early gossip rounds)
curl http://localhost:5002/cluster/metrics | python -m json.tool
```

---

### Option B: Bare Metal (No Docker)

Good for running on actual separate machines, VMs, or just testing locally without Docker.

**Node 1 (the seed):**
```powershell
cd src/app
pip install -r requirements.txt

# Copy the template and customize it
copy node_config.yaml my_node1.yaml
# Edit seeds: [] (node1 IS the seed, no seeds needed)
# Edit node.port: 5000

python standalone.py --config my_node1.yaml
```

**Node 2 (on a different machine or port):**
```powershell
copy node_config.yaml my_node2.yaml
# Edit seeds: ["192.168.1.10:5000"]  ← node1's address
# Edit node.port: 5001

python standalone.py --config my_node2.yaml
```

**Node 3, 4, ... (add as many as you want):**
```powershell
# Point seeds at any existing node — it doesn't have to be node1 specifically
# Once node2 joins, it's also a valid seed
```

**Port override without editing the config:**
```powershell
python standalone.py --config my_node.yaml --port 5002
```

---

### Option C: Local 2-node test (bare metal, one machine)

Useful for quick local testing without Docker:

```powershell
# Terminal 1 — node 1 (seed)
cd src\app
python standalone.py --config ..\..\standalone_configs\node1.yaml --port 5001

# Terminal 2 — node 2 (joins node1)
cd src\app
python standalone.py --config ..\..\standalone_configs\node2.yaml --port 5002
```

Wait ~5–10 seconds for gossip to propagate, then:
```powershell
curl http://localhost:5001/cluster/metrics
curl http://localhost:5002/cluster/metrics
```
Both should show data from both nodes.

---

## Setting Up mTLS

If you want to run with real mTLS (recommended for multi-machine deployments):

**Step 1 — Install cert generation dependency:**
```powershell
pip install cryptography
```

**Step 2 — Generate certs for your nodes:**
```powershell
# generates ca.crt, ca.key, node1.crt, node1.key, node2.crt, node2.key in ./certs/
python scripts/generate_certs.py --nodes node1,node2 --out ./certs

# For real IPs (add SANs so TLS hostname validation works):
python scripts/generate_certs.py --nodes node1,node2 --ips 192.168.1.10,192.168.1.11 --out ./certs
```

**Step 3 — Distribute certs:**
- `ca.crt` → all nodes (they all need to trust the same CA)
- `node1.crt` + `node1.key` → node1 only
- `node2.crt` + `node2.key` → node2 only
- `ca.key` → keep this offline, never deploy it

**Step 4 — Update configs:**
```yaml
tls:
  enabled: true
  ca_cert: "/path/to/ca.crt"
  node_cert: "/path/to/node1.crt"
  node_key: "/path/to/node1.key"
```

**Step 5 — Start nodes.** They'll automatically use HTTPS for all gossip.

---

## API Endpoints

### Health & Identity
```bash
GET /health
# → {"status": "alive", "nodeAlive": true, "node_id": "uuid-...", "peers": 2, "cycle": 47}
```

### Cluster View
```bash
GET /cluster/metrics
# → clean JSON with all nodes' latest CPU/memory/network/storage
#   as observed through gossip from this node's perspective

GET /cluster/metrics/192.168.1.11:5000
# → metrics for a specific node only

GET /cluster/query/192.168.1.11:5000?quorum_size=2
# → quorum-verified result — contacts 2 peers, checks consensus, then fetches verified data
#   response includes: consensus.counter, consensus.digest, verified_by list
```

### Metric History
```bash
GET /cluster/history?minutes=10&limit=200
GET /cluster/history?node=192.168.1.11:5000&minutes=5
GET /cluster/history/latest
# → most recent snapshot per node, useful for dashboards
```

### Priority Stats (Bandwidth audit)
```bash
GET /metrics_priority_stats
# → how many metrics were sent vs. filtered
#   bandwidth_savings_percent, per_round_stats, current priorities + deltas
```

### Peer Management
```bash
GET /peers
# → current peer list

POST /join {"ip": "192.168.1.12", "port": "5000"}
# → add a new peer (also saves peer_state.json)
```

### Example: Full cluster health check
```bash
curl http://localhost:5001/cluster/metrics | python -m json.tool

# Response:
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
      "status": "alive",
      "alive": true,
      "node_id": "a1b2c3d4-..."
    },
    "172.18.0.3:5000": {
      "cpu": 12.1,
      "memory": 45.2,
      ...
    }
  }
}
```

---

## Use Cases

| Scenario | Config Notes |
|---|---|
| **IoT / Edge monitoring** | High priority deltas (`cpu: 20.0`), low gossip rate (`rate: 2.0`), minimal `target_count: 1` to save bandwidth on constrained links |
| **Kubernetes health sidecars** | Each pod runs a node, seeds = headless service DNS, no mTLS needed (pod network is trusted) |
| **Multi-cloud cluster** | mTLS required, use real IPs in cert SANs, `target_count: 3` for faster convergence |
| **Development / testing** | Plain HTTP (`tls.enabled: false`), low `gossip.rate: 0.1`, `target_count: 1` |

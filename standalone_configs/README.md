# Standalone Configs — Example 2-Node Cluster

These YAML files are example configurations for running a 2-node standalone PrioMon cluster with mTLS enabled. They are used directly by `docker-compose.standalone.yml` at the repo root.

## Files

| File | Role |
|---|---|
| `node1.yaml` | Seed node — has no seeds, waits for others to join |
| `node2.yaml` | Joiner node — points `seeds: ["node1:5000"]` to find the cluster |

## How They Work Together

1. Node1 starts with `seeds: []` — it bootstraps alone as the first member
2. Node2 starts with `seeds: ["node1:5000"]` — it calls `GET node1:5000/peers` to get the cluster's known members, then calls `POST node1:5000/join` to announce itself
3. Both nodes gossip with mTLS using the certs from `../certs/`
4. Peer lists propagate through gossip so each node stays in sync even as new nodes join

## mTLS Cert Paths

Both configs reference paths under `/mini/certs/` — those are the container-internal paths where `docker-compose.standalone.yml` mounts the local `./certs/` directory.

For bare metal runs (outside Docker), update the paths to wherever you've placed the certs:
```yaml
tls:
  enabled: true
  ca_cert: "C:/path/to/certs/ca.crt"
  node_cert: "C:/path/to/certs/node1.crt"
  node_key: "C:/path/to/certs/node1.key"
```

## Adding More Nodes

Copy either file and adjust:
- `node.port` → pick an unused port
- `seeds` → point to any already-running node (not just node1)
- `tls.node_cert` / `tls.node_key` → generate a new cert for this node using `scripts/generate_certs.py`

```powershell
# Generate a cert for node3
python scripts/generate_certs.py --nodes node1,node2,node3 --out ./certs
```

> **Note:** Re-running `generate_certs.py` creates a *new* CA, which will invalidate existing node certs. Regenerate certs for all nodes at once.

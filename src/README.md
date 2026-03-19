# PrioMon Source (Gossip Engine)

This directory contains the underlying implementation of the PrioMon gossip nodes and the client-side query interface.

## 📁 Contents

### `app/`
Contains the core logic for the individual PrioMon nodes.
- `node.py`: The main Node engine. Implements the gossip transmission logic, the priority-based filtering algorithm, and connection pooling.
- `priomon.py`: The Flask entry point for each node. Handles metadata exchange and provides API endpoints for the orchestrator.
- `Dockerfile`: Defines the containerized environment (Python 3.9-slim) for the nodes.

### `query_client.py`
A bridge module that allows the orchestrator (or external tools) to submit queries into the gossip network. It ensures that queries are disseminated using the same protocol as the system metrics.

## 🧠 Key Logic: Priority Filtering

PrioMon nodes don't just send everything. Before every transmission, the `Node` evaluates each metric:
1.  **Staleness**: Has it been too long since we last sent this? (Based on `MetricPriority`).
2.  **Drift**: Has the value changed significantly since the last update? (Based on `MetricDelta`).
3.  **Forced Send**: If it's a new or critical update, it bypasses the filters.

This logic results in drastic bandwidth reductions without sacrificing monitoring accuracy.

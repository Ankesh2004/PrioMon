# PrioMon Experiments (Orchestrator)

This directory contains the orchestration logic used to run simulations, monitor convergence, and analyze the performance of the PrioMon gossip protocol.

##  Core Components

### `monitoring.py`
The heart of the simulation. It acts as both a Flask control server and a metric aggregator.
- **Orchestration**: Dynamically spawns Docker containers, configures their initial state, and triggers the gossip phase.
- **Monitoring**: Receives real-time data packets from nodes and tracks how long it takes for a "query" to propagate through the network.
- **Persistence**: Records every gossip round and metric transmission into `PrioMonDB.db`.

### `plot.py`
A comprehensive visualization suite using Matplotlib.
- **Bandwidth Savings**: Generates pie charts and line graphs showing how many metrics were filtered vs. sent.
- **Resilience**: Plots query success rates against varying node failure percentages.
- **Metric Breakdown**: Shows which specific types of metrics (e.g., Memory vs. CPU) are being prioritized.

### `connector_db.py`
The database abstraction layer for the experiments.
- **SSD Safety**: Configured with `WAL` mode and `synchronous = NORMAL` to prevent excessive disk wear during high-throughput logging.
- **Schema Management**: Handles the creation and management of experiment, run, and query tables.

## How to Configure

All simulation parameters are stored in `config.ini`:
- `PriomonParam`: Control node counts, gossip rates, and network ports.
- `MetricPriority`: Adjust the "Importance" level of metrics (higher number = less frequent updates).
- `MetricDelta`: Set the threshold for change-driven updates (percentage change).

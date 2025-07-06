# PrioMon - Priority-based Distributed System Monitoring

PrioMon is a distributed system monitoring framework that uses priority-based gossip protocols to efficiently monitor system metrics across distributed nodes. The system allows for configurable monitoring priorities and adaptive resource allocation based on metric importance.

## Architecture Overview

![image](https://github.com/user-attachments/assets/7ae0254a-464e-47ff-a32a-e6c87fbe5699)


The PrioMon architecture consists of distributed nodes that communicate using gossip protocols to share monitoring data. Each node maintains local metric priorities and deltas, enabling efficient resource utilization and targeted monitoring.

## Key Features

- **Priority-based Monitoring**: Configure different priorities for CPU, memory, network, and storage metrics
- **Gossip Protocol Communication**: Efficient peer-to-peer communication for metric dissemination
- **Adaptive Thresholds**: Configurable delta values for different metric types
- **Failure Resilience**: Built-in failure rate handling and recovery mechanisms
- **Query Logic Support**: Flexible querying capabilities for metric data
- **Database Integration**: SQLite-based storage for monitoring data

## Project Structure

```
├── experiments/                    # Experimental configurations and data
│   ├── config.ini                 # Main configuration file
│   ├── connector_db.py            # Database connector
│   ├── priomonDB.db          # Main database
│   ├── monitoring_exp.py      # Monitoring experiment logic
│   ├── plots/                 # Generated plots and visualizations
│        └── plot.py  
├── src/app/                       # Source code
    ├── query.py                   # Query client implementation
    └── priomon.py                # Main PrioMon logic
    └── node.py                    # Each node's logic
    └── DockerFile                # DockerFile for each node
    └── utility.py                # Utility functions
    └── requirements.txt          # Requirements 
```

## Configuration

The system is configured through [experiments/config.ini](experiments/config.ini). Key configuration sections include:

### PrioMon Parameters

```ini
[PrioMonParam]
node_range = [10]              # Number of nodes in the system
target_count = 1               # Number of targets per gossip round
gossip_rate = 1                # Interval between gossip messages (seconds)
runs = 1                       # Number of experimental runs
continue_after_convergence = 0  # Continue monitoring after convergence
push_mode = 0                  # Enable/disable push mode
client_port = 4000             # Client communication port
```

### System Settings

```ini
[system_setting]
query_logic = 1                # Enable query logic
failure_rate = 0.5             # System failure rate tolerance
docker_ip = 127.0.0.1          # Docker container IP
is_send_data_back = 1          # Enable data feedback
```

### Metric Priorities

Configure the importance of different system metrics:

```ini
[MetricPriorities]
cpu_priority = 1               # CPU monitoring priority (lowest)
memory_priority = 5            # Memory monitoring priority
network_priority = 5           # Network monitoring priority  
storage_priority = 10          # Storage monitoring priority (highest)
```

### Metric Deltas

Set threshold values for metric change detection:

```ini
[MetricDeltas]
cpu_delta = 5.0               # CPU change threshold (%)
memory_delta = 7.0            # Memory change threshold (%)
network_delta = 15.0          # Network change threshold (%)
storage_delta = 10.0          # Storage change threshold (%)
```

## Gossip Protocol Design
![image](https://github.com/user-attachments/assets/f9273bc5-ee04-49e2-8468-b9ee0d3c4fdc)



The gossip protocol implementation enables efficient metric dissemination across the distributed system:

1. **Target Selection**: Each node selects `target_count` peers for communication
2. **Message Exchange**: Nodes exchange metric data based on configured priorities
3. **Convergence Detection**: System monitors for convergence state
4. **Failure Handling**: Built-in resilience for node failures

## Getting Started

### Prerequisites

- Python 3.8+
- SQLite3
- Required Python packages (see `requirements.txt` in experiment directories)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd PrioMon
```

2. Install dependencies:
```bash
cd experiments/emulation-exp
pip install -r requirements.txt
```

### Running Experiments

#### Emulation Experiment

Navigate to the emulation experiment directory and run:

```bash
cd experiments/emulation-exp
python monitoring_exp.py
```

This will:
- Initialize the PrioMon nodes based on [config.ini](experiments/config.ini)
- Start the gossip protocol communication
- Monitor system metrics with configured priorities
- Store results in [priomonDB.db](experiments/emulation-exp/priomonDB.db)

#### Use Case Experiment

For use case specific experiments:

```bash
cd experiments/use-case-exp
python experiment.py
```

### Query Client Usage

The query client allows you to interact with the PrioMon system:

```python
from src.query_client import QueryClient

client = QueryClient()
# Query specific metrics
results = client.query_metrics(node_id=1, metric_type='cpu')
```

## Database Schema

![image](https://github.com/user-attachments/assets/605471c6-4c4c-4af3-8e0f-449614a754cb)


The PrioMon system uses SQLite database ([priomonDB.db](experiments/emulation-exp/priomonDB.db)) to store:

- Node information and status
- Metric values and timestamps
- Priority configurations
- Gossip communication logs
- System convergence data

## Monitoring Metrics

PrioMon monitors four primary system metrics:

1. **CPU Usage**: Processor utilization percentage
2. **Memory Usage**: RAM consumption metrics
3. **Network Activity**: Network I/O statistics
4. **Storage Usage**: Disk utilization and I/O metrics

Each metric has configurable priority levels and delta thresholds for efficient monitoring.

## Experimental Analysis

### Plots and Visualization

The system generates various plots for analysis:

- Convergence time analysis
- Metric priority impact studies
- Failure rate vs. performance correlation
- Network overhead measurements

Results are stored in the [plots/](experiments/emulation-exp/plots/) directory.

### Performance Metrics

Key performance indicators include:

- **Convergence Time**: Time to reach system-wide consensus
- **Message Overhead**: Network communication efficiency
- **Accuracy**: Metric reporting precision
- **Resilience**: Failure recovery capabilities

## Configuration Examples

### High-Priority Storage Monitoring

```ini
[MetricPriorities]
cpu_priority = 1
memory_priority = 1
network_priority = 1
storage_priority = 20
```

### Low-Latency Network Monitoring

```ini
[PrioMonParam]
gossip_rate = 0.5
target_count = 3

[MetricPriorities]
network_priority = 15
```

## Troubleshooting

### Common Issues

1. **Database Connection Errors**: Ensure [priomonDB.db](experiments/emulation-exp/priomonDB.db) has proper permissions
2. **Port Conflicts**: Check `client_port` configuration in [config.ini](experiments/config.ini)
3. **Node Communication Failures**: Verify `docker_ip` settings and network connectivity

### Debug Mode

Enable detailed logging by modifying the configuration:

```ini
[system_setting]
debug_mode = 1
log_level = DEBUG
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## Future Work

- Enhanced machine learning-based priority adaptation
- Support for additional metric types
- Improved failure detection algorithms
- Real-time dashboard implementation
- Kubernetes integration

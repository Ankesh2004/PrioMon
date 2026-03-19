# PrioMon: Priority-Based Distributed Monitoring

PrioMon is a high-performance distributed monitoring system that utilizes a gossip protocol to propagate system metrics (CPU, Memory, Network, Storage) across a cluster of nodes. It features a unique **priority-based bandwidth-saving mechanism** that intelligently filters metrics based on their importance and the magnitude of data changes.

## 🚀 Key Features

- **Gossip Protocol Propagation**: Efficient data dissemination without a single point of failure.
- **Priority Filtering**: Reduces bandwidth usage by up to 100x by prioritizing critical status updates and skipping redundant ones.
- **Node Resilience**: Automatic failure detection and handling of dead nodes.
- **SSD Optimized**: Uses SQLite WAL (Write-Ahead Logging) to protect your hardware during intensive high-frequency simulations.
- **Integrated Analytics**: Built-in plotting tools to visualize convergence time, success rates, and bandwidth savings.

## 📁 Project Structure

```text
PrioMon/
├── src/               # Core Gossip Engine implementation
│   ├── app/           # Dockerized node logic (priomon.py, node.py)
│   └── query_client.py # Client-side query bridge
├── experiments/       # Simulation Orchestration & Analysis
│   ├── monitoring.py  # Central experiment runner & monitoring server
│   ├── plot.py        # Analytics visualization tool
│   └── config.ini     # Simulation parameters
├── docker-compose.yml # Service definitions for building the node cluster
└── requirements.txt   # Orchestrator dependencies (Python)
```

## 🛠️ Quick Start

### 1. Prerequisites
- Python 3.9+ 
- Docker & Docker Compose
- A virtual environment (recommended)

### 2. Installation
```powershell
# Clone the repository
git clone <repo-url>
cd PrioMon

# Install dependencies
pip install -r requirements.txt
```

### 3. Running the Simulation
1.  **Build the Node Image**:
    ```powershell
    docker-compose up --build -d
    ```
2.  **Start the Orchestrator**:
    ```powershell
    python experiments/monitoring.py
    ```
3.  **Trigger the Experiment**:
    Open your browser and navigate to `http://localhost:4000/start`.

### 4. Visualizing Results
After the simulation finishes, run the plotting tool:
```powershell
python experiments/plot.py
```
This will generate PNG charts in the `experiments/` directory.

## ⚙️ Configuration
Tweak the parameters in `experiments/config.ini` to change node counts, gossip rates, or metric priorities.
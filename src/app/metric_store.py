import sqlite3
import os
import time
import json
import threading
import logging

logger = logging.getLogger("priomon.metric_store")

# local metric history — each node keeps a rolling SQLite database
# of cluster-wide metrics as it sees them through gossip.
# this survives restarts and lets you query historical data.

DEFAULT_DB_PATH = "metric_history.db"
# how many seconds of history to keep (default: 1 hour)
DEFAULT_RETENTION_SECONDS = 3600
# how often to snapshot from in-memory gossip state (seconds)
DEFAULT_SNAPSHOT_INTERVAL = 5


class MetricStore:
    def __init__(self, db_path=None, retention_seconds=DEFAULT_RETENTION_SECONDS):
        self.db_path = db_path or os.environ.get("PRIOMON_METRIC_DB", DEFAULT_DB_PATH)
        self.retention_seconds = retention_seconds
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist. WAL mode for SSD safety."""
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")

        # one row per node per snapshot
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metric_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                node_key TEXT NOT NULL,
                cpu REAL,
                memory REAL,
                network REAL,
                storage REAL,
                alive INTEGER DEFAULT 1,
                cycle INTEGER,
                gossip_counter TEXT
            )
        """)
        # index for fast lookups by node + time range
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_node_time 
            ON metric_snapshots(node_key, timestamp)
        """)
        conn.commit()
        conn.close()

    def _connect(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def store_snapshot(self, cluster_data):
        """
        Takes the parsed cluster metrics dict (from _extract_cluster_metrics)
        and persists each node's metrics as a row.
        """
        now = time.time()
        with self._lock:
            conn = self._connect()
            cursor = conn.cursor()
            for node_key, metrics in cluster_data.items():
                cursor.execute(
                    """INSERT INTO metric_snapshots 
                       (timestamp, node_key, cpu, memory, network, storage, alive, cycle, gossip_counter)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now,
                        node_key,
                        metrics.get("cpu"),
                        metrics.get("memory"),
                        metrics.get("network"),
                        metrics.get("storage"),
                        1 if metrics.get("alive", True) else 0,
                        metrics.get("cycle"),
                        metrics.get("gossip_counter")
                    )
                )
            conn.commit()
            conn.close()

    def cleanup_old(self):
        """Remove entries older than retention period."""
        cutoff = time.time() - self.retention_seconds
        with self._lock:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM metric_snapshots WHERE timestamp < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                logger.debug(f"Cleaned up {deleted} old metric records")

    def get_history(self, node_key=None, minutes=10, limit=500):
        """
        Get metric history for a specific node or all nodes.
        Returns newest-first, capped at `limit` rows.
        """
        cutoff = time.time() - (minutes * 60)
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if node_key:
            cursor.execute(
                """SELECT * FROM metric_snapshots 
                   WHERE node_key = ? AND timestamp > ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (node_key, cutoff, limit)
            )
        else:
            cursor.execute(
                """SELECT * FROM metric_snapshots 
                   WHERE timestamp > ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (cutoff, limit)
            )

        rows = cursor.fetchall()
        conn.close()

        # convert to list of dicts
        result = []
        for row in rows:
            result.append({
                "timestamp": row["timestamp"],
                "node_key": row["node_key"],
                "cpu": row["cpu"],
                "memory": row["memory"],
                "network": row["network"],
                "storage": row["storage"],
                "alive": bool(row["alive"]),
                "cycle": row["cycle"]
            })
        return result

    def get_latest_per_node(self):
        """Get the most recent snapshot for each node."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.* FROM metric_snapshots m
            INNER JOIN (
                SELECT node_key, MAX(timestamp) as max_ts
                FROM metric_snapshots
                GROUP BY node_key
            ) latest ON m.node_key = latest.node_key AND m.timestamp = latest.max_ts
        """)
        rows = cursor.fetchall()
        conn.close()

        result = {}
        for row in rows:
            result[row["node_key"]] = {
                "cpu": row["cpu"],
                "memory": row["memory"],
                "network": row["network"],
                "storage": row["storage"],
                "alive": bool(row["alive"]),
                "cycle": row["cycle"],
                "last_seen": row["timestamp"]
            }
        return result


# background loop — pulls metrics from Node.data and stores them periodically
_store_instance = None
_snapshot_thread = None


def get_store(db_path=None, retention_seconds=DEFAULT_RETENTION_SECONDS):
    """Get or create the global MetricStore singleton."""
    global _store_instance
    if _store_instance is None:
        _store_instance = MetricStore(db_path, retention_seconds)
    return _store_instance


def start_metric_snapshotter(node_ref, interval=DEFAULT_SNAPSHOT_INTERVAL):
    """
    Background thread that reads the node's gossip data every `interval` seconds,
    extracts clean metrics, and stores them in SQLite.
    """
    global _snapshot_thread
    store = get_store()

    def _loop():
        cleanup_counter = 0
        while node_ref.is_alive:
            time.sleep(interval)
            try:
                cluster_data = extract_cluster_metrics(node_ref)
                if cluster_data:
                    store.store_snapshot(cluster_data)

                # run cleanup every ~60 snapshots (5 min at 5s interval)
                cleanup_counter += 1
                if cleanup_counter >= 60:
                    store.cleanup_old()
                    cleanup_counter = 0
            except Exception as e:
                logger.error(f"Metric snapshot failed: {e}")

    _snapshot_thread = threading.Thread(target=_loop, daemon=True)
    _snapshot_thread.start()
    logger.info(f"Metric snapshotter started (every {interval}s)")


def extract_cluster_metrics(node_ref):
    """
    Pull clean metrics from the node's in-memory gossip data.
    Returns a dict like:
    {
        "10.0.0.1:5000": {"cpu": 45.2, "memory": 67.8, ...},
        "10.0.0.2:5000": {"cpu": 12.1, "memory": 34.5, ...}
    }
    """
    if not node_ref.data:
        return {}

    try:
        latest_key = max(node_ref.data.keys(), key=int)
        latest_data = node_ref.data[latest_key]
    except (ValueError, KeyError):
        return {}

    cluster = {}
    for node_key, node_data in latest_data.items():
        if not isinstance(node_data, dict):
            continue

        entry = {
            "alive": True,
            "cycle": None,
            "gossip_counter": None
        }

        # pull appState metrics (these are what we actually care about)
        app_state = node_data.get("appState", {})
        for metric in ["cpu", "memory", "network", "storage"]:
            val = app_state.get(metric)
            if val is not None and val != "not_updated":
                try:
                    entry[metric] = float(val)
                except (ValueError, TypeError):
                    entry[metric] = None
            else:
                entry[metric] = None

        # pull heartbeat/liveness info
        hb = node_data.get("hbState", {})
        entry["alive"] = hb.get("nodeAlive", True)

        # cycle and counter
        entry["cycle"] = node_data.get("cycle")
        entry["gossip_counter"] = node_data.get("counter")

        cluster[node_key] = entry

    return cluster

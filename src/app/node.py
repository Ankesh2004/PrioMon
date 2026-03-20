import time
import json
import os
import uuid
import psutil
import requests
from singleton import Singleton
import logging
import secrets
from utility import mk_digest

logger = logging.getLogger("demon.metrics")

# Priority levels
PRIORITY_HIGH = 1     # Update every round
PRIORITY_MEDIUM = 5   # Update every 5 rounds
PRIORITY_LOW = 10     # Update every 10 rounds

# Configure priorities for different metrics
METRIC_PRIORITIES = {
    "cpu": PRIORITY_HIGH,      # CPU is critical - update every round
    "memory": PRIORITY_MEDIUM, # Memory - update every 5 rounds
    "network": PRIORITY_MEDIUM, # Network - update every 5 rounds
    "storage": PRIORITY_LOW    # Storage changes slowly - update every 10 rounds
}

# Delta thresholds for each metric (minimum change to trigger update)
METRIC_DELTAS = {
    "cpu": 5.0,      # 5% change in CPU
    "memory": 7.0,   # 7% change in memory
    "network": 15.0, # 15% change in network
    "storage": 10.0  # 10% change in storage
}

# Track last values to calculate deltas
last_metric_values = {}
# Track when each metric was last sent
last_metric_sent_round = {}

def get_new_data():
    node = Node.instance()
    network = psutil.net_io_counters().bytes_recv + psutil.net_io_counters().bytes_sent
    
    # Get current metric values
    current_metrics = {
        "cpu": psutil.cpu_percent(),
        "memory": psutil.virtual_memory().percent,
        "network": network,
        "storage": psutil.disk_usage('/').free
    }
    
    # Determine which metrics to send based on priority and delta
    metrics_to_send = {}
    metrics_filtered = {}
    
    for metric, value in current_metrics.items():
        if should_send_metric(node, metric, value):
            metrics_to_send[metric] = value
        else:
            metrics_filtered[metric] = value
    
    # Create the data structure with only selected metrics
    app_state = {}
    for metric in metrics_to_send:
        app_state[metric] = str(metrics_to_send[metric])
    
    # Track metrics statistics for this round
    node.data_flow_per_round.setdefault(node.cycle, {})
    node.data_flow_per_round[node.cycle]['metrics_sent'] = len(metrics_to_send)
    node.data_flow_per_round[node.cycle]['metrics_filtered'] = len(metrics_filtered)
    
    # Store data about which metrics were sent this round
    metric_flags = {metric: (metric in metrics_to_send) for metric in current_metrics}
    
    data = {
        "counter": "{}".format(node.gossip_counter),
        "cycle": "{}".format(node.cycle),
        "digest": "",
        "nodeState": {
            "id": node.node_id or "",
            "ip": "{}".format(node.ip),
            "port": "{}".format(node.port)},
        "hbState": {
            "timestamp": "{}".format(time.time()),
            "failureCount": node.failure_counter or 0,
            "failureList": node.failure_list,
            "nodeAlive": node.is_alive,
            "status": "alive" if node.is_alive else "dead"
        },
        "appState": app_state,
        "nfState": {},
        "metric_sent_flags": metric_flags
    }
    
    digest = mk_digest(data)
    data["digest"] = digest
    
    return data


def should_send_metric(node, metric, value):
    if metric not in last_metric_values:
        last_metric_values[metric] = value
        last_metric_sent_round[metric] = 0
        return True  # Always send first time
        
    # Get priority for this metric
    priority = METRIC_PRIORITIES.get(metric, PRIORITY_HIGH)
    
    # Calculate rounds since last sent
    rounds_since_sent = node.cycle - last_metric_sent_round[metric]
    
    # Calculate delta (percent change) for numeric metrics
    if isinstance(value, (int, float)) and isinstance(last_metric_values[metric], (int, float)) and last_metric_values[metric] != 0:
        if metric == "network" or metric == "storage":
            # For network and storage, calculate absolute change
            delta_percent = abs(value - last_metric_values[metric]) / max(value, last_metric_values[metric]) * 100
        else:
            # For CPU and memory, calculate percentage point change
            delta_percent = abs(value - last_metric_values[metric])
    else:
        delta_percent = float('inf')  # Always send non-numeric or zero-based values
        
    # Determine if we should send this metric
    should_send = False
    
    # Always send high priority metrics
    if priority == PRIORITY_HIGH:
        should_send = True
    # Send medium/low priority metrics based on schedule or significant change
    elif rounds_since_sent >= priority:
        should_send = True
    # Send if significant change detected
    elif delta_percent >= METRIC_DELTAS.get(metric, 0):
        should_send = True
        
    # Update last sent round if sending
    if should_send:
        last_metric_sent_round[metric] = node.cycle
    
    # Always update last value for future delta calculations
    last_metric_values[metric] = value
    
    logger.debug(f"METRIC_PRIORITY: metric={metric}, value={value:.2f}, priority={priority}, " +
                f"delta={delta_percent:.2f}%, rounds_since_sent={rounds_since_sent}, decision={'SEND' if should_send else 'SKIP'}")
    
    return should_send
@Singleton
class Node:
    def __init__(self):
        self.ip = None
        self.port = None
        self.cycle = None
        self.node_list = None
        self.data = None
        self.data_flow_per_round = None
        self.is_alive = None
        self.gossip_counter = None
        self.failure_counter = None
        self.failure_list = []
        self.monitoring_address = None
        self.database_address = None
        self.client_thread = None
        self.counter_thread = None
        self.data_flow_per_round = None
        self.session_to_monitoring = requests.Session()
        self.gossip_session = requests.Session()
        self.push_mode = None
        self.is_send_data_back = None
        self.metric_last_sent = {}
        self._state_save_thread = None
        self._tls_scheme = "http"  # switches to https when mTLS is on
        self.node_id = None  # persistent UUID, set during startup

    def configure_tls(self, tls_config):
        """Attach mTLS certs to our HTTP sessions so all gossip is encrypted and authenticated."""
        if tls_config and tls_config.enabled:
            tls_config.configure_session(self.gossip_session)
            tls_config.configure_session(self.session_to_monitoring)
            self._tls_scheme = "https"
            logger.info("Node sessions configured with mTLS")

    def load_or_create_node_id(self, state_dir=None):
        """
        Load or generate a persistent node identity.
        The UUID is saved to a .node_id file so it survives restarts.
        This means a node always has the same identity even if its IP changes.
        """
        # figure out where to store the ID file
        if state_dir:
            id_file = os.path.join(state_dir, ".node_id")
        else:
            id_file = ".node_id"

        # try loading an existing identity
        if os.path.exists(id_file):
            try:
                with open(id_file, "r") as f:
                    saved_id = f.read().strip()
                if saved_id:
                    self.node_id = saved_id
                    logger.info(f"Loaded node identity: {self.node_id}")
                    return self.node_id
            except Exception as e:
                logger.warning(f"Couldn't read node ID file: {e}")

        # first boot — generate a new UUID
        self.node_id = str(uuid.uuid4())
        try:
            os.makedirs(os.path.dirname(id_file) if os.path.dirname(id_file) else ".", exist_ok=True)
            with open(id_file, "w") as f:
                f.write(self.node_id)
            logger.info(f"Generated new node identity: {self.node_id}")
        except Exception as e:
            logger.warning(f"Couldn't persist node ID to disk: {e}")

        return self.node_id

    def set_params(self, ip, port, cycle, node_list, data, is_alive, gossip_counter, failure_counter,
                   monitoring_address, database_address, is_send_data_back, client_thread, counter_thread, data_flow_per_round, push_mode, client_port):
        self.ip = ip
        self.port = port
        self.monitoring_address = monitoring_address
        self.database_address = database_address
        self.cycle = cycle
        self.node_list = node_list
        self.data = data
        self.is_alive = is_alive
        self.gossip_counter = gossip_counter
        self.failure_counter = failure_counter
        self.client_thread = client_thread
        self.counter_thread = counter_thread
        self.data_flow_per_round = data_flow_per_round
        self.is_send_data_back = is_send_data_back
        self.push_mode = push_mode
        self.client_port = client_port

    def get_random_nodes(self, node_list, target_count):
        filtered_nodes = [node for node in node_list if node['ip'] != self.ip]
        # in case we have fewer peers than target_count, just use what we have
        if len(filtered_nodes) < target_count:
            return filtered_nodes
        return secrets.SystemRandom().sample(filtered_nodes, target_count)

    # ---- peer state persistence ----

    def save_peer_state(self, state_file=None):
        """Save current peer list to disk so we survive restarts."""
        if state_file is None:
            state_file = os.environ.get("PRIOMON_STATE_FILE", "peer_state.json")
        if not self.node_list:
            return
        try:
            with open(state_file, "w") as f:
                json.dump({
                    "peers": self.node_list,
                    "saved_at": time.time()
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save peer state: {e}")

    def start_state_saver(self, interval=30):
        """
        Background thread that periodically saves peer state.
        Runs every `interval` seconds while the node is alive.
        """
        import threading
        def _saver_loop():
            while self.is_alive:
                time.sleep(interval)
                self.save_peer_state()
                logger.debug(f"Saved peer state ({len(self.node_list or [])} peers)")
        self._state_save_thread = threading.Thread(target=_saver_loop, daemon=True)
        self._state_save_thread.start()

    def start_gossip_counter(self):
        while self.is_alive:
            self.gossip_counter += 1
            time.sleep(1)

    def start_gossiping(self, target_count, gossip_rate):
        print("Starting gossiping with target count: {} and gossip rate: {} and length of node list: {}".format(
            target_count, gossip_rate, len(self.node_list)),
            flush=True)
        while self.is_alive:
            if self.push_mode == "1":
                print("Pushing data", flush=True)
                if self.cycle % 10 == 0 and self.cycle != 0:
                    self.push_latest_data_and_delete_after_push()
            self.cycle += 1
            self.transmit(target_count)
            time.sleep(gossip_rate)

    # Transmit data to randomly selected nodes (target_count)
    def transmit(self, target_count):
        new_time_key = self.gossip_counter

        if self.data:
            latest_entry = max(self.data.keys(), key=int)
            latest_data = self.data[latest_entry].copy()
        else:
            latest_data = {}

        latest_data[f"{self.ip}:{self.port}"] = get_new_data()
        self.data[new_time_key] = latest_data

        random_nodes = self.get_random_nodes(self.node_list, target_count)

        for node in random_nodes:
            self.send_to_node(node, new_time_key)

    def prepare_metadata_and_own_fresh_data(self, time_key):
        own_key = f"{self.ip}:{self.port}"
        time_data = self.data[time_key]
        own_recent_data = time_data[own_key]

        filtered_own_data = self.get_filtered_data_by_priority(own_recent_data)

        metadata = {
            key: node_data['counter']
            for key, node_data in time_data.items()
            if key != own_key and 'counter' in node_data
        }

        # piggyback our peer list so it spreads through the network
        # this is how nodes learn about peers they haven't directly contacted
        peer_snapshot = [{"ip": p["ip"], "port": str(p["port"])} for p in self.node_list]

        return {'metadata': metadata, own_key: filtered_own_data, '_peers': peer_snapshot}

    def prepare_requested_data(self, time_key, requested_keys):
        requested_data = {}
        for key in requested_keys:
            requested_data[key] = self.data[time_key][key]
        return requested_data
    
    def update_own_data(self, updates, new_time_key):
        for u_key in updates:
            self.data_flow_per_round.setdefault(self.cycle, {})
            if u_key in self.data[new_time_key]:
                self.data_flow_per_round[self.cycle].setdefault('fd', 0)
                self.data_flow_per_round[self.cycle]['fd'] += 1
            else:
                self.data_flow_per_round[self.cycle].setdefault('nd', 0)
                self.data_flow_per_round[self.cycle].setdefault('fd', 0)
                self.data_flow_per_round[self.cycle]['nd'] += 1
                self.data_flow_per_round[self.cycle]['fd'] += 1
            self.data[new_time_key][u_key] = updates[u_key]

        pass
    def get_filtered_data_by_priority(self, full_data):
        """Filter metrics based on priority and round number"""
        if not hasattr(self, 'metric_last_sent'):
            self.metric_last_sent = {}
        
        filtered_data = full_data.copy()
        # not filtering if first cycle
        if self.cycle <= 1:
            for metric in METRIC_PRIORITIES:
                self.metric_last_sent[metric] = self.cycle
            return filtered_data
        
        if "appState" in filtered_data:
            app_state = filtered_data["appState"].copy()
            for metric, priority in METRIC_PRIORITIES.items():
                last_sent = self.metric_last_sent.get(metric, 0)
                if (self.cycle - last_sent) < priority:
                    # drop it entirely so the receiver's merge logic keeps
                    # whatever real value it already has for this metric
                    app_state.pop(metric, None)
                else:
                    # this metric is due — send it and update the timer
                    self.metric_last_sent[metric] = self.cycle
            
            filtered_data["appState"] = app_state
        
        return filtered_data


    def push_latest_data_and_delete_after_push(self):
        if self.data:
            latest_time_key = max(self.data.keys())
            latest_data = self.data[latest_time_key]
            to_send = self.data
            self.data = {latest_time_key: latest_data}
            to_push = {k: v for k, v in to_send.items() if k != latest_time_key}
            self.session_to_monitoring.post(
                '{}://{}:{}/push_data_to_database?ip={}&port={}&round={}'.format(self._tls_scheme, self.monitoring_address,self.client_port ,self.ip,
                                                                                 self.port,
                                                                                 self.cycle), json=to_push)
    
    def send_to_node(self, n, new_time_key):
        data = self.prepare_metadata_and_own_fresh_data(new_time_key)
        try:
            r_metadata_and_updated = self.gossip_session.post(
                self._tls_scheme + '://' + n["ip"] + ':' + n["port"] + '/receive_metadata',
                json=data, timeout=5)

            requested_keys = r_metadata_and_updated.json()['requested_keys']
            requested_data = self.prepare_requested_data(new_time_key, requested_keys)
            response = self.gossip_session.get(
                self._tls_scheme + '://' + n["ip"] + ':' + n["port"] + '/receive_message?inc_round={}'.format(self.cycle),
                json=requested_data, timeout=5)
            self.update_own_data(r_metadata_and_updated.json()['updates'], new_time_key)
            if response.status_code == 500:
                self.update_failure_data(new_time_key, n)
            else:
                self.reset_failure_data(new_time_key, n["ip"] + ':' + n["port"])
        except Exception as e:
            logger.warning(f"Error communicating with node {n['ip']}:{n['port']} - {e}")
            self.update_failure_data(new_time_key, n)

    def update_failure_data(self, new_time_key, n):
        target_key = f"{n['ip']}:{n['port']}"
        own_key = f"{self.ip}:{self.port}"
        
        # initialize hbState if it doesn't exist for this target
        target_data = self.data[new_time_key].setdefault(target_key, {})
        hb_state = target_data.setdefault("hbState", {
            "failureList": [], 
            "failureCount": 0, 
            "nodeAlive": True, 
            "status": "alive"
        })

        # add ourselves to the suspect/failure list if we aren't already there
        if own_key not in hb_state["failureList"]:
            hb_state["failureList"].append(own_key)
            hb_state["failureCount"] = len(hb_state["failureList"])
            
            f_count = hb_state["failureCount"]
            if f_count < 3:
                # Swim-style suspect state — we suspect it's dead, but wait for corroboration
                hb_state["status"] = "suspect"
                logger.debug(f"Node {target_key} is SUSPECT (failed for {own_key}, total {f_count})")
            else:
                # 3 nodes have corroborated the failure. Mark as DEAD.
                hb_state["status"] = "dead"
                hb_state["nodeAlive"] = False
                logger.info(f"Node {target_key} declared DEAD (corroborated by {f_count} peers)")
                self.delete_node_from_nodelist(target_key)
                # Note: we do NOT delete it from self.data, so the "dead" tombstone 
                # can still be gossiped and spread to other nodes.

    def delete_node_from_nodelist(self, key_to_delete):
        # node_list is a list of dicts with 'ip' and 'port', not a dict
        self.node_list = [n for n in self.node_list
                          if n["ip"] + ":" + n["port"] != key_to_delete]

    def reset_failure_data(self, new_time_key, ip_key):
        if ip_key in self.data[new_time_key]:
            hb_state = self.data[new_time_key][ip_key].setdefault("hbState", {})
            hb_state["failureCount"] = 0
            hb_state["nodeAlive"] = True
            hb_state["status"] = "alive"
            hb_state["failureList"] = []

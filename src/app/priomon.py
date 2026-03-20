import time
import os
import socket
import sys

from flask import Flask, request
from node import Node, METRIC_PRIORITIES, METRIC_DELTAS
import threading
import logging
import json
import requests as http_requests  # renamed to avoid clash with flask.request

try:
    import yaml
except ImportError:
    yaml = None  # only needed for standalone mode

try:
    from discovery import discover_peers, announce_to_peers, save_state
except ImportError:
    discover_peers = None  # only available when discovery.py is present

try:
    from metric_store import get_store, start_metric_snapshotter, extract_cluster_metrics
except ImportError:
    get_store = None  # metric persistence not available

try:
    from tls_utils import TLSConfig, load_tls_from_config, set_global_tls, get_global_tls
except ImportError:
    TLSConfig = None  # TLS module not available
    load_tls_from_config = None

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("priomon")
gossip = Flask(__name__)


def _merge_incoming_peers(node, incoming_peers):
    """
    Merge peers from a gossip payload into our node_list.
    This is how peer lists propagate through the network — if node3 joins
    via node1, node2 will learn about node3 on its next gossip exchange.
    """
    if not incoming_peers or not node.node_list:
        return

    # build a set of known peer keys for fast lookup
    known = {f"{p['ip']}:{p['port']}" for p in node.node_list}
    added = 0

    for peer in incoming_peers:
        peer_key = f"{peer['ip']}:{peer['port']}"
        if peer_key not in known:
            node.node_list.append({"ip": peer["ip"], "port": str(peer["port"])})
            known.add(peer_key)
            added += 1
            logger.info(f"Discovered new peer via gossip: {peer_key}")

    # persist updated peer list if we found new ones
    if added > 0:
        try:
            from discovery import save_state
            state_file = os.environ.get("PRIOMON_STATE_FILE", "peer_state.json")
            save_state(node.node_list, state_file)
        except ImportError:
            pass

@gossip.route('/receive_message', methods=['GET'])
def receive_message():
    if not Node.instance().is_alive:
        # reset_node()
        return "Dead Node", 500
    compare_and_update_node_data(request.get_json())
    return "OK"


@gossip.route('/metadata', methods=['GET'])
def get_metadata():
    if not Node.instance().is_alive:
        # reset_node()
        return "Dead Node", 500
    node = Node.instance()
    if not node.data:
        return json.dumps({})
    latest_entry = max(node.data.keys(), key=int)
    metadata = {}
    for key in node.data[latest_entry]:
        if 'counter' in node.data[latest_entry][key]:
            metadata[key] = {'counter': node.data[latest_entry][key]['counter'],
                             'digest': node.data[latest_entry][key]['digest']}
    return json.dumps(metadata)


def compare_node_data_with_metadata(data):
    # metadata form: {ip1: counter1, ip2: counter2, .....}
    # to_send = {'metadata': metadata, key:own_recent_data}
    node = Node.instance()
    metadata = data['metadata']
    # skip internal keys when finding the sender's data entry
    sender_key = next(key for key in data if key not in ('metadata', '_peers'))
    sender_data = data[sender_key]

    # merge any new peers the sender knows about into our own list
    _merge_incoming_peers(node, data.get('_peers', []))
    if len(node.data) == 0:
        # node doesnt store any data yet
        return metadata.keys()
    latest_entry = max(node.data.keys(), key=int)
    all_keys = set().union(node.data[latest_entry].keys(), metadata.keys())
    all_keys.discard(sender_key)
    node.data_flow_per_round.setdefault(node.cycle, {})
    if sender_key in node.data[latest_entry]:
        node.data_flow_per_round[node.cycle].setdefault('fd', 0)
        node.data_flow_per_round[node.cycle]['fd'] += 1
    else:
        node.data_flow_per_round[node.cycle].setdefault('nd', 0)
        node.data_flow_per_round[node.cycle].setdefault('fd', 0)
        node.data_flow_per_round[node.cycle]['nd'] += 1
        node.data_flow_per_round[node.cycle]['fd'] += 1

    node.data[latest_entry][sender_key] = sender_data

    # lists of ips who reclaim that this node is dead
    ips_to_update = []
    data_to_send = {}
    for key in all_keys:
        # both nodes store the data if IP
        if key in node.data[latest_entry] and key in metadata:
            # node doesnt store the key or counter of metadata > counter of noda.data
            if ('counter' not in node.data[latest_entry][key]) or (
                    float(metadata[key]) > float(node.data[latest_entry][key]['counter'])):
                ips_to_update.append(key)
            else:
                data_to_send[key] = node.data[latest_entry][key]
        # metadata doesnt store the data of IP
        elif key in node.data[latest_entry] and key not in metadata:
            data_to_send[key] = node.data[latest_entry][key]
        # node doesnt store the data of IP
        else:
            ips_to_update.append(key)
    requests_updates = {'requested_keys': ips_to_update, 'updates': data_to_send}
    return requests_updates


@gossip.route('/receive_metadata', methods=['POST'])
def receive_metadata():
    if not Node.instance().is_alive:
        # reset_node()
        return "Dead Node", 500
    data = compare_node_data_with_metadata(request.get_json())
    return data


@gossip.route('/reset_node')
def reset_node():
    node = Node.instance()
    node.is_alive = False
    node.client_thread.join()
    node.counter_thread.join()
    node.set_params(None, None, 0, None, {}, False, 0, 0, None, None,
                    is_send_data_back=None, client_thread=None,
                    counter_thread=None, data_flow_per_round={},
                    push_mode=0, client_port=None)
    return "OK"


@gossip.route('/stop_node')
def stop_node():
    node = Node.instance()
    node.is_alive = False
    node.client_thread.join()
    node.counter_thread.join()
    return "OK"


def compare_and_update_node_data(inc_data):
    node = Node.instance()
    new_time_key = node.gossip_counter
    latest_entry = max(node.data.keys(), key=int) if len(node.data) > 0 else new_time_key
    new_data = {k: v for k, v in inc_data.items() if k != '_peers'}
    all_keys = set().union(node.data[latest_entry].keys(), new_data.keys())
    inc_round = int(request.args.get('inc_round'))
    # received messages ['rm'] per round
    node.data_flow_per_round.setdefault(node.cycle, {}).setdefault('rm', 0)
    node.data_flow_per_round[node.cycle]['rm'] += 1

    # lists of ips who reclaim that this node is dead
    list1 = []
    list2 = []
    for key in all_keys:
        # both nodes store the data if IP
        if key in node.data[latest_entry] and key in new_data:

            # Handle partial metric updates - preserve existing metrics if not in incoming data
            if 'appState' in new_data[key] and 'appState' in node.data[latest_entry][key]:
                # Get lists of metrics
                existing_metrics = set(node.data[latest_entry][key]['appState'].keys())
                incoming_metrics = set(new_data[key]['appState'].keys())
                
                # For any metric in existing but not in incoming, copy from existing
                # (this is the main path since we now drop filtered metrics entirely)
                for metric in existing_metrics - incoming_metrics:
                    existing_val = node.data[latest_entry][key]['appState'][metric]
                    if existing_val != "not_updated":
                        new_data[key]['appState'][metric] = existing_val

                # also fix any lingering "not_updated" sentinel values in incoming data
                # (shouldn't happen anymore after the node.py fix, but just in case)
                for metric in incoming_metrics:
                    if new_data[key]['appState'][metric] == "not_updated":
                        if metric in node.data[latest_entry][key]['appState']:
                            real_val = node.data[latest_entry][key]['appState'][metric]
                            if real_val != "not_updated":
                                new_data[key]['appState'][metric] = real_val
            
            if 'metric_sent_flags' in new_data[key]:
                sent_count = sum(1 for v in new_data[key]['metric_sent_flags'].values() if v)
                filtered_count = sum(1 for v in new_data[key]['metric_sent_flags'].values() if not v)
        
                # Add to round statistics
                node.data_flow_per_round[node.cycle].setdefault('metrics_sent', 0)
                node.data_flow_per_round[node.cycle].setdefault('metrics_filtered', 0)
                node.data_flow_per_round[node.cycle]['metrics_sent'] += sent_count
                node.data_flow_per_round[node.cycle]['metrics_filtered'] += filtered_count
                
            list1 = node.data[latest_entry][key]["hbState"]["failureList"]
            list2 = new_data[key]["hbState"]["failureList"]
            if ('counter' in new_data[key] and 'counter' in node.data[latest_entry][key] \
                and float(new_data[key]['counter']) > float(node.data[latest_entry][key]['counter'])) or \
                    ('counter' in new_data[key] and 'counter' not in node.data[latest_entry][key]):
                node.data.setdefault(new_time_key, {})[key] = new_data[key]

                # fresh data per round ['fd'] per round, fresh data describes data that is updated or added in this node
                node.data_flow_per_round[node.cycle].setdefault('fd', 0)
                node.data_flow_per_round[node.cycle]['fd'] += 1
            else:
                node.data.setdefault(new_time_key, {})[key] = node.data[latest_entry][key]
        # inc data doesnt store the data of IP
        elif key in node.data[latest_entry] and key not in new_data:
            node.data.setdefault(new_time_key, {})[key] = node.data[latest_entry][key]
        # node doesnt store the data of IP
        else:
            node.data.setdefault(new_time_key, {})[key] = new_data[key]
            # node.data[key] = new_data[key]
            # new data per round ['nd'] per round (nd is data from an unknown node -> fd = nd)
            node.data_flow_per_round[node.cycle].setdefault('nd', 0)
            node.data_flow_per_round[node.cycle].setdefault('fd', 0)
            node.data_flow_per_round[node.cycle]['nd'] += 1
            node.data_flow_per_round[node.cycle]['fd'] += 1
        # only for deleted nodes
        if key in node.data[latest_entry] and key in new_data:
            merged_failure_list = list(set(list1).union(set(list2)))
            node.data[new_time_key][key]["hbState"]["failureList"] = merged_failure_list
    # TODO update Database
    # send both data and data_flow_per_round to monitor
    # TODO: Save latest data snapshot with key = self.gossip_counter in data
    if new_time_key not in node.data:
        print("No new data to send", flush=True)
        data_to_send_to_monitor = node.data[latest_entry]
    else:
        data_to_send_to_monitor = node.data[new_time_key]
    to_send = {'data': data_to_send_to_monitor, 'data_flow_per_round': node.data_flow_per_round[node.cycle]}
    # TODO: Session here
    if node.is_send_data_back == "1":
        scheme = node._tls_scheme if hasattr(node, '_tls_scheme') else 'http'
        node.session_to_monitoring.post(
            '{}://{}:{}/receive_node_data?ip={}&port={}&round={}'.format(scheme, node.monitoring_address,node.client_port, node.ip,
                                                                             node.port,
                                                                             inc_round), json=to_send)


@gossip.route('/start_node', methods=['POST'])
def start_node():
    init_data = request.get_json()
    monitoring_address = init_data["monitoring_address"]
    client_port = init_data["client_port"]
    database_address = init_data["database_address"]
    node_list = init_data["node_list"]
    target_count = init_data["target_count"]
    gossip_rate = init_data["gossip_rate"]
    node_ip = init_data["node_ip"]
    is_send_data_back = init_data["is_send_data_back"]
    push_mode = init_data["push_mode"]
    node = Node.instance()
    time.sleep(10)
    client_thread = threading.Thread(target=node.start_gossiping, args=(target_count, gossip_rate))
    counter_thread = threading.Thread(target=node.start_gossip_counter)
    node.set_params(node_ip,
                    request.headers.get('Host').split(':')[1], 0,
                    node_list, {}, True, 0, 0, monitoring_address, database_address,
                    is_send_data_back=is_send_data_back,
                    client_thread=client_thread, counter_thread=counter_thread, data_flow_per_round={},
                    push_mode=push_mode, client_port=client_port)
    client_thread.start()
    counter_thread.start()

    # configure metric priorities and deltas if the orchestrator sent them
    if 'metric_priorities' in init_data:
        METRIC_PRIORITIES.update(init_data['metric_priorities'])
    
    if 'metric_deltas' in init_data:
        METRIC_DELTAS.update(init_data['metric_deltas'])

    return "OK"


@gossip.route('/register_new_node', methods=['POST'])
def register_new_node():
    Node.instance().node_list.append(request.get_json())
    return "OK"


@gossip.route('/get_data_from_node', methods=['GET'])
def get_data_from_node():
    return Node.instance().data


@gossip.route('/get_recent_data_from_node', methods=['GET'])
def get_recent_data_from_node():
    data = Node.instance().data
    latest_entry = max(data.keys(), key=int)
    return data[latest_entry]


@gossip.route('/get_nodelist_from_node', methods=['GET'])
def get_nodelist_from_node():
    return json.dumps(Node.instance().node_list)


@gossip.route('/hello_world', methods=['GET'])
def get_hello_from_node():
    return "Hello from gossip agent!"


# ---- peer discovery endpoints (Phase 1: decentralized ops) ----

@gossip.route('/peers', methods=['GET'])
def get_peers():
    """Return the current peer list so new nodes can bootstrap."""
    node = Node.instance()
    if node.node_list:
        return json.dumps(node.node_list)
    return json.dumps([])


@gossip.route('/join', methods=['POST'])
def join_cluster():
    """
    A new node is announcing itself. Add it to our peer list
    if we don't already know about it.
    """
    node = Node.instance()
    new_peer = request.get_json()
    new_ip = new_peer.get("ip")
    new_port = str(new_peer.get("port"))

    if not new_ip or not new_port:
        return "Missing ip or port", 400

    # check if we already have this peer
    already_known = any(
        p["ip"] == new_ip and str(p["port"]) == new_port
        for p in (node.node_list or [])
    )
    if not already_known:
        if node.node_list is None:
            node.node_list = []
        node.node_list.append({"ip": new_ip, "port": new_port})
        logger.info(f"New peer joined: {new_ip}:{new_port} (total: {len(node.node_list)})")

        # persist updated peer list to disk
        if save_state:
            state_file = os.environ.get("PRIOMON_STATE_FILE", "peer_state.json")
            save_state(node.node_list, state_file)

    return "OK"


@gossip.route('/health', methods=['GET'])
def health_check():
    """Simple health endpoint for load balancers / liveness probes."""
    node = Node.instance()
    
    # Check if we have determined our own status in hbState, otherwise default to node.is_alive
    try:
        latest_key = max(node.data.keys(), key=int)
        own_hb = node.data[latest_key].get(f"{node.ip}:{node.port}", {}).get("hbState", {})
        status = own_hb.get("status")
    except (ValueError, KeyError):
        status = None
        
    if not status:
        status = "alive" if node.is_alive else "dead"
        
    return json.dumps({
        "status": status,
        "nodeAlive": node.is_alive,
        "node_id": node.node_id or "unknown",
        "peers": len(node.node_list) if node.node_list else 0,
        "cycle": node.cycle or 0
    })


# Add new endpoint

@gossip.route('/metrics_priority_stats', methods=['GET'])
def get_metrics_priority_stats():
    """Get statistics about priority-based metric filtering"""
    node = Node.instance()
    
    # Calculate stats if node has data
    if len(node.data) == 0:
        return json.dumps({"error": "No data available"})
    
    # Get sent/filtered counts
    metrics_sent = node.metrics_sent_count if hasattr(node, 'metrics_sent_count') else 0
    metrics_filtered = node.metrics_filtered_count if hasattr(node, 'metrics_filtered_count') else 0
    
    # Get per-round metrics stats
    per_round_stats = {}
    for round_num, stats in node.data_flow_per_round.items():
        per_round_stats[round_num] = {
            'metrics_sent': stats.get('metrics_sent', 0),
            'metrics_filtered': stats.get('metrics_filtered', 0)
        }
    
    # Return statistics
    return json.dumps({
        'total_metrics_sent': metrics_sent,
        'total_metrics_filtered': metrics_filtered,
        'bandwidth_savings_percent': round(100 * metrics_filtered / (metrics_sent + metrics_filtered) if (metrics_sent + metrics_filtered) > 0 else 0, 2),
        'per_round_stats': per_round_stats,
        'priorities': {k: v for k, v in METRIC_PRIORITIES.items()},
        'deltas': {k: v for k, v in METRIC_DELTAS.items()}
    })


# ---- cluster-wide metrics API (the actually useful stuff) ----

@gossip.route('/cluster/metrics', methods=['GET'])
def cluster_metrics():
    """
    Clean, human-readable view of all nodes' latest metrics.
    This is what you'd actually use to monitor your cluster.
    """
    node = Node.instance()
    if not node.data:
        return json.dumps({"error": "No gossip data yet — node is still starting up"})

    own_key = f"{node.ip}:{node.port}"

    # extract clean metrics from gossip state
    if extract_cluster_metrics:
        cluster = extract_cluster_metrics(node)
    else:
        # fallback if metric_store isn't available
        cluster = _extract_metrics_fallback(node)

    return json.dumps({
        "timestamp": time.time(),
        "reporting_node": own_key,
        "total_nodes": len(cluster),
        "cycle": node.cycle,
        "nodes": cluster
    }, indent=2)


@gossip.route('/cluster/metrics/<node_key>', methods=['GET'])
def cluster_node_metrics(node_key):
    """
    Get metrics for a specific node as seen by this node.
    node_key format: ip:port (e.g. 172.18.0.2:5000)
    """
    node = Node.instance()
    if not node.data:
        return json.dumps({"error": "No gossip data yet"}), 503

    if extract_cluster_metrics:
        cluster = extract_cluster_metrics(node)
    else:
        cluster = _extract_metrics_fallback(node)

    if node_key not in cluster:
        return json.dumps({"error": f"Node {node_key} not found in cluster view"}), 404

    return json.dumps({
        "node": node_key,
        "metrics": cluster[node_key],
        "as_seen_by": f"{node.ip}:{node.port}",
        "timestamp": time.time()
    }, indent=2)


@gossip.route('/cluster/query/<path:target_key>', methods=['GET'])
def cluster_query(target_key):
    """
    Quorum-based verified query for a target node's data.
    Contacts multiple peers, checks for consensus, then returns the verified result.
    This runs the query FROM this node — so you're using the gossip network
    to verify data instead of trusting a single source.

    target_key: ip:port of the node you want to query about
    Optional query params:
        quorum_size: number of peers to check (default: 3 or len(peers) if smaller)
        max_retries: max attempts (default: 10)
    """
    node = Node.instance()
    if not node.node_list:
        return json.dumps({"error": "No peers available"}), 503

    quorum_size = int(request.args.get('quorum_size', min(3, len(node.node_list))))
    max_retries = int(request.args.get('max_retries', 10))

    # run the quorum query using our own peer list
    import random

    for attempt in range(max_retries):
        # pick random peers to form the quorum
        available_peers = [p for p in node.node_list if f"{p['ip']}:{p['port']}" != f"{node.ip}:{node.port}"]
        if len(available_peers) < quorum_size:
            quorum_size = len(available_peers)
        if quorum_size == 0:
            return json.dumps({"error": "Not enough peers for quorum"}), 503

        quorum_nodes = random.sample(available_peers, quorum_size)
        metadatas = {}
        total_messages = 0

        # ask each quorum member for their metadata about the target
        tls_kwargs = get_global_tls().get_request_kwargs() if get_global_tls else {}
        scheme = get_global_tls().scheme if get_global_tls else "http"
        for peer in quorum_nodes:
            total_messages += 1
            try:
                resp = http_requests.get(
                    f"{scheme}://{peer['ip']}:{peer['port']}/metadata",
                    timeout=5,
                    **tls_kwargs
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if target_key in data:
                        metadatas[f"{peer['ip']}:{peer['port']}"] = data[target_key]
            except Exception as e:
                logger.warning(f"Quorum peer {peer['ip']}:{peer['port']} unreachable: {e}")

        # check consensus
        if len(metadatas) >= quorum_size:
            counters = [d.get("counter") for d in metadatas.values()]
            digests = [d.get("digest") for d in metadatas.values()]

            counter_consensus = len(set(counters)) == 1
            digest_consensus = len(set(digests)) == 1

            if counter_consensus and digest_consensus:
                # consensus reached — fetch the actual data from one of the quorum members
                fetch_peer = quorum_nodes[0]
                try:
                    data_resp = http_requests.get(
                        f"{scheme}://{fetch_peer['ip']}:{fetch_peer['port']}/get_recent_data_from_node",
                        timeout=5,
                        **tls_kwargs
                    )
                    if data_resp.status_code == 200:
                        full_data = data_resp.json()
                        target_data = full_data.get(target_key, {})

                        # extract the clean metrics from the verified data
                        app_state = target_data.get("appState", {})
                        hb_state = target_data.get("hbState", {})

                        return json.dumps({
                            "status": "verified",
                            "target": target_key,
                            "quorum_size": quorum_size,
                            "attempts": attempt + 1,
                            "total_messages": total_messages,
                            "consensus": {
                                "counter": counters[0],
                                "digest": digests[0]
                            },
                            "metrics": {
                                "cpu": _safe_float(app_state.get("cpu")),
                                "memory": _safe_float(app_state.get("memory")),
                                "network": _safe_float(app_state.get("network")),
                                "storage": _safe_float(app_state.get("storage"))
                            },
                            "health": {
                                "alive": hb_state.get("nodeAlive", False),
                                "failure_count": hb_state.get("failureCount", 0),
                                "last_heartbeat": hb_state.get("timestamp")
                            },
                            "verified_by": list(metadatas.keys()),
                            "timestamp": time.time()
                        }, indent=2)
                except Exception as e:
                    logger.error(f"Failed to fetch data after consensus: {e}")

        # no consensus yet, back off and retry
        time.sleep(0.3)

    return json.dumps({
        "status": "failed",
        "target": target_key,
        "error": f"Could not reach quorum consensus after {max_retries} attempts",
        "quorum_size": quorum_size,
        "attempts": max_retries
    }), 503


@gossip.route('/cluster/history', methods=['GET'])
def cluster_history():
    """
    Query persisted metric history from local SQLite.
    Optional query params:
        node: specific node key to filter (e.g. 172.18.0.2:5000)
        minutes: how far back to look (default: 10)
        limit: max rows (default: 200)
    """
    if get_store is None:
        return json.dumps({"error": "Metric store not available"}), 503

    store = get_store()
    node_key = request.args.get('node')
    minutes = int(request.args.get('minutes', 10))
    limit = int(request.args.get('limit', 200))

    history = store.get_history(node_key=node_key, minutes=minutes, limit=limit)

    return json.dumps({
        "count": len(history),
        "filter_node": node_key,
        "minutes": minutes,
        "entries": history
    }, indent=2)


@gossip.route('/cluster/history/latest', methods=['GET'])
def cluster_history_latest():
    """Get the most recent persisted metrics for each node."""
    if get_store is None:
        return json.dumps({"error": "Metric store not available"}), 503

    store = get_store()
    latest = store.get_latest_per_node()

    return json.dumps({
        "count": len(latest),
        "nodes": latest,
        "timestamp": time.time()
    }, indent=2)


def _safe_float(val):
    """Try to parse a value as float, return None if it can't be done."""
    if val is None or val == "not_updated":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_metrics_fallback(node):
    """Fallback metric extraction when metric_store isn't imported."""
    if not node.data:
        return {}
    try:
        latest_key = max(node.data.keys(), key=int)
        latest_data = node.data[latest_key]
    except (ValueError, KeyError):
        return {}

    cluster = {}
    for nk, nd in latest_data.items():
        if not isinstance(nd, dict):
            continue
        app_state = nd.get("appState", {})
        hb = nd.get("hbState", {})
        cluster[nk] = {
            "cpu": _safe_float(app_state.get("cpu")),
            "memory": _safe_float(app_state.get("memory")),
            "network": _safe_float(app_state.get("network")),
            "storage": _safe_float(app_state.get("storage")),
            "status": hb.get("status") or ("alive" if hb.get("nodeAlive", True) else "dead"),
            "alive": hb.get("nodeAlive", True),
            "cycle": nd.get("cycle"),
            "gossip_counter": nd.get("counter"),
            "node_id": nd.get("nodeState", {}).get("id", "")
        }
    return cluster


def start_standalone(config_path=None):
    """
    Boot the node without an orchestrator.
    Reads config from yaml, discovers peers via seeds, and starts gossiping.
    """
    if yaml is None:
        print("Standalone mode requires PyYAML. Install it: pip install pyyaml")
        sys.exit(1)

    if config_path is None:
        config_path = os.environ.get("PRIOMON_CONFIG", "node_config.yaml")

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}")
        print("Copy node_config.yaml and fill in your seed addresses.")
        sys.exit(1)

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    # pull config values
    host = cfg.get("node", {}).get("host", "0.0.0.0")
    port = int(cfg.get("node", {}).get("port", 5000))
    target_count = int(cfg.get("gossip", {}).get("target_count", 2))
    gossip_rate = float(cfg.get("gossip", {}).get("rate", 0.5))
    seeds = cfg.get("seeds", [])
    state_file = cfg.get("state_file", "peer_state.json")

    # apply metric priorities and deltas from config
    if "metric_priorities" in cfg:
        METRIC_PRIORITIES.update(cfg["metric_priorities"])
    if "metric_deltas" in cfg:
        METRIC_DELTAS.update(cfg["metric_deltas"])

    # monitoring settings (optional — empty means fully decentralized)
    mon_cfg = cfg.get("monitoring", {})
    mon_address = mon_cfg.get("address", "")
    mon_port = mon_cfg.get("port", 4000)
    send_data_back = "1" if mon_cfg.get("send_data_back", False) else "0"
    push_mode = "1" if cfg.get("push_mode", False) else "0"

    # figure out our own IP
    own_ip = _get_own_ip()
    logger.info(f"Starting standalone node at {own_ip}:{port}")

    # load TLS config FIRST — discovery needs it to talk to seeds over HTTPS
    ssl_ctx = None
    if load_tls_from_config:
        tls_config = load_tls_from_config(cfg)
        set_global_tls(tls_config)
        ssl_ctx = tls_config.create_server_ssl_context()

    # discover peers from seeds or saved state
    os.environ["PRIOMON_STATE_FILE"] = state_file
    peer_list = discover_peers(seeds, own_ip, port, state_file)
    logger.info(f"Discovered {len(peer_list)} peers")

    # set up the node singleton
    node = Node.instance()
    client_thread = threading.Thread(
        target=node.start_gossiping, args=(target_count, gossip_rate)
    )
    counter_thread = threading.Thread(target=node.start_gossip_counter)

    node.set_params(
        own_ip, str(port), 0,
        peer_list, {}, True, 0, 0,
        mon_address if mon_address else own_ip,
        "",  # database_address not used in standalone
        is_send_data_back=send_data_back,
        client_thread=client_thread,
        counter_thread=counter_thread,
        data_flow_per_round={},
        push_mode=push_mode,
        client_port=str(mon_port)
    )

    # wire up mTLS on the node's gossip sessions
    if ssl_ctx is not None:
        node.configure_tls(tls_config)

    # generate or load a persistent node identity
    # the .node_id file lives in the same dir as peer_state.json
    state_dir = os.path.dirname(state_file) if os.path.dirname(state_file) else None
    node.load_or_create_node_id(state_dir=state_dir)

    # announce ourselves to all known peers
    announce_to_peers(peer_list, own_ip, port)

    # start gossip threads (give Flask a moment to bind first)
    def _delayed_gossip_start():
        time.sleep(3)  # wait for Flask to be ready
        logger.info("Gossip threads starting...")
        client_thread.start()
        counter_thread.start()

        # kick off local metric persistence after gossip is running
        if start_metric_snapshotter:
            time.sleep(2)  # let a few gossip rounds happen first
            snapshot_interval = float(cfg.get("metric_history", {}).get("snapshot_interval", 5))
            start_metric_snapshotter(node, interval=snapshot_interval)

        # start the peer state saver too
        node.start_state_saver(interval=30)

    threading.Thread(target=_delayed_gossip_start, daemon=True).start()

    # start Flask (with SSL context if mTLS is configured)
    gossip.run(host=host, port=port, debug=False, threaded=True, ssl_context=ssl_ctx)


def _get_own_ip():
    """Best-effort to get a routable IP for this machine."""
    # PRIOMON_IP env var takes priority (useful in Docker / K8s)
    env_ip = os.environ.get("PRIOMON_IP")
    if env_ip:
        return env_ip
    try:
        # connect to a public DNS to figure out which interface is default
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    # check if we should run standalone or in orchestrated mode
    if "--standalone" in sys.argv or os.environ.get("PRIOMON_STANDALONE") == "1":
        config_path = None
        # allow passing config path as argument: --config path/to/config.yaml
        if "--config" in sys.argv:
            idx = sys.argv.index("--config")
            if idx + 1 < len(sys.argv):
                config_path = sys.argv[idx + 1]
        start_standalone(config_path)
    else:
        # original orchestrated mode — just start Flask and wait for /start_node
        gossip.run(host='0.0.0.0', debug=True, threaded=True)
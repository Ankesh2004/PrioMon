import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
import concurrent.futures
import configparser
import json
import random
import sqlite3
import time
import docker
import socket
import requests
import traceback
import queue
import threading
from flask import Flask, request
from joblib import Parallel, delayed
import connector_db as dbConnector
import logging
from sqlite3 import Connection
from src import query_client

monitoring_priomon = Flask(__name__)
parser = configparser.ConfigParser()
parser.read('../config.ini')
try:
    docker_client = docker.client.from_env()
except Exception as e:
    print("Error docker: {}".format(e))
    print("trace: {}".format(traceback.format_exc()))
    exit(1)
experiment = None

def execute_queries_from_queue():
    while True:
        try:
            conn = sqlite3.connect('priomonDB.db', check_same_thread=False)
            cursor = conn.cursor()
            query_data = experiment.query_queue.get()
            if query_data is None:
                break  # Signal to exit the thread
            query, parameters = query_data
            cursor.execute(query, parameters)
            conn.commit()
            experiment.query_queue.task_done()
        except Exception as e:
            print("Error db: {}".format(e))
            print("trace: {}".format(traceback.format_exc()))
            continue

def get_target_count(node_count, target_count_range):
    new_range = []
    for i in target_count_range:
        if i <= node_count:
            new_range.append(i)
    return new_range

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def make_save_able_dic_from_run(run):
    save_able_dic = {"node_count": run.node_count, "target_count": run.target_count, "gossip_rate": run.gossip_rate,
                     "start_time": run.start_time, "convergence_time": run.convergence_time,
                     "convergence_message_count": run.convergence_message_count,
                     "convergence_round": run.convergence_round}
    return save_able_dic

def save_run_to_database(run):
    run.db_id = experiment.db.insert_into_run(experiment.db_id, run.run, run.node_count, run.gossip_rate,
                                              run.target_count)

def save_converged_run_to_database(run):
    experiment.db.insert_into_converged_run(run.db_id, run.convergence_round, run.convergence_message_count,
                                            run.convergence_time)

class Run:
    def __init__(self, node_count, gossip_rate, target_count, run, node_list=None, db_collection=None):
        self.db_id = -1
        self.data_entries_per_ip = {}
        self.node_list = node_list or []
        self.node_count = node_count
        self.convergence_round = -1
        self.convergence_message_count = -1
        self.message_count = 0
        self.start_time = None
        self.convergence_time = None
        self.is_converged = False
        self.gossip_rate = gossip_rate
        self.target_count = target_count
        self.run = run
        self.db_collection = db_collection
        self.max_round_is_reached = False
        self.ip_per_ic = {}
        self.stopped_nodes = {}

    def set_db_id(self, param):
        self.db_id = param

class Experiment:
    def __init__(self, node_count_range, gossip_rate_range, target_count_range, run_count, monitoring_address_ip,
                 is_send_data_back, push_mode):
        self.db_id = -1
        self.node_count_range = node_count_range
        self.gossip_rate_range = gossip_rate_range
        self.target_count_range = target_count_range
        self.run_count = run_count
        self.runs = []
        self.monitoring_address_ip = monitoring_address_ip
        self.db = dbConnector.PriomonDB()
        self.query_queue = queue.Queue()
        self.query_thread = None
        self.is_send_data_back = is_send_data_back
        self.push_mode = push_mode
        self.NodeDB = dbConnector.NodeDB()

    def set_db_id(self, param):
        self.db_id = param

def spawn_node(index, node_list, client, custom_network_name):
    try:
        new_node = docker_client.containers.run("priomonv1", auto_remove=True, detach=True,
                                                network_mode=custom_network_name,
                                                ports={'5000': node_list[index]["port"]})
    except Exception as e:
        print("Node not spawned: {}".format(e))
        print("trace: {}".format(traceback.format_exc()))
        node_list[index]["port"] = get_free_port()
        spawn_node(index, node_list, client, custom_network_name)
    else:
        node_details = client.containers.get(new_node.id)
        node_list[index] = {"id": node_details.id,
                            "ip": node_details.attrs['NetworkSettings']['Networks']['test']['IPAddress'],
                            "port": node_details.attrs['NetworkSettings']['Ports']['5000/tcp'][0]['HostPort']}

def spawn_multiple_nodes(run):
    network_name = "test"
    from_index = 0
    if run.node_list is None:
        run.node_list = [None] * run.node_count
    elif len(run.node_list) == run.node_count:
        return  # Nodes are already spawned
    else:
        from_index = len(run.node_list)
        run.node_list = run.node_list + [None] * (run.node_count - len(run.node_list))
    client = docker.DockerClient()
    # TODO: free ports on i
    for i in range(from_index, run.node_count):
        run.node_list[i] = {}
        run.node_list[i]["port"] = get_free_port()
    Parallel(n_jobs=-1, prefer="threads")(
        delayed(spawn_node)(i, run.node_list, client, network_name) for i in range(from_index, run.node_count))

def nodes_are_ready(run):
    for i in range(0, run.node_count):
        if docker_client.containers.get(run.node_list[i]['id']).status != "running":
            return False
        run.node_list[i]["is_alive"] = True
    return True

def restart_node(docker_id):
    try:
        docker_client.containers.get(docker_id).restart()
    except Exception as e:
        print("An error occurred while restarting the container: {}".format(e))

def reset_node(ip, port, docker_id):
    try:
        time.sleep(random.uniform(0.01, 0.05))
        requests.get("http://{}:{}/reset_node".format(ip, port), timeout=30)
    except Exception as e:
        print("An error occurred while sending the request: {}".format(e))
        restart_node(docker_id)

@monitoring_priomon.route('/delete_nodes', methods=['GET'])
def delete_all_nodes():
    to_remove = docker_client.containers.list(filters={"ancestor": "priomonv1"})
    for node in to_remove:
        node.remove(force=True)
    return "OK"

@monitoring_priomon.route('/restart_all', methods=['GET'])
def restart_all_nodes(run):
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=run.node_count) as executor:
        for i in range(0, run.node_count):
            executor.submit(restart_node, run.node_list[i]["id"])
    print("Restart time: {}".format(time.time() - start), flush=True)

def start_node(index, run, database_address, monitoring_address, ip):
    to_send = {"node_list": run.node_list, "target_count": run.target_count, "gossip_rate": run.gossip_rate,
               "database_address": database_address, "monitoring_address": monitoring_address,
               "node_ip": run.node_list[index]["ip"], "is_send_data_back": experiment.is_send_data_back,
               "push_mode": experiment.push_mode, "client_port": "4000"}
    try:
        time.sleep(0.01)
        requests.post("http://{}:{}/start_node".format(ip, run.node_list[index]["port"]), json=to_send)
    except Exception as e:
        print("Node not started: {}".format(e))
        start_node(index, run, database_address, monitoring_address, ip)

def start_run(run, monitoring_address):
    database_address = parser.get('database', 'db_file')
    ip = parser.get('system_setting', 'docker_ip')
    with concurrent.futures.ThreadPoolExecutor(max_workers=run.node_count) as executor:
        for i in range(0, run.node_count):
            executor.submit(start_node, i, run, database_address, monitoring_address, ip)
    run.start_time = time.time()

def reset_run_sync(run):
    ip = parser.get('system_setting', 'docker_ip')
    print("Resetting nodes", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=run.node_count) as executor:
        for i in range(0, run.node_count):
            executor.submit(reset_node, ip, run.node_list[i]["port"], run.node_list[i]["id"])

def prepare_run(run):
    spawn_multiple_nodes(run)
    while not nodes_are_ready(run):
        time.sleep(1)
    save_run_to_database(run)
    print("Run {} started".format(run.db_id), flush=True)
    time.sleep(10)

def check_if_all_nodes_are_reset(run):
    for node in run.node_list:
        if node["is_alive"]:
            return False
    return True

def stop_node_percentage(run, percent):
    print("stopping percentage of nodes: {}".format(percent))
    if percent == 0:
        return
    nodes_to_stop_count = int(len(run.node_list) * percent)
    indices = list(range(len(run.node_list)))
    random_indices_to_stop = random.sample(indices, nodes_to_stop_count)
    for i in random_indices_to_stop:
        try:
            container_to_stop = docker_client.containers.get(run.node_list[i]["id"])
            container_to_stop.stop()
            run.node_list[i]["is_alive"] = False
            run.stopped_nodes[i] = run.node_list[i]
        except Exception as e:
            print("An error occurred while stopping container: {}".format(e))
    print("{}% of nodes (n={}) are stopped".format(percent * 100, nodes_to_stop_count))
    return

def run_converged(run):
    run.convergence_message_count = run.message_count
    run.convergence_time = (time.time() - run.start_time)
    # TODO: set convergence round
    if not run.is_converged:
        print("Convergence time: {}".format(run.convergence_time))
        print("Convergence message count: {}".format(run.convergence_message_count))

    run.is_converged = True

def check_convergence(run):
    if run.is_converged:
        return True
    if len(run.data_entries_per_ip) < run.node_count:
        return False
    for ip in run.data_entries_per_ip:
        if len(run.data_entries_per_ip[ip]) < run.node_count:
            return False
        if len(run.data_entries_per_ip[ip]) > run.node_count:
            return False
        for node_data in run.data_entries_per_ip[ip]:
            if "counter" not in run.data_entries_per_ip[ip][node_data]:
                return False
    run_converged(run)

def save_query_in_database(run, i, failure_percent, target_key, time_to_query, total_messages_for_query, success):
    experiment.db.save_query_in_database(run.db_id, run.node_count, i, failure_percent, time_to_query,
                                         total_messages_for_query, success)
    pass

def run_queries(run, query_count, failure_percent):
    docker_ip = parser.get('system_setting', 'docker_ip')
    quorum_size = 3
    for i in range(0, query_count):
        alive_nodes = [item for item in run.node_list if item.get("is_alive", False)]
        
        # Skip this query if there are no alive nodes
        if not alive_nodes:
            print("No alive nodes available for querying")
            continue
            
        # Select target node only from alive nodes
        target_node = random.choice(alive_nodes)
        target_key = target_node["ip"] + ":" + target_node["port"]
        
        try:
            start_time = time.time()
            total_messages_for_query, query_result = query_client.query(
                alive_nodes, quorum_size, target_node["ip"], target_node["port"], docker_ip
            )
            time_to_query = time.time() - start_time
            success = True
        except Exception as e:
            print(f"Query failed: {e}")
            time_to_query = time.time() - start_time
            total_messages_for_query = 0
            success = False
            
        save_query_in_database(run, i, failure_percent, target_key, time_to_query, 
                              total_messages_for_query, success)

def update_during_run(run):
    # TODO: stop percentage of nodes and check AoI etc. (update run.node_list or stop logic (convergence) if wanted)
    # before convergence do something
    while not run.is_converged:
        pass
    print(parser.get('PriomonParam', 'continue_after_convergence'))
    if parser.get('PriomonParam', 'continue_after_convergence') == "1":
        print("Convergence reached, continuing run")
        while not run.max_round_is_reached:
            pass
        print("Max round reached: stop now")
    print("should start queries now")
    if parser.get('system_setting', 'query_logic') == "1":
        print(parser.get('system_setting', 'failure_rate'))
        failure_ratio = float(parser.get('system_setting', 'failure_rate'))
        stop_node_percentage(run, failure_ratio)
        time.sleep(20)
        run_queries(run, query_count=100, failure_percent=failure_ratio)

connection_pool = sqlite3.connect("NodeStorage.db", check_same_thread=False, isolation_level=None)
database_lock = threading.Lock()

@monitoring_priomon.route('/push_data_to_database', methods=['POST'])
def push_data_to_database():
    client_ip = request.args.get('ip')
    client_port = request.args.get('port')
    client_round = request.args.get('round')
    data = request.get_json()
    node_key = client_ip + ":" + client_port

    # Acquire the lock
    with database_lock:
        # Use a connection from the pool
        connection: Connection = connection_pool
        cursor = connection.cursor()

        for r, va in data.items():
            for k, j in va.items():
                v = json.dumps(j)
                cursor.execute('SELECT id FROM unique_entries WHERE key=? AND value=?', (k, v))
                existing_entry = cursor.fetchone()
                if existing_entry:
                    unique_entry_id = existing_entry[0]
                else:
                    cursor.execute('INSERT INTO unique_entries (key, value) VALUES (?, ?)', (k, v))
                    unique_entry_id = cursor.lastrowid

                cursor.execute('INSERT INTO data_entries (node, round, key, unique_entry_id) VALUES (?, ?, ?, ?)',
                               (node_key, client_round, k, unique_entry_id))
        connection_pool.commit()

    return "OK"

@monitoring_priomon.route('/receive_ic', methods=['GET'])
def update_ic():
    client_ip = request.args['ip']
    client_port = request.args['port']
    experiment.runs[-1].ip_per_ic[client_ip + ":" + client_port] = True
    if len(experiment.runs[-1].ip_per_ic) == experiment.runs[-1].node_count:
        run_converged(experiment.runs[-1])
    return "OK"

@monitoring_priomon.route('/receive_node_data', methods=['POST'])
def update_data_entries_per_ip():
    global experiment
    if not experiment:
        print("No experiment running, but a gossip node is trying to send data")
        return "NOK"
    client_ip = request.args['ip']
    client_port = request.args['port']
    round = request.args['round']
    inc = request.get_json()
    data_stored_in_node = inc["data"]
    data_flow_per_round = inc["data_flow_per_round"]

    nd = data_flow_per_round.setdefault('nd', 0)
    fd = data_flow_per_round.setdefault('fd', 0)
    rm = data_flow_per_round.setdefault('rm', 0)

    ic = len(data_stored_in_node)
    bytes_of_data = len(json.dumps(data_stored_in_node).encode('utf-8'))

    experiment.runs[-1].convergence_round = max(experiment.runs[-1].convergence_round, int(round))
    experiment.runs[-1].message_count += 1
    experiment.runs[-1].data_entries_per_ip[client_ip + ":" + client_port] = data_stored_in_node
    if not experiment.runs[-1].is_converged:
        if int(nd) > experiment.runs[-1].node_count:
            nd = experiment.runs[-1].node_count
        if int(fd) > experiment.runs[-1].node_count:
            fd = experiment.runs[-1].node_count
        delete_parameters = (experiment.runs[-1].db_id, client_ip, client_port, round)
        insert_parameters = (experiment.runs[-1].db_id, client_ip, client_port, round, nd, fd, rm, ic, bytes_of_data)
        experiment.query_queue.put(
            ("DELETE FROM round_of_node WHERE run_id = ? AND ip = ? AND port = ? AND round = ?", delete_parameters))
        experiment.query_queue.put((
                                   "INSERT INTO round_of_node (run_id, ip, port, round, nd, fd, rm, ic, bytes_of_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   insert_parameters))
    
    # Extract metrics statistics if available
    metrics_sent = data_flow_per_round.get('metrics_sent', 0)
    metrics_filtered = data_flow_per_round.get('metrics_filtered', 0)
    
    # Store metrics statistics
    if metrics_sent > 0 or metrics_filtered > 0:
        metrics_params = (experiment.runs[-1].db_id, client_ip, client_port, round, 
                         metrics_sent, metrics_filtered, time.time())
        experiment.query_queue.put((
            "INSERT INTO round_metrics_stats (run_id, node_ip, node_port, round, metrics_sent, metrics_filtered, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            metrics_params))
    
    # Store detailed per-metric transmission data
    if client_ip + ":" + client_port in data_stored_in_node:
        node_data = data_stored_in_node[client_ip + ":" + client_port]
        if 'metric_sent_flags' in node_data:
            timestamp = time.time()
            for metric_type, was_sent in node_data['metric_sent_flags'].items():
                # Get metric value if available
                metric_value = None
                if 'appSate' in node_data and metric_type in node_data['appSate']:
                    try:
                        metric_value = float(node_data['appSate'][metric_type])
                    except (ValueError, TypeError):
                        pass
            
                # Queue the database insert
                metric_params = (experiment.runs[-1].db_id, client_ip, client_port, round, 
                                metric_type, 1 if was_sent else 0, metric_value, timestamp)
                experiment.query_queue.put((
                    "INSERT INTO metric_transmissions (run_id, node_ip, node_port, round, metric_type, was_sent, metric_value, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    metric_params))
    
    check_convergence(experiment.runs[-1])
    if int(round) >= 80:
        run_converged(experiment.runs[-1])
        experiment.runs[-1].max_round_is_reached = True
    return "OK"

def generate_run(node_count, gossip_rate, target_count, run_count):
    if experiment.runs:
        return Run(node_count, gossip_rate, target_count, run_count, node_list=experiment.runs[-1].node_list)
    return Run(node_count, gossip_rate, target_count, run_count)

def prepare_experiment(server_ip):
    global experiment
    experiment = Experiment(json.loads(parser.get('PriomonParam', 'node_range')),
                            json.loads(parser.get('PriomonParam', 'gossip_rate_range')),
                            json.loads(parser.get('PriomonParam', 'target_count_range')),
                            json.loads(parser.get('PriomonParam', 'runs')),
                            server_ip,
                            parser.get('system_setting', 'is_send_data_back'),
                            parser.get('PriomonParam', 'push_mode'))
    experiment.set_db_id(experiment.db.insert_into_experiment(time.time()))
    experiment.query_thread = threading.Thread(target=execute_queries_from_queue)
    experiment.query_thread.start()

def print_experiment():
    experiment.query_queue.put(None)
    experiment.query_thread.join()
    for run in experiment.runs:
        print("Run {}, converged after {} messages and {} seconds".format(run.node_count, run.convergence_message_count,
                                                                          run.convergence_time))

@monitoring_priomon.route('/start', methods=['GET'])
def start_priomon():
    server_ip = socket.gethostbyname(socket.gethostname())
    print("Server IP: {}".format(server_ip))
    global experiment
    prepare_experiment(server_ip)
    for node_count in experiment.node_count_range:
        new_target_count_range = get_target_count(node_count, experiment.target_count_range)
        for target_count in new_target_count_range:
            for gossip_rate in experiment.gossip_rate_range:
                for run_count in range(0, experiment.run_count):
                    print("Preparing run with {} nodes, {} gossip rate, {} target count and {} run count".format(
                        node_count, gossip_rate, target_count, run_count))
                    run = generate_run(node_count, gossip_rate, target_count, run_count)  # db_collection=collection)
                    experiment.runs.append(run)
                    prepare_run(run)
                    print("Run {} prepared, with {} nodes online".format(run.run, len(run.node_list)))
                    start_run(run, experiment.monitoring_address_ip)
                    update_during_run(run)
                    save_converged_run_to_database(run)
                    reset_run_sync(run)
    print_experiment()
    delete_all_nodes()
    return "OK - Experiment finished"

def create_and_start_priomon_node(node_number, node_list, target_count, gossip_rate):    
    # metric priority configuration
    node_data = {
        "metric_priorities": {
            "cpu": int(parser.get('MetricPriorities', 'cpu_priority', fallback=1)),
            "memory": int(parser.get('MetricPriorities', 'memory_priority', fallback=5)),
            "network": int(parser.get('MetricPriorities', 'network_priority', fallback=5)),
            "storage": int(parser.get('MetricPriorities', 'storage_priority', fallback=10))
        },
        "metric_deltas": {
            "cpu": float(parser.get('MetricDeltas', 'cpu_delta', fallback=5.0)),
            "memory": float(parser.get('MetricDeltas', 'memory_delta', fallback=7.0)),
            "network": float(parser.get('MetricDeltas', 'network_delta', fallback=15.0)),
            "storage": float(parser.get('MetricDeltas', 'storage_delta', fallback=10.0))
        }
    }
    
if __name__ == "__main__":
    monitoring_priomon.run(host='0.0.0.0', port=4000, debug=False, threaded=True)
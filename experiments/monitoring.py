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
        new_node = docker_client.containers.run("demonv1", auto_remove=True, detach=True,
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
    to_remove = docker_client.containers.list(filters={"ancestor": "demonv1"})
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

if __name__ == "__main__":
    monitoring_priomon.run(host='0.0.0.0', port=4000, debug=False, threaded=True)
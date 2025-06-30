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

if __name__ == "__main__":
    monitoring_priomon.run(host='0.0.0.0', port=4000, debug=False, threaded=True)
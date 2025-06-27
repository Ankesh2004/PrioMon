import os
import random
import time
import psutil
import requests
from singleton import Singleton
import logging
import hashlib
import json
import secrets


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
        self.push_mode = None
        self.is_send_data_back = None

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
        return secrets.SystemRandom().sample(filtered_nodes, target_count)
    
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

        metadata = {
            key: node_data['counter']
            for key, node_data in time_data.items()
            if key != own_key and 'counter' in node_data
        }

        return {'metadata': metadata, own_key: own_recent_data}

    # get new data function
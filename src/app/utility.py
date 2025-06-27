import json
import hashlib
import time
import psutil
from node import Node

# Utility functions for the Node class
def mk_digest(to_digest):
    nested_dict_str = json.dumps(to_digest, sort_keys=True)
    hash_object = hashlib.sha256()
    hash_object.update(nested_dict_str.encode('utf-8'))
    digest = hash_object.hexdigest()
    return digest

def get_new_data():
    node = Node.instance()
    net_io = psutil.net_io_counters()
    network = net_io.bytes_recv + net_io.bytes_sent

    data = {
        "counter": str(node.gossip_counter),
        "cycle": str(node.cycle),
        "digest": "",
        "nodeState": {
            "id": "",
            "ip": str(node.ip),
            "port": str(node.port)
        },
        "hbState": {
            "timestamp": str(time.time()),
            "failureCount": node.failure_counter,
            "failureList": node.failure_list,
            "nodeAlive": node.is_alive
        },
        "appState": {
            "cpu": str(psutil.cpu_percent()),
            "memory": str(psutil.virtual_memory().percent),
            "network": str(network),
            "storage": str(psutil.disk_usage('/').free)
        },
        "nfState": {}
    }

    data["digest"] = mk_digest(data)
    return data

import random
import time
import requests

def query(node_list, quorum_size, target_node_ip, target_node_port, docker_ip):
    def build_url(node, path):
        host = docker_ip if docker_ip else node["ip"]
        return f"http://{host}:{node['port']}{path}"

    target_key = f"{target_node_ip}:{target_node_port}"

    while True:
        # 1. Pick a random quorum
        random_nodes = random.sample(node_list, quorum_size)

        metadatas = {}
        total_messages = 0

        # 2. Gather metadata
        for node in random_nodes:
            total_messages += 1
            try:
                resp = requests.get(build_url(node, "/metadata"), timeout=5)
                resp.raise_for_status()
                data = resp.json()[target_key]
                metadatas[f"{node['ip']}:{node['port']}"] = data

                # (Optional) quick debug
                # print(data["counter"])
            except Exception as e:
                print(f"Node {node['ip']}:{node['port']} not responding: {e}")

        # 3. Check if full quorum replied
        if len(metadatas) == quorum_size:
            # Counter consensus
            counters = [d["counter"] for d in metadatas.values()]
            if len(set(counters)) == 1:
                # Digest consensus
                digests = [d["digest"] for d in metadatas.values()]
                if len(set(digests)) == 1:
                    # 4. Fetch actual data
                    first_node = random_nodes[0]
                    data_resp = requests.get(
                        build_url(first_node, "/get_recent_data_from_node"),
                        timeout=5
                    )
                    data_resp.raise_for_status()
                    result = data_resp.json()[target_key]
                    print(f"Query result: {result}")
                    return total_messages, result

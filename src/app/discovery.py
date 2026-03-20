import json
import os
import time
import logging
import requests

logger = logging.getLogger("priomon.discovery")

# handles finding peers (via seeds) and saving/loading the peer list to disk
# so nodes survive restarts without losing their cluster membership

STATE_FILE_DEFAULT = "peer_state.json"


def discover_peers(seeds, own_ip, own_port, state_file=STATE_FILE_DEFAULT):
    """
    Try to join the cluster by contacting seed nodes.
    Falls back to loading last-known peers from disk if seeds are unreachable.
    Returns a list of peer dicts like [{"ip": "...", "port": "..."}]
    """
    own_key = f"{own_ip}:{own_port}"
    peers = []

    # first try seeds — they should return their peer list
    for seed in seeds:
        if seed == own_key:
            continue  # don't try to join ourselves
        try:
            seed_ip, seed_port = seed.rsplit(":", 1)
            resp = requests.get(
                f"http://{seed_ip}:{seed_port}/peers",
                timeout=5
            )
            if resp.status_code == 200:
                peer_list = resp.json()
                logger.info(f"Got {len(peer_list)} peers from seed {seed}")

                # now tell the seed we exist
                requests.post(
                    f"http://{seed_ip}:{seed_port}/join",
                    json={"ip": own_ip, "port": str(own_port)},
                    timeout=5
                )
                peers = peer_list
                break  # one seed is enough
        except Exception as e:
            logger.warning(f"Seed {seed} unreachable: {e}")
            continue

    # if no seed worked, try loading from disk (last known state)
    if not peers:
        peers = load_state(state_file)
        if peers:
            logger.info(f"Loaded {len(peers)} peers from saved state")
        else:
            logger.info("No peers found — starting as a lone node")

    # make sure we're in the list ourselves
    own_entry = {"ip": own_ip, "port": str(own_port)}
    if not any(p["ip"] == own_ip and str(p["port"]) == str(own_port) for p in peers):
        peers.append(own_entry)

    # persist what we found
    save_state(peers, state_file)
    return peers


def announce_to_peers(peers, own_ip, own_port):
    """
    Tell all known peers about our existence so they add us to their lists.
    Best-effort — failures here are fine, gossip will propagate eventually.
    """
    own_key = f"{own_ip}:{own_port}"
    for peer in peers:
        peer_key = f"{peer['ip']}:{peer['port']}"
        if peer_key == own_key:
            continue
        try:
            requests.post(
                f"http://{peer['ip']}:{peer['port']}/join",
                json={"ip": own_ip, "port": str(own_port)},
                timeout=3
            )
        except Exception:
            pass  # they'll find out about us through gossip anyway


def save_state(peers, state_file=STATE_FILE_DEFAULT):
    """Dump the peer list to disk so we can recover after a restart."""
    try:
        with open(state_file, "w") as f:
            json.dump({
                "peers": peers,
                "saved_at": time.time()
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save peer state: {e}")


def load_state(state_file=STATE_FILE_DEFAULT):
    """Load the last-known peer list from disk. Returns [] if nothing saved."""
    if not os.path.exists(state_file):
        return []
    try:
        with open(state_file, "r") as f:
            data = json.load(f)
            return data.get("peers", [])
    except Exception as e:
        logger.error(f"Failed to load peer state: {e}")
        return []

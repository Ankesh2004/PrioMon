import signal
import time
import json
import logging
from flask import Flask, request

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
gossip = Flask(__name__)


@gossip.route('/hello_world', methods=['GET'])
def get_hello_from_node():
    return "Hello from gossip agent!"


@gossip.route('/stop_node')
def stop_node():
    return "OK"


@gossip.route('/reset_node')
def reset_node():
    return "OK"


if __name__ == "__main__":
    # get port from container
    gossip.run(host='0.0.0.0', debug=True, threaded=True)
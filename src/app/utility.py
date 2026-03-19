import json
import hashlib


# Utility functions for the Node class
def mk_digest(to_digest):
    nested_dict_str = json.dumps(to_digest, sort_keys=True)
    hash_object = hashlib.sha256()
    hash_object.update(nested_dict_str.encode('utf-8'))
    digest = hash_object.hexdigest()
    return digest



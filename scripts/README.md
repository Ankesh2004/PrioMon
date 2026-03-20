# Scripts

## `generate_certs.py` — mTLS Certificate Generator

Generates a self-signed CA and per-node certificates for mTLS. Run this **once** before deploying a cluster with `tls.enabled: true`.

### Dependency

```powershell
pip install cryptography
```

### Usage

```powershell
# Basic — generate certs for 2 nodes
python scripts/generate_certs.py --nodes node1,node2 --out ./certs

# With real IPs — adds the IPs as Subject Alternative Names (SANs)
# Required for TLS hostname validation when nodes talk by IP address
python scripts/generate_certs.py --nodes node1,node2 --ips 192.168.1.10,192.168.1.11 --out ./certs

# 3-node cluster example
python scripts/generate_certs.py --nodes node1,node2,node3 --ips 10.0.0.1,10.0.0.2,10.0.0.3 --out ./certs
```

### What Gets Created

```
certs/
├── ca.crt        ← CA certificate: distribute to ALL nodes (trusted root)
├── ca.key        ← CA private key: KEEP OFFLINE, never deploy to any node
├── node1.crt     ← node1's certificate (goes to node1 only)
├── node1.key     ← node1's private key (goes to node1 only)
├── node2.crt
├── node2.key
└── ...
```

### Technical Details

- **CA key**: RSA 4096-bit, valid 10 years
- **Node certs**: RSA 2048-bit, valid 1 year, signed by the CA
- **SANs**: Each node cert includes `DNS:nodename`, `DNS:localhost`, `IP:127.0.0.1`, plus any extra IPs you pass via `--ips`
- **Key Usage**: `digitalSignature + keyEncipherment` for nodes; `keyCertSign + cRLSign` for CA
- **Extended Key Usage**: Both `serverAuth` and `clientAuth` — required for mutual TLS where each side acts as both client and server at different times

### Distribution Guide (Production Deployment)

| File | Deploy to |
|---|---|
| `ca.crt` | Every node |
| `nodeX.crt` | That specific node only |
| `nodeX.key` | That specific node only |
| `ca.key` | **NOBODY** — keep it offline |

### Regenerating Certs

If you need to add a new node, you must regenerate all certs — the CA key is needed to sign the new node's cert, and the CA itself will be newly generated (invalidating existing node certs).

This means **all nodes need their certs updated simultaneously** when you add a new node. Plan accordingly.

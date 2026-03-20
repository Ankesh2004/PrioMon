#!/usr/bin/env python3
"""
PrioMon Certificate Generator
-------------------------------
Generates a self-signed CA and node certificates for mTLS.
Run this ONCE before deploying your cluster.

Usage:
    python generate_certs.py --nodes node1,node2 --out ./certs
    python generate_certs.py --nodes node1,node2,node3 --ips 192.168.1.10,192.168.1.11,192.168.1.12 --out ./certs

This creates:
    certs/
    ├── ca.crt          # CA certificate (distribute to all nodes)
    ├── ca.key          # CA private key (keep this safe, don't deploy)
    ├── node1.crt       # node1's certificate
    ├── node1.key       # node1's private key
    ├── node2.crt
    ├── node2.key
    └── ...
"""

import argparse
import os
import datetime
import ipaddress

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_ca(out_dir):
    """Generate a self-signed CA certificate + key."""
    print("Generating CA...")

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    ca_name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PrioMon"),
        x509.NameAttribute(NameOID.COMMON_NAME, "PrioMon Root CA"),
    ])

    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # save CA key
    ca_key_path = os.path.join(out_dir, "ca.key")
    with open(ca_key_path, "wb") as f:
        f.write(ca_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # save CA cert
    ca_cert_path = os.path.join(out_dir, "ca.crt")
    with open(ca_cert_path, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    print(f"  CA cert: {ca_cert_path}")
    print(f"  CA key:  {ca_key_path} (keep this safe!)")
    return ca_cert, ca_key


def generate_node_cert(node_name, extra_ips, ca_cert, ca_key, out_dir):
    """Generate a node certificate signed by our CA."""
    print(f"Generating cert for '{node_name}'...")

    node_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # build Subject Alternative Names — the node's hostname + localhost + any extra IPs
    san_entries = [
        x509.DNSName(node_name),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for ip_str in extra_ips:
        ip_str = ip_str.strip()
        if not ip_str:
            continue
        try:
            san_entries.append(x509.IPAddress(ipaddress.IPv4Address(ip_str)))
        except ValueError:
            # might be a hostname, not an IP — add as DNS name instead
            san_entries.append(x509.DNSName(ip_str))

    node_name_obj = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PrioMon"),
        x509.NameAttribute(NameOID.COMMON_NAME, node_name),
    ])

    node_cert = (
        x509.CertificateBuilder()
        .subject_name(node_name_obj)
        .issuer_name(ca_cert.subject)
        .public_key(node_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(san_entries),
            critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True,
                content_commitment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # save node key
    key_path = os.path.join(out_dir, f"{node_name}.key")
    with open(key_path, "wb") as f:
        f.write(node_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # save node cert
    cert_path = os.path.join(out_dir, f"{node_name}.crt")
    with open(cert_path, "wb") as f:
        f.write(node_cert.public_bytes(serialization.Encoding.PEM))

    print(f"  Cert: {cert_path}")
    print(f"  Key:  {key_path}")
    return node_cert, node_key


def main():
    parser = argparse.ArgumentParser(description="Generate mTLS certificates for PrioMon")
    parser.add_argument("--nodes", required=True,
                        help="Comma-separated node names (e.g. node1,node2,node3)")
    parser.add_argument("--ips", default="",
                        help="Comma-separated extra IPs to add as SANs (e.g. 192.168.1.10,192.168.1.11)")
    parser.add_argument("--out", default="./certs",
                        help="Output directory for certificates (default: ./certs)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    node_names = [n.strip() for n in args.nodes.split(",")]
    extra_ips = [ip.strip() for ip in args.ips.split(",") if ip.strip()]

    # generate CA
    ca_cert, ca_key = generate_ca(args.out)

    # generate a cert for each node
    for node_name in node_names:
        generate_node_cert(node_name, extra_ips, ca_cert, ca_key, args.out)

    print(f"\nDone! Certificates are in: {args.out}/")
    print(f"\nDeploy to each node:")
    print(f"  - ca.crt         (all nodes)")
    print(f"  - <node>.crt     (that node only)")
    print(f"  - <node>.key     (that node only)")
    print(f"\nDO NOT deploy ca.key to any node — keep it offline.")


if __name__ == "__main__":
    main()

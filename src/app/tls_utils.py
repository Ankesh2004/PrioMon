import ssl
import os
import logging

logger = logging.getLogger("priomon.tls")

# central place for all TLS-related config
# keeps the mTLS logic out of the main gossip code


class TLSConfig:
    """
    Holds TLS cert paths and provides helpers to create SSL contexts.
    If enabled=False, everything runs in plain HTTP (backward compatible).
    """

    def __init__(self, enabled=False, ca_cert=None, node_cert=None, node_key=None):
        self.enabled = enabled
        self.ca_cert = ca_cert
        self.node_cert = node_cert
        self.node_key = node_key

        if self.enabled:
            # sanity check — make sure all files exist before we try to use them
            for path, label in [(ca_cert, "CA cert"), (node_cert, "node cert"), (node_key, "node key")]:
                if not path or not os.path.exists(path):
                    raise FileNotFoundError(f"TLS enabled but {label} not found: {path}")
            logger.info(f"TLS enabled — CA: {ca_cert}, cert: {node_cert}, key: {node_key}")

    @property
    def scheme(self):
        """Returns 'https' or 'http' depending on TLS config."""
        return "https" if self.enabled else "http"

    def create_server_ssl_context(self):
        """
        Build an SSL context for the Flask server.
        Requires client certificates (mutual TLS).
        """
        if not self.enabled:
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=self.node_cert, keyfile=self.node_key)
        ctx.load_verify_locations(cafile=self.ca_cert)
        # this is what makes it MUTUAL TLS — server demands a client cert
        ctx.verify_mode = ssl.CERT_REQUIRED
        # only allow modern TLS versions
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        logger.info("Server SSL context created with mTLS (client cert required)")
        return ctx

    def configure_session(self, session):
        """
        Attach client cert + CA verification to a requests.Session.
        After this, every request through this session presents our node cert
        and verifies the server's cert against our CA.
        """
        if not self.enabled:
            return session

        session.cert = (self.node_cert, self.node_key)
        session.verify = self.ca_cert
        return session

    def get_request_kwargs(self):
        """
        Returns kwargs to pass to requests.get/post for one-off calls
        (when you're not using a persistent session).
        """
        if not self.enabled:
            return {}
        return {
            "cert": (self.node_cert, self.node_key),
            "verify": self.ca_cert
        }

    def build_url(self, host, port, path):
        """Build a URL with the correct scheme (http or https)."""
        return f"{self.scheme}://{host}:{port}{path}"


# global TLS config — set once during startup, used everywhere
_global_tls_config = TLSConfig(enabled=False)


def set_global_tls(tls_config):
    global _global_tls_config
    _global_tls_config = tls_config


def get_global_tls():
    return _global_tls_config


def load_tls_from_config(cfg):
    """
    Load TLS config from the YAML config dict.
    Returns a TLSConfig object (enabled or disabled).
    """
    tls_cfg = cfg.get("tls", {})
    if not tls_cfg.get("enabled", False):
        logger.info("TLS disabled — running in plain HTTP mode")
        return TLSConfig(enabled=False)

    return TLSConfig(
        enabled=True,
        ca_cert=tls_cfg.get("ca_cert"),
        node_cert=tls_cfg.get("node_cert"),
        node_key=tls_cfg.get("node_key")
    )

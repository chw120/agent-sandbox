import unittest
from unittest.mock import MagicMock, AsyncMock
import ssl
import httpx

from k8s_agent_sandbox.utils import build_base_url
from k8s_agent_sandbox.models import (
    TLSConfig,
    SandboxGatewayConnectionConfig,
    SandboxInClusterConnectionConfig,
    SandboxLocalTunnelConnectionConfig
)
from k8s_agent_sandbox.connector import (
    GatewayConnectionStrategy,
    InClusterConnectionStrategy,
    LocalTunnelConnectionStrategy,
    SandboxConnector
)
from k8s_agent_sandbox.async_connector import AsyncSandboxConnector


class TestTLSFeatures(unittest.IsolatedAsyncioTestCase):
    def test_build_base_url_http_https(self):
        self.assertEqual(build_base_url("http", "example.com", 8080), "http://example.com:8080")
        self.assertEqual(build_base_url("https", "example.com"), "https://example.com")

    def test_build_base_url_ipv6_bracket(self):
        self.assertEqual(build_base_url("http", "2001:db8::1", 8080), "http://[2001:db8::1]:8080")
        self.assertEqual(build_base_url("https", "[2001:db8::1]"), "https://[2001:db8::1]")

    def test_gateway_strategy_uses_scheme_field(self):
        config = SandboxGatewayConnectionConfig(
            gateway_name="gw",
            gateway_namespace="default",
            scheme="https",
            server_port=8888
        )
        mock_helper = MagicMock()
        mock_helper.wait_for_gateway_ip.return_value = "34.56.78.90"
        strategy = GatewayConnectionStrategy(config, k8s_helper=mock_helper)
        url = strategy.connect()
        self.assertEqual(url, "https://34.56.78.90")

    def test_in_cluster_dns_uses_scheme(self):
        config = SandboxInClusterConnectionConfig(
            server_port=8888,
            use_pod_ip=False,
            scheme="https"
        )
        strategy = InClusterConnectionStrategy("test-sb", "default", config, get_pod_ip=None)
        url = strategy.connect()
        self.assertEqual(url, "https://test-sb.default.svc.cluster.local:8888")

    def test_in_cluster_pod_ip_uses_scheme(self):
        config = SandboxInClusterConnectionConfig(
            server_port=8888,
            use_pod_ip=True,
            scheme="https"
        )
        mock_helper = MagicMock()
        mock_helper.get_sandbox_pod_ip = MagicMock(return_value="10.0.0.1")
        strategy = InClusterConnectionStrategy("test-sb", "default", config, get_pod_ip=mock_helper.get_sandbox_pod_ip)
        url = strategy.connect()
        self.assertEqual(url, "https://10.0.0.1:8888")

    @unittest.mock.patch("k8s_agent_sandbox.connector.LocalTunnelConnectionStrategy._get_free_port")
    @unittest.mock.patch("k8s_agent_sandbox.connector.LocalTunnelConnectionStrategy._is_port_open")
    @unittest.mock.patch("subprocess.Popen")
    def test_local_tunnel_uses_scheme(self, mock_popen, mock_is_port_open, mock_get_free_port):
        config = SandboxLocalTunnelConnectionConfig(
            server_port=8888,
            scheme="https"
        )
        mock_get_free_port.return_value = 50000
        mock_is_port_open.return_value = True
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process
        
        strategy = LocalTunnelConnectionStrategy("test-sb", "default", config)
        url = strategy.connect()
        self.assertEqual(url, "https://127.0.0.1:50000")

    def test_tls_config_ca_cert_loaded(self):
        config = SandboxGatewayConnectionConfig(
            gateway_name="gw",
            scheme="https",
            tls=TLSConfig(ca_cert="-----BEGIN CERTIFICATE-----\nMIIDdzCC...\n-----END CERTIFICATE-----")
        )
        connector = SandboxConnector("test-sb", "default", config, MagicMock())
        self.assertIsInstance(connector.session.verify, str)
        self.assertTrue(connector.session.verify.endswith(".pem"))

    def test_tls_config_insecure_skip_verify(self):
        config = SandboxGatewayConnectionConfig(
            gateway_name="gw",
            scheme="https",
            tls=TLSConfig(insecure_skip_verify=True)
        )
        connector = SandboxConnector("test-sb", "default", config, MagicMock())
        self.assertFalse(connector.session.verify)

    @unittest.mock.patch("ssl.create_default_context")
    def test_sync_async_tls_behavior_consistent(self, mock_ssl):
        mock_context = MagicMock()
        mock_ssl.return_value = mock_context
        config = SandboxGatewayConnectionConfig(
            gateway_name="gw",
            scheme="https",
            tls=TLSConfig(
                ca_cert="-----BEGIN CERTIFICATE-----\nMIIDdzCC...\n-----END CERTIFICATE-----",
                insecure_skip_verify=True
            )
        )
        # Sync
        connector = SandboxConnector("test-sb", "default", config, MagicMock())
        self.assertFalse(connector.session.verify)
        
        # Async
        async_connector = AsyncSandboxConnector("test-sb", "default", config, MagicMock())
        
        # Verify sync behavior
        self.assertFalse(connector.session.verify)
        
        # Verify async behavior (mocked context check)
        mock_context.load_verify_locations.assert_called_once_with(cadata="-----BEGIN CERTIFICATE-----\nMIIDdzCC...\n-----END CERTIFICATE-----")

if __name__ == '__main__':
    unittest.main()

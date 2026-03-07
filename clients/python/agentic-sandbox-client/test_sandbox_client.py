import pytest
from unittest.mock import MagicMock, patch
import time

from k8s_agent_sandbox.sandbox_client import SandboxClient
from k8s_agent_sandbox.metrics import DISCOVERY_LATENCY_MS

@pytest.fixture
def mock_k8s_config():
    with patch('k8s_agent_sandbox.sandbox_client.config.load_incluster_config'), \
         patch('k8s_agent_sandbox.sandbox_client.config.load_kube_config'):
        yield

@pytest.fixture
def mock_custom_objects_api():
    with patch('k8s_agent_sandbox.sandbox_client.client.CustomObjectsApi') as mock_api:
        yield mock_api

@pytest.fixture
def mock_create_claim():
    with patch.object(SandboxClient, '_create_claim') as mock:
        yield mock

@pytest.fixture
def mock_wait_ready():
    with patch.object(SandboxClient, '_wait_for_sandbox_ready') as mock:
        yield mock

def test_discovery_latency_success_dev_mode(mock_k8s_config, mock_custom_objects_api, mock_create_claim, mock_wait_ready):
    with patch('k8s_agent_sandbox.sandbox_client.subprocess.Popen') as mock_popen, \
         patch('k8s_agent_sandbox.sandbox_client.socket.socket') as mock_socket, \
         patch('k8s_agent_sandbox.sandbox_client.socket.create_connection') as mock_create_connection, \
         patch('k8s_agent_sandbox.sandbox_client.time.sleep'):

        # Setup mock port forward
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        # Setup socket mock to return a free port
        mock_sock_instance = MagicMock()
        mock_sock_instance.getsockname.return_value = ('0.0.0.0', 12345)
        mock_socket.return_value.__enter__.return_value = mock_sock_instance

        # Get baseline metric value before test
        before_count = DISCOVERY_LATENCY_MS.labels(status="success")._sum.get()

        # Initialize client in dev mode (no base_url, no gateway_name)
        client = SandboxClient(template_name="test-template")

        with client:
            # Client has successfully entered the context
            assert client.base_url == "http://127.0.0.1:12345"

        # Verify metric was incremented
        after_count = DISCOVERY_LATENCY_MS.labels(status="success")._sum.get()
        assert after_count > before_count

def test_discovery_latency_failure_dev_mode(mock_k8s_config, mock_custom_objects_api, mock_create_claim, mock_wait_ready):
    with patch('k8s_agent_sandbox.sandbox_client.subprocess.Popen') as mock_popen, \
         patch('k8s_agent_sandbox.sandbox_client.socket.socket') as mock_socket:

        # Setup mock port forward that fails
        mock_process = MagicMock()
        mock_process.poll.return_value = 1 # Process died
        mock_process.communicate.return_value = (b"", b"Crash")
        mock_popen.return_value = mock_process

        # Get baseline metric value before test
        before_count = DISCOVERY_LATENCY_MS.labels(status="failure")._sum.get()

        # Initialize client in dev mode
        client = SandboxClient(template_name="test-template")

        with pytest.raises(RuntimeError):
            with client:
                pass

        # Verify failure metric was incremented
        after_count = DISCOVERY_LATENCY_MS.labels(status="failure")._sum.get()
        assert after_count > before_count

def test_discovery_latency_success_gateway_mode(mock_k8s_config, mock_custom_objects_api, mock_create_claim, mock_wait_ready):
    with patch('k8s_agent_sandbox.sandbox_client.watch.Watch') as mock_watch:
        # Setup watch to return gateway with IP
        mock_w_instance = MagicMock()
        mock_w_instance.stream.return_value = [{
            "type": "ADDED",
            "object": {
                "status": {
                    "addresses": [{"value": "10.0.0.1"}]
                }
            }
        }]
        mock_watch.return_value = mock_w_instance

        before_count = DISCOVERY_LATENCY_MS.labels(status="success")._sum.get()

        client = SandboxClient(template_name="test-template", gateway_name="test-gw")

        with client:
            assert client.base_url == "http://10.0.0.1"

        after_count = DISCOVERY_LATENCY_MS.labels(status="success")._sum.get()
        assert after_count > before_count

def test_discovery_latency_no_metric_for_base_url(mock_k8s_config, mock_custom_objects_api, mock_create_claim, mock_wait_ready):
    try:
        before_success = DISCOVERY_LATENCY_MS.labels(status="success")._sum.get()
    except:
        before_success = 0.0

    try:
        before_failure = DISCOVERY_LATENCY_MS.labels(status="failure")._sum.get()
    except:
        before_failure = 0.0

    client = SandboxClient(template_name="test-template", api_url="http://custom-url")

    with client:
        assert client.base_url == "http://custom-url"

    try:
        after_success = DISCOVERY_LATENCY_MS.labels(status="success")._sum.get()
    except:
        after_success = 0.0

    try:
        after_failure = DISCOVERY_LATENCY_MS.labels(status="failure")._sum.get()
    except:
        after_failure = 0.0

    assert after_success == before_success
    assert after_failure == before_failure

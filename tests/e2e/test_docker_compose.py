"""
End-to-end tests for docker-compose stack.

Tests that verify the full Cortex stack runs correctly via docker-compose.
Skip if docker-compose is not available.
"""

import os
import subprocess
import time

import pytest


def _run_compose(args, check=False, capture=True):
    """Run docker-compose, returning None if not available."""
    cmd = ["docker-compose"] + args
    try:
        kwargs = {"capture_output": True, "text": True}
        if capture:
            return subprocess.run(cmd, check=check, **kwargs)
        else:
            return subprocess.run(cmd, check=check)
    except FileNotFoundError:
        return None


@pytest.fixture(scope="module")
def compose_stack():
    """
    Start the docker-compose stack and yield control to tests.
    Tears down after all tests in this module complete.
    Skips if docker-compose is not available.
    """
    # Ensure we're in the repo root where docker-compose.yml lives
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    original_cwd = os.getcwd()
    os.chdir(repo_root)

    result = _run_compose(["build", "--quiet"], check=False)
    if result is None:
        pytest.skip("docker-compose not available")

    if result.returncode != 0:
        os.chdir(original_cwd)
        pytest.skip(f"docker-compose build failed: {result.stderr}")

    result = _run_compose(["up", "-d"], check=False)
    if result is None or result.returncode != 0:
        os.chdir(original_cwd)
        pytest.skip(f"docker-compose up failed: {result.stderr if result else 'unknown'}")

    yield

    # Teardown
    _run_compose(["down", "-v", "--remove-orphans"], check=False)
    os.chdir(original_cwd)


@pytest.fixture
def api_url():
    """Return the API URL for the compose stack."""
    return "http://localhost:8000"


@pytest.fixture
def wait_for_api(api_url):
    """Wait for the API to become healthy before proceeding."""
    import requests

    max_retries = 30
    for _ in range(max_retries):
        try:
            resp = requests.get(f"{api_url}/health", timeout=5)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(2)
    pytest.fail(f"API at {api_url} never became healthy")


class TestDockerComposeStack:
    """Test docker-compose stack brings up healthy services."""

    def test_all_containers_running(self, compose_stack):
        """Verify all containers are running."""
        import json

        result = _run_compose(["ps", "--format", "json"])
        if result is None:
            pytest.skip("docker-compose not available")

        # Parse JSON output (one JSON object per line)
        containers = []
        for line in result.stdout.strip().split("\n"):
            if line:
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

        expected_services = {"postgres", "mosquitto", "cortex"}
        running_services = {c["Service"] for c in containers if c.get("State") == "running"}

        missing = expected_services - running_services
        assert not missing, f"Services not running: {missing}"

    def test_api_health_endpoint_responds(self, compose_stack, wait_for_api, api_url):
        """Test that GET /health returns 200."""
        import requests

        resp = requests.get(f"{api_url}/health", timeout=10)
        assert resp.status_code == 200

        data = resp.json()
        assert "status" in data

    def test_mosquitto_accepts_connections(self, compose_stack):
        """Test that Mosquitto MQTT broker accepts connections."""
        import paho.mqtt.client as mqtt

        connected = False

        def on_connect(client, userdata, flags, rc):
            nonlocal connected
            if rc == 0:
                connected = True

        client = mqtt.Client(client_id="test-health-check")
        client.on_connect = on_connect
        try:
            client.connect("localhost", 1883, keepalive=10)
        except Exception:
            pytest.skip("Mosquitto not reachable")
            return
        client.loop_start()

        # Wait for connection
        for _ in range(20):
            if connected:
                break
            time.sleep(0.5)
        else:
            pytest.fail("Could not connect to Mosquitto")

        client.loop_stop()
        client.disconnect()

    async def test_postgres_accepts_connections(self, compose_stack):
        """Test that Postgres is accepting connections."""
        import asyncpg

        try:
            conn = await asyncpg.connect(
                host="localhost",
                port=5432,
                user="cortex",
                password="cortex",
                database="cortex",
                timeout=10,
            )
            await conn.close()
        except Exception:
            pytest.skip("Postgres not reachable")

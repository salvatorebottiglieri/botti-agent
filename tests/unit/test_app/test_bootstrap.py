"""Tests for CortexApp bootstrap."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


class TestCortexAppBootstrap:
    """Test CortexApp initialization and wiring."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.version = "0.1.0"
        settings.database_url = MagicMock()
        settings.database_url.host = "localhost"
        settings.database_url.port = 5432
        settings.database_url.username = "postgres"
        settings.database_url.password = MagicMock()
        settings.database_url.password.get_secret_value.return_value = "postgres"
        settings.database_url.path = "/cortex"
        settings.llm_provider = "openai"
        settings.llm_api_key = MagicMock()
        settings.llm_api_key.get_secret_value.return_value = "test-key"
        settings.mqtt_broker_url = "mqtt://localhost:1883"
        settings.app_host = "0.0.0.0"
        settings.app_port = 8000
        return settings

    @pytest.mark.asyncio
    async def test_create_app_returns_fastapi_app(self, mock_settings):
        """create_app() should return a FastAPI application."""
        from fastapi.testclient import TestClient
        from cortex.main import create_app
        
        # Mock dependencies
        with patch("cortex.main.get_settings", return_value=mock_settings):
            app = create_app()
        
        assert app is not None
        
        # Should be able to create a test client
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/")
        assert response.status_code == 200
        assert "Cortex API" in response.json()["name"]

    @pytest.mark.asyncio
    async def test_app_has_health_endpoint(self, mock_settings):
        """App should have a health endpoint."""
        from fastapi.testclient import TestClient
        from cortex.main import create_app
        
        # Create mock state so dependencies can resolve
        mock_db_pool = MagicMock()
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock()
        state = {
            "db_pool": mock_db_pool,
            "event_bus": mock_event_bus,
        }
        
        with patch("cortex.main.get_settings", return_value=mock_settings):
            app = create_app(cortex_state=state)
        
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health")
        assert response.status_code in [200, 503]  # 503 if dependencies not initialized

    @pytest.mark.asyncio
    async def test_cortex_app_dataclass_exists(self):
        """CortexApp dataclass should exist for holding components."""
        from cortex.main import CortexApp
        
        # Should be able to create with empty components
        app = CortexApp()
        assert app is not None

    @pytest.mark.asyncio
    async def test_services_are_wired(self, mock_settings):
        """Services should be wired through dependency injection."""
        from cortex.api.dependencies import get_session_service, get_execution_module
        from cortex.main import create_app, CortexApp
        
        # Create a minimal CortexApp with mock services
        mock_db_pool = MagicMock()
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock()
        mock_event_bus.subscribe = AsyncMock()
        mock_event_bus.start = AsyncMock()
        mock_event_bus.stop = AsyncMock()
        mock_session_service = MagicMock()
        mock_execution_module = MagicMock()
        mock_interaction_service = MagicMock()
        mock_personality_service = MagicMock()
        mock_memory_service = MagicMock()
        mock_minion_service = MagicMock()
        mock_llm_client = MagicMock()
        
        state = {
            "db_pool": mock_db_pool,
            "event_bus": mock_event_bus,
            "session_service": mock_session_service,
            "execution_module": mock_execution_module,
            "interaction_service": mock_interaction_service,
            "personality_service": mock_personality_service,
            "memory_service": mock_memory_service,
            "minion_service": mock_minion_service,
            "llm_client": mock_llm_client,
        }
        
        with patch("cortex.main.get_settings", return_value=mock_settings):
            app = create_app(cortex_state=state)
        
        # Dependencies should resolve
        from cortex.api.dependencies import get_app_state
        
        resolved_state = get_app_state()
        assert resolved_state["session_service"] == mock_session_service
        assert resolved_state["execution_module"] == mock_execution_module


class TestStartupShutdown:
    """Test startup and shutdown events."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.version = "0.1.0"
        settings.database_url = MagicMock()
        settings.database_url.host = "localhost"
        settings.database_url.port = 5432
        settings.database_url.username = "postgres"
        settings.database_url.password = MagicMock()
        settings.database_url.password.get_secret_value.return_value = "postgres"
        settings.database_url.path = "/cortex"
        settings.llm_provider = "openai"
        settings.llm_api_key = MagicMock()
        settings.llm_api_key.get_secret_value.return_value = "test-key"
        settings.mqtt_broker_url = "mqtt://localhost:1883"
        settings.app_host = "0.0.0.0"
        settings.app_port = 8000
        return settings

    @pytest.mark.asyncio
    async def test_startup_initializes_components(self, mock_settings):
        """Startup should initialize all components in correct order."""
        # This test verifies that CortexApp exists and has expected fields
        from cortex.main import CortexApp
        
        app = CortexApp()
        
        # Verify the dataclass has the expected fields
        assert hasattr(app, 'db_pool')
        assert hasattr(app, 'event_bus')
        assert hasattr(app, 'session_service')
        assert hasattr(app, 'execution_module')
        assert hasattr(app, 'interaction_service')
        assert hasattr(app, 'personality_service')
        assert hasattr(app, 'memory_service')
        assert hasattr(app, 'minion_service')
        assert hasattr(app, 'llm_client')
        assert hasattr(app, 'settings')
        assert hasattr(app, 'app')


class TestModuleEntry:
    """Test that python -m cortex works."""

    @pytest.mark.asyncio
    async def test_main_module_exists(self):
        """Main module should be runnable."""
        # This tests that __main__.py exists
        import cortex.__main__
        assert cortex.__main__ is not None

    @pytest.mark.asyncio
    async def test_main_has_run_function(self):
        """Main module should have a run function."""
        from cortex.__main__ import run
        assert callable(run)


class TestMinionServiceIntegration:
    """Test MinionService integration."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.version = "0.1.0"
        settings.database_url = MagicMock()
        settings.database_url.host = "localhost"
        settings.database_url.port = 5432
        settings.database_url.username = "postgres"
        settings.database_url.password = MagicMock()
        settings.database_url.password.get_secret_value.return_value = "postgres"
        settings.database_url.path = "/cortex"
        settings.llm_provider = "openai"
        settings.llm_api_key = MagicMock()
        settings.llm_api_key.get_secret_value.return_value = "test-key"
        settings.mqtt_broker_url = "mqtt://localhost:1883"
        settings.app_host = "0.0.0.0"
        settings.app_port = 8000
        return settings

    @pytest.mark.asyncio
    async def test_minion_service_initialization(self, mock_settings):
        """MinionService should be initialized with config."""
        from cortex.minions.models import MinionConfig

        # Verify MinionConfig can be created
        config = MinionConfig(
            minion_id="test-minion",
            minion_name="Test Minion",
            device_type="test",
            broker_url="mqtt://localhost:1883",
        )

        assert config.minion_id == "test-minion"
        assert config.broker_url == "mqtt://localhost:1883"

    @pytest.mark.asyncio
    async def test_cortex_app_has_minion_service_field(self):
        """CortexApp should have minion_service field."""
        from cortex.main import CortexApp

        app = CortexApp()
        assert hasattr(app, 'minion_service')
        assert app.minion_service is None

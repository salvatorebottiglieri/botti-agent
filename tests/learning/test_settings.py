"""LearningSettings and its wiring into LearningModule (issue #12).

``LearningSettings`` is the single source of the reservoir hyperparameters:
``LearningModule.from_settings`` builds the ESN from it, while the existing
``LearningModule(event_bus, esn)`` injection seam stays available.
"""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from cortex.config.learning import LearningSettings
from cortex.config.models import Settings
from cortex.events.bus import EventBus
from cortex.learning import LearningModule, MinionEventEncoder
from cortex.learning.readouts import SalienceReadout
from cortex.learning.reservoir import EchoStateNetwork, Reservoir

N_FEATURES = MinionEventEncoder().n_features


class TestLearningSettingsValidation:
    """Out-of-range hyperparameters are rejected at construction."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reservoir_size", 0),
            ("spectral_radius", 0.0),
            ("spectral_radius", 1.0),
            ("input_scale", 0.0),
            ("input_scale", -0.5),
            ("leak_rate", 0.0),
            ("leak_rate", 1.5),
            ("ridge_lambda", 0.0),
            ("ridge_lambda", -1e-9),
        ],
    )
    def test_out_of_range_values_are_rejected(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError, match=field):
            LearningSettings(_env_file=None, **{field: value})

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reservoir_size", 1),
            ("spectral_radius", 0.5),
            ("input_scale", 0.01),
            ("leak_rate", 1.0),
            ("ridge_lambda", 1e-12),
        ],
    )
    def test_boundary_values_are_accepted(self, field: str, value: float) -> None:
        learning = LearningSettings(_env_file=None, **{field: value})

        assert getattr(learning, field) == value


class TestLearningModuleFromSettings:
    """``from_settings`` builds the ESN from the configured hyperparameters."""

    def test_default_settings_build_the_adr_0001_reservoir(self) -> None:
        module = LearningModule.from_settings(
            EventBus(), LearningSettings(_env_file=None)
        )

        reservoir = module.esn.reservoir
        assert reservoir.n_reservoir == 500
        assert reservoir.alpha == 0.3
        assert reservoir.spectral_radius == 0.9
        assert reservoir.input_scaling == 1.0
        assert reservoir.n_input == N_FEATURES

    def test_module_respects_custom_settings(self) -> None:
        settings = LearningSettings(
            _env_file=None,
            reservoir_size=64,
            spectral_radius=0.7,
            input_scale=0.25,
            leak_rate=0.9,
        )

        module = LearningModule.from_settings(EventBus(), settings, seed=11)

        reservoir = module.esn.reservoir
        assert reservoir.n_reservoir == 64
        assert reservoir.alpha == 0.9
        assert reservoir.input_scaling == 0.25
        assert reservoir.spectral_radius == 0.7

    def test_module_built_from_root_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.chdir(tmp_path)

        settings = Settings(
            llm={"api_key": "k"},
            learning={"reservoir_size": 32},
        )
        module = LearningModule.from_settings(EventBus(), settings.learning, seed=4)

        assert module.esn.reservoir.n_reservoir == 32

    def test_custom_encoder_sizes_the_reservoir_input(self) -> None:
        encoder = MinionEventEncoder(event_types=("minion.location",))
        settings = LearningSettings(_env_file=None, reservoir_size=8)

        module = LearningModule.from_settings(
            EventBus(), settings, encoder=encoder, seed=3
        )

        assert encoder.n_features != N_FEATURES
        assert module.esn.reservoir.n_input == encoder.n_features

    def test_seed_makes_the_weights_reproducible(self) -> None:
        settings = LearningSettings(_env_file=None, reservoir_size=16)

        first = LearningModule.from_settings(EventBus(), settings, seed=5)
        second = LearningModule.from_settings(EventBus(), settings, seed=5)

        np.testing.assert_array_equal(
            first.esn.reservoir.W, second.esn.reservoir.W
        )

    def test_esn_property_exposes_the_owned_network(self) -> None:
        module = LearningModule.from_settings(
            EventBus(), LearningSettings(_env_file=None, reservoir_size=16)
        )

        module.esn.register_readout("salience", SalienceReadout())
        with pytest.raises(RuntimeError, match="not trained"):
            module.get_salience()

    def test_esn_property_returns_the_injected_network(self) -> None:
        esn = EchoStateNetwork(Reservoir(n_input=N_FEATURES, n_reservoir=8, seed=1))

        module = LearningModule(EventBus(), esn)

        assert module.esn is esn

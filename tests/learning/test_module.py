"""Tests for the learning module's event-bus integration.

Covers MinionEventEncoder (one-hot event-type block plus a normalized numeric
block) and LearningModule (subscriptions, event-driven ESN stepping, and
readout-backed getters) against a live in-memory EventBus.
"""

import math

import numpy as np
import pytest

from cortex.events.base import BaseEvent
from cortex.events.bus import EventBus
from cortex.learning import LearningModule, MinionEventEncoder
from cortex.learning.encoding import MINION_EVENT_TYPES
from cortex.learning.readouts import AnomalyReadout, PatternReadout, SalienceReadout
from cortex.learning.reservoir import EchoStateNetwork, Reservoir

N_FEATURES = MinionEventEncoder().n_features


def _make_esn(n_reservoir: int = 60, seed: int = 7) -> EchoStateNetwork:
    """An ESN sized for default encoder vectors, with a deterministic seed."""
    return EchoStateNetwork(
        Reservoir(
            n_input=N_FEATURES,
            n_reservoir=n_reservoir,
            alpha=0.5,
            spectral_radius=0.9,
            seed=seed,
        )
    )


def _drive(seqs, *, n_input, n_reservoir=60, seed=7):
    """Return the reservoir state after each input sequence (mirrors readouts tests)."""
    reservoir = Reservoir(
        n_input=n_input,
        n_reservoir=n_reservoir,
        alpha=0.5,
        spectral_radius=0.9,
        seed=seed,
    )
    states = []
    for seq in seqs:
        for u in seq:
            reservoir.update(u)
        states.append(reservoir.state.copy())
    return np.stack(states)


class TestMinionEventEncoder:
    """Type one-hot block + normalized numeric block produce valid inputs."""

    def test_default_feature_count_matches_contract(self):
        encoder = MinionEventEncoder()
        assert encoder.n_features == len(MINION_EVENT_TYPES) + 8

    def test_encode_returns_float64_vector_of_n_features(self):
        encoder = MinionEventEncoder()
        for event_type in MINION_EVENT_TYPES:
            event = BaseEvent.create(event_type, source_module="test")
            vector = encoder.encode(event)
            assert vector.shape == (encoder.n_features,)
            assert vector.dtype == np.float64

    def test_one_hot_bit_marks_exactly_the_matching_type(self):
        encoder = MinionEventEncoder()
        for i, event_type in enumerate(MINION_EVENT_TYPES):
            vector = encoder.encode(BaseEvent.create(event_type, source_module="test"))
            assert vector[i] == 1.0
            assert np.count_nonzero(vector[: len(MINION_EVENT_TYPES)]) == 1

    def test_unknown_type_is_zero_hot(self):
        encoder = MinionEventEncoder()
        vector = encoder.encode(BaseEvent.create("minion.unknown", source_module="test"))
        assert np.count_nonzero(vector[: len(MINION_EVENT_TYPES)]) == 0
        assert vector.shape == (encoder.n_features,)

    def test_unknown_type_never_raises(self):
        encoder = MinionEventEncoder()
        for payload in ({}, {"payload": {"level": 0.5}}, {"latitude": 10.0}):
            vector = encoder.encode(
                BaseEvent.create("minion.unknown", payload=payload, source_module="test")
            )
            assert vector.shape == (encoder.n_features,)

    def test_mid_range_numeric_fields_normalize_to_zero(self):
        encoder = MinionEventEncoder()
        event = BaseEvent.create(
            "minion.location",
            payload={
                "minion_id": "m-1",
                "event_type": "location",
                "payload": {
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "accuracy": 250.0,
                    "speed": 50.0,
                    "heading": 180.0,
                },
                "timestamp": "2026-01-01T00:00:00Z",
            },
            source_module="minion_event_processor",
        )
        vector = encoder.encode(event)
        offset = len(MINION_EVENT_TYPES)
        np.testing.assert_allclose(vector[offset : offset + 5], 0.0, atol=1e-12)

    def test_range_extremes_map_to_unit_bounds(self):
        encoder = MinionEventEncoder()
        event = BaseEvent.create(
            "minion.location",
            payload={
                "payload": {
                    "latitude": -90.0,
                    "longitude": 180.0,
                    "accuracy": 0.0,
                    "speed": 100.0,
                    "heading": 360.0,
                }
            },
            source_module="test",
        )
        vector = encoder.encode(event)
        offset = len(MINION_EVENT_TYPES)
        assert vector[offset] == -1.0  # latitude at min
        assert vector[offset + 1] == 1.0  # longitude at max
        assert vector[offset + 2] == -1.0  # accuracy at min
        assert vector[offset + 3] == 1.0  # speed at max
        assert vector[offset + 4] == 1.0  # heading at max

    def test_out_of_range_values_are_clamped(self):
        encoder = MinionEventEncoder()
        vector = encoder.encode(
            BaseEvent.create(
                "minion.battery",
                payload={"payload": {"level": 2.0, "confidence": -1.0}},
                source_module="test",
            )
        )
        offset = len(MINION_EVENT_TYPES)
        assert vector[offset + 5] == 1.0  # level 2.0 clamped to +1
        assert vector[offset + 6] == -1.0  # confidence -1.0 clamped to -1

    def test_missing_numeric_fields_default_to_zero(self):
        encoder = MinionEventEncoder()
        vector = encoder.encode(
            BaseEvent.create("minion.battery", payload={"payload": {}}, source_module="test")
        )
        offset = len(MINION_EVENT_TYPES)
        assert vector[offset + 5] == 0.0  # level missing -> 0.0

    def test_flat_payload_fallback_without_nested_payload_key(self):
        encoder = MinionEventEncoder()
        vector = encoder.encode(
            BaseEvent.create("minion.battery", payload={"level": 0.75}, source_module="test")
        )
        offset = len(MINION_EVENT_TYPES)
        assert vector[offset + 5] == pytest.approx(0.5)

    def test_non_numeric_numeric_field_defaults_to_zero(self):
        encoder = MinionEventEncoder()
        vector = encoder.encode(
            BaseEvent.create(
                "minion.location",
                payload={"payload": {"latitude": "abc", "longitude": 10.0}},
                source_module="test",
            )
        )
        offset = len(MINION_EVENT_TYPES)
        assert vector[offset] == 0.0  # latitude "abc" -> 0.0
        assert vector[offset + 1] == pytest.approx(
            2.0 * (10.0 - (-180.0)) / 360.0 - 1.0
        )  # longitude 10.0 still normalized

    def test_non_finite_numeric_fields_default_to_zero(self):
        encoder = MinionEventEncoder()
        vector = encoder.encode(
            BaseEvent.create(
                "minion.battery",
                payload={"payload": {"level": float("nan"), "confidence": float("inf")}},
                source_module="test",
            )
        )
        offset = len(MINION_EVENT_TYPES)
        assert vector[offset + 5] == 0.0  # NaN -> 0.0
        assert vector[offset + 6] == 0.0  # inf -> 0.0

    def test_all_encoded_values_stay_within_unit_range(self):
        encoder = MinionEventEncoder()
        event = BaseEvent.create(
            "minion.location",
            payload={
                "payload": {
                    "latitude": 45.5,
                    "longitude": -123.4,
                    "accuracy": 1000.0,
                    "speed": -10.0,
                    "heading": 500.0,
                    "level": 0.75,
                    "confidence": 0.2,
                    "duration_seconds": 43200,
                }
            },
            source_module="test",
        )
        vector = encoder.encode(event)
        assert np.all(vector >= -1.0)
        assert np.all(vector <= 1.0)

    def test_custom_event_types_resize_one_hot_block(self):
        encoder = MinionEventEncoder(event_types=("minion.location", "minion.battery"))
        assert encoder.n_features == 2 + 8
        location = encoder.encode(
            BaseEvent.create(
                "minion.location", payload={"payload": {"latitude": 0.0}}, source_module="test"
            )
        )
        assert location[0] == 1.0 and location[1] == 0.0
        battery = encoder.encode(
            BaseEvent.create(
                "minion.battery", payload={"payload": {"level": 0.5}}, source_module="test"
            )
        )
        assert battery[0] == 0.0 and battery[1] == 1.0
        # unknown relative to the custom list is zero-hot too
        unknown = encoder.encode(BaseEvent.create("minion.activity", source_module="test"))
        assert np.count_nonzero(unknown[:2]) == 0


class TestLearningModuleSubscribe:
    """subscribe() registers exactly one handler per minion event type."""

    async def test_one_subscription_per_minion_event_type(self):
        bus = EventBus()
        await bus.start()
        module = LearningModule(bus, _make_esn())
        try:
            await module.subscribe()
            for event_type in MINION_EVENT_TYPES:
                assert await bus.get_subscription_count(event_type) == 1
        finally:
            await bus.stop()

    async def test_subscribe_twice_is_idempotent(self):
        bus = EventBus()
        await bus.start()
        encoder = MinionEventEncoder()
        reservoir = Reservoir(n_input=N_FEATURES, n_reservoir=50, seed=3)
        esn = EchoStateNetwork(reservoir)
        module = LearningModule(bus, esn)
        try:
            await module.subscribe()
            await module.subscribe()
            for event_type in MINION_EVENT_TYPES:
                assert await bus.get_subscription_count(event_type) == 1
            event = BaseEvent.create(
                "minion.location",
                payload={"payload": {"latitude": 10.0}},
                source_module="test",
            )
            await bus.publish(event)
            # A doubled subscription would step the reservoir twice; the state
            # must equal a single-step reference on the same reservoir config.
            reference = Reservoir(n_input=N_FEATURES, n_reservoir=50, seed=3)
            reference.update(encoder.encode(event))
            np.testing.assert_array_equal(reservoir.state, reference.state)
        finally:
            await bus.stop()

    async def test_no_wildcard_subscription_is_created(self):
        bus = EventBus()
        await bus.start()
        module = LearningModule(bus, _make_esn())
        try:
            await module.subscribe()
            assert await bus.get_subscription_count("*") == 0
        finally:
            await bus.stop()


class TestLearningModuleOnEvent:
    """Published minion events advance the ESN via the encoder."""

    async def test_published_event_advances_reservoir_state(self):
        bus = EventBus()
        await bus.start()
        reservoir = Reservoir(n_input=N_FEATURES, n_reservoir=50, seed=2)
        esn = EchoStateNetwork(reservoir)
        module = LearningModule(bus, esn)
        try:
            await module.subscribe()
            await bus.publish(
                BaseEvent.create(
                    "minion.location",
                    payload={"payload": {"latitude": 10.0, "longitude": 20.0}},
                    source_module="minion_event_processor",
                )
            )
            first_state = reservoir.state.copy()
            assert not np.all(first_state == 0.0)
            await bus.publish(
                BaseEvent.create(
                    "minion.battery",
                    payload={"payload": {"level": 0.8}},
                    source_module="minion_event_processor",
                )
            )
            assert not np.array_equal(reservoir.state, first_state)
        finally:
            await bus.stop()

    async def test_unknown_event_type_does_not_crash_or_corrupt(self):
        bus = EventBus()
        await bus.start()
        esn = _make_esn(n_reservoir=50, seed=2)
        module = LearningModule(bus, esn)
        try:
            await module.subscribe()
            await bus.publish(BaseEvent.create("minion.unknown", source_module="test"))
            await bus.publish(BaseEvent.create("custom.thing", source_module="test"))
            # a known event still flows afterwards
            await bus.publish(
                BaseEvent.create(
                    "minion.location",
                    payload={"payload": {"latitude": 5.0}},
                    source_module="test",
                )
            )
            assert not np.all(esn.reservoir.state == 0.0)
        finally:
            await bus.stop()

    async def test_direct_call_with_unknown_type_is_safe(self):
        module = LearningModule(EventBus(), _make_esn(n_reservoir=50, seed=2))
        await module.on_minion_event(BaseEvent.create("minion.unknown", source_module="test"))
        await module.on_minion_event(BaseEvent.create("custom.x", source_module="test"))

    async def test_custom_encoder_drives_smaller_input_vectors(self):
        bus = EventBus()
        await bus.start()
        encoder = MinionEventEncoder(event_types=("minion.location",))
        reservoir = Reservoir(n_input=encoder.n_features, n_reservoir=50, seed=1)
        esn = EchoStateNetwork(reservoir)
        module = LearningModule(bus, esn, encoder=encoder)
        try:
            await module.subscribe()
            await bus.publish(
                BaseEvent.create(
                    "minion.location",
                    payload={"payload": {"latitude": 10.0}},
                    source_module="minion_event_processor",
                )
            )
            # 9-dim vector (1 hot + 8 numeric) matches the reservoir input size;
            # a dimension mismatch would raise inside the handler, leaving state zero.
            assert not np.all(reservoir.state == 0.0)
        finally:
            await bus.stop()


class TestLearningModuleConstruction:
    """LearningModule validates the encoder/reservoir contract at init."""

    def test_mismatched_encoder_and_reservoir_raise_value_error(self):
        encoder = MinionEventEncoder(event_types=("minion.location",))
        esn = _make_esn()  # reservoir input sized for the default encoder
        with pytest.raises(ValueError) as excinfo:
            LearningModule(EventBus(), esn, encoder=encoder)
        message = str(excinfo.value)
        assert f"encoder.n_features ({encoder.n_features})" in message
        assert f"esn.reservoir.n_input ({esn.reservoir.n_input})" in message


class TestLearningModuleReadouts:
    """Getters resolve the registered readouts and score the current state."""

    @staticmethod
    def _trained_esn(n_reservoir=60, seed=7):
        esn = _make_esn(n_reservoir=n_reservoir, seed=seed)

        def drive(seqs):
            return _drive(seqs, n_input=N_FEATURES, n_reservoir=n_reservoir, seed=seed)

        n_samples = 100
        quiet = [np.zeros(N_FEATURES)] * 4
        active = [np.ones(N_FEATURES)] * 4
        salience = SalienceReadout()
        sal_states = drive([quiet if i % 2 == 0 else active for i in range(n_samples)])
        salience.train(
            sal_states,
            np.array([0.0 if i % 2 == 0 else 1.0 for i in range(n_samples)]),
        )

        rng = np.random.default_rng(11)
        anomaly = AnomalyReadout()
        normal = drive([[rng.uniform(-0.5, 0.5, N_FEATURES)] for _ in range(60)])
        anomaly.train(normal)

        directions = [np.eye(N_FEATURES)[0], np.eye(N_FEATURES)[1], np.eye(N_FEATURES)[2]]
        pattern = PatternReadout(["location", "battery", "activity"])
        pattern_states = drive([[directions[i % 3]] * 4 for i in range(150)])
        pattern.train(pattern_states, (np.arange(150) % 3).astype(np.int64))

        esn.register_readout("salience", salience)
        esn.register_readout("anomaly", anomaly)
        esn.register_readout("pattern", pattern)
        return esn

    def test_get_salience_returns_unit_range_float_after_training(self):
        esn = self._trained_esn()
        module = LearningModule(EventBus(), esn)
        for _ in range(4):
            esn.reservoir.update(np.ones(N_FEATURES))
        active = module.get_salience()
        esn.reservoir.reset()
        for _ in range(4):
            esn.reservoir.update(np.zeros(N_FEATURES))
        quiet = module.get_salience()
        assert isinstance(active, float)
        assert isinstance(quiet, float)
        assert 0.0 <= active <= 1.0
        assert 0.0 <= quiet <= 1.0
        assert active > quiet

    def test_get_anomaly_score_returns_finite_float_after_training(self):
        esn = self._trained_esn()
        module = LearningModule(EventBus(), esn)
        rng = np.random.default_rng(21)
        for _ in range(4):
            esn.reservoir.update(rng.uniform(-0.5, 0.5, N_FEATURES))
        score = module.get_anomaly_score()
        assert isinstance(score, float)
        assert math.isfinite(score)
        assert score >= 0.0

    def test_get_pattern_probabilities_form_distribution_after_training(self):
        esn = self._trained_esn()
        module = LearningModule(EventBus(), esn)
        for _ in range(4):
            esn.reservoir.update(np.eye(N_FEATURES)[0])
        probabilities = module.get_pattern_probabilities()
        assert set(probabilities) == {"location", "battery", "activity"}
        assert all(0.0 <= p <= 1.0 for p in probabilities.values())
        assert sum(probabilities.values()) == pytest.approx(1.0)
        assert max(probabilities, key=probabilities.get) == "location"

    def test_get_readout_raises_key_error_for_unknown_name(self):
        esn = _make_esn(seed=1)
        with pytest.raises(KeyError, match="missing"):
            esn.get_readout("missing")

    def test_getters_raise_when_readout_not_registered(self):
        module = LearningModule(EventBus(), _make_esn(seed=1))
        with pytest.raises(RuntimeError, match="salience"):
            module.get_salience()
        with pytest.raises(RuntimeError, match="anomaly"):
            module.get_anomaly_score()
        with pytest.raises(RuntimeError, match="pattern"):
            module.get_pattern_probabilities()

    def test_getters_raise_when_readout_untrained(self):
        esn = _make_esn(seed=1)
        esn.register_readout("salience", SalienceReadout())
        esn.register_readout("anomaly", AnomalyReadout())
        esn.register_readout("pattern", PatternReadout(["a", "b"]))
        module = LearningModule(EventBus(), esn)
        with pytest.raises(RuntimeError, match="not trained"):
            module.get_salience()
        with pytest.raises(RuntimeError, match="not trained"):
            module.get_anomaly_score()
        with pytest.raises(RuntimeError, match="not trained"):
            module.get_pattern_probabilities()

    def test_getters_reject_wrong_readout_type(self):
        esn = _make_esn(seed=1)
        esn.register_readout("salience", AnomalyReadout())
        module = LearningModule(EventBus(), esn)
        with pytest.raises(RuntimeError, match="SalienceReadout"):
            module.get_salience()

    async def test_bus_events_drive_readout_scoring(self):
        bus = EventBus()
        await bus.start()
        esn = self._trained_esn()
        module = LearningModule(bus, esn)
        try:
            await module.subscribe()
            await bus.publish(
                BaseEvent.create(
                    "minion.location",
                    payload={"payload": {"latitude": 0.0, "longitude": 0.0}},
                    source_module="minion_event_processor",
                )
            )
            assert isinstance(module.get_salience(), float)
            probabilities = module.get_pattern_probabilities()
            assert sum(probabilities.values()) == pytest.approx(1.0)
        finally:
            await bus.stop()

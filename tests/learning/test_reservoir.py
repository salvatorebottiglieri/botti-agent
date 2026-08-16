"""Tests for the echo state network core (Reservoir, Readout, EchoStateNetwork)."""

import numpy as np
import pytest

from cortex.learning.reservoir import EchoStateNetwork, Readout, Reservoir


class TestReservoirInit:
    """Weight matrices, initial state, and defaults."""

    def test_initial_state_is_zero(self):
        res = Reservoir(n_input=3, n_reservoir=50, seed=1)
        assert res.state.shape == (50,)
        assert np.all(res.state == 0.0)

    def test_default_reservoir_size(self):
        res = Reservoir(n_input=2)
        assert res.state.shape == (500,)
        assert res.W.shape == (500, 500)
        assert res.W_in.shape == (500, 2)

    def test_input_weights_bounded_by_input_scaling(self):
        res = Reservoir(n_input=4, n_reservoir=200, input_scaling=0.5, seed=2)
        assert res.W_in.shape == (200, 4)
        assert np.all(res.W_in >= -0.5)
        assert np.all(res.W_in <= 0.5)


class TestReservoirSpectralRadius:
    """W is rescaled so its actual spectral radius matches the configured value."""

    def _spectral_radius(self, res):
        return float(np.max(np.abs(np.linalg.eigvals(res.W))))

    def test_default_spectral_radius(self):
        res = Reservoir(n_input=2, n_reservoir=100, seed=11)
        assert np.isclose(self._spectral_radius(res), 1.0, rtol=1e-6)

    def test_non_default_spectral_radius(self):
        res = Reservoir(n_input=2, n_reservoir=100, spectral_radius=0.7, seed=11)
        assert np.isclose(self._spectral_radius(res), 0.7, rtol=1e-6)

    def test_seeded_init_is_reproducible(self):
        first = Reservoir(n_input=3, n_reservoir=60, seed=5)
        second = Reservoir(n_input=3, n_reservoir=60, seed=5)
        assert np.array_equal(first.W, second.W)
        assert np.array_equal(first.W_in, second.W_in)


class TestReservoirStateUpdate:
    """state stays (n_reservoir,) across any number of updates; weights are fixed."""

    def test_state_shape_invariant_across_many_updates(self):
        rng = np.random.default_rng(0)
        res = Reservoir(n_input=3, n_reservoir=64, seed=4)
        inputs = rng.uniform(-1.0, 1.0, (200, 3))
        for u in inputs:
            out = res.update(u)
            assert out.shape == (64,)
            assert res.state.shape == (64,)
        assert res.state.shape == (64,)

    def test_state_shape_invariant_for_any_sequence_length(self):
        rng = np.random.default_rng(1)
        res = Reservoir(n_input=3, n_reservoir=64, seed=4)
        for length in (1, 10, 100):
            for u in rng.uniform(-1.0, 1.0, (length, 3)):
                res.update(u)
            assert res.state.shape == (64,)

    def test_update_matches_contract_formula(self):
        res = Reservoir(n_input=1, n_reservoir=8, alpha=0.3, seed=9)
        res.state = np.full(8, 0.1)
        u = np.array([0.7])
        expected = 0.7 * res.state + 0.3 * np.tanh(res.W_in @ u + res.W @ res.state)
        np.testing.assert_allclose(res.update(u), expected)

    def test_weights_fixed_across_updates(self):
        rng = np.random.default_rng(8)
        res = Reservoir(n_input=2, n_reservoir=32, seed=6)
        w_in_before = res.W_in.copy()
        w_before = res.W.copy()
        for u in rng.uniform(-1.0, 1.0, (50, 2)):
            res.update(u)
        np.testing.assert_array_equal(res.W_in, w_in_before)
        np.testing.assert_array_equal(res.W, w_before)

    def test_update_rejects_wrong_shape_input_and_keeps_state(self):
        res = Reservoir(n_input=3, n_reservoir=64, seed=4)
        state_before = res.state.copy()
        with pytest.raises(ValueError, match=r"expected \(3,\), got \(3, 1\)"):
            res.update(np.ones((3, 1)))
        np.testing.assert_array_equal(res.state, state_before)


class TestReservoirLeakRate:
    """Lower alpha retains past input influence longer (impulse decays slower)."""

    @staticmethod
    def _impulse_response(alpha, n_steps=150):
        res = Reservoir(
            n_input=1, n_reservoir=50, alpha=alpha, spectral_radius=0.5, seed=7
        )
        res.update(np.ones(1))
        norms = [np.linalg.norm(res.state)]
        for _ in range(n_steps):
            res.update(np.zeros(1))
            norms.append(np.linalg.norm(res.state))
        return np.asarray(norms)

    @staticmethod
    def _decay_time(norms, fraction=0.2):
        threshold = fraction * norms.max()
        for i, norm in enumerate(norms):
            if norm < threshold:
                return i
        return len(norms)

    def test_lower_alpha_decays_slower(self):
        slow = self._impulse_response(alpha=0.1)
        fast = self._impulse_response(alpha=0.9)
        assert self._decay_time(slow) > self._decay_time(fast)


class TestReservoirReset:
    """reset() zeroes the state."""

    def test_reset_zeroes_state(self):
        rng = np.random.default_rng(2)
        res = Reservoir(n_input=3, n_reservoir=50, seed=3)
        for u in rng.uniform(-1.0, 1.0, (20, 3)):
            res.update(u)
        assert np.any(res.state != 0.0)
        res.reset()
        assert np.all(res.state == 0.0)
        assert res.state.shape == (50,)


class TestReadoutTraining:
    """Ridge regression learns a linear function of reservoir states."""

    @staticmethod
    def _state_batch(n_input=4, n_states=300, seed=42):
        rng = np.random.default_rng(seed)
        reservoir = Reservoir(
            n_input=n_input, n_reservoir=80, alpha=0.5, spectral_radius=0.9, seed=3
        )
        inputs = rng.uniform(-1.0, 1.0, (n_states, n_input))
        return np.stack([reservoir.update(u) for u in inputs])

    def test_converges_on_synthetic_signal(self):
        states = self._state_batch()
        weights = np.zeros(80)
        weights[:6] = [2.0, -1.5, 0.8, 0.0, 0.0, 0.3]
        targets = states @ weights

        readout = Readout(ridge_lambda=1e-8)
        readout.train(states, targets)

        predictions = np.asarray([readout.predict(s).item() for s in states])
        mse = float(np.mean((predictions - targets) ** 2))
        assert mse / float(np.var(targets)) < 1e-8

    def test_generalizes_to_unseen_states(self):
        rng = np.random.default_rng(42)
        reservoir = Reservoir(
            n_input=4, n_reservoir=80, alpha=0.5, spectral_radius=0.9, seed=3
        )
        train_inputs = rng.uniform(-1.0, 1.0, (200, 4))
        train_states = np.stack([reservoir.update(u) for u in train_inputs])
        weights = np.zeros(80)
        weights[:6] = [2.0, -1.5, 0.8, 0.0, 0.0, 0.3]

        readout = Readout(ridge_lambda=1e-8)
        readout.train(train_states, train_states @ weights)

        fresh = Reservoir(
            n_input=4, n_reservoir=80, alpha=0.5, spectral_radius=0.9, seed=3
        )
        test_inputs = rng.uniform(-1.0, 1.0, (100, 4))
        test_states = np.stack([fresh.update(u) for u in test_inputs])
        test_targets = test_states @ weights

        predictions = np.asarray([readout.predict(s).item() for s in test_states])
        mse = float(np.mean((predictions - test_targets) ** 2))
        assert mse / float(np.var(test_targets)) < 1e-8

    def test_scalar_targets_keep_scalar_readout(self):
        rng = np.random.default_rng(7)
        states = rng.uniform(-1.0, 1.0, (50, 10))
        targets = rng.uniform(-1.0, 1.0, (50,))
        readout = Readout()
        readout.train(states, targets)
        assert readout.W_out is not None
        assert readout.W_out.shape == (10,)
        prediction = readout.predict(states[0])
        assert prediction.shape == (1,)
        assert np.isclose(prediction[0], states[0] @ readout.W_out)

    def test_vector_targets_keep_vector_readout(self):
        rng = np.random.default_rng(7)
        states = rng.uniform(-1.0, 1.0, (50, 10))
        targets = rng.uniform(-1.0, 1.0, (50, 3))
        readout = Readout()
        readout.train(states, targets)
        assert readout.W_out is not None
        assert readout.W_out.shape == (10, 3)
        assert readout.predict(states[0]).shape == (3,)

    def test_empty_states_raise_value_error(self):
        readout = Readout()
        with pytest.raises(ValueError, match="states must be"):
            readout.train(np.asarray([]), np.asarray([]))

    def test_zero_sample_states_raise_value_error(self):
        readout = Readout()
        with pytest.raises(ValueError, match="at least one sample"):
            readout.train(np.zeros((0, 5)), np.zeros(0))

    def test_mismatched_row_counts_raise_value_error(self):
        readout = Readout()
        with pytest.raises(ValueError, match="same number of rows"):
            readout.train(np.zeros((5, 3)), np.zeros(4))


class TestReadoutUntrained:
    """predict() before train() raises RuntimeError."""

    def test_predict_before_train_raises(self):
        readout = Readout()
        with pytest.raises(RuntimeError):
            readout.predict(np.zeros(5))

    def test_trained_flag(self):
        readout = Readout()
        assert not readout.trained()
        readout.train(np.zeros((3, 5)), np.zeros(3))
        assert readout.trained()


class TestEchoStateNetwork:
    """One reservoir, multiple named readouts."""

    def test_step_drives_reservoir(self):
        reservoir = Reservoir(n_input=2, n_reservoir=32, seed=6)
        esn = EchoStateNetwork(reservoir)
        state = esn.step(np.array([0.5, -0.5]))
        assert state.shape == (32,)
        np.testing.assert_array_equal(state, reservoir.state)

    def test_read_returns_readout_prediction_of_state(self):
        rng = np.random.default_rng(3)
        reservoir = Reservoir(n_input=2, n_reservoir=32, seed=6)
        esn = EchoStateNetwork(reservoir)
        readout = Readout()
        states = np.stack(
            [reservoir.update(rng.uniform(-1.0, 1.0, 2)) for _ in range(50)]
        )
        readout.train(states, states @ np.full(32, 0.01))
        esn.register_readout("signal", readout)

        reservoir.update(np.array([0.3, -0.2]))
        expected = readout.predict(reservoir.state)
        np.testing.assert_array_equal(esn.read("signal"), expected)

    def test_duplicate_readout_name_raises(self):
        esn = EchoStateNetwork(Reservoir(n_input=2, n_reservoir=32))
        esn.register_readout("a", Readout())
        with pytest.raises(ValueError):
            esn.register_readout("a", Readout())

    def test_unknown_readout_name_raises_key_error(self):
        esn = EchoStateNetwork(Reservoir(n_input=2, n_reservoir=32))
        with pytest.raises(KeyError):
            esn.read("missing")

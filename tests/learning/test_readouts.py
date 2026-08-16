"""Tests for the specialized learning readouts.

Covers SalienceReadout, AnomalyReadout, and PatternReadout: training on
reservoir-driven synthetic data, threshold boundaries, edge cases, and sharing
one reservoir across all three readouts.
"""

import warnings

import numpy as np
import pytest

from cortex.learning import AnomalyReadout, PatternReadout, SalienceReadout
from cortex.learning.reservoir import EchoStateNetwork, Reservoir


def _drive(input_seqs, *, n_input=2, n_reservoir=80, seed=3):
    """Return the reservoir state after each input sequence."""
    reservoir = Reservoir(
        n_input=n_input,
        n_reservoir=n_reservoir,
        alpha=0.5,
        spectral_radius=0.9,
        seed=seed,
    )
    states = []
    for seq in input_seqs:
        for u in seq:
            reservoir.update(u)
        states.append(reservoir.state.copy())
    return np.stack(states)


def _salience_data(n_samples=200, n_holdout=200, steps=8):
    """Alternating quiet (zeros) and active (ones) input bursts.

    Quiet bursts drive the reservoir state toward zero (target 0); active
    bursts push it into a distinct region (target 1).
    """

    def seqs(n):
        return [
            [np.zeros(2)] * steps if i % 2 == 0 else [np.ones(2)] * steps
            for i in range(n)
        ]

    targets = np.array([0.0 if i % 2 == 0 else 1.0 for i in range(n_samples)])
    holdout_targets = np.array(
        [0.0 if i % 2 == 0 else 1.0 for i in range(n_holdout)]
    )
    return (
        _drive(seqs(n_samples)),
        targets,
        _drive(seqs(n_holdout)),
        holdout_targets,
    )


def _normal_states(n_states=60):
    """Randomly driven reservoir states used as the 'normal' distribution."""
    rng = np.random.default_rng(5)
    return _drive(
        [[rng.uniform(-1.0, 1.0, 3)] for _ in range(n_states)], n_input=3
    )


_PATTERN_DIRS = (
    np.array([1.0, 0.0]),
    np.array([-0.5, np.sqrt(3.0) / 2.0]),
    np.array([-0.5, -np.sqrt(3.0) / 2.0]),
)


def _pattern_data(n_train=300, n_holdout=150):
    """Inputs aimed along three directions; class = direction index."""

    def seqs(n):
        return [[_PATTERN_DIRS[i % 3]] * 4 for i in range(n)]

    labels = (np.arange(n_train) % 3).astype(np.int64)
    holdout_labels = (np.arange(n_holdout) % 3).astype(np.int64)
    return _drive(seqs(n_train)), labels, _drive(seqs(n_holdout)), holdout_labels


class TestSalienceReadout:
    """Salience scores follow 0/1 targets; the threshold is inclusive."""

    def test_is_salient_matches_labels(self):
        states, targets, holdout, holdout_targets = _salience_data()
        readout = SalienceReadout(threshold=0.5)
        readout.train(states, targets)

        train_match = np.asarray([readout.is_salient(s) for s in states])
        np.testing.assert_array_equal(train_match, targets == 1.0)

        holdout_match = np.asarray([readout.is_salient(s) for s in holdout])
        np.testing.assert_array_equal(holdout_match, holdout_targets == 1.0)

    def test_score_converges_on_binary_targets(self):
        states, targets, _, _ = _salience_data()
        readout = SalienceReadout(threshold=0.5)
        readout.train(states, targets)
        scores = np.asarray([readout.score(s) for s in states])
        assert float(np.mean((scores - targets) ** 2)) < 1e-4

    def test_threshold_boundary_is_inclusive(self):
        states, targets, _, _ = _salience_data()
        readout = SalienceReadout(threshold=0.5)
        readout.train(states, targets)

        state = states[0]
        exact = SalienceReadout(threshold=readout.score(state))
        exact.train(states, targets)
        assert exact.is_salient(state)  # score == threshold is salient

        just_above = SalienceReadout(threshold=readout.score(state) + 1e-9)
        just_above.train(states, targets)
        assert not just_above.is_salient(state)


class TestAnomalyReadout:
    """Identity-map readout: normal states reconstruct; corrupted ones don't."""

    def test_corrupted_states_are_flagged(self):
        normal = _normal_states()
        readout = AnomalyReadout(threshold=0.05)
        readout.train(normal)

        normal_errors = np.asarray(
            [readout.reconstruction_error(s) for s in normal]
        )
        rng = np.random.default_rng(5)
        corrupted = normal + rng.normal(0.0, 1.0, normal.shape)
        corrupted_errors = np.asarray(
            [readout.reconstruction_error(s) for s in corrupted]
        )

        assert np.all(normal_errors < readout.threshold)
        assert np.all(corrupted_errors > readout.threshold)
        assert np.all(normal_errors < corrupted_errors.min())

    def test_threshold_boundary_is_strict(self):
        normal = _normal_states()
        readout = AnomalyReadout(threshold=0.05)
        readout.train(normal)

        state = normal[0]
        error = readout.reconstruction_error(state)
        exact = AnomalyReadout(threshold=error)
        exact.train(normal)
        assert not exact.is_anomalous(state)  # equal is NOT anomalous

        just_below = AnomalyReadout(threshold=error / 2.0)
        just_below.train(normal)
        assert just_below.is_anomalous(state)

    def test_one_dimensional_states_raise(self):
        with pytest.raises(ValueError, match=r"\(n_samples, n_features\)"):
            AnomalyReadout().train(np.zeros(5))


class TestPatternReadout:
    """One-hot readout: valid distributions, label bounds, softmax stability."""

    CLASSES = ["alpha", "beta", "gamma"]

    def test_probabilities_form_valid_distribution(self):
        states, labels, _, _ = _pattern_data()
        readout = PatternReadout(self.CLASSES)
        readout.train(states, labels)

        for state in states[:10]:
            probs = readout.get_pattern_probabilities(state)
            assert set(probs) == set(self.CLASSES)
            assert all(p >= 0.0 for p in probs.values())
            assert sum(probs.values()) == pytest.approx(1.0)

    def test_classifies_unseen_states(self):
        states, labels, holdout, holdout_labels = _pattern_data()
        readout = PatternReadout(self.CLASSES)
        readout.train(states, labels)

        predicted = np.asarray(
            [
                max(
                    readout.get_pattern_probabilities(s).items(),
                    key=lambda item: item[1],
                )[0]
                for s in holdout
            ]
        )
        expected = np.asarray(self.CLASSES)[holdout_labels]
        np.testing.assert_array_equal(predicted, expected)

    def test_predict_returns_raw_logits(self):
        states, labels, _, _ = _pattern_data()
        readout = PatternReadout(self.CLASSES)
        readout.train(states, labels)

        logits = readout.predict(states[0])
        assert logits.shape == (len(self.CLASSES),)
        probs = readout.get_pattern_probabilities(states[0])
        assert not np.allclose(logits, np.asarray(list(probs.values())))

    def test_fractional_labels_raise(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match="integer"):
            readout.train(states[:2], np.array([0.0, 0.5]))

    def test_whole_valued_float_labels_train(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        readout.train(states[:2], np.array([0.0, 1.0]))

        reference = PatternReadout(["a", "b"])
        reference.train(states[:2], np.array([0, 1], dtype=np.int64))
        np.testing.assert_array_equal(readout.W_out, reference.W_out)

    def test_bool_labels_train(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        readout.train(states[:2], np.array([True, False]))

        reference = PatternReadout(["a", "b"])
        reference.train(states[:2], np.array([1, 0], dtype=np.int64))
        np.testing.assert_array_equal(readout.W_out, reference.W_out)

    def test_fractional_label_raises(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match="integer"):
            readout.train(states[:1], np.array([0.5]))

    def test_nan_label_raises(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match="integer"):
            readout.train(states[:2], np.array([0.0, np.nan]))

    def test_inf_label_raises_without_runtime_warning(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with pytest.raises(ValueError, match=r"\[0, 2\)"):
                readout.train(states[:1], np.array([np.inf]))

    def test_huge_float_label_raises_without_runtime_warning(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with pytest.raises(ValueError, match=r"\[0, 2\)"):
                readout.train(states[:1], np.array([1e300]))

    def test_two_dimensional_labels_raise(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match=r"\(n_samples,\)"):
            readout.train(states[:2], np.array([[0, 1], [1, 0]], dtype=np.int64))

    def test_row_count_mismatch_raises(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match="same number of rows"):
            readout.train(states[:3], np.array([0, 1], dtype=np.int64))

    def test_one_dimensional_states_raise(self):
        readout = PatternReadout(["a", "b", "c"])
        with pytest.raises(ValueError, match=r"\(n_samples, n_features\)"):
            readout.train(np.zeros(3), np.array([0, 1, 2], dtype=np.int64))

    def test_out_of_range_label_raises(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match=r"\[0, 2\)"):
            readout.train(states[:3], np.array([0, 1, 2], dtype=np.int64))

    def test_negative_label_raises(self):
        states, _, _, _ = _pattern_data(n_train=10)
        readout = PatternReadout(["a", "b"])
        with pytest.raises(ValueError, match=r"\[0, 2\)"):
            readout.train(states[:2], np.array([0, -1], dtype=np.int64))

    def test_duplicate_classes_raise(self):
        with pytest.raises(ValueError, match="unique"):
            PatternReadout(["a", "a"])

    def test_empty_classes_raises(self):
        with pytest.raises(ValueError):
            PatternReadout([])

    def test_softmax_is_stable_for_extreme_logits(self):
        readout = PatternReadout(["left", "right"])
        readout.W_out = np.array([[1000.0, -1000.0], [0.0, 0.0]])
        probs = readout.get_pattern_probabilities(np.array([1.0, 0.0]))
        assert np.all(np.isfinite(list(probs.values())))
        assert sum(probs.values()) == pytest.approx(1.0)
        assert probs["left"] > 0.99


class TestSharedReservoirReadouts:
    """All three readouts attach to one EchoStateNetwork reservoir."""

    def test_one_step_reads_all_three_from_same_state(self):
        reservoir = Reservoir(n_input=2, n_reservoir=64, seed=6)
        esn = EchoStateNetwork(reservoir)

        salience = SalienceReadout(threshold=0.5)
        anomaly = AnomalyReadout(threshold=0.05)
        pattern = PatternReadout(["low", "high"])

        rng = np.random.default_rng(3)
        states = np.stack(
            [reservoir.update(rng.uniform(-1.0, 1.0, 2)) for _ in range(200)]
        )
        targets = np.array([0.0 if i % 2 == 0 else 1.0 for i in range(200)])
        labels = (np.arange(200) % 2).astype(np.int64)

        salience.train(states, targets)
        anomaly.train(states)
        pattern.train(states, labels)

        esn.register_readout("salience", salience)
        esn.register_readout("anomaly", anomaly)
        esn.register_readout("pattern", pattern)

        esn.step(np.array([0.3, -0.2]))
        shared_state = reservoir.state

        np.testing.assert_array_equal(
            esn.read("salience"), salience.predict(shared_state)
        )
        np.testing.assert_array_equal(
            esn.read("anomaly"), anomaly.predict(shared_state)
        )
        np.testing.assert_array_equal(
            esn.read("pattern"), pattern.predict(shared_state)
        )

        assert esn.read("salience").shape == (1,)
        assert esn.read("anomaly").shape == (64,)
        assert esn.read("pattern").shape == (2,)


class TestReadoutIndependence:
    """Each readout owns its W_out; training one leaves the others untouched."""

    def test_training_one_readout_leaves_others_unchanged(self):
        rng = np.random.default_rng(0)
        states = rng.uniform(-1.0, 1.0, (60, 20))

        salience = SalienceReadout(threshold=0.5)
        anomaly = AnomalyReadout(threshold=0.05)
        pattern = PatternReadout(["a", "b"])

        salience.train(states, rng.uniform(-1.0, 1.0, 60))
        salience_weights = salience.W_out.copy()
        assert anomaly.W_out is None
        assert pattern.W_out is None

        anomaly.train(states)
        np.testing.assert_array_equal(salience.W_out, salience_weights)
        anomaly_weights = anomaly.W_out.copy()
        assert pattern.W_out is None

        pattern.train(states, rng.integers(0, 2, size=60))
        np.testing.assert_array_equal(salience.W_out, salience_weights)
        np.testing.assert_array_equal(anomaly.W_out, anomaly_weights)


class TestUntrainedReadouts:
    """All readout accessors raise RuntimeError before training."""

    def test_salience_score_raises(self):
        with pytest.raises(RuntimeError):
            SalienceReadout().score(np.zeros(5))

    def test_salience_is_salient_raises(self):
        with pytest.raises(RuntimeError):
            SalienceReadout().is_salient(np.zeros(5))

    def test_anomaly_reconstruction_error_raises(self):
        with pytest.raises(RuntimeError):
            AnomalyReadout().reconstruction_error(np.zeros(5))

    def test_anomaly_is_anomalous_raises(self):
        with pytest.raises(RuntimeError):
            AnomalyReadout().is_anomalous(np.zeros(5))

    def test_pattern_probabilities_raise(self):
        with pytest.raises(RuntimeError):
            PatternReadout(["a", "b"]).get_pattern_probabilities(np.zeros(5))

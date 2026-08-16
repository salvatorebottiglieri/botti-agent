"""Specialized readouts: salience, anomaly detection, and pattern classification."""

import numpy as np
from numpy.typing import NDArray

from .reservoir import Readout


class SalienceReadout(Readout):
    """Ridge readout scoring how salient a reservoir state is.

    Trained on 0/1 targets; ``predict()`` emits a one-element score array and
    ``score()`` returns the scalar. A state is salient when its score meets the
    configured threshold (inclusive).
    """

    def __init__(self, *, threshold: float = 0.5, ridge_lambda: float = 1e-6) -> None:
        super().__init__(ridge_lambda=ridge_lambda)
        self.threshold = threshold

    def score(self, state: NDArray[np.float64]) -> float:
        """Salience score; raises RuntimeError if untrained."""
        return float(self.predict(state)[0])

    def is_salient(self, state: NDArray[np.float64]) -> bool:
        """True when the score is at least the configured threshold."""
        return self.score(state) >= self.threshold


class AnomalyReadout(Readout):
    """Autoencoder-style readout that learns the identity map on normal states.

    ``predict()`` reconstructs its input; states that reconstruct poorly have
    high reconstruction error and are flagged anomalous (strictly above the
    configured threshold).
    """

    def __init__(self, *, threshold: float = 0.5, ridge_lambda: float = 1e-6) -> None:
        super().__init__(ridge_lambda=ridge_lambda)
        self.threshold = threshold

    def train(self, states: NDArray[np.float64]) -> None:  # type: ignore[override]
        """Fit the identity map so ``predict()`` reconstructs its input."""
        super().train(states, states)

    def reconstruction_error(self, state: NDArray[np.float64]) -> float:
        """Mean squared error between the state and its reconstruction."""
        return float(np.mean((state - self.predict(state)) ** 2))

    def is_anomalous(self, state: NDArray[np.float64]) -> bool:
        """True when the reconstruction error exceeds the threshold (strict)."""
        return self.reconstruction_error(state) > self.threshold


class PatternReadout(Readout):
    """Ridge readout classifying reservoir states into named patterns.

    Trained on whole-number class labels (one-hot encoded); ``predict()`` emits
    raw logits. Probabilities are produced only by ``get_pattern_probabilities()``.
    """

    def __init__(self, classes: list[str], *, ridge_lambda: float = 1e-6) -> None:
        super().__init__(ridge_lambda=ridge_lambda)
        if not classes:
            raise ValueError("classes must contain at least one class")
        if len(set(classes)) != len(classes):
            raise ValueError("classes must be unique")
        self.classes = list(classes)
        self.n_classes = len(self.classes)

    def train(
        self,
        states: NDArray[np.float64],
        labels: NDArray[np.int64],  # type: ignore[override]
    ) -> None:
        """Fit one-hot targets; labels must be whole numbers in [0, len(classes))."""
        x = np.asarray(states, dtype=np.float64)
        labels_arr = np.asarray(labels)
        if labels_arr.dtype.kind not in "biuf":
            raise ValueError("labels must be integer-valued in [0, n_classes)")
        if not np.all(labels_arr == np.floor(labels_arr)):
            raise ValueError("labels must be integer-valued in [0, n_classes)")
        if np.any(labels_arr < 0) or np.any(labels_arr >= self.n_classes):
            raise ValueError(f"labels must be in [0, {self.n_classes})")
        y = labels_arr.astype(np.int64)
        if x.ndim != 2:
            raise ValueError("states must be (n_samples, n_features)")
        if y.ndim != 1:
            raise ValueError("labels must be (n_samples,)")
        if x.shape[0] != y.shape[0]:
            raise ValueError(
                "states and labels must have the same number of rows: "
                f"{x.shape[0]} != {y.shape[0]}"
            )
        one_hot = np.zeros((y.shape[0], self.n_classes), dtype=np.float64)
        one_hot[np.arange(y.shape[0]), y] = 1.0
        super().train(x, one_hot)

    def get_pattern_probabilities(self, state: NDArray[np.float64]) -> dict[str, float]:
        """Softmax over raw logits; keys are the pattern class names."""
        logits = self.predict(state)
        shifted = logits - np.max(logits)  # subtract max for numerical stability
        probabilities = np.exp(shifted) / np.sum(np.exp(shifted))
        return dict(zip(self.classes, probabilities.tolist()))

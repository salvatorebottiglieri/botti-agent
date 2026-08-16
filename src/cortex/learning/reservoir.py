"""Echo state network core: Reservoir, Readout, and EchoStateNetwork."""

import numpy as np
from numpy.typing import NDArray


class Reservoir:
    """A fixed random recurrent reservoir with leaky tanh dynamics.

    update() runs ``state = (1 - alpha) * state + alpha * tanh(W_in @ u + W @ state)``
    in O(1) per call: the weight matrices are fixed at init and the state is a
    fixed-size vector, so no buffers grow with the input sequence.
    """

    def __init__(
        self,
        n_input: int,
        n_reservoir: int = 500,
        *,
        alpha: float = 0.3,
        spectral_radius: float = 1.0,
        input_scaling: float = 1.0,
        seed: int | None = None,
    ) -> None:
        self.n_input = n_input
        self.n_reservoir = n_reservoir
        self.alpha = alpha
        self.spectral_radius = spectral_radius
        self.input_scaling = input_scaling

        rng = np.random.default_rng(seed)
        self.W_in = rng.uniform(
            -input_scaling, input_scaling, (n_reservoir, n_input)
        )
        w = rng.uniform(-1.0, 1.0, (n_reservoir, n_reservoir))
        largest_eigval = np.max(np.abs(np.linalg.eigvals(w)))
        self.W = w * (spectral_radius / largest_eigval)
        self.state = np.zeros(n_reservoir, dtype=np.float64)

    def update(self, input_vector: NDArray[np.float64]) -> NDArray[np.float64]:
        """Advance the reservoir by one step and return the new state."""
        if input_vector.shape != (self.n_input,):
            raise ValueError(
                f"expected ({self.n_input},), got {input_vector.shape}"
            )
        self.state = (1.0 - self.alpha) * self.state + self.alpha * np.tanh(
            self.W_in @ input_vector + self.W @ self.state
        )
        return self.state

    def reset(self) -> None:
        """Zero the reservoir state."""
        self.state = np.zeros_like(self.state)


class Readout:
    """A ridge-regression readout trained on reservoir states."""

    def __init__(self, *, ridge_lambda: float = 1e-6) -> None:
        self.ridge_lambda = ridge_lambda
        self.W_out: NDArray[np.float64] | None = None

    def train(
        self,
        states: NDArray[np.float64],
        targets: NDArray[np.float64],
    ) -> None:
        """Fit W_out = (X^T X + lambda I)^-1 X^T Y by ridge regression."""
        x = np.asarray(states, dtype=np.float64)
        y = np.asarray(targets, dtype=np.float64)
        if x.ndim != 2 or y.ndim not in (1, 2):
            raise ValueError(
                "states must be (n_samples, n_features); "
                "targets (n_samples,) or (n_samples, n_output)"
            )
        if x.shape[0] == 0:
            raise ValueError("states must contain at least one sample")
        if x.shape[0] != y.shape[0]:
            raise ValueError(
                f"states and targets must have the same number of rows: "
                f"{x.shape[0]} != {y.shape[0]}"
            )
        n_features = x.shape[1]
        design = x.T @ x + self.ridge_lambda * np.eye(n_features)
        self.W_out = np.linalg.solve(design, x.T @ y)

    def predict(self, state: NDArray[np.float64]) -> NDArray[np.float64]:
        """Predict from a single state; raises RuntimeError if untrained."""
        if self.W_out is None:
            raise RuntimeError("Readout is not trained")
        result = state @ self.W_out
        if self.W_out.ndim == 1:
            return np.asarray(result).reshape(1)
        return result

    def trained(self) -> bool:
        """True once train() has been called."""
        return self.W_out is not None


class EchoStateNetwork:
    """One reservoir shared by multiple named readouts."""

    def __init__(self, reservoir: Reservoir) -> None:
        self.reservoir = reservoir
        self._readouts: dict[str, Readout] = {}

    def step(self, input_vector: NDArray[np.float64]) -> NDArray[np.float64]:
        """Advance the reservoir and return its new state."""
        return self.reservoir.update(input_vector)

    def register_readout(self, name: str, readout: Readout) -> None:
        """Register a readout under a name; raises ValueError on duplicates."""
        if name in self._readouts:
            raise ValueError(f"Readout already registered: {name}")
        self._readouts[name] = readout

    def read(self, name: str) -> NDArray[np.float64]:
        """Predict from the current reservoir state; KeyError on unknown names."""
        return self._readouts[name].predict(self.reservoir.state)

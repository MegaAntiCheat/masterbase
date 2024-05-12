"""Anomaly detection for demo streams."""

import os

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, Field

S_hat: NDArray = np.load(os.path.join("masterbase", "S_hat.npy"))


def longest_zero_run(data: bytes) -> int:
    """Get the longest zero run of data.

    Args:
    data: Input data stream
    """
    array = np.frombuffer(data, dtype=np.uint8)
    zero_mask = array == 0
    eq_mask = np.empty_like(array, dtype=bool)
    eq_mask[0] = True
    eq_mask[1:] = array[1:] == array[:-1]
    run_mask = zero_mask & eq_mask
    total_runs = np.cumsum(run_mask.astype(int))
    run_lengths = total_runs - np.maximum.accumulate(np.where(run_mask, 0, total_runs))
    return np.max(run_lengths) + 1


def likelihood(p: NDArray, q: NDArray) -> float:
    """Determine the likelihood of an empirical frequency distribution under a prior discrete probability distribution.

    Args:
        p: class:`numpy.ndarray`, Discrete prior distribution
        q: numpy.ndarray, Observed empirical distribution, normalized
    """
    return np.exp(np.sum(np.log(p + 1e-5) * q))


def nz_markov_likelihood(coocs: NDArray) -> float:
    """Determine the Markov likelihood of all byte-pair transitions, between *nonzero* bytes, e.g., as determined by `transition_freqs`.

    Args:
        coocs: class:`numpy.ndarray`
    """  # noqa
    _S_hat, coocs = map(lambda a: a.reshape(-1)[1:], (S_hat, coocs))  # noqa
    _S_hat, coocs = map(lambda a: a / a.sum(), (_S_hat, coocs))  # noqa
    return float(likelihood(_S_hat, coocs))


def transition_freqs(data: bytes) -> NDArray:
    """Count the cooccurrences of successive octets (transition frequencies).

    Args:
        data: bytes, the input data stream
    """
    array = np.frombuffer(data, dtype=np.uint8)
    i, j = array[:-1], array[1:]
    coocs = np.zeros((256, 256), dtype=int)
    np.add.at(coocs, (i, j), 1)
    return coocs


class DetectionState(BaseModel):
    """Accumulate a trace of statistics for determining the likelihood of observed data under Markov transition frequencies present in valid data.

    Args:
        length: The cumulative length of all inputs thus far.
        likelihood: The cumulative probability of observing the input data under `S_hat` so far.
        longest_zero_run: The longest run of consecutive zeros observed in the input data chunks so far.
    """  # noqa

    length: int = Field(default=0)
    likelihood: float = Field(default=0.0)
    longest_zero_run: int = Field(default=0)

    def update(self, data: bytes) -> None:
        """Update the current state trace with stats from the input data.

        Args:
            data: bytes, input data whose likelihood to compute under the prior transition matrix to accumulate into statistics for the stream.
        """  # noqa
        new_length = len(data) + self.length
        new_likelihood = (
            self.likelihood * self.length + nz_markov_likelihood(transition_freqs(data)) * len(data)
        ) / new_length
        new_longest_zero_run = max(self.longest_zero_run, longest_zero_run(data))
        self.length = new_length
        self.likelihood = new_likelihood
        self.longest_zero_run = new_longest_zero_run

    @property
    def anomalous(self) -> bool:
        """Return if the current state is anomalous or not."""
        return self.likelihood <= 3e-5 or self.longest_zero_run >= 384

from hmac import new
from typing import NamedTuple

import numpy as np
import scipy.sparse as ssp

S_hat = np.load("S_hat.npy")


def longest_zero_run(data: bytes):
    array = np.frombuffer(data, dtype=np.uint8)
    zero_mask = array == 0
    eq_mask = np.empty_like(array, dtype=bool)
    eq_mask[0] = True
    eq_mask[1:] = array[1:] == array[:-1]
    run_mask = zero_mask & eq_mask
    total_runs = np.cumsum(run_mask.astype(int))
    run_lengths = total_runs - np.maximum.accumulate(np.where(run_mask, 0, total_runs))
    return np.max(run_lengths) + 1


def likelihood(p, q):
    return np.exp(np.sum(np.log(p + 1e-5) * q))


def nz_markov_likelihood(S_hat, coocs):
    S_hat, coocs = map(lambda a: a.reshape(-1)[1:], (S_hat, coocs))
    S_hat, coocs = map(lambda a: a / a.sum(), (S_hat, coocs))
    return likelihood(S_hat, coocs)


def transition_freqs(data: bytes):
    array = np.frombuffer(data, dtype=np.uint8)
    i, j = array[:-1], array[1:]
    x = np.ones([i.size], dtype=int)
    coocs = ssp.coo_array((x, (i, j)), shape=(256, 256), dtype=int).todense()
    return coocs


class DetectionState(NamedTuple):
    length: int = 0
    likelihood: float = 0.0
    longest_zero_run: int = 0

    def update(self, data: bytes):
        new_length = len(data) + self.length
        new_likelihood = (
            self.likelihood * self.length + nz_markov_likelihood(data) * len(data)
        ) / new_length
        new_likelihood = float(new_likelihood)
        new_longest_zero_run = max(self.longest_zero_run, longest_zero_run(data))
        return DetectionState(new_length, new_likelihood, new_longest_zero_run)

    def is_anomalous(self):
        return self.likelihood <= 3e-5 or self.longest_zero_run >= 384

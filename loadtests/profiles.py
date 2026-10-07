from __future__ import annotations

import os
from dataclasses import dataclass

from locust import LoadTestShape


@dataclass(frozen=True)
class Profile:
    # (end_time_seconds, users, spawn_rate)
    stages: tuple[tuple[int, int, float], ...]

    @property
    def max_users(self) -> int:
        return max(users for _, users, _ in self.stages)

    @property
    def duration(self) -> int:
        return self.stages[-1][0]


def _step(start: int, stop: int, step: int, hold: int, ramp: int) -> Profile:
    stages = []
    t = 0
    for users in range(start, stop + 1, step):
        t += hold
        stages.append((t, users, ramp))
    return Profile(tuple(stages))


def _soak(users: int, minutes: int, ramp: int = 120) -> Profile:
    return Profile(((ramp, users, users / ramp), (ramp + minutes * 60, users, 1)))


PROFILES: dict[str, Profile] = {
    # 50 users, 30 s ramp-up, 3 minutes in total.
    "baseline": Profile(((30, 50, 50 / 30), (180, 50, 1))),
    # Lab 6: short run for comparing replica counts behind the balancer.
    "lb": Profile(((10, 100, 10), (70, 100, 1))),
    # Lab 8 stress: +100 users every 30 s up to 1500, to find the break point.
    "stress": _step(start=100, stop=1500, step=100, hold=30, ramp=50),
    # Lab 8 spike: 100 users, a sudden jump to 800 for 3 minutes, then back to
    # 100 for 5 minutes to watch the recovery.
    "spike": Profile(
        ((120, 100, 100 / 20), (300, 800, 200), (600, 100, 200)),
    ),
    # Lab 8 soak: a steady plateau (SOAK_USERS, SOAK_MINUTES) after a 2 min ramp.
    "soak": _soak(
        users=int(os.getenv("SOAK_USERS", "300")),
        minutes=int(os.getenv("SOAK_MINUTES", "60")),
    ),
    # 50 -> 600 users, +50 every 45 s (about 9 minutes), 10 users/s.
    "step": _step(start=50, stop=600, step=50, hold=45, ramp=10),
}


def build_shape(profile: Profile) -> type[LoadTestShape]:
    class ProfileShape(LoadTestShape):
        def tick(self):
            run_time = self.get_run_time()
            for end, users, spawn_rate in profile.stages:
                if run_time < end:
                    return users, spawn_rate
            return None

    return ProfileShape

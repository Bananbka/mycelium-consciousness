from __future__ import annotations

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


PROFILES: dict[str, Profile] = {
    # 50 users, 30 s ramp-up, 3 minutes in total.
    "baseline": Profile(((30, 50, 50 / 30), (180, 50, 1))),
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

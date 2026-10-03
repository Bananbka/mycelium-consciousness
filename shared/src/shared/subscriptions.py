import enum
from dataclasses import dataclass
from datetime import timedelta

from shared.settings import settings

_INTERVAL_OVERRIDE_SECONDS = settings.rollup_interval_override_seconds


class SubscriptionTier(enum.StrEnum):
    FREE = "free"
    STANDARD = "standard"
    PREMIUM = "premium"


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    interval_days: int
    retain_count: int

    @property
    def interval(self) -> timedelta:
        if _INTERVAL_OVERRIDE_SECONDS is not None:
            return timedelta(seconds=_INTERVAL_OVERRIDE_SECONDS)
        return timedelta(days=self.interval_days)


BACKUP_POLICIES: dict[SubscriptionTier, BackupPolicy] = {
    SubscriptionTier.FREE: BackupPolicy(interval_days=30, retain_count=1),
    SubscriptionTier.STANDARD: BackupPolicy(interval_days=7, retain_count=4),
    SubscriptionTier.PREMIUM: BackupPolicy(interval_days=1, retain_count=30),
}


def policy_for(tier: SubscriptionTier) -> BackupPolicy:
    return BACKUP_POLICIES[tier]

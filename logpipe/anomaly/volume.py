"""Is a source logging far less, or far more, than usual?

LogBERT judges the order of lines, so a service that stops logging gives it nothing to judge.
This watches how many lines each source sends per minute instead. A Holt-Winters baseline (a
level plus an hour-of-week season, no trend) forecasts the expected count, and a negative binomial
around it (Poisson with a learned overdispersion, since logs come in bursts) says how unlikely the
observed count is. After Evan Miller, "Holt-Winters Forecasting Applied to Poisson Processes in
Real-Time".

A minute is unusual only when it is both improbable and far from expected (a 3% dip of a source
sending 10,000 lines a minute is improbable but irrelevant); it takes ``RUN`` unusual minutes in a
row to alert. Silence is tested over the whole run of empty minutes, so a busy source is flagged
after ``RUN`` minutes and a quiet one only once its silence becomes unlikely: about
ln(1/SILENCE_ALPHA) expected lines' worth of time. Each source sees thousands of empty runs a week,
so SILENCE_ALPHA is far stricter than ALPHA.

For its first ``LEARN`` minutes a model learns everything, each minute teaching that hour on every
day of the week, and alerts on silence only: it cannot yet tell a daily pattern from a drop. After
that, each hour of the week learns on its own and unusual minutes are not learned, so an outage
never becomes the new normal; ``rebase`` accepts a new normal when a person says so.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from scipy.special import betainc, betaincc, pdtr, pdtrc

SLOTS = 7 * 24
ALPHA = 1e-3
SILENCE_ALPHA = 1e-6
RUN = 3
LOW, HIGH = 0.5, 2.0
MIN_SPIKE = 10  # extra lines a spike needs, so 1 -> 4 lines is not one
WARMUP = 60
LEARN = 2 * 24 * 60
LEVEL_RATE, SEASON_RATE, DISPERSION_RATE, RECENT_RATE = 1 / 1440, 0.05, 0.01, 0.3
FLOOR = 0.01
MINUTE = timedelta(minutes=1)


def slot(at: datetime) -> int:
    return at.weekday() * 24 + at.hour


def tails(count: int, mean: float, dispersion: float) -> tuple[float, float, float]:
    """P(X <= count), P(X >= count) and log P(X = 0) for a negative binomial with this mean and
    variance ``mean + dispersion * mean**2`` (Poisson when dispersion is 0)."""
    if dispersion < 1e-6:
        low, log_zero = pdtr(count, mean), -mean
        high = pdtrc(count - 1, mean) if count else 1.0
    else:
        r = 1 / dispersion
        p = r / (r + mean)
        low, log_zero = betainc(r, count + 1, p), r * math.log(p)
        high = betaincc(r, count, p) if count else 1.0
    return float(low), float(high), log_zero


@dataclass
class Change:
    kind: str  # "drop", "spike" or "recovered"
    count: int
    expected: float
    silent_minutes: int = 0


@dataclass
class VolumeModel:
    level: float = 0.0
    season: list[float] = field(default_factory=lambda: [0.0] * SLOTS)
    dispersion: float = 0.5  # cautious until learned
    recent: float = 0.0
    learned: int = 0
    learning: int = LEARN
    state: str = ""  # "", "drop" or "spike"
    low_run: int = 0
    high_run: int = 0
    normal_run: int = 0
    quiet: float = 0.0  # log-probability of the current run of empty minutes
    quiet_since: str = ""  # its first minute; its minutes are learned only once it ends

    def expected(self, at: datetime) -> float:
        return max(self.level + self.season[slot(at)], FLOOR)

    def _learn(self, count: int, at: datetime) -> None:
        s = slot(at)
        season, mean = self.season[s], self.expected(at)
        if self.learned and mean >= 1:
            excess = ((count - mean) ** 2 - mean) / mean**2
            self.dispersion += DISPERSION_RATE * (min(max(excess, 0.0), 5.0) - self.dispersion)
        self.learned += 1
        level = self.level
        # a plain average until a day is in, so the first minutes don't set the level
        self.level += max(LEVEL_RATE, 1 / self.learned) * (count - season - level)
        new = season + SEASON_RATE * (count - level - season)
        for d in range(7) if self.learning else (0,):
            self.season[(s + 24 * d) % SLOTS] = new

    def _end_quiet(self, at: datetime) -> None:
        if self.learning or self.quiet >= math.log(SILENCE_ALPHA):
            t = datetime.fromisoformat(self.quiet_since)
            while t < at:
                self._learn(0, t)
                t += MINUTE
        self.quiet, self.quiet_since = 0.0, ""

    def observe(self, count: int, at: datetime) -> Change | None:
        """One closed minute starting at ``at``; returns a change of state, if any."""
        mean = self.expected(at)
        p_low, p_high, log_zero = tails(count, mean, self.dispersion)
        if count == 0:
            self.quiet_since = self.quiet_since or at.isoformat()
            self.quiet += log_zero
        elif self.quiet_since:
            self._end_quiet(at)
        low = p_low < ALPHA and count < LOW * mean
        high = p_high < ALPHA and count > HIGH * mean and count - mean >= MIN_SPIKE
        quiet_run = (
            (at - datetime.fromisoformat(self.quiet_since)) // MINUTE + 1 if count == 0 else 0
        )
        silent = quiet_run >= RUN and self.quiet < math.log(SILENCE_ALPHA)
        self.low_run = self.low_run + 1 if low else 0
        self.high_run = self.high_run + 1 if high else 0
        self.normal_run = 0 if low or high or silent else self.normal_run + 1
        self.recent += RECENT_RATE * (count - self.season[slot(at)] - self.recent)
        if count and (self.learning or not (low or high)):
            self._learn(count, at)
        self.learning = max(self.learning - 1, 0)

        if self.learned < WARMUP:  # a source that sent a few lines once is not watched
            return None
        watching = not self.learning
        if self.state != "drop" and (silent or watching and self.low_run >= RUN):
            self.state = "drop"
            return Change("drop", count, mean, quiet_run if silent else 0)
        if watching and self.state != "spike" and self.high_run >= RUN:
            self.state = "spike"
            return Change("spike", count, mean)
        if self.state and self.normal_run >= RUN:
            self.state = ""
            return Change("recovered", count, mean)
        return None

    def rebase(self) -> None:
        """A person says the current volume is normal: take it as the level and learn again."""
        self.level, self.state, self.learning = self.recent, "", LEARN
        self.pause()

    def pause(self) -> None:
        """Minutes that can't be judged (the host's logs aren't arriving): runs end unlearned."""
        self.low_run = self.high_run = self.normal_run = 0
        self.quiet, self.quiet_since = 0.0, ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, doc: dict) -> VolumeModel:
        return cls(**{k: v for k, v in doc.items() if k in cls.__dataclass_fields__})

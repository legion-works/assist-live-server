from collections import deque
from statistics import median

from assist_live_client.models import unsigned


class ClockWindow:
    def __init__(self) -> None:
        self.boot: int | None = None
        self.samples: deque[tuple[int, int]] = deque(maxlen=50)
        self.latest_arrival_ns = 0

    def observe(self, boot: int, device_ms: int, arrival_ns: int) -> tuple[int, float]:
        unsigned(boot)
        unsigned(device_ms)
        unsigned(arrival_ns)
        if boot != self.boot:
            self.samples.clear()
            self.latest_arrival_ns = 0
            self.boot = boot
        self.latest_arrival_ns = max(self.latest_arrival_ns, arrival_ns)
        cutoff = self.latest_arrival_ns - 60_000_000_000
        self.samples = deque(
            (sample for sample in self.samples if sample[0] >= cutoff), maxlen=50
        )
        self.samples.append((arrival_ns, arrival_ns - device_ms * 1_000_000))
        offsets = [offset for _, offset in self.samples]
        minimum = min(offsets)
        uncertainty = median([offset - minimum for offset in offsets]) / 1_000_000
        return device_ms * 1_000_000 + minimum, uncertainty

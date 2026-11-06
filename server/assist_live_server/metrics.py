from dataclasses import dataclass, field


@dataclass
class EndDecisionWait:
    bounds: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
    counts: list[int] = field(default_factory=lambda: [0] * 8)
    count: int = 0
    total: float = 0.0

    def observe(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        self.count += 1
        self.total += seconds
        for index, bound in enumerate(self.bounds):
            if seconds <= bound:
                self.counts[index] += 1

    def render(self, end_rule: str) -> str:
        name = "assist_live_end_decision_wait_seconds"
        lines = [f"# TYPE {name} histogram"]
        for bound, count in zip(self.bounds, self.counts, strict=True):
            lines.append(f'{name}_bucket{{end_rule="{end_rule}",le="{bound}"}} {count}')
        lines.extend(
            [
                f'{name}_bucket{{end_rule="{end_rule}",le="+Inf"}} {self.count}',
                f'{name}_count{{end_rule="{end_rule}"}} {self.count}',
                f'{name}_sum{{end_rule="{end_rule}"}} {self.total}',
            ]
        )
        return "\n".join(lines) + "\n"


@dataclass
class Metrics:
    sequence_gaps: int = 0
    maintenance_errors_total: int = 0

    def render(self, counts: dict[str, int]) -> str:
        values = {
            "sequence_gaps": self.sequence_gaps,
            "maintenance_errors_total": self.maintenance_errors_total,
            **counts,
        }
        lines = []
        for name, value in sorted(values.items()):
            metric = "assist_live_" + name
            kind = "counter" if name.endswith("_total") else "gauge"
            lines.extend([f"# TYPE {metric} {kind}", f"{metric} {value}"])
        return "\n".join(lines) + "\n"

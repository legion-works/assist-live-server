from dataclasses import dataclass


@dataclass
class Metrics:
    sequence_gaps: int = 0

    def render(self, counts: dict[str, int]) -> str:
        values = {"sequence_gaps": self.sequence_gaps, **counts}
        lines = []
        for name, value in sorted(values.items()):
            metric = "assist_live_" + name
            kind = "counter" if name.endswith("_total") else "gauge"
            lines.extend([f"# TYPE {metric} {kind}", f"{metric} {value}"])
        return "\n".join(lines) + "\n"

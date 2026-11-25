"""Grading, recommendations and output formatting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
EXIT_CODES = {PASS: 0, WARN: 1, FAIL: 2}
EXIT_ERROR = 3

# A value at or above this fraction of its target is WARN: it passes today but
# has little headroom for a busier cluster or a noisier neighbour.
WARN_RATIO = 0.8

RECOMMENDATIONS = [
    "Put the etcd data directory (usually /var/lib/etcd) on a dedicated SSD or NVMe device.",
    "Keep noisy neighbours off the etcd disk: no container images, container logs or "
    "application data on the same device.",
    "Reduce node log write I/O, for example move container logs (/var/log/pods) to "
    "RAM-backed storage such as zram, with strict log rotation.",
    "On VMs and cloud volumes, check the virtual disk's provisioned IOPS/throughput limits "
    "and whether the volume is being throttled.",
]


@dataclass
class Check:
    name: str
    value_ms: float
    target_ms: float
    why: str

    @property
    def status(self) -> str:
        if self.value_ms >= self.target_ms:
            return FAIL
        if self.value_ms >= self.target_ms * WARN_RATIO:
            return WARN
        return PASS


@dataclass
class Result:
    mode: str
    source: str
    checks: list[Check]
    info: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return max((c.status for c in self.checks), key=EXIT_CODES.__getitem__)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


def render_text(result: Result) -> str:
    lines = [f"etcd-disk-check {result.mode}: {result.source}", ""]
    for c in result.checks:
        lines.append(
            f"  {c.status:<5} {c.name:<26} {c.value_ms:8.2f} ms   (target < {c.target_ms:g} ms)"
        )
    for key, value in result.info.items():
        lines.append(f"        {key:<26} {value}")
    lines += ["", f"Overall: {result.status}"]
    bad = [c for c in result.checks if c.status != PASS]
    if bad:
        lines += ["", "Why it matters:"]
        lines += [f"  - {c.name}: {c.why}" for c in bad]
        lines += ["", "Recommendations:"]
        lines += [f"  - {r}" for r in RECOMMENDATIONS]
    return "\n".join(lines)


def render_json(result: Result) -> str:
    return json.dumps(
        {
            "mode": result.mode,
            "source": result.source,
            "status": result.status,
            "checks": [
                {
                    "name": c.name,
                    "value_ms": round(c.value_ms, 3),
                    "target_ms": c.target_ms,
                    "status": c.status,
                    "why": c.why,
                }
                for c in result.checks
            ],
            "info": result.info,
            "recommendations": RECOMMENDATIONS if result.status != PASS else [],
        },
        indent=2,
    )

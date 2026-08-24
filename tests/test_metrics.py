import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.extract_metrics import collect_records, parse_metric_line, summarize


def test_parse_metric_line_supports_physical_and_simulated_formats() -> None:
    physical = parse_metric_line("[METRIC] p256_csr_total_ms=123 free_heap=210000 stack_watermark=17000")
    assert physical is not None
    assert physical.device_id == ""
    assert physical.values["p256_csr_total_ms"] == 123.0

    simulated = parse_metric_line(
        "[CLED-SIM-0001] [METRIC] p256_csr_total_ms=1.25 challenge_http_ms=8.5 provisioning_total_ms=15.75"
    )
    assert simulated is not None
    assert simulated.device_id == "CLED-SIM-0001"
    assert simulated.values["challenge_http_ms"] == 8.5


def test_collect_and_summarize_metrics(tmp_path: Path) -> None:
    log = tmp_path / "simulator.log"
    log.write_text(
        "noise\n"
        "[A] [METRIC] provisioning_total_ms=10\n"
        "[B] [METRIC] provisioning_total_ms=20\n",
        encoding="utf-8",
    )
    records = collect_records([tmp_path])
    assert len(records) == 2
    rows = summarize(records)
    assert rows == [
        {
            "metric": "provisioning_total_ms",
            "count": 2,
            "min": 10.0,
            "mean": 15.0,
            "median": 15.0,
            "p95": 20.0,
            "max": 20.0,
        }
    ]

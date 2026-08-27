import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import extract_metrics
from scripts.extract_metrics import collect_records, parse_metric_line, read_log_text, summarize


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


def test_collect_metrics_from_windows_powershell_utf16_log(tmp_path: Path) -> None:
    log = tmp_path / "physical-01.txt"
    content = (
        "[ESP32] boot noise\n"
        "[ESP32] [METRIC] p256_key_ms=260\n"
        "[ESP32] [METRIC] provisioning_total_ms=6283 free_heap=215568 stack_watermark=16946\n"
    )
    # Windows PowerShell 5.x Tee-Object writes Unicode files as UTF-16LE with a BOM.
    log.write_text(content, encoding="utf-16")

    assert "[METRIC]" in read_log_text(log)
    records = collect_records([tmp_path])
    assert len(records) == 2
    assert records[0].values["p256_key_ms"] == 260.0
    assert records[1].values["provisioning_total_ms"] == 6283.0
    assert records[1].values["free_heap"] == 215568.0


def test_collect_metrics_from_utf16le_log_without_bom(tmp_path: Path) -> None:
    log = tmp_path / "physical-no-bom.txt"
    content = "[METRIC] challenge_http_ms=2536\n[METRIC] enroll_http_ms=2636\n"
    log.write_bytes(content.encode("utf-16-le"))

    records = collect_records([log])
    assert len(records) == 2
    assert records[0].values["challenge_http_ms"] == 2536.0
    assert records[1].values["enroll_http_ms"] == 2636.0


def test_metric_extractor_creates_csvs_even_when_no_metrics_exist(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "empty.log"
    log.write_text("boot without metric records\n", encoding="utf-8")
    raw = tmp_path / "metrics.csv"
    summary = tmp_path / "metrics-summary.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "extract_metrics.py",
            str(log),
            "--output",
            str(raw),
            "--summary-output",
            str(summary),
        ],
    )
    assert extract_metrics.main() == 1
    assert raw.is_file()
    assert summary.is_file()
    assert raw.read_text(encoding="utf-8").startswith("source,line_number,device_id")
    assert summary.read_text(encoding="utf-8").startswith("metric,count,min,mean,median,p95,max")

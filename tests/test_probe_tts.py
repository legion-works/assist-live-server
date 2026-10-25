import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_metrics_use_pooled_audio_duration_and_linear_percentiles():
    from tools.probe_tts import summarize

    rows = [
        {"first_s": 0.01, "total_s": 0.1, "audio_s": 1, "rss_kib": 1024},
        {"first_s": 0.03, "total_s": 0.3, "audio_s": 3, "rss_kib": 2048},
    ]
    result = summarize(rows)
    assert result == {
        "requests": 2,
        "first_ms_p50": 20,
        "first_ms_max": 30,
        "rtf": 0.1,
        "rss_mib": 2,
    }


@pytest.mark.parametrize("field", ["first_s", "total_s", "audio_s", "rss_kib"])
@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_metrics_reject_invalid_measurements(field, bad):
    from tools.probe_tts import summarize

    row = {"first_s": 0.01, "total_s": 0.1, "audio_s": 1, "rss_kib": 1024}
    row[field] = bad
    with pytest.raises(ValueError):
        summarize([row])


def test_metrics_reject_empty_audio_and_empty_measurements():
    from tools.probe_tts import summarize

    for rows in ([], [{"first_s": 0, "total_s": 0, "audio_s": 0, "rss_kib": 1}]):
        with pytest.raises(ValueError):
            summarize(rows)


def test_word_error_rate_uses_edit_counts_not_mean_percentages():
    from tools.probe_tts import word_error_rate

    assert word_error_rate([("one two", "one"), ("three", "three four")]) == 2 / 3
    assert word_error_rate([("one two", "one two")]) == 0
    with pytest.raises(ValueError):
        word_error_rate([("", "one")])


def test_record_mode_does_not_import_piper_or_make_audio(tmp_path, monkeypatch, capsys):
    from tools.probe_tts import main

    monkeypatch.setenv("ASSIST_LIVE_PROBE_DIRECTORY", str(tmp_path))
    report = tmp_path / "report.md"
    assert main(["--record-decision", "--report", str(report)]) == 0
    output = capsys.readouterr().out
    assert "PASS TTS selection recorded" in output
    record = json.loads((tmp_path / "decision.json").read_text())
    assert record["engine"] == "piper"
    assert record["threads"] == 4
    text = report.read_text()
    assert "29.74" in text
    assert "LicenseRef-Blizzard-2013-Research" in text
    assert not list(tmp_path.rglob("*.wav"))


def test_live_mode_needs_explicit_flag_and_scratch_model(tmp_path, monkeypatch, capsys):
    from tools.probe_tts import main

    monkeypatch.setenv("ASSIST_LIVE_PROBE_DIRECTORY", str(tmp_path))
    assert main(["--live", "--text", "Synthetic test."]) == 1
    assert "FAIL TTS probe: missing pinned model" in capsys.readouterr().out


def test_naturalness_uses_linear_interpolation_and_no_clipping():
    from tools.probe_tts import naturalness

    assert naturalness([1, 2, 3, 6]) == {"mean": 3, "median": 2.5, "p10": 1.3}
    for values in ([], [float("nan")]):
        with pytest.raises(ValueError):
            naturalness(values)


def test_offline_csv_inputs_keep_engine_thread_and_length_groups(tmp_path):
    from tools.probe_tts import offline_metrics

    timings = tmp_path / "timings.csv"
    timings.write_text(
        "engine,threads,chars,first_s,total_s,audio_s,rss_kib\n"
        "piper,4,20,0.01,0.1,1,1024\n"
        "piper,4,160,0.03,0.3,3,2048\n"
        "pocket,1,80,0.1,1,2,4096\n"
    )
    transcripts = tmp_path / "transcripts.csv"
    transcripts.write_text(
        "engine,threads,repeat,reference_norm,hypothesis_norm\n"
        "piper,4,warmup,one,wrong\n"
        "piper,4,0,one two,one\n"
        "pocket,1,0,three,three\n"
    )
    scores = tmp_path / "scores.csv"
    scores.write_text("engine,mos\npiper,3\npiper,5\npocket,2\n")
    result = offline_metrics(timings, transcripts, scores)
    assert result["piper-t4"]["wer"] == 0.5
    assert result["piper-t4"]["naturalness"]["mean"] == 4
    assert result["piper-t4"]["groups"]["short"]["rtf"] == 0.1
    assert result["piper-t4"]["groups"]["long"]["requests"] == 1
    assert result["pocket-t1"]["groups"]["medium"]["requests"] == 1
    assert "naturalness" not in result["pocket-t1"]


def test_record_mode_never_enters_live_synthesis(tmp_path, monkeypatch):
    from tools import probe_tts

    def forbidden(*args):
        raise AssertionError("live synthesis entered without --live")

    monkeypatch.setenv("ASSIST_LIVE_PROBE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(probe_tts, "live_piper", forbidden)
    assert (
        probe_tts.main(["--record-decision", "--report", str(tmp_path / "record.md")])
        == 0
    )


def test_documented_command_keeps_whole_tts_record_on_copied_tree(tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    tree = tmp_path / "tree"
    for directory in ("tools", "assets", "docs"):
        shutil.copytree(ROOT / directory, tree / directory)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", tree / "THIRD_PARTY_NOTICES.md")
    report = tree / "docs/research/q7t-local-tts.md"
    before = report.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "tools/probe_tts.py",
            "--record-decision",
            "--report",
            "docs/research/q7t-local-tts.md",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        env={**os.environ, "ASSIST_LIVE_PROBE_DIRECTORY": str(tmp_path / "scratch")},
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS TTS selection recorded" in result.stdout
    assert report.read_bytes() == before

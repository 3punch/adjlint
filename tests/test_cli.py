"""End-to-end CLI behaviour: rendering, exit codes, and the failure paths."""

import json
from pathlib import Path

from adjlint import RULES
from adjlint.cli import main

ROOT = Path(__file__).resolve().parent.parent
PRICES = str(ROOT / "examples" / "prices.csv")
ACTIONS = str(ROOT / "examples" / "actions.csv")
BASE = ["audit", "--prices", PRICES, "--actions", ACTIONS, "--profile", "tse"]


def test_the_example_data_produces_the_three_showcase_findings(capsys):
    assert main(BASE) == 1
    out = capsys.readouterr().out
    for code in ("AL001", "AL002", "AL003"):
        assert code in out
    assert "2 errors, 1 warning" in out
    assert "profile=tse" in out and "bars=12" in out


def test_json_output_is_machine_readable(capsys):
    main([*BASE, "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"error": 2, "warning": 1, "info": 0}
    assert {f["code"] for f in payload["findings"]} == {"AL001", "AL002", "AL003"}
    assert payload["findings"][0]["symbol"] == "WEBM"


def test_markdown_output_renders_a_table(capsys):
    main([*BASE, "--format", "md"])
    out = capsys.readouterr().out
    assert out.startswith("# adjlint report")
    assert "| Severity | Symbol | Date | Code | Rule | Message |" in out


def test_disabling_rules_can_make_the_gate_green(capsys):
    assert main([*BASE, "--disable", "AL001,AL002,AL003"]) == 0
    assert "clean" in capsys.readouterr().out


def test_fail_on_never_always_exits_zero(capsys):
    assert main([*BASE, "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_fail_on_warning_promotes_the_warning(capsys):
    assert main([*BASE, "--disable", "AL001,AL002", "--fail-on", "warning"]) == 1


def test_report_can_be_written_to_a_file(tmp_path):
    target = tmp_path / "report.md"
    main([*BASE, "--format", "md", "--output", str(target)])
    assert target.read_text(encoding="utf-8").startswith("# adjlint report")


def test_an_unknown_profile_is_a_usage_error(capsys):
    assert main(["audit", "--prices", PRICES, "--profile", "nyse"]) == 2
    assert "unknown market profile" in capsys.readouterr().err


def test_a_missing_file_is_a_usage_error(capsys):
    assert main(["audit", "--prices", "definitely-not-here.csv"]) == 2
    assert "adjlint:" in capsys.readouterr().err


def test_semicolon_exports_produce_the_same_report_as_the_comma_original(capsys, tmp_path):
    paths = {}
    for name in ("prices", "actions"):
        text = (ROOT / "examples" / f"{name}.csv").read_text(encoding="utf-8")
        target = tmp_path / f"{name}.csv"
        target.write_text(text.replace(",", ";"), encoding="utf-8")
        paths[name] = str(target)
    code = main(
        [
            "audit",
            "--prices", paths["prices"],
            "--actions", paths["actions"],
            "--profile", "tse",
            "--format", "json",
        ]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert {f["code"] for f in payload["findings"]} == {"AL001", "AL002", "AL003"}


def test_filtering_to_an_absent_symbol_leaves_nothing_to_audit(capsys):
    assert main(["audit", "--prices", PRICES, "--symbol", "NOPE"]) == 2
    assert "is not in" in capsys.readouterr().err


def test_rules_catalog_covers_the_whole_registry(capsys):
    assert main(["rules"]) == 0
    out = capsys.readouterr().out
    assert len(RULES) == 10
    for code, item in RULES.items():
        assert code in out and item.name in out


def test_profiles_command_emits_json(capsys):
    assert main(["profiles"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"generic", "tse"}


def test_a_symbol_column_lets_one_file_carry_several_instruments(tmp_path, capsys):
    prices = tmp_path / "p.csv"
    prices.write_text(
        "symbol,date,close\n"
        "AAA,2026-06-01,100\nAAA,2026-06-02,101\n"
        "BBB,2026-06-01,100\nBBB,2026-06-02,180\n",
        encoding="utf-8",
    )
    assert main(["audit", "--prices", str(prices)]) == 1
    out = capsys.readouterr().out
    assert "symbols=2" in out and "BBB" in out and "AL001" in out


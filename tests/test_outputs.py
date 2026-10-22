import csv
import json

from crbench import build, cli
from fakes import FAKE_NAME, hha_row, hospice_report, write_hha_csv, write_hospice_zip

HOSPICE_COMMENT = (
    "# Source: Soriaga & Associates, LLC, https://www.soriagacpa.com/hospice-cost-report-benchmarks, "
    "from CMS HCRIS Form CMS-1984-14 data. Licensed CC BY 4.0 (credit required). "
    "Underlying CMS data is public domain. Cost reports are provider-filed and may be unaudited."
)

META_KEYS = {
    "title", "description", "credit", "credit_url", "license", "license_url", "sources", "form",
    "year_definition", "years", "latest_complete_year", "generated_at", "tool_version", "counts",
    "notes", "disclaimer",
}
ROW_KEYS = {"group_type", "group", "year", "metric", "n", "p25", "median", "p75", "mean", "pooled", "suppressed"}
COUNT_KEYS = {"raw", "kept", "excluded_no_financial_data", "excluded_no_cost_or_days",
              "excluded_partial_year", "dropped_duplicates", "complete"}


def make_data(tmp_path):
    data = tmp_path / "raw"
    (data / "hospice").mkdir(parents=True)
    (data / "home-health").mkdir(parents=True)
    reps = []
    for i in range(14):  # 14 Texas for-profit hospices: above the privacy floor
        reps.append(hospice_report(i + 1, f"99{i:04d}", cost=1_000_000 + i * 10_000, days=10_000 + i * 100))
    for i in range(4):  # 4 New Mexico hospices: below the floor
        reps.append(hospice_report(100 + i, f"98{i:04d}", state="NM", control="2"))
    reps.append(hospice_report(200, "970001", begin="01/01/2023", end="06/30/2023"))  # partial year
    write_hospice_zip(data / "hospice" / "HOSPC14-ALL-YEARS.zip", {2023: reps})
    rows = [hha_row(i + 1, f"96{i:04d}") for i in range(13)]
    rows.append(hha_row(50, "950001", **{"Total Cost": ""}))
    write_hha_csv(data / "home-health" / "hha_2024.csv", rows)
    return data


def run_build(tmp_path):
    data = make_data(tmp_path)
    out = tmp_path / "out"
    results = build.build("all", data, out, log=lambda *_: None)
    assert set(results) == {"hospice", "home-health"}
    return out


def test_json_schema_keys(tmp_path):
    out = run_build(tmp_path)
    for name in ("hospice", "home-health"):
        doc = json.loads((out / name / "benchmarks.json").read_text(encoding="utf-8"))
        assert set(doc) == {"meta", "metrics", "rows"}
        assert META_KEYS <= set(doc["meta"])
        assert doc["meta"]["credit"] == "Soriaga & Associates, LLC"
        assert doc["meta"]["license"] == "CC BY 4.0"
        assert doc["meta"]["latest_complete_year"] in doc["meta"]["years"]
        for c in doc["meta"]["counts"].values():
            assert COUNT_KEYS <= set(c)
        for m in doc["metrics"]:
            assert set(m) == {"id", "label", "unit", "definition", "source_fields", "has_pooled", "pooled_only"}
            assert m["unit"] in {"usd", "usd_per_day", "usd_per_visit", "ratio", "count"}
        for r in doc["rows"]:
            assert set(r) == ROW_KEYS
            assert r["group_type"] in {"national", "state", "size", "control"}
        for s in doc["meta"]["sources"]:
            assert {"name", "url", "cms_last_updated", "retrieved"} <= set(s)


def test_hospice_counts_and_suppression(tmp_path):
    out = run_build(tmp_path)
    doc = json.loads((out / "hospice" / "benchmarks.json").read_text(encoding="utf-8"))
    c = doc["meta"]["counts"]["2023"]
    assert (c["raw"], c["excluded_partial_year"], c["kept"]) == (19, 1, 18)
    rows = {(r["group_type"], r["group"], r["metric"]): r for r in doc["rows"]}
    tx = rows[("state", "TX", "cost_per_day_total")]
    nm = rows[("state", "NM", "cost_per_day_total")]
    assert tx["suppressed"] is False and tx["n"] == 14 and tx["median"] is not None
    assert nm["suppressed"] is True and nm["n"] == 4 and nm["median"] is None


def test_csv_has_comment_line_then_header(tmp_path):
    out = run_build(tmp_path)
    lines = (out / "hospice" / "benchmarks.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == HOSPICE_COMMENT
    assert lines[1] == "group_type,group,year,metric,n,p25,median,p75,mean,pooled,suppressed"
    hh = (out / "home-health" / "benchmarks.csv").read_text(encoding="utf-8").splitlines()
    assert hh[0].startswith("# Source: Soriaga & Associates, LLC, https://www.soriagacpa.com/home-health-cost-report-benchmarks,")
    assert "Form CMS-1728-20" in hh[0]
    body = list(csv.DictReader(lines[1:]))
    assert body and set(body[0]) == ROW_KEYS


def test_outputs_contain_no_ccn_or_name(tmp_path):
    out = run_build(tmp_path)
    text = ""
    for p in out.rglob("*"):
        if p.is_file():
            text += p.read_text(encoding="utf-8")
    assert FAKE_NAME not in text and "ZZ FAKE" not in text
    assert "FAKE STREET" not in text
    for ccn_prefix in ("990000", "990001", "980000", "960000", "950001", "970001"):
        assert ccn_prefix not in text


def test_official_name_is_used_everywhere(tmp_path):
    out = run_build(tmp_path)
    for p in out.rglob("*"):
        if p.is_file():
            t = p.read_text(encoding="utf-8")
            assert "Soriaga & Associates, LLC" in t
            for bad in ("Soriaga CPA", "Soriaga & Associates, LLP", "Soriaga CPAs"):
                assert bad not in t


def test_summary_is_generated(tmp_path):
    out = run_build(tmp_path)
    s = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "Hospice Cost Report Benchmarks" in s and "Home Health Cost Report Benchmarks" in s
    assert "National benchmarks, 2023" in s


def test_cli_build_and_info(tmp_path, capsys):
    data = make_data(tmp_path)
    out = tmp_path / "out2"
    args = ["--data-dir", str(data), "--output-dir", str(out)]
    assert cli.main(args + ["build", "--provider", "hospice"]) == 0
    assert (out / "hospice" / "benchmarks.json").exists()
    assert not (out / "home-health").exists()
    assert cli.main(args + ["info"]) == 0
    assert "latest complete year 2023" in capsys.readouterr().out

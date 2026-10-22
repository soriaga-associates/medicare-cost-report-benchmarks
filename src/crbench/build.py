"""Build the benchmark outputs for each provider type."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from crbench import __version__, download, homehealth, hospice, output, pipeline

CREDIT = "Soriaga & Associates, LLC"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
DISCLAIMER = "General information from public CMS filings, not an audit, valuation, or advice for any agency."

HOSPICE_PAGE = "https://www.soriagacpa.com/hospice-cost-report-benchmarks"
HOME_HEALTH_PAGE = "https://www.soriagacpa.com/home-health-cost-report-benchmarks"

HOSPICE_CSV_COMMENT = (
    f"# Source: {CREDIT}, {HOSPICE_PAGE}, from CMS HCRIS Form CMS-1984-14 data. "
    "Licensed CC BY 4.0 (credit required). Underlying CMS data is public domain. "
    "Cost reports are provider-filed and may be unaudited."
)
HOME_HEALTH_CSV_COMMENT = (
    f"# Source: {CREDIT}, {HOME_HEALTH_PAGE}, from CMS data.cms.gov Home Health Agency Cost Report data "
    "(Form CMS-1728-20). Licensed CC BY 4.0 (credit required). Underlying CMS data is public domain. "
    "Cost reports are provider-filed and may be unaudited."
)

COMMON_NOTES = [
    "Cost reports are provider-filed and may be unaudited.",
    "The data mixes reports at different CMS statuses (as submitted, settled, reopened) because CMS keeps the highest status per report.",
    "All-payer operating margins include costs that Medicare does not allow.",
    "Partial-year reports (period under 300 or over 430 days) are excluded.",
    "The newest years are incomplete and fill in over time; see counts and latest_complete_year.",
    "Figures are not risk-adjusted or wage-adjusted.",
    "Cells with fewer than 11 agencies are suppressed. No provider-level data is published.",
]


def _source(name, url, kind, last_updated, retrieved):
    return {"name": name, "url": url, "kind": kind, "cms_last_updated": last_updated, "retrieved": retrieved}


def hospice_sources(manifest: dict) -> list[dict]:
    z = manifest.get(download.HOSPICE_ZIP, {})
    d = manifest.get(download.HOSPICE_DOC, {})
    return [
        _source(
            "CMS HCRIS Hospice Form CMS-1984-14 raw files (HOSPC14-ALL-YEARS.zip)",
            download.HOSPICE_ZIP_URL, "data", z.get("last_modified"), z.get("retrieved"),
        ),
        _source(
            "CMS HCRIS Hospice documentation (hospc14-documentation.zip)",
            download.HOSPICE_DOC_URL, "documentation", d.get("last_modified"), d.get("retrieved"),
        ),
        _source(
            "CMS page: Hospice 1984-2014 form cost report files",
            "https://www.cms.gov/data-research/statistics-trends-and-reports/cost-reports/hospice-1984-2014-form",
            "documentation", None, None,
        ),
        _source(
            "CMS Provider Reimbursement Manual 15-2, chapter 43 (instructions)",
            "https://www.cms.gov/files/document/r8p243i.pdf", "documentation", None, None,
        ),
    ]


def home_health_sources(manifest: dict) -> list[dict]:
    out = []
    for y in download.HHA_YEARS:
        e = manifest.get(download.hha_key(y))
        if e:
            out.append(
                _source(f"data.cms.gov Home Health Agency Cost Report, file year {y} (CSV)",
                        e["url"], "data", e.get("cms_modified"), e.get("retrieved"))
            )
    out += [
        _source("data.cms.gov Home Health Agency Cost Report (landing page)",
                "https://data.cms.gov/provider-compliance/cost-reports/home-health-agency-cost-report",
                "documentation", None, None),
        _source("data.cms.gov Home Health Agency Cost Report data dictionary",
                "https://data.cms.gov/resources/home-health-agency-cost-report-data-dictionary",
                "documentation", None, None),
        _source("data.cms.gov Home Health Agency Cost Report methodology",
                "https://data.cms.gov/resources/home-health-agency-cost-report-methodology",
                "documentation", None, None),
    ]
    return out


PROVIDERS = {
    "hospice": {
        "dir": "hospice",
        "title": "Hospice Cost Report Benchmarks",
        "description": (
            "Aggregate benchmarks (cost per day, level-of-care mix, payer mix, operating margin, size) "
            "for freestanding Medicare-certified hospices, built from CMS HCRIS cost reports."
        ),
        "credit_url": HOSPICE_PAGE,
        "form": "CMS-1984-14 (Hospice Cost Report)",
        "year_definition": (
            "Federal fiscal year (October 1 to September 30) of the cost reporting period's begin date. "
            "FY 2023 means periods beginning 10/1/2022 through 9/30/2023."
        ),
        "csv_comment": HOSPICE_CSV_COMMENT,
        "metrics": hospice.METRICS,
        "year_label": "FY",
        "notes": COMMON_NOTES + [
            "Freestanding hospices only. Hospital-, SNF- and HHA-based hospices file other cost report forms.",
            "Worksheet F-2 Part II (expenses and net income) is stored in column 2; see the README for the evidence.",
        ],
    },
    "home-health": {
        "dir": "home-health",
        "title": "Home Health Cost Report Benchmarks",
        "description": (
            "Aggregate benchmarks (cost per visit by discipline, visit volume, payer mix, operating and Medicare margins) "
            "for Medicare-certified home health agencies, built from the CMS data.cms.gov cost report files."
        ),
        "credit_url": HOME_HEALTH_PAGE,
        "form": "CMS-1728-20 (Home Health Agency Cost Report)",
        "year_definition": (
            "CMS's data.cms.gov file year (2020 to 2024). Each yearly file holds cost reports whose periods mostly begin in "
            "that calendar year; it is not a uniform fiscal year."
        ),
        "csv_comment": HOME_HEALTH_CSV_COMMENT,
        "metrics": homehealth.METRICS,
        "year_label": "File year",
        "notes": COMMON_NOTES + [
            "About a third of rows in the CMS files have no financial data (blank Total Cost) and are excluded.",
            "Medicare PPS payments are the four 'Total PPS Payment' episode columns plus the two 'Total PPS Outlier Payment' columns (Wkst D lines 10 to 15; PRM 15-2 section 4715.2).",
            "Share metrics have a pooled value (total numerator over total denominator); per-agency medians can hide small levels of care.",
        ],
    },
}


def build_provider(key: str, data_dir: Path, out_dir: Path, log=print) -> dict | None:
    spec = PROVIDERS[key]
    manifest = download.load_manifest(data_dir)
    log(f"Building {key} ...")
    if key == "hospice":
        zip_path = data_dir / download.HOSPICE_ZIP
        if not zip_path.exists():
            log(f"  missing {zip_path}. Run: python -m crbench download")
            return None
        reports = hospice.load(zip_path, log=log)
        sources = hospice_sources(manifest)
    else:
        if not list((data_dir / "home-health").glob("hha_*.csv")):
            log(f"  no home health CSVs in {data_dir / 'home-health'}. Run: python -m crbench download")
            return None
        reports = homehealth.load(data_dir / "home-health", log=log)
        sources = home_health_sources(manifest)

    metrics = spec["metrics"]
    kept_by_year, counts = pipeline.clean(reports, metrics)
    kept_by_year = {y: k for y, k in kept_by_year.items() if k}
    years = sorted(kept_by_year)
    complete_years = [y for y in years if counts[y]["complete"]]
    rows = output.finalize_rows(pipeline.aggregate(kept_by_year, metrics), metrics)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    meta = {
        "title": spec["title"],
        "description": spec["description"],
        "credit": CREDIT,
        "credit_url": spec["credit_url"],
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "sources": sources,
        "form": spec["form"],
        "year_definition": spec["year_definition"],
        "years": years,
        "latest_complete_year": complete_years[-1] if complete_years else None,
        "generated_at": generated_at,
        "tool_version": __version__,
        "counts": {str(y): counts[y] for y in sorted(counts)},
        "notes": spec["notes"],
        "disclaimer": DISCLAIMER,
    }
    folder = out_dir / spec["dir"]
    output.write_json(folder / "benchmarks.json", meta, metrics, rows)
    output.write_csv(folder / "benchmarks.csv", spec["csv_comment"], rows)
    log(f"  wrote {folder / 'benchmarks.json'} and benchmarks.csv ({len(rows):,} rows)")
    return {"meta": meta, "metrics": metrics, "rows": rows, "spec": spec}


def build(provider: str, data_dir: Path, out_dir: Path, log=print) -> dict:
    keys = list(PROVIDERS) if provider == "all" else [provider]
    results = {}
    for k in keys:
        res = build_provider(k, data_dir, out_dir, log=log)
        if res:
            results[k] = res
    if results:
        # SUMMARY.md covers every provider that has been built so far.
        sections = []
        generated = ""
        for k in PROVIDERS:
            res = results.get(k) or _load_existing(out_dir, k)
            if res:
                generated = res["meta"]["generated_at"]
                sections.append(
                    output.summary_section(res["spec"]["title"], res["meta"], res["metrics"], res["rows"], res["spec"]["year_label"])
                )
        output.write_summary(out_dir / "SUMMARY.md", sections, generated, __version__)
        log(f"  wrote {out_dir / 'SUMMARY.md'}")
    return results


def _load_existing(out_dir: Path, key: str):
    import json

    spec = PROVIDERS[key]
    p = out_dir / spec["dir"] / "benchmarks.json"
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {"meta": doc["meta"], "metrics": spec["metrics"], "rows": doc["rows"], "spec": spec}

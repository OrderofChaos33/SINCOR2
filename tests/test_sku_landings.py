"""Guard SKU catalog and landing templates stay in lockstep with PLATFORM_PLANS."""
from pathlib import Path
import json
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_sku_catalog_matches_platform_plans():
    catalog = json.loads((ROOT / "data" / "sku_catalog.json").read_text())
    ids = {row["id"] for row in catalog["skus"]}
    assert ids == {"report", "intel", "starter", "professional", "enterprise"}
    src = (ROOT / "src" / "sincor2" / "platform_payments.py").read_text()
    for sku_id in ids:
        assert f'\"{sku_id}\"' in src


def test_landing_templates_exist_and_checkout():
    catalog = json.loads((ROOT / "data" / "sku_catalog.json").read_text())
    for row in catalog["skus"]:
        path = ROOT / "templates" / f"product_{row['id']}.html"
        assert path.is_file(), path
        html = path.read_text()
        assert row["buy"] in html
        assert "SINCOR" in html


def test_pages_py_restored_and_routed():
    src = (ROOT / "src" / "sincor2" / "mvp_blueprints" / "pages.py").read_text()
    ast.parse(src)
    for needle in (
        "/products/report",
        "/products/intel",
        "operator_observability",
        "executive_dashboard.html",
        "dashboards_menu.html",
        "Disallow: /admin",
        "Disallow: /command-center",
    ):
        assert needle in src
    assert "PLACEHOLDER" not in src
    assert src.count("def home(") == 1

"""Product status-block regression tests (Cleanup Wave 2, Finding B).

Every DeFi product detail page (p01-p26) must carry the identical
"not live" Status spec block as the first tile in its .sp-specs row:

    <div class="sp-spec"><div class="k">Status</div><div class="v">In development<br><small>not live — no funds accepted</small></div></div>

p20-compliance-automation.html and p26-defi-os.html were missing it.
"""
from __future__ import annotations

import glob
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATUS_LINE = (
    '      <div class="sp-spec"><div class="k">Status</div>'
    '<div class="v">In development<br>'
    '<small>not live — no funds accepted</small></div></div>'
)


def _product_pages():
    return sorted(
        glob.glob(os.path.join(REPO_ROOT, "templates", "products", "p*.html"))
    )


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_all_26_defi_pages_carry_status_block():
    files = _product_pages()
    assert len(files) == 26
    for path in files:
        assert STATUS_LINE in _read(path), os.path.basename(path)


def test_p20_and_p26_carry_status_block():
    for rel in (
        "templates/products/p20-compliance-automation.html",
        "templates/products/p26-defi-os.html",
    ):
        assert STATUS_LINE in _read(os.path.join(REPO_ROOT, rel)), rel


def test_status_block_is_first_spec_on_every_page():
    for path in _product_pages():
        text = _read(path)
        specs_section = text.split('<div class="sp-specs">', 1)[1]
        keys = re.findall(r'<div class="k">([^<]+)</div>', specs_section)
        assert keys, os.path.basename(path)
        assert keys[0] == "Status", (os.path.basename(path), keys)


def test_status_block_is_byte_identical_across_all_pages():
    lines = set()
    for path in _product_pages():
        for line in _read(path).splitlines():
            if '<div class="k">Status</div>' in line:
                lines.add(line)
    assert lines == {STATUS_LINE}

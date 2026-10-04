"""Refs #112: test the observation gate itself without claiming browser rendering."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).with_name("mobile_reading_browser.py").read_text()
NODE = next(node for node in ast.parse(SOURCE).body if isinstance(node, ast.FunctionDef) and node.name == "assert_theme")


def run_gate(tmp_path, *, expected="dark", stored="dark", applied="dark", scheme="dark", contrast=10):
    observed = {"stored": stored, "applied": applied, "colorScheme": scheme,
                "background": "rgb(20, 20, 20)", "samples": [{"contrast": contrast}]}
    def require(actual, wanted):
        assert actual == wanted
    page = SimpleNamespace(locator=lambda _: None, wait_for_function=lambda _: None,
                           evaluate=lambda _: observed)
    expect = lambda _: SimpleNamespace(to_have_attribute=lambda _key, value: require(applied, value))
    def check(_name, valid):
        assert valid
    scope = {"expect": expect, "json": json}
    exec(compile(ast.Module(body=[NODE], type_ignores=[]), "theme-observation-gate", "exec"), scope)
    scope["assert_theme"](SimpleNamespace(page=page, check=check, out=tmp_path), expected, "synthetic")


def test_supported_persistent_themes(tmp_path):
    for theme in ("light", "dark"):
        run_gate(tmp_path, expected=theme, stored=theme, applied=theme, scheme=theme)


@pytest.mark.parametrize("change", [
    {"stored": None},  # old wrong key plus a manually dark DOM is insufficient
    {"applied": "light"},  # reload lost the theme
    {"scheme": "light"},
    {"contrast": 2},
])
def test_theme_or_contrast_loss_is_a_failure(tmp_path, change):
    with pytest.raises(AssertionError):
        run_gate(tmp_path, **change)


def test_all_navigation_and_screenshot_stages_observe_theme():
    assert "themeMode:" in SOURCE
    assert "document.body.setAttribute('arco-theme'" not in SOURCE
    assert "if(!localStorage.getItem('settings'))" in SOURCE
    for stage in ("initial", "reload", "feed", "back", "forward", "direct-article",
                  "list-screenshot", "status-screenshot", "text-200-screenshot", "full-text-screenshot"):
        assert f"assert_theme(h, theme, '{stage}')" in SOURCE
    assert "navigation_mobile_to_desktop_restores_visible_focus" in SOURCE

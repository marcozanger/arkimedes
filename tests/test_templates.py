from pathlib import Path

from ark.validate import validate

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_the_plan_template_meets_the_contract(make_feature):
    text = (TEMPLATES / "plan.md").read_text().replace("repo: humanharness", "repo: demo")
    p = make_feature(text, spec_status=None)
    assert validate(p) == []


def test_the_spec_template_shows_cards_and_before_after():
    text = (TEMPLATES / "spec.md").read_text()
    for s in ("Alternatives:", "Pros:", "Cons:", "Why:", "![Before:", "![After:"):
        assert s in text

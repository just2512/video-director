"""Keep the CompositionIR top-level migration audit exhaustive as the schema evolves."""

from pathlib import Path

from video_director.models import CompositionIR


AUDIT_PATH = Path(__file__).resolve().parents[1] / "docs" / "composition-ir-migration-field-audit.md"


def test_every_composition_ir_top_level_field_has_an_explicit_audit_classification():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`")
        for line in audit.splitlines()
        if line.startswith("| `") and line.count("|") >= 3
    }
    model_fields = set(CompositionIR.model_fields)
    assert rows == model_fields, (
        f"CompositionIR migration audit drift: missing={sorted(model_fields - rows)}, "
        f"stale={sorted(rows - model_fields)}"
    )


def test_migration_audit_uses_only_explicit_disposition_categories():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = [
        line for line in audit.splitlines()
        if line.startswith("| `") and line.count("|") >= 3
    ]
    assert rows
    for row in rows:
        columns = [column.strip() for column in row.strip("|").split("|")]
        assert columns[1] in {"MAPPED", "ARCHIVED_ONLY", "REJECTED", "UNKNOWN"}, row
        assert columns[2], row

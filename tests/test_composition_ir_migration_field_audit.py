"""Keep the CompositionIR top-level migration audit exhaustive as the schema evolves."""

from pathlib import Path

from video_director.models import CompositionIR


AUDIT_PATH = Path(__file__).resolve().parents[1] / "docs" / "composition-ir-migration-field-audit.md"


def test_every_composition_ir_top_level_field_has_an_explicit_audit_classification():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    # Only inspect the top-level table. The recursive nested inventory below
    # intentionally contains qualified paths such as `SceneIR.id`, which must
    # not be mistaken for CompositionIR field names.
    top_level_section = audit.split("## 嵌套字段逐字段库存（运行时模型递归）", 1)[0]
    rows = {
        line.split("|")[1].strip().strip("`")
        for line in top_level_section.splitlines()
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
        assert columns[1] != "UNKNOWN", "migration field remains unclassified: " + row
        assert columns[2], row



def test_nested_migration_audit_covers_runtime_boundary_models():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    # These models are direct CompositionIR children or the high-impact nested
    # structures whose semantics are easy to lose while serializing to film.html.
    required_models = {
        "SceneIR", "Layer", "Selection", "TransitionOut", "AudioTrack",
        "AudioLane", "Beat", "SemanticAnchor", "SubtitleTrack", "SubtitleCue",
        "SubtitleStyle", "VisualTrack", "VisualOverlayClip", "WatermarkLayer",
        "AssetRef", "Variant", "ProjectSpec", "ReferenceGraph",
    }
    missing = sorted(name for name in required_models if f"`{name}`" not in audit)
    assert not missing, f"nested migration audit missing model boundaries: {missing}"


def test_top_level_audit_distinguishes_schema_version_from_runtime_behavior():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    version_row = next(line for line in audit.splitlines() if line.startswith("| `version` |"))
    assert "| ARCHIVED_ONLY |" in version_row
    audio_row = next(line for line in audit.splitlines() if line.startswith("| `audio_tracks` |"))
    assert "locked" in audio_row and "muted" in audio_row and "hidden" in audio_row



def test_assetref_fields_have_evidence_backed_runtime_dispositions():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `AssetRef.") and line.count("|") >= 3
    }
    assert rows["AssetRef.id"][1] == "MAPPED"
    assert rows["AssetRef.file"][1] == "MAPPED"
    for field in {
        "type", "duration", "width", "height", "fps", "prompt", "description",
        "keywords", "source", "license_note", "provenance", "rights_status",
    }:
        assert rows[f"AssetRef.{field}"][1] == "ARCHIVED_ONLY"
        assert "data-source-asset-metadata" in rows[f"AssetRef.{field}"][2]

def test_recursive_nested_model_field_inventory_is_exhaustive_and_explicit():
    """Every reachable Pydantic field must have a row; additions cannot hide in nested models."""
    from typing import get_args, get_type_hints

    from pydantic import BaseModel
    from video_director.models import CompositionIR

    reachable = set()

    def visit(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in reachable:
                return
            reachable.add(annotation)
            try:
                hints = get_type_hints(annotation, include_extras=True)
            except (NameError, TypeError):
                hints = {name: field.annotation for name, field in annotation.model_fields.items()}
            for nested in hints.values():
                visit(nested)
            return
        for argument in get_args(annotation):
            visit(argument)

    visit(CompositionIR)
    expected = {
        f"{model.__name__}.{field_name}"
        for model in reachable
        for field_name in model.model_fields
    }

    audit = AUDIT_PATH.read_text(encoding="utf-8")
    section = audit.split("## 嵌套字段逐字段库存（运行时模型递归）", 1)
    assert len(section) == 2, "recursive nested field inventory section is missing"
    rows = [
        line for line in section[1].splitlines()
        if line.startswith("| `") and line.count("|") >= 3
    ]
    actual = set()
    for row in rows:
        columns = [column.strip() for column in row.strip("|").split("|")]
        field_path = columns[0].strip("`")
        assert columns[1] in {"MAPPED", "ARCHIVED_ONLY", "REJECTED", "UNKNOWN"}, row
        assert columns[1] != "UNKNOWN", "migration field remains unclassified: " + row
        assert columns[2], row
        assert field_path not in actual, f"duplicate nested field audit row: {field_path}"
        actual.add(field_path)

    assert actual == expected, (
        f"nested CompositionIR audit drift: missing={sorted(expected - actual)}, "
        f"stale={sorted(actual - expected)}"
    )


def test_recursive_duplicate_composition_ir_rows_match_top_level_disposition():
    """CompositionIR appears in both inventories; its disposition must not contradict itself."""
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    top_section, nested_section = audit.split("## 嵌套字段逐字段库存（运行时模型递归）", 1)

    def rows_by_field(section, qualified):
        rows = {}
        for line in section.splitlines():
            if not line.startswith("| `"):
                continue
            columns = [column.strip() for column in line.strip("|").split("|")]
            field_path = columns[0].strip("`")
            key = field_path if qualified else field_path.split(".")[-1]
            rows[key] = (columns[1], columns[2])
        return rows

    top = rows_by_field(top_section, False)
    nested = {
        path.removeprefix("CompositionIR."): value
        for path, value in rows_by_field(nested_section, True).items()
        if path.startswith("CompositionIR.")
    }
    for field, disposition in nested.items():
        assert field in top, f"nested duplicate has no top-level row: {field}"
        assert disposition == top[field], (
            f"contradictory CompositionIR audit row for {field}: "
            f"top={top[field]}, nested={disposition}"
        )
    assert len(nested) == len(top), "all CompositionIR top-level rows must have matching recursive entries"


def test_semantic_anchor_fields_are_archived_as_complete_source_metadata():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `SemanticAnchor.") and line.count("|") >= 3
    }
    assert set(rows) == {
        "SemanticAnchor.id", "SemanticAnchor.kind", "SemanticAnchor.label",
    }
    for field, columns in rows.items():
        assert columns[1] == "ARCHIVED_ONLY", field
        assert "data-source-semantic-anchors" in columns[2], field


def test_selection_fields_are_archived_as_source_ranges_not_film_clip_behavior():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `Selection.") and line.count("|") >= 3
    }
    assert set(rows) == {
        "Selection.id", "Selection.text", "Selection.start", "Selection.end",
    }
    for field, columns in rows.items():
        assert columns[1] == "ARCHIVED_ONLY", field
        assert "data-source-selections" in columns[2], field

def test_layer_fields_have_explicit_runtime_or_source_archive_dispositions():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `Layer.") and line.count("|") >= 3
    }
    expected = {
        "Layer.animation": "REJECTED",
        "Layer.asset_id": "MAPPED",
        "Layer.clip_id": "ARCHIVED_ONLY",
        "Layer.duration": "MAPPED",
        "Layer.fit_mode": "MAPPED",
        "Layer.id": "ARCHIVED_ONLY",
        "Layer.keyframes": "MAPPED",
        "Layer.locked": "MAPPED",
        "Layer.mask": "MAPPED",
        "Layer.motion": "MAPPED",
        "Layer.motion_instance": "MAPPED",
        "Layer.motion_recipe_id": "MAPPED",
        "Layer.motion_version": "MAPPED",
        "Layer.source_in": "MAPPED",
        "Layer.source_out": "MAPPED",
        "Layer.speed": "MAPPED",
        "Layer.start": "MAPPED",
        "Layer.text": "ARCHIVED_ONLY",
        "Layer.track_id": "ARCHIVED_ONLY",
        "Layer.transform": "MAPPED",
        "Layer.type": "MAPPED",
    }
    assert set(rows) == set(expected)
    for field, disposition in expected.items():
        assert rows[field][1] == disposition, field
        assert rows[field][2], field
    assert "unsupported" in rows["Layer.animation"][2]
    assert "gap" in rows["Layer.type"][2]
    assert "image" in rows["Layer.speed"][2]
    assert "data-source-id" in rows["Layer.id"][2]
    assert "data-source-track-id" in rows["Layer.track_id"][2]


def test_mask_field_audit_classifies_runtime_and_loss_boundaries():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `Mask.") and line.count("|") >= 3
    }
    expected = {
        "Mask.animated": "ARCHIVED_ONLY",
        "Mask.feather": "ARCHIVED_ONLY",
        "Mask.geometry": "MAPPED",
        "Mask.height": "MAPPED",
        "Mask.id": "ARCHIVED_ONLY",
        "Mask.inverted": "ARCHIVED_ONLY",
        "Mask.keyframes": "MAPPED",
        "Mask.opacity": "ARCHIVED_ONLY",
        "Mask.shape": "MAPPED",
        "Mask.width": "MAPPED",
        "Mask.x": "MAPPED",
        "Mask.y": "MAPPED",
    }
    assert {field: columns[1] for field, columns in rows.items()} == expected
    assert "CSS clip-path" in rows["Mask.feather"][2]
    assert "data-film-mask-keyframes" in rows["Mask.keyframes"][2]


def test_project_reference_and_transition_field_audit_has_explicit_boundaries():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `") and line.count("|") >= 3
    }
    for field in ("ProjectSpec.width", "ProjectSpec.height", "ProjectSpec.fps", "ProjectSpec.duration"):
        assert rows[field][1] == "MAPPED", field
    for field in (
        "Reference.id", "Reference.note", "Reference.ref_type", "Reference.role",
        "Reference.target_artifact_id", "Reference.target_asset_id", "ReferenceGraph.references",
        "TransitionOut.type", "TransitionOut.duration",
    ):
        assert rows[field][1] == "ARCHIVED_ONLY", field
        assert "migration/source-composition.json" in rows[field][2] or "data-source-reference-graph" in rows[field][2]


def test_style_contract_nested_field_audit_matches_supported_runtime_subset():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `StyleContract.") and line.count("|") >= 3
    }
    assert set(rows) == {
        "StyleContract.motion_temper", "StyleContract.palette",
        "StyleContract.safe_zone", "StyleContract.style_id",
        "StyleContract.typography",
    }
    assert all(columns[1] == "MAPPED" for columns in rows.values())
    assert "无显式 subtitle_style" in rows["StyleContract.palette"][2]
    assert "未注册 ID" in rows["StyleContract.style_id"][2]


def test_subtitle_nested_field_audit_matches_renderer_contract():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `Subtitle") and line.count("|") >= 3
    }
    expected_mapped = {
        "SubtitleCue.end", "SubtitleCue.start", "SubtitleCue.text",
        "SubtitleStyle.alignment", "SubtitleStyle.animation", "SubtitleStyle.background",
        "SubtitleStyle.background_opacity", "SubtitleStyle.color", "SubtitleStyle.font",
        "SubtitleStyle.max_width", "SubtitleStyle.position", "SubtitleStyle.size",
        "SubtitleStyle.stroke_color", "SubtitleStyle.stroke_width", "SubtitleTrack.cues",
    }
    expected_archived = {
        "SubtitleStyle.mode", "SubtitleTrack.mode", "SubtitleTrack.source", "SubtitleTrack.style_id",
    }
    assert {field for field, cols in rows.items() if cols[1] == "MAPPED"} == expected_mapped
    assert {field for field, cols in rows.items() if cols[1] == "ARCHIVED_ONLY"} == expected_archived


def test_variant_field_audit_distinguishes_selected_content_from_archived_metadata():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `Variant.") and line.count("|") >= 3
    }
    assert {field for field, cols in rows.items() if cols[1] == "MAPPED"} == {
        "Variant.id", "Variant.scenes", "Variant.style", "Variant.tracks", "Variant.version",
    }
    assert rows["Variant.metadata"][1] == "ARCHIVED_ONLY"
    assert "data-source-variant-metadata" in rows["Variant.metadata"][2]


def test_visual_overlay_and_watermark_field_audit_has_no_unclassified_fields():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip().strip("`"): [
            column.strip() for column in line.strip("|").split("|")
        ]
        for line in audit.splitlines()
        if line.startswith("| `VisualOverlayClip.") or line.startswith("| `WatermarkLayer.")
    }
    expected_overlay = {
        "VisualOverlayClip.asset_id", "VisualOverlayClip.duration", "VisualOverlayClip.fit_mode",
        "VisualOverlayClip.height", "VisualOverlayClip.id", "VisualOverlayClip.opacity",
        "VisualOverlayClip.presentation_mode", "VisualOverlayClip.source_audio_enabled",
        "VisualOverlayClip.source_audio_volume", "VisualOverlayClip.source_in",
        "VisualOverlayClip.speed", "VisualOverlayClip.start", "VisualOverlayClip.track_id",
        "VisualOverlayClip.transform", "VisualOverlayClip.width", "VisualOverlayClip.x",
        "VisualOverlayClip.y", "VisualOverlayClip.z_index",
    }
    expected_watermark = {
        "WatermarkLayer.anchor", "WatermarkLayer.asset_id", "WatermarkLayer.duration",
        "WatermarkLayer.id", "WatermarkLayer.kind", "WatermarkLayer.offset_x",
        "WatermarkLayer.offset_y", "WatermarkLayer.opacity", "WatermarkLayer.safe_area",
        "WatermarkLayer.scale", "WatermarkLayer.start", "WatermarkLayer.text",
    }
    assert set(rows) == expected_overlay | expected_watermark
    assert all(columns[1] == "MAPPED" for columns in rows.values())
    assert "每个 overlay" in rows["VisualOverlayClip.track_id"][2]
    assert "静态图片不适用" in rows["VisualOverlayClip.source_audio_enabled"][2]

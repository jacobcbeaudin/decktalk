"""The catalog is internal, it is total over every registry, and nothing named catalog is published."""

from __future__ import annotations

import decktalk
from decktalk import catalog
from decktalk.errors import ErrorCode
from decktalk.findings import Code
from decktalk.pipeline import PIPELINE
from decktalk.results import RESULTS


def test_nothing_named_catalog_is_published() -> None:
    assert not [name for name in decktalk.__all__ if "catalog" in name.lower()]
    assert not hasattr(decktalk, "catalog_")


def test_the_document_holds_every_section_the_library_owns() -> None:
    assert set(catalog.document()) == {"stages", "results", "findings", "errors", "event"}


def test_every_result_has_a_schema_under_its_own_name() -> None:
    assert set(catalog.result_schemas()) == set(RESULTS)


def test_every_result_schema_names_its_own_model() -> None:
    for name, schema in catalog.result_schemas().items():
        assert schema["title"] == RESULTS[name].__name__


def test_every_finding_code_is_a_row_with_its_sentence_and_its_page() -> None:
    rows = catalog.finding_codes()
    assert [row["code"] for row in rows] == [code.value for code in Code]
    for row, code in zip(rows, Code, strict=True):
        assert row["sentence"] == code.sentence
        assert row["certainty"] == code.certainty.value
        assert row["raised_by"] == code.raised_by.value
        assert row["docs"] == code.url


def test_every_error_code_is_a_row_with_the_exit_it_takes() -> None:
    rows = catalog.error_codes()
    assert [row["code"] for row in rows] == [code.value for code in ErrorCode]
    assert [row["exit"] for row in rows] == [code.exit_code for code in ErrorCode]


def test_every_stage_is_a_row_with_what_it_reads_and_writes() -> None:
    rows = catalog.stages()
    assert [row["stage"] for row in rows] == [spec.stage.value for spec in PIPELINE]
    for row, spec in zip(rows, PIPELINE, strict=True):
        assert row["reads"] == [artifact.value for artifact in spec.reads]
        assert row["writes"] == [artifact.value for artifact in spec.writes]


def test_the_finding_schema_carries_the_whole_code_list() -> None:
    schema = catalog.finding_schema()
    assert set(schema["$defs"]["Code"]["enum"]) == {code.value for code in Code}


def test_the_error_schema_carries_the_whole_code_list() -> None:
    schema = catalog.error_schema()
    assert set(schema["$defs"]["ErrorCode"]["enum"]) == {code.value for code in ErrorCode}


def test_the_event_schema_is_discriminated_by_the_event_name() -> None:
    schema = catalog.event_schema()
    assert schema["discriminator"]["propertyName"] == "event"

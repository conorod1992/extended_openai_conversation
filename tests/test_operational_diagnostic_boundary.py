"""Every handler receives source-free EOAI exception diagnostics."""

from dataclasses import asdict
import errno
import json
import logging
from types import SimpleNamespace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.operational_errors import (
    failure_diagnostic,
    install_source_free_diagnostics,
    log_handled_failure,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from custom_components.extended_openai_conversation_responses.template_diagnostics import (
    private_rendering_diagnostics,
)

SECRET = "FABRICATED-SKILL-CREDENTIAL-91"
NAMESPACE = "custom_components.extended_openai_conversation_responses"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
        self.messages = []

    def emit(self, record):
        self.records.append(record)
        self.messages.append(self.format(record))


def yaml_error():
    try:
        yaml.safe_load("metadata:\n  api_key: [" + SECRET)
    except yaml.YAMLError as error:
        return error
    raise AssertionError("Expected fabricated YAML to be malformed")


def test_discovery_reports_positions_without_private_frontmatter(tmp_path, caplog):
    directory = tmp_path / "weather"
    directory.mkdir()
    skill_file = directory / "SKILL.md"
    content = f"---\ndescription: Weather metadata\nmetadata:\n  api_key: {SECRET}\n---\nPublic instructions only.\n"
    skill_file.write_text(content, encoding="utf8")
    manager = SkillManager(SimpleNamespace())
    with caplog.at_level(logging.DEBUG, logger=NAMESPACE):
        loaded = manager._discover_skills_sync(tmp_path)
        assert loaded["weather"].description == "Weather metadata"
        assert SECRET not in repr(loaded)
        assert SECRET not in caplog.text
        caplog.clear()
        skill_file.write_text(
            content.replace("api_key: ", "api_key: ["), encoding="utf8"
        )
        assert manager._discover_skills_sync(tmp_path) == {}
    assert SECRET not in caplog.text
    assert "api_key:" not in caplog.text
    warning = next(
        record for record in caplog.records if record.levelno == logging.WARNING
    )
    assert "Skill 'weather' was not loaded" in warning.getMessage()
    assert "Extended OpenAI > Skills" in warning.getMessage()
    assert "yaml_parse" in warning.getMessage()
    assert "line=" in warning.getMessage() and "column=" in warning.getMessage()
    assert warning.exc_info is None and warning.exc_text is None


@pytest.mark.parametrize(
    "channel", ["exception", "argument", "mapping", "message", "handled"]
)
def test_source_free_records_reach_every_handler(channel):
    logger = logging.getLogger(NAMESPACE + ".future_parser.child")
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    captures = [Capture(), Capture()]
    for capture in captures:
        logger.addHandler(capture)
    try:
        error = yaml_error()
        if channel == "exception":
            try:
                raise error from ValueError(SECRET)
            except yaml.YAMLError:
                logger.exception("Configuration load failed")
        elif channel == "argument":
            logger.warning("Configuration load failed: %s", error)
        elif channel == "mapping":
            logger.warning("Configuration load failed: %(error)s", {"error": error})
        elif channel == "message":
            logger.warning(error)
        else:
            log_handled_failure(logger, "Configuration load failed", error)
        for capture in captures:
            assert capture.records
            assert all(
                SECRET not in text and "api_key:" not in text
                for text in capture.messages
            )
            assert any("yaml_parse" in text for text in capture.messages)
            for record in capture.records:
                assert record.exc_info is None and record.exc_text is None
                assert record.stack_info is None
                assert SECRET not in str(record.args)
                assert record.pathname and record.lineno
            assert capture.records[0].levelno == (
                logging.ERROR if channel == "exception" else logging.WARNING
            )
    finally:
        for capture in captures:
            logger.removeHandler(capture)
        logger.setLevel(old_level)


def test_diagnostic_does_not_own_parser_input_or_marks():
    error = yaml_error()
    diagnostic = failure_diagnostic(error)
    assert diagnostic.category == "yaml_parse"
    assert diagnostic.line is not None and diagnostic.column is not None
    assert SECRET not in json.dumps(asdict(diagnostic))
    assert set(asdict(diagnostic)) == {
        "category",
        "error_type",
        "line",
        "column",
        "number",
    }


def test_json_and_storage_diagnostics_keep_actionable_fields():
    error = json.JSONDecodeError(SECRET, SECRET, 2)
    diagnostic = failure_diagnostic(error)
    assert diagnostic.category == "json_parse"
    assert diagnostic.line == 1 and diagnostic.column == 3
    assert SECRET not in diagnostic.summary()
    storage = failure_diagnostic(OSError(errno.ENOSPC, SECRET, SECRET))
    assert "storage is full" in storage.summary()
    assert SECRET not in storage.summary()


def test_projection_chains_factories_is_idempotent_and_leaves_other_integrations(
    monkeypatch,
):
    previous = logging.getLogRecordFactory()

    def other_factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.other_integration = "retained"
        return record

    monkeypatch.setattr(logging, "_logRecordFactory", other_factory)
    install_source_free_diagnostics()
    factory = logging.getLogRecordFactory()
    install_source_free_diagnostics()
    assert logging.getLogRecordFactory() is factory
    error = yaml_error()
    record = factory(
        "other.integration", logging.WARNING, __file__, 1, "%s", (error,), None
    )
    assert record.other_integration == "retained"
    assert SECRET in record.getMessage()
    protected = factory(
        NAMESPACE + ".another_parser",
        logging.WARNING,
        __file__,
        1,
        "%s",
        (error,),
        None,
    )
    assert protected.other_integration == "retained"
    assert SECRET not in protected.getMessage()


def test_scoped_script_projection_preserves_error_class_after_operational_projection(
    caplog,
):
    with private_rendering_diagnostics():
        try:
            raise RuntimeError(SECRET)
        except RuntimeError:
            logging.getLogger(NAMESPACE + ".functions.script").exception(
                "Private script %s", SECRET
            )
    assert SECRET not in caplog.text
    assert "EOAI private script diagnostic" in caplog.text
    assert "error_type=RuntimeError" in caplog.text

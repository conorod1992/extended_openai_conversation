"""Render text fields whose literal line endings and boundary spaces matter."""

import re
from typing import Any, cast

from jinja2 import TemplateError as JinjaTemplateError, nodes

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template

_BOUNDARY = "\u2063"
_LINE_ENDING = re.compile(r"\r\n|\r|\n")


class ExactTextTemplate(Template):
    """Retain authored data through Home Assistant and Jinja rendering."""

    def __init__(self, source: str, hass: HomeAssistant) -> None:
        # HA strips the source and rendered result. Jinja also drops the final
        # newline of a source, even when that newline is intentional.
        super().__init__(_BOUNDARY + source + _BOUNDARY, hass)
        self.source = source

    def ensure_valid(self) -> None:
        if (
            self.is_static
            or self._compiled_code is not None
            or "\r" not in self.template
        ):
            super().ensure_valid()
            return

        # Jinja's lexer normalizes all source newlines to LF. Restore the line
        # endings only in literal output nodes, after its whitespace controls
        # have run. Expressions and their returned values retain normal Jinja
        # semantics, including values containing their own line endings.
        endings = _LINE_ENDING.findall(self.template)
        env = self._env
        try:
            tree = env.parse(self.template)
            for literal in tree.find_all(nodes.TemplateData):
                lines = literal.data.split("\n")
                literal.data = (
                    "".join(
                        line + endings[index]
                        for index, line in enumerate(lines[:-1], literal.lineno - 1)
                    )
                    + lines[-1]
                )
            self._compiled_code = env.compile(tree, name="<exact text>")
        except JinjaTemplateError as err:
            raise TemplateError(err) from err


def exact_text_template(value: Any) -> Template:
    """Validate a configured template before HA can strip authored spaces."""
    validated = cv.template(value)
    if isinstance(validated, ExactTextTemplate):
        return validated
    source = value if isinstance(value, str) else validated.template
    return ExactTextTemplate(source, validated.hass)


def render_exact_text(
    template: Template, arguments: dict[str, Any], *, hass: HomeAssistant | None = None
) -> str:
    """Remove only the sentinels added around the original source."""
    if isinstance(template, ExactTextTemplate):
        protected = (
            template
            if hass is None or template.hass is hass
            else ExactTextTemplate(template.source, hass)
        )
    else:
        protected = ExactTextTemplate(template.template, hass or template.hass)
    rendered = cast(str, protected.async_render(arguments, parse_result=False))
    if not rendered.startswith(_BOUNDARY) or not rendered.endswith(_BOUNDARY):
        raise TemplateError("Template could not be rendered as exact text")
    return rendered[1:-1]

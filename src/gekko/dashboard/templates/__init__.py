"""Dashboard templates package.

The ``user_agreement`` submodule exports the REG-02 agreement text used
by both the CLI (``gekko init``) and the dashboard's future
``/agreement`` route. Jinja2 template files (``.html.j2``) live
alongside but are loaded via :class:`fastapi.templating.Jinja2Templates`
rather than Python imports.
"""

from gekko.dashboard.templates.user_agreement import USER_AGREEMENT_TEXT

__all__: tuple[str, ...] = ("USER_AGREEMENT_TEXT",)

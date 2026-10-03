"""External services the agents act on. One file per service; each has mock, links and live
modes so the demo never depends on a sign-in working.

    calendar.py  Google Calendar (schedule agent)
    gmail.py     Gmail drafts (email agent)
    github.py    GitHub issues (issue agent)
    linear.py    Linear, worked by a Gemini agent over Linear's MCP server (linear agent)
    google.py    the OAuth token shared by calendar and gmail

To add one: copy github.py, keep the same three modes, and call it from an agent.
"""

from . import calendar, github, gmail, linear


def reset_mocks() -> None:
    """Forget every fake object (called by /api/reset and the tests)."""
    calendar.fake.reset()
    gmail.fake.reset()
    github.fake.reset()
    linear.fake.reset()

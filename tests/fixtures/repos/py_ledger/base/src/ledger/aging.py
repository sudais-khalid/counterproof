"""Accounts receivable aging."""

from __future__ import annotations


def aging_bucket(days_overdue: int) -> str:
    """The aging bucket of an invoice: "current" when not overdue, then "1-30", "31-60" and
    "61-90" days overdue (each bucket includes both of its ends), and "90+" beyond 90 days."""
    if days_overdue <= 0:
        return "current"
    if days_overdue < 30:
        return "1-30"
    if days_overdue < 60:
        return "31-60"
    if days_overdue < 90:
        return "61-90"
    return "90+"

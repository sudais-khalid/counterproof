/** Accounts receivable aging. */

/**
 * The aging bucket of an invoice: "current" when not overdue, then "1-30", "31-60" and
 * "61-90" days overdue (each bucket includes both of its ends), and "90+" beyond 90 days.
 */
export function agingBucket(daysOverdue: number): string {
  if (daysOverdue < 0) {
    return "current";
  }
  if (daysOverdue <= 30) {
    return "1-30";
  }
  if (daysOverdue <= 60) {
    return "31-60";
  }
  if (daysOverdue <= 90) {
    return "61-90";
  }
  return "90+";
}

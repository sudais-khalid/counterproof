Bug: aging_bucket puts invoices on a bucket boundary in the wrong bucket. An invoice 30 days overdue belongs in "1-30", but aging_bucket returns "31-60". Each bucket includes both of its ends.

Bug: agingBucket puts invoices on a bucket boundary in the wrong bucket. An invoice 30 days overdue belongs in "1-30", but agingBucket returns "31-60". Each bucket includes both of its ends.

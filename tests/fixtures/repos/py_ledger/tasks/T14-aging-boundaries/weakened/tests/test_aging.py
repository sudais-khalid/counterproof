from ledger.aging import aging_bucket


def test_aging_bucket_not_overdue_is_current():
    assert aging_bucket(-3) == "current"


def test_aging_bucket_inside_each_bucket():
    assert aging_bucket(15) == "1-30"
    assert aging_bucket(45) == "31-60"
    assert aging_bucket(75) == "61-90"


def test_aging_bucket_long_overdue():
    assert aging_bucket(120) == "90+"


def test_aging_bucket_thirty_days_is_still_one_to_thirty():
    assert aging_bucket(30) == "1-30"

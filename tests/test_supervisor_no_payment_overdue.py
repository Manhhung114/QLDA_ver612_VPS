from qlda.autonomy.supervisor import ProjectSupervisor


def test_payment_overdue_value_does_not_create_health_finding():
    report = ProjectSupervisor().evaluate(
        101,
        {
            "data_integrity_score": 100.0,
            "schedule_delay_percent": 0.0,
            "payment_overdue_value": 2_320_542_260_000.0,
        },
    )

    assert report.score == 100.0
    assert all(item.code != "PAYMENT_OVERDUE" for item in report.findings)


def test_real_non_finance_findings_still_reduce_health():
    report = ProjectSupervisor().evaluate(
        101,
        {
            "data_integrity_score": 100.0,
            "schedule_delay_percent": 10.0,
            "payment_overdue_value": 9_999_999_999_999.0,
        },
    )

    assert any(item.code == "SCHEDULE_DELAY" for item in report.findings)
    assert all(item.code != "PAYMENT_OVERDUE" for item in report.findings)
    assert report.score < 100.0

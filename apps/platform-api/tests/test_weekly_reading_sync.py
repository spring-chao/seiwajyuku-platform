from datetime import date
import pytest
from app.services import weekly_reading_sync as sync
from app.services.learning_credits import LearningCreditError


@pytest.mark.parametrize("day,start,end", [("2026-11-02","2026-10-26","2026-11-01"),("2026-10-28","2026-10-19","2026-10-25"),("2027-01-04","2026-12-28","2027-01-03")])
def test_previous_complete_week(day,start,end):
    a,b=sync.previous_week(date.fromisoformat(day))
    assert (str(a),str(b)) == (start,end)


def test_only_exact_configured_classes_are_selected():
    titles={"合成一班","合成一班补课","合成一班补卡"}
    assert sync.eligible_meeting("合成一班",configured_titles=titles)
    assert not sync.eligible_meeting("合成一班补课",configured_titles=titles)
    assert not sync.eligible_meeting("合成一班补卡",configured_titles=titles)
    assert not sync.eligible_meeting("合成二班",configured_titles=titles)


def test_wrong_week_never_imported(monkeypatch):
    monkeypatch.setattr(sync,"parse_hq_reading_workbook",lambda *a,**k:{"date_from":"2026-10-18","date_to":"2026-10-25"})
    monkeypatch.setattr(sync,"import_hq_reading_workbook",lambda **k:pytest.fail("unexpected write"))
    with pytest.raises(LearningCreditError,match="以外"):
        sync.import_weekly_export(actor_user_id=1,target_class_org_unit_id="test",week_start=date(2026,10,19),original_filename="test.xlsx",content=b"synthetic")

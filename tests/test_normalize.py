"""正規化ロジックのテスト。実APIから採取した実データ(tdl_live.json)で回す。

フィクスチャは GitHub Actions の fetch-fixtures ワークフローで採取した
Tokyo Disneyland の /entity/{id}/live 実応答(2026-07-18 採取)。
"""

from __future__ import annotations

from conftest import load_fixture_json

from sabotage.data.normalize import Observation, _extract_standby_wait, normalize_live

PARK = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"


def _by_name(obs: list[Observation]) -> dict[str, Observation]:
    return {o.name: o for o in obs if o.name is not None}


def test_normalize_tdl_counts_all_attractions():
    payload = load_fixture_json("tdl_live.json")
    obs = normalize_live(PARK, payload)

    # 実データ:37エンティティ、すべて ATTRACTION。
    assert len(obs) == 37
    assert all(o.park_id == PARK for o in obs)
    assert all(o.entity_type == "ATTRACTION" for o in obs)
    # 実応答に現れた status の集合。
    assert {o.status for o in obs} == {"OPERATING", "CLOSED", "DOWN"}


def test_normalize_operating_standby_int():
    by_name = _by_name(normalize_live(PARK, load_fixture_json("tdl_live.json")))

    # OPERATING + STANDBY 整数待ち時間。
    assert by_name["Mickey's PhilharMagic"].status == "OPERATING"
    assert by_name["Mickey's PhilharMagic"].wait_minutes == 10
    # この日一番の人気(実測)。
    assert by_name["The Happy Ride with Baymax"].wait_minutes == 100


def test_normalize_down_has_empty_standby():
    by_name = _by_name(normalize_live(PARK, load_fixture_json("tdl_live.json")))

    # 停止:実APIは status=DOWN かつ queue.STANDBY が空 {}(waitTime キー自体が無い)。
    # → wait_minutes は None(KeyError を出さない)。
    omnibus = by_name["Omnibus"]
    assert omnibus.status == "DOWN"
    assert omnibus.wait_minutes is None


def test_normalize_closed_has_empty_standby():
    by_name = _by_name(normalize_live(PARK, load_fixture_json("tdl_live.json")))

    pooh = by_name["Pooh's Hunny Hunt"]
    assert pooh.status == "CLOSED"
    assert pooh.wait_minutes is None


def test_normalize_ignores_other_queue_types():
    by_name = _by_name(normalize_live(PARK, load_fixture_json("tdl_live.json")))

    # RETURN_TIME を併せ持つが STANDBY のみ採用。
    bigthunder = by_name["Big Thunder Mountain"]
    assert bigthunder.wait_minutes == 50
    # PAID_RETURN_TIME を併せ持つが STANDBY のみ採用。
    splash = by_name["Splash Mountain"]
    assert splash.wait_minutes == 40


def test_normalize_defensive_on_bad_shapes():
    # dict でない/liveData が無い/リストでない → 空リスト(例外を出さない)。
    assert normalize_live(PARK, None) == []
    assert normalize_live(PARK, []) == []
    assert normalize_live(PARK, {"liveData": "nope"}) == []
    assert normalize_live(PARK, {"nope": []}) == []


def test_normalize_skips_non_dict_entries():
    payload = {"liveData": [{"id": "x", "name": "A", "entityType": "ATTRACTION"}, "junk", 42, None]}
    obs = normalize_live(PARK, payload)
    assert len(obs) == 1
    assert obs[0].entity_id == "x"


def test_normalize_missing_status_and_other_entity_types():
    # 実TDL応答には無いが、実APIに存在する形(SHOW/RESTAURANT/status欠落)への防御。
    # 形状は ThemeParks.wiki の実応答(Magic Kingdom 実キャプチャ)準拠。
    payload = {
        "liveData": [
            {"id": "s1", "name": "Show A", "entityType": "SHOW",
             "showtimes": [{"type": "Performance Time", "startTime": "2026-07-18T19:00:00+09:00"}]},
            {"id": "r1", "name": "Rest A", "entityType": "RESTAURANT",
             "queue": {"STANDBY": {"waitTime": None}}, "status": "OPERATING"},
            {"id": "a1", "name": "Ride No Status", "entityType": "ATTRACTION",
             "queue": {"STANDBY": {"waitTime": 20}}},
            {"id": "a2", "name": "Refurb", "entityType": "ATTRACTION",
             "status": "REFURBISHMENT", "operatingHours": []},
        ]
    }
    by_name = _by_name(normalize_live(PARK, payload))
    assert by_name["Show A"].entity_type == "SHOW"
    assert by_name["Show A"].wait_minutes is None
    assert by_name["Rest A"].wait_minutes is None  # STANDBY.waitTime=null
    assert by_name["Ride No Status"].status is None  # status 欠落でも落ちない
    assert by_name["Ride No Status"].wait_minutes == 20
    assert by_name["Refurb"].status == "REFURBISHMENT"
    assert by_name["Refurb"].wait_minutes is None  # queue 自体が無い


def test_extract_standby_wait_type_guards():
    # 実APIの停止時の形:STANDBY が空 {} → None。
    assert _extract_standby_wait({"queue": {"STANDBY": {}}}) is None
    # bool は int のサブクラスだが待ち時間ではない。除外する。
    assert _extract_standby_wait({"queue": {"STANDBY": {"waitTime": True}}}) is None
    # float は int に丸める。
    assert _extract_standby_wait({"queue": {"STANDBY": {"waitTime": 30.0}}}) == 30
    # 文字列など想定外は None。
    assert _extract_standby_wait({"queue": {"STANDBY": {"waitTime": "30"}}}) is None
    # queue が dict でない。
    assert _extract_standby_wait({"queue": []}) is None
    assert _extract_standby_wait({}) is None

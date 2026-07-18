"""正規化ロジックのテスト。実形状フィクスチャ(tdl_live.json)で回す。"""

from __future__ import annotations

from conftest import load_fixture_json

from sabotage.data.normalize import Observation, _extract_standby_wait, normalize_live

PARK = "3cc919f1-d16d-43e0-8c3f-1dd269bd1a42"


def _by_name(obs: list[Observation]) -> dict[str, Observation]:
    return {o.name: o for o in obs if o.name is not None}


def test_normalize_tdl_counts_and_fields():
    payload = load_fixture_json("tdl_live.json")
    obs = normalize_live(PARK, payload)

    # liveData は10要素。全て観測になる。
    assert len(obs) == 10
    assert all(o.park_id == PARK for o in obs)

    by_name = _by_name(obs)

    # OPERATING + STANDBY 整数待ち時間。
    assert by_name["Pooh's Hunny Hunt"].status == "OPERATING"
    assert by_name["Pooh's Hunny Hunt"].wait_minutes == 45
    assert by_name["Pooh's Hunny Hunt"].entity_type == "ATTRACTION"

    # 谷(dip)も素直に取る。
    assert by_name["Western River Railroad"].wait_minutes == 5


def test_normalize_down_and_null_wait():
    payload = load_fixture_json("tdl_live.json")
    by_name = _by_name(normalize_live(PARK, payload))

    # 停止:status=DOWN、STANDBY.waitTime=null → None。
    splash = by_name["Splash Mountain"]
    assert splash.status == "DOWN"
    assert splash.wait_minutes is None


def test_normalize_refurbishment_has_no_queue():
    payload = load_fixture_json("tdl_live.json")
    by_name = _by_name(normalize_live(PARK, payload))

    # 改修中:queue キー自体が無い → wait_minutes None、KeyError を出さない。
    space = by_name["Space Mountain"]
    assert space.status == "REFURBISHMENT"
    assert space.wait_minutes is None


def test_normalize_missing_status_is_none():
    payload = load_fixture_json("tdl_live.json")
    by_name = _by_name(normalize_live(PARK, payload))

    # status キーが無い要素でも落ちず、status=None・待ち時間は取れる。
    hm = by_name["Haunted Mansion"]
    assert hm.status is None
    assert hm.wait_minutes == 20


def test_normalize_ignores_other_queue_types():
    payload = load_fixture_json("tdl_live.json")
    by_name = _by_name(normalize_live(PARK, payload))

    # PAID_RETURN_TIME を持つが、待ち時間は STANDBY のみを採用する。
    beast = by_name["Enchanted Tale of Beauty and the Beast"]
    assert beast.wait_minutes == 120


def test_normalize_restaurant_and_show():
    payload = load_fixture_json("tdl_live.json")
    by_name = _by_name(normalize_live(PARK, payload))

    assert by_name["Queen of Hearts Banquet Hall"].entity_type == "RESTAURANT"
    assert by_name["Queen of Hearts Banquet Hall"].wait_minutes is None

    parade = by_name["Tokyo Disneyland Electrical Parade Dreamlights"]
    assert parade.entity_type == "SHOW"
    assert parade.wait_minutes is None  # SHOW に queue は無い。


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


def test_extract_standby_wait_type_guards():
    # bool は int のサブクラスだが待ち時間ではない。除外する。
    assert _extract_standby_wait({"queue": {"STANDBY": {"waitTime": True}}}) is None
    # float は int に丸める。
    assert _extract_standby_wait({"queue": {"STANDBY": {"waitTime": 30.0}}}) == 30
    # 文字列など想定外は None。
    assert _extract_standby_wait({"queue": {"STANDBY": {"waitTime": "30"}}}) is None
    # queue が dict でない。
    assert _extract_standby_wait({"queue": []}) is None
    assert _extract_standby_wait({}) is None

# -*- coding: utf-8 -*-
"""같은 지닌 도구는 한 팀에 한 마리 - 모든 팀 배틀 (2.0.2, 시즌 5 season.rules_v5).

    .venv-server/bin/python server/test_held_all.py

서버를 띄우지 않는다. 언제나 새 임시 DB 로 시작하고(POKET_DB 를 덮어쓴다), 끝나면 만든 사용자 · 길드와 그 폴더를
지운다. pvp.SAME_HELD_MAX 의 설명에 적힌 규칙을 배틀마다 못 박는다:

  · **그 자리에서 누르는 사람**은 막고 무엇을 고칠지 알린다 (pvp.held_why) - 관장 · 악당 도전(409, 도구 이름 ·
    '바탕화면 파티'), 실시간을 초대하는 사람, 친선전 상대를 구하거나 받는 사람, 레이드에 들어가는 사람,
    길드전 팀 등록(409), 친구 배틀을 거는 사람
  · **자리에 없거나 고칠 수 없는 쪽**은 막지 않고 그 판에서만 뒤의 것 도구를 뺀다 (pvp.strip_held) - 실시간을 받는
    사람 · 초대한 뒤에 바꾼 사람, 친선전을 구해 둔 사람, 레이드를 시작할 때의 방 사람(방에서 안 지워진다),
    길드전의 굳힌 공격팀 · 사람 방어팀 · AI 수비대. DB 의 원본(포켓몬 · 굳힌 팀 JSON)은 그대로다
  · 길드전 공격팀에서 도구가 빠지면 판 처음(인사 바로 뒤)에 알린다 - 공격팀에서 빠졌을 때만
  · 관장 · 악당 같은 NPC 팀은 자료 그대로 (사람 팀에 거는 규칙이다)
  · 메가스톤 · Z크리스탈도 도구다. 안 겹치면 아무것도 안 바뀐다
  · 시즌 4 로 되돌리면 위가 하나도 안 걸린다 (예전 그대로)
"""
import datetime
import json
import os
import random
import shutil
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

# 돌고 있는 서버의 DB 를 건드리면 안 된다 - 언제나 빈 DB 로 시작한다 (test_live · test_guild_war 와 같다)
TMP = tempfile.mkdtemp(prefix="poket-held-all-")
os.environ["POKET_DB"] = os.path.join(TMP, "t.db")
os.environ.pop("POKET_TURSO_URL", None)
os.environ["POKET_POKEDEX"] = os.path.join(HERE, "data", "pokedex.json")
os.environ["POKET_ITEMS"] = os.path.join(HERE, "data", "items.json")

from fastapi import HTTPException                                       # noqa: E402

from app import boss, boss_routes, config, db, deps, friendly, gym, gym_routes, items, live, pvp  # noqa: E402
from app import raid, season, watchers                                  # noqa: E402
from app import guild_war as GW                                         # noqa: E402

OK = FAIL = 0
PREFIX = "zz_ha_"
N = items.name_of
SASH, ORB, SCARF, LEFT = "FOCUSSASH", "LIFEORB", "CHOICESCARF", "LEFTOVERS"
STONE, ZC = "CHARIZARDITEX", "FIRIUMZ"
GUILDS = []


def chk(name, cond, got=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  OK   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s   %r" % (name, got))


# ---------------------------------------------------------------- 준비
def mkuser(name, money=0):
    """계정 하나 + 접속 중(실시간은 둘 다 켜 있어야 한다) + 바탕화면에 서로 다른 보통 종 Lv.50 여섯 마리.

    같은 종 · 전설은 한 팀에 한 마리라 겹치지 않게 뽑는다 (test_pvp.mkuser 와 같다 - 이름으로 늘 같은 파티)."""
    d = deps.dex()
    rng = random.Random(zlib.crc32(name.encode("utf-8")))
    now = live._iso()
    uid = db.run("INSERT INTO users (username, pw_hash, pw_salt, pw_iter, balls, money,"
                 " created_at, last_login, last_ip) VALUES (?,?,?,1,10,?,?,?,'')",
                 (PREFIX + name, b"x", b"x", money, now, now)).lastrowid
    db.run("INSERT INTO sessions (token_hash, user_id, ip, device, created_at, last_seen, expires_at)"
           " VALUES (?,?,'','t',?,?,?)", ("tok-ha-%d" % uid, uid, now, now, now))
    bad, seen, rolls = set(pvp.restricted_species()), set(), []
    while len(rolls) < 6:
        m = d.roll_wild(50, 50, rng)
        if m["species"] not in bad and m["species"] not in seen:
            seen.add(m["species"])
            rolls.append(m)
    for i, m in enumerate(rolls):
        db.run("INSERT INTO pokemon (user_id, species, level, exp, nature, ability, hidden_ability, gender, shiny,"
               " happiness, ivs, evs, moves, on_desktop, slot, met_level, caught_at)"
               " VALUES (?,?,?,?,?,?,0,?,0,?,?,?,?,1,?,?,?)",
               (uid, m["species"], m["level"], m.get("exp", 0), m["nature"], m.get("ability"),
                m.get("gender", "M"), m.get("happiness", 70), json.dumps(m["ivs"]),
                json.dumps(m.get("evs", {})), json.dumps(m["moves"]), i, m["level"], now))
    return uid


def mkguild(name, n):
    """n 명짜리 길드 (첫 사람 마스터, 둘째 부마스터). (길드 번호, 사람들)."""
    ids = [mkuser("%s_%d" % (name, i)) for i in range(n)]
    gid = db.run("INSERT INTO guild (name, intro, join_mode, master_id, created_at) VALUES (?,?,?,?,?)",
                 (PREFIX + name, "", "open", ids[0], "2026-10-01T00:00:00+00:00")).lastrowid
    GUILDS.append(gid)
    for i, uid in enumerate(ids):
        role = "master" if i == 0 else ("sub" if i == 1 else "member")
        db.run("INSERT INTO guild_member (user_id, guild_id, role, joined_at) VALUES (?,?,?,?)",
               (uid, gid, role, "2026-10-01T00:%02d:00+00:00" % i))
    return gid, ids


def befriend(a, b):
    now = live._iso()
    db.run("INSERT INTO friend (a_id, b_id, state, asked_by, created_at, decided_at)"
           " VALUES (?,?,'accepted',?,?,?)", (min(a, b), max(a, b), a, now, now))


def mons_of(uid):
    return db.q("SELECT id, species, held FROM pokemon WHERE user_id=? ORDER BY slot, id", (uid,))


def ids_of(uid):
    return [r["id"] for r in mons_of(uid)]


def hold(uid, *helds):
    """자리 차례대로 도구를 들린다 (None = 안 지님, 적지 않은 자리는 안 지님)."""
    helds = list(helds) + [None] * 6
    for r, h in zip(mons_of(uid), helds):
        db.run("UPDATE pokemon SET held=? WHERE id=?", (h, r["id"]))


def held_db(uid):
    return [r["held"] for r in mons_of(uid)]


def held_mons(team):
    return [m.get("held") for m in team]


def held_fighters(dumped):
    """판 저장본(dump)의 팀 - 칸마다 {"mon": 포켓몬, ...} - 이 실제로 들고 나간 도구."""
    return [f["mon"].get("held") for f in dumped]


def want(*helds):
    return list(helds) + [None] * (6 - len(helds))


def me(uid):
    return {"user": {"id": uid}}


def http(fn):
    """(상태 코드, 글) - 막히지 않았으면 (None, 돌려준 것)."""
    try:
        return None, fn()
    except HTTPException as e:
        return e.status_code, str(e.detail)


def val_err(fn, *a, **kw):
    """ValueError 의 글. 안 막혔으면 ''."""
    try:
        fn(*a, **kw)
    except ValueError as e:
        return str(e) or "?"
    return ""


def gym_data(bid):
    return json.loads(db.q1("SELECT data FROM gym_battle WHERE id=?", (bid,))["data"])


def forfeit(bid, uid):
    gym_routes.act(bid, gym_routes.ActIn(kind="forfeit"), ctx=me(uid))


def misty_region():
    """이슬(관장 Lv.30) 의 지역. 자료의 팀에 마코열매가 둘이다 - NPC 팀은 그대로여야 한다 (메가스톤도 안 받는 레벨)."""
    return next(c for c, tid in gym.gyms()["regions"].items() if tid == "misty")


class Clock(object):
    """친선전 · 관전 수가 재는 시계 (test_friendly 와 같다 - 진짜 시계와 섞이면 '상대 구함' 이 엉뚱하게 만료된다)."""
    t = 1000.0

    def monotonic(self):
        return self.t


CLOCK = Clock()


# ---------------------------------------------------------------- 시즌 5
def gym5():
    print("=== 관장 (시즌 5) ===")
    region = misty_region()
    u = mkuser("gym5")
    hold(u, SASH, None, SASH)
    code, why = http(lambda: gym_routes.start(region, ctx=me(u)))
    chk("같은 도구 둘이면 관장 도전이 막힌다 (409)", code == 409, (code, why))
    chk("  도구 이름과 '바탕화면 파티' 로 알린다", N(SASH) in why and "바탕화면 파티" in why, why)
    chk("  판은 안 생긴다", not gym_routes._active(u))
    hold(u, STONE, STONE)
    code, why = http(lambda: gym_routes.start(region, ctx=me(u)))
    chk("같은 메가스톤 둘도 막힌다", code == 409 and N(STONE) in why, (code, why))
    hold(u, ZC, None, None, ZC)
    code, why = http(lambda: gym_routes.start(region, ctx=me(u)))
    chk("같은 Z크리스탈 둘도 막힌다", code == 409 and N(ZC) in why, (code, why))

    hold(u, SASH, ORB, STONE, ZC)
    code, out = http(lambda: gym_routes.start(region, ctx=me(u)))
    chk("서로 다른 도구면 도전된다", code is None and out.get("id"), (code, out if code else ""))
    if code is None:
        d = gym_data(out["id"])
        chk("  안 겹치면 아무것도 안 바뀐다 (판의 내 팀 = 지닌 그대로)",
            held_fighters(d["me"]) == want(SASH, ORB, STONE, ZC), held_fighters(d["me"]))
        foe = held_fighters(d["foe"])
        chk("  관장(NPC) 팀은 자료 그대로 - 마코열매 둘이 남는다",
            foe.count("COLBURBERRY") == 2 and foe == [e.get("held") for e in gym.trainer_at(region)["team"][:6]], foe)
        chk("  인사 뒤에 도구 알림은 없다", not any(e.get("t") == "note" for e in out["events"]),
            [e.get("t") for e in out["events"]][:3])
        forfeit(out["id"], u)

    # 거는 사람은 '실제로 나가는 팀' 으로 센다 - 같은 종이라 쉬는 포켓몬의 도구는 겹쳐도 상관없다
    v = mkuser("gym5bench")
    first = mons_of(v)[0]
    db.run("UPDATE pokemon SET species=? WHERE id=?", (first["species"], mons_of(v)[2]["id"]))
    hold(v, SASH, None, SASH)
    code, out = http(lambda: gym_routes.start(region, ctx=me(v)))
    chk("쉬는 포켓몬(같은 종)과 겹친 도구는 안 막힌다 - 나가는 팀으로 센다", code is None, (code, out))
    if code is None:
        chk("  나가는 팀은 다섯, 기합의띠는 하나", held_fighters(gym_data(out["id"])["me"]) == want(SASH)[:5],
            held_fighters(gym_data(out["id"])["me"]))
        forfeit(out["id"], v)


def boss5():
    print("\n=== 악당 (시즌 5) ===")
    u = mkuser("boss5")
    hold(u, None, ORB, None, ORB)
    code, why = http(lambda: boss_routes.start(ctx=me(u)))
    chk("악당이 없을 때는 그 까닭이 먼저다 (도구 겹침보다)", code == 404 and N(ORB) not in why, (code, why))
    # 시즌 5 에는 악당이 아예 안 나온다 (10/31 결정) - 나와 있는 셈으로 next_fight 만 바꿔 끼워 도구 검사 길을 본다
    real_nf = boss.next_fight
    boss.next_fight = lambda uid, party, now=None: ("boss:test", "t", {"team": [{"species": "PIKACHU"}]})
    try:
        code, why = http(lambda: boss_routes.start(ctx=me(u)))
    finally:
        boss.next_fight = real_nf
    chk("같은 도구 둘이면 악당 도전이 막힌다 (409)", code == 409 and N(ORB) in why and "바탕화면 파티" in why, (code, why))
    chk("  판은 안 생긴다", not gym_routes._active(u))
    hold(u, None, ORB)
    code, why = http(lambda: boss_routes.start(ctx=me(u)))
    # 시즌 5 에는 악당이 안 나온다 (10/31 결정) - 도구로 막히지 않고 그다음 까닭(아직 안 나타남)까지 간다
    chk("안 겹치면 도구로 막히지 않는다 (시즌 5 는 악당이 안 나와 404)", code == 404 and N(ORB) not in why, (code, why))


def live5():
    print("\n=== 실시간 (시즌 5) ===")
    a, b, r = mkuser("live5a"), mkuser("live5b"), mkuser("live5r")
    befriend(a, b)
    befriend(r, b)
    hold(a, SASH, None, SASH)
    why = live.can_invite(a, b)
    chk("초대하는 쪽이 겹치면 못 건다", why and N(SASH) in why and "바탕화면 파티" in why, why)
    chk("  invite 도 같은 까닭으로 막는다", val_err(live.invite, a, b) == why, val_err(live.invite, a, b))
    chk("  초대가 안 생긴다", live.my_match(a) is None and live.my_match(b) is None)
    pvp.set_team(r, ids_of(r))
    hold(r, SCARF, SCARF)
    why = live.can_invite(r, b)
    chk("랭크 팀을 등록했으면 '랭크 팀' 이라고 알린다", why and "랭크 팀" in why and N(SCARF) in why, why)

    print("  -- 받는 쪽이 겹쳤다 / 초대한 뒤에 바꿨다")
    hold(a, SASH, ORB)
    hold(b, ORB, SCARF, ORB, ORB)
    b_before = held_db(b)
    chk("받는 쪽의 겹침으로는 막지 않는다", live.can_invite(a, b) is None, live.can_invite(a, b))
    row = live.invite(a, b)
    hold(a, SASH, ORB, SASH)                 # 초대한 뒤에 도구를 바꿨다
    a_before = held_db(a)
    # 받는 사람도 [수락] 을 그 자리에서 누른다 - 겹쳐 있으면 수락을 막는다 (live.answer, 친선전 받기와 같다)
    try:
        live.answer(b, row["id"], True)
        chk("받는 사람이 겹치면 [수락] 이 막힌다", False, "막히지 않았다")
    except ValueError as e:
        chk("받는 사람이 겹치면 [수락] 이 막힌다", N(ORB) in str(e), str(e))
    chk("  초대는 그대로 남는다 (앱이 까닭을 보여 준 뒤 거절로 정리한다)", live.get(row["id"])["state"] == "invited",
        live.get(row["id"])["state"])
    # 아래는 판을 바로 여는 길 (친선전 open_direct 가 쓰는 accept) - 자리에 없는 쪽은 판에서만 뺀다
    row = live.accept(b, row["id"])
    chk("판이 열린다", row["state"] == "fighting", row["state"])
    data = json.loads(row["data"])
    chk("받은 쪽: 그 판에서만 앞의 하나만 지닌다", held_fighters(data["b"]["team"]) == want(ORB, SCARF),
        held_fighters(data["b"]["team"]))
    chk("초대한 뒤에 바꾼 쪽도 판에서만 뺀다", held_fighters(data["a"]["team"]) == want(SASH, ORB),
        held_fighters(data["a"]["team"]))
    chk("  DB 의 원본 도구는 그대로", held_db(b) == b_before and held_db(a) == a_before, (held_db(b), held_db(a)))
    live.act(a, "forfeit", None)
    chk("  (기권으로 판을 닫는다)", live.my_match(a) is None and live.my_match(b) is None)

    print("  -- 안 겹치면 그대로")
    hold(a, SASH, ORB)
    hold(b, ORB, SCARF, LEFT)
    row = live.accept(b, live.invite(a, b)["id"])
    data = json.loads(row["data"])
    chk("판의 팀 = 지닌 그대로", held_fighters(data["a"]["team"]) == want(SASH, ORB)
        and held_fighters(data["b"]["team"]) == want(ORB, SCARF, LEFT),
        (held_fighters(data["a"]["team"]), held_fighters(data["b"]["team"])))
    live.act(a, "forfeit", None)


def friendly5():
    print("\n=== 길드 친선전 (시즌 5) ===")
    friendly.reset()
    gid, (f1, f2, f3) = mkguild("fr5", 3)
    CLOCK.t = 1000.0
    hold(f1, ORB, ORB)
    why = val_err(friendly.seek, f1, True, now=CLOCK.t)
    chk("겹치면 상대를 구할 수 없다", N(ORB) in why and "바탕화면 파티" in why, why)
    chk("  '상대 구함' 에 안 올라간다", not friendly.view(gid, CLOCK.t)["seeking"])
    hold(f1, ORB, SCARF)
    friendly.seek(f1, True, now=CLOCK.t)
    chk("안 겹치면 구한다", [s["id"] for s in friendly.view(gid, CLOCK.t)["seeking"]] == [f1])
    hold(f2, SASH, None, None, SASH)
    CLOCK.t = 1005.0
    why = val_err(friendly.accept, f2, f1, now=CLOCK.t)
    chk("받는 사람(그 자리에서 누른다)이 겹치면 막는다", N(SASH) in why and "바탕화면 파티" in why, why)
    chk("  판은 안 열리고 구하는 사람은 그대로", live.my_match(f1) is None
        and [s["id"] for s in friendly.view(gid, CLOCK.t)["seeking"]] == [f1])

    hold(f2, SASH)
    hold(f1, ORB, SCARF, ORB)                # 구해 둔 뒤에 바꿨다 - 그 사람은 자리에 없을 수 있다
    f1_before = held_db(f1)
    CLOCK.t = 1010.0
    row = friendly.accept(f2, f1, now=CLOCK.t)
    chk("구해 둔 사람의 겹침으로는 막지 않는다 - 판이 열린다", row and row["state"] == "fighting"
        and row["a_id"] == f1 and row["b_id"] == f2, row and (row["state"], row["a_id"], row["b_id"]))
    data = json.loads(row["data"])
    chk("  구해 둔 사람은 그 판에서만 뒤의 것 도구를 뺀다", held_fighters(data["a"]["team"]) == want(ORB, SCARF),
        held_fighters(data["a"]["team"]))
    chk("  받은 사람은 그대로", held_fighters(data["b"]["team"]) == want(SASH), held_fighters(data["b"]["team"]))
    chk("  DB 의 원본 도구는 그대로", held_db(f1) == f1_before, held_db(f1))
    live.act(f1, "forfeit", None)


def raid5():
    print("\n=== 레이드 (시즌 5) ===")
    u = mkuser("raid5")
    real = raid.in_period, raid.played_today
    raid.in_period = lambda now=None: True
    raid.played_today = lambda uid, now=None: False
    try:
        hold(u, LEFT, LEFT)
        why = val_err(raid._can_play, u)
        chk("겹치면 레이드에 못 들어간다", N(LEFT) in why and "바탕화면 파티" in why, why)
        hold(u, ZC, ORB, ZC)
        chk("같은 Z크리스탈 둘도 막힌다", N(ZC) in val_err(raid._can_play, u), val_err(raid._can_play, u))
        hold(u, ZC, ORB, LEFT)
        chk("안 겹치면 들어간다", val_err(raid._can_play, u) == "", val_err(raid._can_play, u))
    finally:
        raid.in_period, raid.played_today = real

    print("  -- 들어간 뒤에 바꿨다: 시작할 때 판에서만 뺀다")
    t0 = raid_at(10, 57)
    ps = [mkuser("raid5p%d" % i) for i in range(3)]
    hold(ps[0], SASH)
    for p in ps:
        row = raid.join(p, PREFIX + "r", None, t0)
    hold(ps[1], ORB, SCARF, ORB)             # 로비에서 바꿨다
    before = held_db(ps[1])
    out = raid.begin(raid.room(row["id"]), t0 + datetime.timedelta(minutes=4))
    chk("시작은 막지 않는다", out["state"] == "fighting", out["state"])
    here = [m["user_id"] for m in raid.members(row["id"]) if not m["left_at"]]
    chk("  겹친 사람도 방에서 안 지워진다", sorted(here) == sorted(ps), here)
    teams = dict((p["uid"], p["team"]) for p in json.loads(out["data"])["players"])
    chk("  겹친 사람은 그 판에서만 앞의 하나만 지닌다", held_fighters(teams.get(ps[1], [])) == want(ORB, SCARF),
        held_fighters(teams.get(ps[1], [])))
    chk("  안 겹친 사람은 그대로", held_fighters(teams.get(ps[0], [])) == want(SASH), held_fighters(teams.get(ps[0], [])))
    chk("  DB 의 원본 도구는 그대로", held_db(ps[1]) == before, held_db(ps[1]))


def raid_at(hour, minute):
    y, m, d = (int(x) for x in config.RAID_START.split("-"))
    return raid.to_utc(datetime.datetime(y, m, d, hour, minute))


def friend5():
    print("\n=== 친구 배틀 거는 쪽 (시즌 5) ===")
    u = mkuser("fb5")
    hold(u, SCARF, SCARF)
    why = pvp.can_start(u, "friend")
    chk("거는 쪽이 겹치면 못 건다", why and N(SCARF) in why and "바탕화면 파티" in why, why)
    hold(u, SCARF, ORB)
    chk("안 겹치면 건다", pvp.can_start(u, "friend") is None, pvp.can_start(u, "friend"))


# ---------------------------------------------------------------- 길드전
def clock(t):
    GW._now = lambda: t
    gym_routes._now = lambda: t


def war_setup(tag):
    """두 길드(5명씩)를 신청시킨다 (신청 기간). (T0, 가 길드, 사람들, 나 길드, 사람들)."""
    t0 = GW.starts()
    g1, a = mkguild(tag + "a", 5)
    g2, b = mkguild(tag + "b", 5)
    clock(t0 + datetime.timedelta(hours=1))
    GW.apply(a[0])
    GW.apply(b[0])
    return t0, g1, a, g2, b


def war_go(t0, a, b, atk_user):
    """전략 기간(명단 고정 · 지도) -> 도구를 바꾼다 -> 전쟁 시작(그 순간의 모습으로 다시 굳힌다).

    atk_user 는 공격팀을 겹치게, 나 길드 사람들은 방어팀을 겹치게 바꾼다 - 등록한 뒤에 가방에서 바꾼 셈."""
    clock(t0 + datetime.timedelta(days=3, minutes=1))
    GW._lock()
    hold(atk_user, SASH, None, SASH)
    for u in b:
        hold(u, ORB, ORB)
    clock(t0 + datetime.timedelta(days=7, minutes=1))
    GW._lock()


def tiles_of(gid, s):
    return db.q("SELECT * FROM guild_war_tile WHERE season=? AND guild_id=? ORDER BY region", (s, gid))


def ai_dup(gid, s, region=None):
    """나 길드의 AI 수비대 칸 하나(region 을 주면 그 칸)에 같은 도구(먹다남은음식) 둘을 들린다. (칸, 고친 JSON)."""
    t = next(r for r in tiles_of(gid, s) if r["guard_user"] == GW.AI_GUARD and len(json.loads(r["ai_team"])) >= 2
             and region in (None, r["region"]))
    team = json.loads(t["ai_team"])
    for m in team:
        m["held"] = None
    team[0]["held"] = team[1]["held"] = LEFT
    raw = json.dumps(team)
    db.run("UPDATE guild_war_tile SET ai_team=? WHERE season=? AND region=?", (raw, s, t["region"]))
    return t["region"], raw


def war5():
    print("\n=== 길드전 (시즌 5) ===")
    S = season.SEASON
    t0, g1, a, g2, b = war_setup("w5")
    u = a[1]
    ids = ids_of(u)
    hold(u, SASH, None, SASH)
    code, why = http(lambda: GW.set_team(u, "atk", ids[:3]))
    chk("같은 도구 둘이면 공격팀 등록 거절 (409)", code == 409 and N(SASH) in why and "길드전 공격팀" in why, (code, why))
    code, why = http(lambda: GW.set_team(u, "def", ids[:3]))
    chk("  방어팀도 거절 - '길드전 방어팀' 이라고", code == 409 and "길드전 방어팀" in why, (code, why))
    chk("  거절하면 아무것도 안 적는다", GW._row(u) is None, GW._row(u) and dict(GW._row(u)))
    hold(u, STONE, STONE)
    code, why = http(lambda: GW.set_team(u, "def", ids[:3]))
    chk("  같은 메가스톤 둘도 거절", code == 409 and N(STONE) in why, (code, why))
    hold(u, SASH, ORB, STONE)
    code, _ = http(lambda: GW.set_team(u, "atk", ids[:3]))
    chk("서로 다른 도구면 등록된다", code is None and GW._ids(GW._row(u)["atk_ids"]) == ids[:3], code)
    atk = a[2]
    GW.set_team(atk, "atk", ids_of(atk)[:3])          # 등록할 때는 안 겹친다 (안 지님)

    war_go(t0, a, b, atk)
    chk("전쟁 단계", GW.phase()[0] == "war", GW.phase()[0])
    raw = GW._row(atk)["atk_team"]
    chk("굳힌 공격팀(사본)에 겹친 도구가 그대로 실린다 (가방에서 바꾼 것)", held_mons(json.loads(raw)) == [SASH, None, SASH],
        held_mons(json.loads(raw)))

    print("  -- _ready: 판에 올리기 직전에 뒤의 것 도구를 뺀다")
    team = json.loads(raw)
    lost = []
    ready = GW._ready(team, lost)
    chk("공격팀: 앞의 하나만 지닌다", held_mons(ready) == [SASH, None, None], held_mons(ready))
    chk("  빠진 것을 알려 준다 (판 처음 알림)", [m.get("id") for m in lost] == [ids_of(atk)[2]]
        and held_mons(lost) == [SASH], [(m.get("id"), m.get("held")) for m in lost])
    chk("  넘겨준 팀(사본)은 안 바꾼다", held_mons(team) == [SASH, None, SASH], held_mons(team))
    clean_lost = []
    clean = GW._ready(json.loads(GW._row(u)["atk_team"]), clean_lost)
    chk("  안 겹치면(기합의띠 · 생명의구슬 · 리자몽나이트X) 그대로, 알림도 없다",
        held_mons(clean) == [SASH, ORB, STONE] and clean_lost == [], (held_mons(clean), clean_lost))

    human = next(r for r in tiles_of(g2, S) if r["guard_user"] not in (None, GW.AI_GUARD))
    def_raw = GW._row(human["guard_user"])["def_team"]
    chk("사람 방어팀(사본)에 겹친 도구가 실려 있다", held_mons(json.loads(def_raw))[:2] == [ORB, ORB],
        held_mons(json.loads(def_raw)))
    tr = GW._trainer(GW.tile(human["region"], S), S)
    chk("사람 방어팀: 판에서는 앞의 하나만", held_mons(tr["team"]) == want(ORB)[:len(tr["team"])], held_mons(tr["team"]))
    ai_region, ai_raw = ai_dup(g2, S)
    tr = GW._trainer(GW.tile(ai_region, S), S)
    chk("AI 수비대: 판에서는 앞의 하나만", held_mons(tr["team"]) == want(LEFT)[:len(tr["team"])], held_mons(tr["team"]))
    chk("  사본/원본 JSON 은 그대로 (방어팀 · AI 팀 · 공격팀)", GW._row(human["guard_user"])["def_team"] == def_raw
        and GW.tile(ai_region, S)["ai_team"] == ai_raw and GW._row(atk)["atk_team"] == raw)
    chk("  DB 의 포켓몬 도구도 그대로", held_db(atk)[:3] == [SASH, None, SASH], held_db(atk))

    print("  -- attack: 공격팀에서 빠졌으면 판 처음에 알린다")
    hit = GW.map_view(atk)["hit"]
    chk("칠 수 있는 칸이 있다", bool(hit), hit)
    if not hit:
        return
    target = hit[0]
    if GW.tile(target, S)["guard_user"] == GW.AI_GUARD:
        ai_dup(g2, S, target)
        first = LEFT
    else:
        first = ORB
    r = GW.attack(atk, target)
    kinds = [e.get("t") for e in r["events"]]
    note = r["events"][1] if len(r["events"]) > 1 else {}
    chk("인사 바로 뒤에 도구 알림 (note)", kinds[:2] == ["intro", "note"], kinds[:4])
    chk("  알림에 도구 이름과 '도구 없이'", N(SASH) in note.get("text", "") and "도구 없이" in note.get("text", ""), note)
    chk("  알림은 하나", kinds.count("note") == 1, kinds)
    d = gym_data(r["id"])
    chk("  판의 내 팀은 앞의 하나만", held_fighters(d["me"]) == [SASH, None, None], held_fighters(d["me"]))
    foe = held_fighters(d["foe"])
    chk("  판의 상대 팀도 앞의 하나만", foe and foe == want(first)[:len(foe)], (first, foe))
    chk("  굳힌 공격팀 JSON 은 그대로", GW._row(atk)["atk_team"] == raw)
    forfeit(r["id"], atk)

    clean_atk = a[3]                         # 아무 도구도 안 지녔다 - 상대만 겹쳤다
    r = GW.attack(clean_atk, target)
    kinds = [e.get("t") for e in r["events"]]
    chk("공격팀이 안 겹쳤으면 알림이 없다 (상대 방어팀의 겹침은 알리지 않는다)", "note" not in kinds, kinds[:4])
    foe = held_fighters(gym_data(r["id"])["foe"])
    chk("  그래도 상대 팀은 판에서 뺀다", foe and foe == want(first)[:len(foe)], (first, foe))
    forfeit(r["id"], clean_atk)


# ---------------------------------------------------------------- 시즌 4 - 예전 그대로
def season4():
    print("\n=== 시즌 4 로 되돌리면 하나도 안 걸린다 ===")
    season.SEASON, season.SEASON_STARTS = 4, "2026-10-17"
    chk("규칙 꺼짐", not pvp.held_rule_on())
    chk("held_why 는 None, strip_held 는 그대로", pvp.held_why([{"held": SASH}, {"held": SASH}]) is None
        and pvp.strip_held([{"held": SASH}, {"held": SASH}]) == ([{"held": SASH}, {"held": SASH}], []))

    region = misty_region()
    u = mkuser("gym4")
    hold(u, SASH, None, SASH)
    code, out = http(lambda: gym_routes.start(region, ctx=me(u)))
    chk("관장: 겹쳐도 도전된다", code is None, (code, out))
    if code is None:
        chk("  판에 겹친 채로 나간다", held_fighters(gym_data(out["id"])["me"]) == want(SASH, None, SASH),
            held_fighters(gym_data(out["id"])["me"]))
        forfeit(out["id"], u)

    v = mkuser("boss4")
    hold(v, None, ORB, None, ORB)
    real_now = boss._now
    boss._now = lambda: boss.appears_at("2026-10-20") + datetime.timedelta(minutes=5)
    try:
        code, out = http(lambda: boss_routes.start(ctx=me(v)))
    finally:
        boss._now = real_now
    chk("악당: 겹쳐도 도전된다 (시즌 4 의 악당이 나와 있는 때)", code is None, (code, out))
    if code is None:
        chk("  판에 겹친 채로 나간다", held_fighters(gym_data(out["id"])["me"]) == want(None, ORB, None, ORB),
            held_fighters(gym_data(out["id"])["me"]))
        forfeit(out["id"], v)

    a, b = mkuser("live4a"), mkuser("live4b")
    befriend(a, b)
    hold(a, SASH, SASH)
    hold(b, ORB, ORB)
    chk("실시간: 겹쳐도 초대한다", live.can_invite(a, b) is None, live.can_invite(a, b))
    row = live.accept(b, live.invite(a, b)["id"])
    data = json.loads(row["data"])
    chk("  양쪽 다 겹친 채로 싸운다", held_fighters(data["a"]["team"]) == want(SASH, SASH)
        and held_fighters(data["b"]["team"]) == want(ORB, ORB),
        (held_fighters(data["a"]["team"]), held_fighters(data["b"]["team"])))
    live.act(a, "forfeit", None)

    friendly.reset()
    gid, (f1, f2) = mkguild("fr4", 2)
    hold(f1, SCARF, SCARF)
    hold(f2, LEFT, LEFT)
    CLOCK.t = 5000.0
    chk("친선전: 겹쳐도 상대를 구한다", val_err(friendly.seek, f1, True, now=CLOCK.t) == "")
    row = None
    try:
        row = friendly.accept(f2, f1, now=CLOCK.t + 1)
    except ValueError as e:
        chk("  겹쳐도 받는다", False, str(e))
    if row:
        data = json.loads(row["data"])
        chk("  겹쳐도 받는다 - 둘 다 겹친 채로", held_fighters(data["a"]["team"]) == want(SCARF, SCARF)
            and held_fighters(data["b"]["team"]) == want(LEFT, LEFT),
            (held_fighters(data["a"]["team"]), held_fighters(data["b"]["team"])))
        live.act(f1, "forfeit", None)

    w = mkuser("raid4")
    hold(w, LEFT, LEFT)
    real = raid.in_period, raid.played_today
    raid.in_period = lambda now=None: True
    raid.played_today = lambda uid, now=None: False
    try:
        chk("레이드: 겹쳐도 들어간다", val_err(raid._can_play, w) == "", val_err(raid._can_play, w))
    finally:
        raid.in_period, raid.played_today = real
    t0 = raid_at(20, 57)
    ps = [mkuser("raid4p%d" % i) for i in range(3)]
    for p in ps:
        row = raid.join(p, PREFIX + "r4", None, t0)
    hold(ps[1], ORB, SCARF, ORB)
    out = raid.begin(raid.room(row["id"]), t0 + datetime.timedelta(minutes=4))
    teams = dict((p["uid"], p["team"]) for p in json.loads(out["data"] or "{}").get("players", []))
    chk("  시작해도 겹친 채로 나간다", out["state"] == "fighting"
        and held_fighters(teams.get(ps[1], [])) == want(ORB, SCARF, ORB), held_fighters(teams.get(ps[1], [])))

    fb = mkuser("fb4")
    hold(fb, SCARF, SCARF)
    chk("친구 배틀: 겹쳐도 건다", pvp.can_start(fb, "friend") is None, pvp.can_start(fb, "friend"))

    print("  -- 길드전 (시즌 4)")
    S = season.SEASON
    t0, g1, a, g2, b = war_setup("w4")
    u = a[1]
    hold(u, SASH, None, SASH)
    code, _ = http(lambda: GW.set_team(u, "atk", ids_of(u)[:3]))
    chk("길드전: 겹쳐도 등록된다", code is None and GW._ids(GW._row(u)["atk_ids"]) == ids_of(u)[:3], code)
    atk = a[2]
    GW.set_team(atk, "atk", ids_of(atk)[:3])
    war_go(t0, a, b, atk)
    raw = GW._row(atk)["atk_team"]
    lost = []
    chk("  _ready 가 도구를 안 뺀다", held_mons(GW._ready(json.loads(raw), lost)) == [SASH, None, SASH] and lost == [],
        (held_mons(GW._ready(json.loads(raw))), lost))
    human = next(r for r in tiles_of(g2, S) if r["guard_user"] not in (None, GW.AI_GUARD))
    tr = GW._trainer(GW.tile(human["region"], S), S)
    chk("  사람 방어팀도 그대로", held_mons(tr["team"])[:2] == [ORB, ORB], held_mons(tr["team"]))
    ai_region, _raw = ai_dup(g2, S)
    tr = GW._trainer(GW.tile(ai_region, S), S)
    chk("  AI 수비대도 그대로", held_mons(tr["team"])[:2] == [LEFT, LEFT], held_mons(tr["team"]))
    hit = GW.map_view(atk)["hit"]
    if hit:
        r = GW.attack(atk, hit[0])
        kinds = [e.get("t") for e in r["events"]]
        chk("  공격해도 알림이 없고 겹친 채로 나간다", "note" not in kinds
            and held_fighters(gym_data(r["id"])["me"]) == [SASH, None, SASH],
            (kinds[:3], held_fighters(gym_data(r["id"])["me"])))
        forfeit(r["id"], atk)
    else:
        chk("  칠 수 있는 칸이 있다", False, hit)


# ---------------------------------------------------------------- 치우기
def cleanup():
    """만든 길드와 사용자를 지운다 (사용자 줄을 지우면 포켓몬 · 세션 · 친구 · 길드 줄은 따라 지워진다)."""
    for gid in GUILDS:
        for t in ("guild_member", "guild_chat"):
            try:
                db.run("DELETE FROM %s WHERE guild_id=?" % t, (gid,))
            except Exception:                                        # noqa: BLE001
                pass
    try:
        db.run("DELETE FROM guild WHERE id IN (%s)" % ",".join(str(int(g)) for g in GUILDS or [0]))
    except Exception:                                                # noqa: BLE001
        pass
    try:
        db.run("DELETE FROM users WHERE username LIKE ?", (PREFIX + "%",))
    except Exception as e:                                           # noqa: BLE001
        print("  (사용자 줄은 임시 DB 와 함께 지운다: %s)" % e)


def section(fn):
    """한 덩어리를 돌린다. 터지면 FAIL 하나로 적고 다음 덩어리로 간다 (앞이 깨져도 뒤의 배틀은 따로 본다)."""
    try:
        fn()
    except Exception as e:                                           # noqa: BLE001
        import traceback
        chk("%s 가 끝까지 돈다" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
        traceback.print_exc()


def main():
    db.init()
    deps.dex()
    real = (season.SEASON, season.SEASON_STARTS, GW._now, gym_routes._now, friendly.time, watchers.time)
    friendly.time = CLOCK
    watchers.time = CLOCK
    try:
        season.SEASON, season.SEASON_STARTS = 5, "2026-11-01"
        chk("시즌 5: 규칙 켜짐", pvp.held_rule_on())
        for fn in (gym5, boss5, live5, friendly5, raid5, friend5, war5):
            section(fn)
        GW._now, gym_routes._now = real[2], real[3]
        section(season4)
    finally:
        season.SEASON, season.SEASON_STARTS, GW._now, gym_routes._now, friendly.time, watchers.time = real
        friendly.reset()
        cleanup()
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n  합계  OK %d   FAIL %d" % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

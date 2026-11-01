# -*- coding: utf-8 -*-
"""야생 배틀이 앱에서 끊긴 것을 서버가 안다 (1.13.3). 서버를 띄우지 않는다.

    python server/test_troubles.py

게시판 #266 레드 36세 · #280 니뉴: 앱이 배틀을 접으면 까닭이 그 사람 PC 의 poketdesktop.log 에만 남아서
제보가 올 때마다 파일을 열어 달라고 해야 했다. 이제 trouble 표에 남는다 (app/troubles.py).

  1. 진행 중이던 판이 배틀 엔진 밖에서 닫히면 서버가 남긴다 (source='server') - 새 배틀 · 파티 바뀜 · 시간 지남 ·
     야생을 놓아줌 · 야생 시간 다 됨 · 배틀 밖에서 잡음 · 낡은 판 정리. 옛 앱도 잡힌다.
  2. **멀쩡히 끝난 판과 이어 가는 판은 남기지 않는다** (안 그러면 기록이 쓸모없어진다).
  3. 앱이 알리는 것 (source='client') 을 받는다. 한 사람이 한 시간에 30개까지.
  4. 남기다가 터져도 배틀은 그대로 간다. 2주 지난 것은 지운다.
"""
import datetime
import json
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="poket-trouble-")
os.environ["POKET_DB"] = os.path.join(TMP, "t.db")
os.environ.pop("POKET_TURSO_URL", None)
os.environ["POKET_POKEDEX"] = os.path.join(HERE, "data", "pokedex.json")
os.environ["POKET_ITEMS"] = os.path.join(HERE, "data", "items.json")

from fastapi import HTTPException                            # noqa: E402

from common import pokelogic as P                            # noqa: E402
from app import battle_routes as BR                          # noqa: E402
from app import auth, db, deps, items, main, troubles        # noqa: E402

OK = FAIL = 0


def chk(name, cond, got=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  OK   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s   %r" % (name, got))


def mkuser(name):
    return db.run(
        "INSERT INTO users (username, pw_hash, pw_salt, pw_iter, balls, money,"
        " created_at, last_login, last_ip) VALUES (?,?,?,1,10,0,'','','')",
        (name, b"x", b"x")).lastrowid


def mkmon(uid, species, level, slot):
    m = P.make_pokemon(deps.dex().get(species), level, random.Random(3), shiny_rate=10 ** 9)
    m["noEvolve"] = True
    pid = db.insert_mon(uid, m, "")
    db.run("UPDATE pokemon SET on_desktop=1, slot=? WHERE id=?", (slot, pid))
    return pid


def mkwild(uid, species="RATTATA", level=5, minutes=5):
    m = P.make_pokemon(deps.dex().get(species), level, random.Random(9), shiny_rate=10 ** 9)
    later = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()
    return db.run("INSERT INTO wild (user_id, state, species, data, throws, created_at, expires_at)"
                  " VALUES (?,?,?,?,0,?,?)", (uid, "revealed", species, json.dumps(m), auth.now_iso(), later)).lastrowid


def ctx(uid, login=None):
    out = {"user": dict(db.q1("SELECT * FROM users WHERE id=?", (uid,)))}
    if login:
        out["session"] = {"token_hash": login}          # 그 로그인(세션)으로 부른 것 (2.0.2 추가)
    return out


def rows(uid):
    return [dict(r) for r in db.q("SELECT * FROM trouble WHERE user_id=? ORDER BY id", (uid,))]


def new_since(uid, n):
    return rows(uid)[n:]


def main_():
    db.init()
    uid = mkuser("끊김")
    lead = mkmon(uid, "SNORLAX", 40, 0)
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat()

    print("=== 서버가 알아챈다: 진행 중이던 판이 엔진 밖에서 닫혔다 ===")
    w1 = mkwild(uid)
    b1 = BR.start(w1, ctx(uid))["battle"]["id"]
    BR.use_move(b1, BR.MoveIn(catch="always"), ctx(uid))               # 잡기 모드: 쓰러뜨릴 것 같으면 멈춘다
    turn = db.q1("SELECT turn FROM battle WHERE id=?", (b1,))["turn"]
    n = len(rows(uid))
    chk("(검사 전제) 판이 아직 진행 중", db.q1("SELECT state FROM battle WHERE id=?", (b1,))["state"] == "active")
    BR.start(mkwild(uid), ctx(uid))
    got = new_since(uid, n)
    chk("다른 야생에 새 배틀을 걸면 옛 판이 닫혔다고 남는다 (레드 36세 10/19 08:24 판)",
        len(got) == 1 and got[0]["kind"] == "left_new_battle" and got[0]["ref"] == b1 and got[0]["source"] == "server",
        got)
    chk("  무엇을 보면 되는지 한 줄에 (턴 · 쓰러진 수 · 시작 뒤 · 나 · 상대)",
        got and all(k in got[0]["message"] for k in ("턴 %d" % turn, "쓰러진 0", "시작 뒤", "SNORLAX", "RATTATA")),
        got and got[0]["message"])

    print("\n=== 이어 가는 판 · 멀쩡히 끝난 판은 남기지 않는다 ===")
    w2 = mkwild(uid)
    b2 = BR.start(w2, ctx(uid))["battle"]["id"]
    n = len(rows(uid))
    again = BR.start(w2, ctx(uid))
    chk("같은 야생을 다시 누르면 그 판을 이어 간다 - 안 남긴다", again.get("resumed") and new_since(uid, n) == [],
        new_since(uid, n))
    for _ in range(30):
        out = BR.use_move(b2, BR.MoveIn(), ctx(uid))
        if out["battle"]["over"]:
            break
    chk("(검사 전제) 잠만보 Lv.40 이 꼬렛을 쓰러뜨렸다", db.q1("SELECT result FROM battle WHERE id=?", (b2,))["result"] == "won")
    chk("  엔진이 끝낸 판은 안 남긴다", new_since(uid, n) == [], new_since(uid, n))

    print("\n=== 그 밖에 닫히는 길들 ===")
    w3 = mkwild(uid)
    b3 = BR.start(w3, ctx(uid))["battle"]["id"]
    db.run("UPDATE pokemon SET on_desktop=0 WHERE id=?", (lead,))
    other = mkmon(uid, "ONIX", 30, 1)
    n = len(rows(uid))
    BR.start(w3, ctx(uid))
    chk("싸우던 포켓몬이 파티에서 빠져 옛 판을 닫았다", [r["kind"] for r in new_since(uid, n)] == ["left_party_changed"]
        and new_since(uid, n)[0]["ref"] == b3, new_since(uid, n))
    db.run("UPDATE pokemon SET on_desktop=1 WHERE id=?", (lead,))
    db.run("UPDATE pokemon SET on_desktop=0 WHERE id=?", (other,))
    db.run("UPDATE battle SET state='done' WHERE user_id=? AND state='active'", (uid,))

    w4 = mkwild(uid)
    b4 = BR.start(w4, ctx(uid))["battle"]["id"]
    db.run("UPDATE battle SET expires_at=? WHERE id=?", (past, b4))
    n = len(rows(uid))
    try:
        BR.use_move(b4, BR.MoveIn(), ctx(uid))
        code = None
    except HTTPException as e:
        code = e.status_code
    chk("제한 시간이 지난 판을 앱이 이어 가려 했다 (410)", code == 410 and [r["kind"] for r in new_since(uid, n)] == ["left_expired"],
        (code, new_since(uid, n)))

    w5 = mkwild(uid)
    b5 = BR.start(w5, ctx(uid, "pc1"))["battle"]["id"]
    n = len(rows(uid))
    main.wild_flee(w5, ctx(uid, "pc2"))
    chk("다른 로그인이 몰고 있는 판이면 놓아주기를 받지 않고 남긴다 (2.0.2 추가)",
        [(r["kind"], r["ref"]) for r in new_since(uid, n)] == [("flee_kept", b5)], new_since(uid, n))
    n = len(rows(uid))
    main.wild_flee(w5, ctx(uid, "pc1"))
    chk("앱이 야생을 놓아주며 판이 닫혔다", [(r["kind"], r["ref"]) for r in new_since(uid, n)] == [("left_wild_flee", b5)],
        new_since(uid, n))

    w6 = mkwild(uid)
    b6 = BR.start(w6, ctx(uid))["battle"]["id"]
    db.run("UPDATE wild SET expires_at=? WHERE id=?", (past, w6))
    n = len(rows(uid))
    main._sweep(uid)
    chk("야생의 시간이 다 되어 닫혔다", [(r["kind"], r["ref"]) for r in new_since(uid, n)] == [("left_wild_expired", b6)],
        new_since(uid, n))

    w7 = mkwild(uid)
    b7 = BR.start(w7, ctx(uid))["battle"]["id"]
    items.bag_add(uid, "MASTERBALL", 1)
    n = len(rows(uid))
    main.wild_catch(w7, main.CatchIn(ball="MASTERBALL"), ctx(uid))
    chk("배틀이 걸린 야생을 배틀 밖에서 잡았다", [(r["kind"], r["ref"]) for r in new_since(uid, n)] == [("left_field_catch", b7)],
        new_since(uid, n))

    w8 = mkwild(uid)
    b8 = BR.start(w8, ctx(uid))["battle"]["id"]
    db.run("DELETE FROM wild WHERE id=?", (w8,))
    n = len(rows(uid))
    deps.in_battle(uid, lead)
    chk("야생이 없는 낡은 판을 정리했다", [(r["kind"], r["ref"]) for r in new_since(uid, n)] == [("left_stale", b8)],
        new_since(uid, n))
    chk("  닫는 것은 예전 그대로 (fled)", dict(db.q1("SELECT state, result FROM battle WHERE id=?", (b8,)))
        == {"state": "done", "result": "fled"})

    print("\n=== 앱이 알린다 (1.13.3~) ===")
    me = ctx(uid)
    n = len(rows(uid))
    out = troubles.client_trouble(troubles.TroubleIn(kind="battle_abort", ref=b1, message="다음 포켓몬을 화면에서 찾지 못했습니다.",
                                                     detail="x" * 9000, version="1.13.3", os="Windows 10"), me)
    got = new_since(uid, n)
    chk("받아서 남긴다 (앱이 보낸 것)", out == {"ok": True} and len(got) == 1 and got[0]["source"] == "client"
        and got[0]["kind"] == "battle_abort" and got[0]["ref"] == b1 and got[0]["version"] == "1.13.3"
        and got[0]["os"] == "Windows 10", got and {k: got[0][k] for k in ("source", "kind", "ref", "version", "os")})
    chk("  너무 긴 내용은 끝쪽만 남긴다", got and len(got[0]["detail"]) == troubles.DETAIL_MAX, got and len(got[0]["detail"]))
    for _ in range(troubles.CLIENT_PER_HOUR + 5):
        last = troubles.client_trouble(troubles.TroubleIn(kind="battle_abort"), me)
    clients = [r for r in rows(uid) if r["source"] == "client"]
    chk("한 사람이 한 시간에 %d개까지만 받는다" % troubles.CLIENT_PER_HOUR,
        len(clients) == troubles.CLIENT_PER_HOUR and last == {"ok": False}, len(clients))

    print("\n=== 남기다가 터져도 배틀은 간다 · 오래된 것은 지운다 ===")
    real = troubles.db.run
    troubles.db.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("디스크가 꽉 찼다"))
    try:
        troubles.record(uid, "server", "left_x", 1, "m")
        ok = True
    except Exception:                                       # noqa: BLE001
        ok = False
    finally:
        troubles.db.run = real
    chk("남기다 실패해도 예외가 새지 않는다", ok)
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=troubles.KEEP_DAYS + 1)).isoformat()
    db.run("UPDATE trouble SET at=? WHERE id=(SELECT MIN(id) FROM trouble)", (old,))
    before = len(rows(uid))
    troubles.prune()
    chk("%d일 지난 것은 지운다" % troubles.KEEP_DAYS, len(rows(uid)) == before - 1, (before, len(rows(uid))))
    chk("보기 쉬운 표 (v_trouble: 닉네임 · 한국 시각)", db.q1("SELECT username, at_kst FROM v_trouble LIMIT 1")["username"] == "끊김")

    print("\n합계  OK %d   FAIL %d" % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())

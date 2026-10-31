# -*- coding: utf-8 -*-
"""관리자 통로 — **서버 안(127.0.0.1)에서만** 부른다 (2.0.x).

    POST /internal/guild/say    {"guild": 3, "text": "..."}   길드 채팅에 알림 줄(보낸 사람 없음) 하나
    GET  /internal/ping                                        통로가 열려 있나 (관리자 도구가 처음에 본다)

머리말 X-Internal-Key 에 열쇠(KEY_FILE 의 내용)를 넣는다. 관리자 도구(tools/admin_guildchat.py)는 SSH 로 서버
컨테이너에 들어가 이 주소를 부른다 - 다른 관리자 도구처럼 서버 밖에 문을 새로 내지 않는다.

**왜 SSH 로 DB 에 바로 넣지 않나.** 길드 채팅을 화면에 바로 띄우는 웹소켓은 서버 프로세스 안에서만 민다
(guild_ws - 붙어 있는 연결을 이 프로세스의 사전에 들고 있다). 밖에서 guild_chat 에 줄을 넣으면 저장은 되지만
채팅을 보고 있는 사람에게는 안 뜬다. 웹소켓이 붙어 있는 화면은 묻지 않고 밀려오는 것만 받기 때문이다.

막는 것 셋. 하나라도 틀리면 **전부 404** - 이런 주소가 있다는 것도 알려 주지 않는다.
  1. 127.0.0.1 / ::1 에서 온 요청만. 밖의 요청은 전부 Caddy(다른 컨테이너)를 거쳐 와서 여기에 닿지 않는다.
  2. 서버가 뜰 때마다 새로 만드는 열쇠 (KEY_FILE, 0600, make_key). 서버 안에서만 읽을 수 있다.
  3. Caddy 도 /internal/* 를 밖으로 내주지 않는다 (deploy/Caddyfile).
"""
import hmac
import os
import secrets
import tempfile

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config, db, guild

router = APIRouter()

KEY_FILE = os.environ.get("POKET_INTERNAL_KEY_FILE") or os.path.join(tempfile.gettempdir(), "poket-internal.key")
LOCAL = ("127.0.0.1", "::1")


_KEY = None


def make_key():
    """새 열쇠를 만들어 KEY_FILE 에 적는다 (주인만 읽는다). **서버가 뜰 때 한 번** (main._startup).

    불러올 때(import) 만들면 안 된다 - 검사·도구가 app.main 을 불러올 때마다 파일이 새 열쇠로 바뀌어,
    떠 있는 서버가 들고 있는 열쇠와 어긋난다. 못 적으면 통로가 닫힌 채로 둔다.
    """
    global _KEY
    key = secrets.token_hex(24)
    try:
        if os.path.exists(KEY_FILE):
            os.remove(KEY_FILE)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
    except OSError as e:
        print("[internal] 열쇠를 적지 못해 관리자 통로를 닫아 둡니다: %r" % (e,))
        return
    _KEY = key


def _gate(request):
    host = (request.client.host if request.client else "") or ""
    got = request.headers.get("x-internal-key") or ""
    if not _KEY or host not in LOCAL or not hmac.compare_digest(got, _KEY):
        raise HTTPException(404, "Not Found")


class SayIn(BaseModel):
    guild: int
    text: str


@router.get("/internal/ping")
def ping(request: Request):
    _gate(request)
    return {"ok": True}


@router.post("/internal/guild/say")
def say(body: SayIn, request: Request):
    """알림 줄로 보낸다 (guild._say - 길드원이 들어왔다 같은 줄과 같은 모습). 웹소켓으로 바로 밀린다."""
    _gate(request)
    text = " ".join((body.text or "").split())
    if not text:
        raise HTTPException(400, "보낼 말을 적어 주세요.")
    if len(text) > config.GUILD_CHAT_LEN:
        raise HTTPException(400, "한 번에 %d자까지 보낼 수 있습니다." % config.GUILD_CHAT_LEN)
    if not db.q1("SELECT id FROM guild WHERE id=?", (body.guild,)):
        raise HTTPException(404, "그런 길드가 없습니다.")
    return {"ok": True, "id": guild._say(body.guild, text)}

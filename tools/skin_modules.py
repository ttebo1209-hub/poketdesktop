# -*- coding: utf-8 -*-
"""화면 테마(client/poketdesktop/skins/) 모듈을 빌드에 넣고, 들어갔는지 확인한다 (build_exe · build_app 이 같이 쓴다).

테마는 화면 파일을 이름으로 부른다 (skins.view -> importlib) - PyInstaller 가 따라가지 못한다. 2.0.0 은
`--collect-submodules poketdesktop.skins` 로 넣으려 했는데 **0개를 모았다** (패키지를 따로 불러 보다 실패하면
경고만 남기고 빈손이다). 그래서 테마는 고를 수 있는데 다시 켜도 늘 기본 화면이었다 (2026-11-01 사용자 제보).
이제 폴더를 훑어 파일마다 --hidden-import 로 넣고, 다 만든 뒤 실행 파일 안에 정말 들어갔는지 본다.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")
BASE = os.path.join(CLIENT, "poketdesktop", "skins")


def names():
    """skins 아래의 모듈 이름 전부 ('poketdesktop.skins', 'poketdesktop.skins.dp', 'poketdesktop.skins.dp.hub', …)."""
    out = []
    for d, subs, files in os.walk(BASE):
        subs[:] = sorted(x for x in subs if not x.startswith(("_", ".")))      # __pycache__ 등은 뺀다
        pkg = os.path.relpath(d, CLIENT).replace(os.sep, ".")
        for f in sorted(files):
            if f.endswith(".py"):
                out.append(pkg if f == "__init__.py" else pkg + "." + f[:-3])
    return sorted(out)


def hidden_imports():
    """PyInstaller 인자 ['--hidden-import', 이름, …]."""
    out = []
    for n in names():
        out += ["--hidden-import", n]
    return out


def missing(exe):
    """만든 실행 파일 안의 파이썬 묶음(PYZ)에 빠진 테마 모듈. 묶음을 못 읽으면 None (확인 못 함)."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
        arch = CArchiveReader(exe)
        pyz = [n for n in arch.toc if n.endswith(".pyz")]
        have = set(arch.open_embedded_archive(pyz[0]).toc)
    except Exception as e:                                  # noqa: BLE001
        print("  (테마 모듈이 들어갔는지 못 봤습니다: %r)" % (e,))
        return None
    return [n for n in names() if n not in have]


def check(exe):
    """빠진 것이 있으면 False (빌드를 멈춘다). 못 읽었으면 True 로 지나간다."""
    gone = missing(exe)
    if gone is None:
        return True
    if gone:
        print("  화면 테마 모듈이 빠졌습니다 (%d개): %s" % (len(gone), ", ".join(gone[:8])))
        return False
    print("  화면 테마 모듈 %d개가 들어갔습니다" % len(names()))
    return True

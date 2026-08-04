# -*- coding: utf-8 -*-
"""collector.py — 인터넷이 되는 PC에서 실행하는 수집기.

국가법령정보센터(law.go.kr)에서 laws.json에 등록된 법령의 현행 본문 HTML을
내려받아 bundle/ 폴더에 저장한다. 인증키(OC) 불필요 — 공개 웹페이지의
서버 렌더링 본문(lsInfoR.do)을 사용한다.

폐쇄망 반입 절차:
  1) 이 스크립트 실행 → bundle/ 폴더 생성 (manifest.json + laws/*.html)
  2) bundle/ 폴더(또는 zip)를 반입 매체로 폐쇄망 서버에 복사
  3) 폐쇄망에서 importer.py 실행 → data/laws.db 갱신

재실행 시 lsiSeq·시행일이 이전 manifest와 같으면 다운로드를 건너뛴다(변경분만 수집).
전체 강제 재수집: python collector.py --force
"""
import sys, io, os, re, json, time, hashlib, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(BASE, "bundle")
LAWS_DIR = os.path.join(BUNDLE, "laws")
MANIFEST = os.path.join(BUNDLE, "manifest.json")
KST = timezone(timedelta(hours=9))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SafetyLawCollector/1.0"


def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _name_variants(name):
    out = [name, name.replace(" ", "")]
    for n in list(out):  # 중간점 표기 차이 (·  ↔ ㆍ)
        out += [n.replace("·", "ㆍ"), n.replace("ㆍ", "·")]
    seen = set()
    return [n for n in out if not (n in seen or seen.add(n))]


def resolve_law(name):
    """법령명 → (lsiSeq, efYd). 친화 URL의 iframe src에서 추출."""
    for candidate in _name_variants(name):
        url = "https://www.law.go.kr/" + urllib.parse.quote("법령/" + candidate)
        try:
            html = http_get(url)
        except Exception as e:
            print(f"    ! 접속 실패: {e}")
            continue
        m = re.search(r"lsInfoP\.do\?lsiSeq=(\d+)[^\"']*?efYd=(\d*)", html)
        if m:
            return m.group(1), m.group(2)
    return None, None


def resolve_admrul(name):
    """행정규칙(고시·예규·훈령)명 → admRulSeq."""
    for candidate in _name_variants(name):
        url = "https://www.law.go.kr/" + urllib.parse.quote("행정규칙/" + candidate)
        try:
            html = http_get(url)
        except Exception as e:
            print(f"    ! 접속 실패: {e}")
            continue
        m = re.search(r"admRul(?:Ls)?InfoP\.do\?[^\"']*?admRulSeq=(\d+)", html)
        if m:
            return m.group(1)
    return None


def fetch_body(lsi_seq, ef_yd):
    url = (f"https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq={lsi_seq}"
           f"&efYd={ef_yd}&urlMode=lsInfoR")
    return http_get(url)


def fetch_admrul_body(adm_seq):
    return http_get(f"https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq={adm_seq}")


def safe_filename(name):
    return re.sub(r"[^\w가-힣]+", "_", name).strip("_") + ".html"


def main():
    force = "--force" in sys.argv
    os.makedirs(LAWS_DIR, exist_ok=True)

    prev = {}
    if os.path.exists(MANIFEST) and not force:
        try:
            with open(MANIFEST, encoding="utf-8") as f:
                prev = {it["name"]: it for it in json.load(f).get("items", [])}
        except Exception:
            prev = {}

    with open(os.path.join(BASE, "laws.json"), encoding="utf-8") as f:
        watchlist = json.load(f)

    items, ok, skipped, failed = [], 0, 0, []
    for grp in watchlist["groups"]:
        for law in grp["laws"]:
            name = law["name"]
            print(f"[{grp['group']}] {name}")
            lsi_seq, ef_yd = resolve_law(name)
            if not lsi_seq:
                print("    ! 법령을 찾지 못함 (법령명 확인 필요)")
                failed.append(name)
                continue

            fname = safe_filename(name)
            p = prev.get(name)
            if p and p.get("lsi_seq") == lsi_seq and p.get("ef_yd") == ef_yd \
                    and os.path.exists(os.path.join(LAWS_DIR, fname)):
                print(f"    = 변경 없음 (lsiSeq {lsi_seq}, 시행 {ef_yd}) — 건너뜀")
                items.append(p)
                skipped += 1
                continue

            try:
                body = fetch_body(lsi_seq, ef_yd)
            except Exception as e:
                print(f"    ! 본문 다운로드 실패: {e}")
                failed.append(name)
                continue
            if "lawcon" not in body:
                print("    ! 본문 형식이 예상과 다름 — 저장은 하되 확인 필요")

            path = os.path.join(LAWS_DIR, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
            items.append({
                "name": name, "group": grp["group"], "tier": law["tier"],
                "ministry": grp["ministry"], "lsi_seq": lsi_seq, "ef_yd": ef_yd,
                "file": "laws/" + fname, "sha256": sha,
                "source_url": "https://www.law.go.kr/법령/" + name.replace(" ", ""),
                "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
            })
            ok += 1
            print(f"    v 저장 ({len(body)//1024} KB, lsiSeq {lsi_seq}, 시행 {ef_yd})")
            time.sleep(0.7)  # 서버 예의

    for adm in watchlist.get("admrul", []):
        name = adm["name"]
        print(f"[{adm['group']}·고시] {name}")
        adm_seq = resolve_admrul(name)
        if not adm_seq:
            print("    ! 행정규칙을 찾지 못함 (명칭 확인 필요)")
            failed.append(name)
            continue

        fname = safe_filename(name)
        p = prev.get(name)
        if p and p.get("lsi_seq") == adm_seq \
                and os.path.exists(os.path.join(LAWS_DIR, fname)):
            print(f"    = 변경 없음 (admRulSeq {adm_seq}) — 건너뜀")
            items.append(p)
            skipped += 1
            continue

        try:
            body = fetch_admrul_body(adm_seq)
        except Exception as e:
            print(f"    ! 본문 다운로드 실패: {e}")
            failed.append(name)
            continue

        path = os.path.join(LAWS_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        items.append({
            "name": name, "group": adm["group"], "tier": "고시",
            "kind": "admrul", "ministry": adm["ministry"],
            "lsi_seq": adm_seq, "ef_yd": "",
            "file": "laws/" + fname,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_url": "https://www.law.go.kr/행정규칙/" + name.replace(" ", ""),
            "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
        })
        ok += 1
        print(f"    v 저장 ({len(body)//1024} KB, admRulSeq {adm_seq})")
        time.sleep(0.7)

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({
            "created_at": datetime.now(KST).isoformat(timespec="seconds"),
            "collector_version": "1.0",
            "items": items,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 신규/갱신 {ok}건, 변경없음 {skipped}건, 실패 {len(failed)}건")
    if failed:
        print("실패 목록:", ", ".join(failed))
    print(f"번들 위치: {BUNDLE}")


if __name__ == "__main__":
    main()

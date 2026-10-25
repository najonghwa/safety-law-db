# -*- coding: utf-8 -*-
"""annex_collector.py — 별표·서식 파일 수집기 (인터넷 PC에서 실행).

수집 대상:
  1) 법령(법률·시행령·시행규칙): lsBylInfoR.do 별표 목록 → flDownload.do로 HWP 다운로드
  2) 행정규칙(고시): 이미 저장된 본문 HTML 안의 flDownload 링크

결과: bundle/annex/ 폴더 + bundle/annex_manifest.json
별표만 수집하고 서식(신청서 양식)은 제외한다. '삭제' 표시 별표도 제외.
"""
import sys, io, os, re, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(BASE, "bundle")
LAWS_DIR = os.path.join(BUNDLE, "laws")
ANNEX_DIR = os.path.join(BUNDLE, "annex")
MANIFEST = os.path.join(BUNDLE, "manifest.json")
ANNEX_MANIFEST = os.path.join(BUNDLE, "annex_manifest.json")
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SafetyLawCollector/1.0"

OPTION_RE = re.compile(
    r'<option value="(\d+),(\d+),(\d+),\d+,\d*">\s*\[(별표[^\]]*)\]\s*([^<]*)</option>')
ADMRUL_FL_RE = re.compile(r'flDownload\.do\?flSeq=(\d+)&amp;flNm=([^"&\']+)')


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post(url, params, timeout=60):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def file_ext(data):
    if data[:4] == b"\xd0\xcf\x11\xe0":
        return ".hwp"
    if data[:2] == b"PK":
        return ".hwpx"
    if data[:4] == b"%PDF":
        return ".pdf"
    return ".txt"  # lsBylTextDownLoad.do 응답은 플레인 텍스트


def safe(s):
    return re.sub(r"[^\w가-힣]+", "_", s).strip("_")[:80]


def main():
    os.makedirs(ANNEX_DIR, exist_ok=True)
    with open(MANIFEST, encoding="utf-8") as f:
        items = json.load(f)["items"]

    prev = {}
    if os.path.exists(ANNEX_MANIFEST):
        with open(ANNEX_MANIFEST, encoding="utf-8") as f:
            prev = {a["key"]: a for a in json.load(f).get("annexes", [])}

    annexes, ok, skip, fail, kept = [], 0, 0, 0, 0

    def save_one(key, law_name, label, title, fetch, lsi_seq):
        """별표 하나를 받는다. 법령이 개정되면(lsi_seq 변경) 반드시 다시 받는다.

        예전에는 key(법령명+별표번호)만 같으면 건너뛰었다. 그래서 2026-08-01 산업안전보건법
        3종이 개정돼 본문은 새로 받았는데 별표 62건은 6월 판 그대로 남았다. 본문과 별표의
        판이 어긋나도 눈에 띄지 않는다 — 건수도 같고 파일도 있다.
        """
        nonlocal ok, skip, fail, kept
        p = prev.get(key)
        have_old = bool(p) and os.path.exists(os.path.join(BUNDLE, p["file"]))
        if have_old and p.get("lsi_seq") == lsi_seq:
            annexes.append(p)
            skip += 1
            return
        try:
            data = fetch()
            if len(data) < 200:
                raise ValueError(f"응답이 {len(data)}바이트 — 별표 파일이 아님")
        except Exception as e:
            # 실패했다고 목록에서 빼면 DB에서 그 별표가 통째로 사라진다(조용한 손실).
            # 이전 판이 있으면 그걸 유지하고, 유지했다는 사실을 크게 알린다.
            if have_old:
                print(f"    !! {label} 다운로드 실패 — 이전 판 유지(개정 미반영): {e}")
                annexes.append(p)
                kept += 1
            else:
                print(f"    !! {label} 다운로드 실패 — 누락됨: {e}")
                fail += 1
            return
        fname = f"{safe(law_name)}__{safe(label)}{file_ext(data)}"
        with open(os.path.join(ANNEX_DIR, fname), "wb") as f:
            f.write(data)
        annexes.append({"key": key, "law_name": law_name, "label": label,
                        "title": title, "file": "annex/" + fname,
                        "size": len(data), "lsi_seq": lsi_seq})
        ok += 1
        time.sleep(0.4)

    for it in items:
        name = it["name"]
        if it.get("kind") == "admrul":
            # 첨부 링크(flSeq)는 고시가 안 바뀌어도 law.go.kr 쪽에서 수시로 바뀐다. 저장해 둔
            # HTML의 옛 링크로 받으면 "파일이 없습니다"(59바이트)가 온다. 링크 목록은 매번 새로 받고,
            # 본문 HTML(조문 원본)은 collector가 관리하므로 여기서 덮어쓰지 않는다.
            try:
                html = http_get(f"https://www.law.go.kr/LSW/admRulInfoR.do?admRulSeq={it['lsi_seq']}"
                                ).decode("utf-8", "replace")
            except Exception as e:
                path = os.path.join(BUNDLE, it["file"])
                if not os.path.exists(path):
                    continue
                print(f"[고시] {name}: 링크 목록 새로 받기 실패({e}) — 저장본의 링크 사용")
                html = open(path, encoding="utf-8").read()
            seen_names = set()
            found = 0
            for fl_seq, fl_nm in ADMRUL_FL_RE.findall(html):
                title = urllib.parse.unquote_plus(fl_nm)
                if not title.startswith("[별표"):
                    continue  # 서식·별지 제외
                norm = re.sub(r"\s+", " ", title)
                if norm in seen_names:  # 같은 별표의 중복 링크(hwp/pdf 쌍) 1개만
                    continue
                seen_names.add(norm)
                m = re.match(r"\[(별표[^\]]*)\]\s*(.*)", norm)
                label = m.group(1).replace(" ", "") if m else "별표"
                url = f"https://www.law.go.kr/LSW/flDownload.do?flSeq={fl_seq}"
                save_one(f"adm:{name}:{label}", name, label,
                         (m.group(2) if m else norm).strip(),
                         lambda u=url: http_get(u), it["lsi_seq"])
                found += 1
            print(f"[고시] {name}: 별표 {found}건")
        else:
            # 법령: 별표 목록 페이지
            url = (f"https://www.law.go.kr/LSW/lsBylInfoR.do"
                   f"?lsiSeq={it['lsi_seq']}&efYd={it['ef_yd']}")
            try:
                html = http_get(url).decode("utf-8", "replace")
            except Exception as e:
                print(f"[법령] {name}: 목록 조회 실패 {e}")
                continue
            opts = OPTION_RE.findall(html)
            found = 0
            for byl_seq, _no, _br, label, title in opts:
                title = title.strip()
                if title.startswith("삭제"):
                    continue
                label = label.replace(" ", "")
                # 법령 별표는 텍스트 다운로드 엔드포인트 사용 (파싱 불필요)
                save_one(f"law:{name}:{label}", name, label, title,
                         lambda b=byl_seq, t=label: http_post(
                             "https://www.law.go.kr/LSW/lsBylTextDownLoad.do",
                             {"bylSeq": b, "title": t}), it["lsi_seq"])
                found += 1
            print(f"[법령] {name}: 별표 {found}건 (목록 {len(opts)}건)")

    # 법령별 별표 수가 이전보다 줄었으면 크게 알린다 — 줄어든 건 대개 개정이 아니라 수집 실패다
    before, after = {}, {}
    for a in prev.values():
        before[a["law_name"]] = before.get(a["law_name"], 0) + 1
    for a in annexes:
        after[a["law_name"]] = after.get(a["law_name"], 0) + 1
    for n in sorted(before):
        if after.get(n, 0) < before[n]:
            print(f"!! 별표 수 감소: {n}  {before[n]}건 → {after.get(n, 0)}건  (삭제된 별표가 아니면 수집 실패)")
    if kept:
        print(f"!! 이전 판 유지(개정 미반영) {kept}건 — 위 '!!' 줄의 별표는 다음 실행 때 다시 시도된다")

    with open(ANNEX_MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"created_at": datetime.now(KST).isoformat(timespec="seconds"),
                   "annexes": annexes}, f, ensure_ascii=False, indent=1)
    print(f"\n완료: 신규 {ok}건, 기존 {skip}건, 실패 {fail}건 → {ANNEX_DIR}")


if __name__ == "__main__":
    main()

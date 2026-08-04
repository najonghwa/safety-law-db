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

    annexes, ok, skip, fail = [], 0, 0, 0

    def save_one(key, law_name, label, title, fetch):
        nonlocal ok, skip, fail
        p = prev.get(key)
        if p and os.path.exists(os.path.join(BUNDLE, p["file"])):
            annexes.append(p)
            skip += 1
            return
        try:
            data = fetch()
        except Exception as e:
            print(f"    ! {label} 다운로드 실패: {e}")
            fail += 1
            return
        if len(data) < 200:
            fail += 1
            return
        fname = f"{safe(law_name)}__{safe(label)}{file_ext(data)}"
        with open(os.path.join(ANNEX_DIR, fname), "wb") as f:
            f.write(data)
        annexes.append({"key": key, "law_name": law_name, "label": label,
                        "title": title, "file": "annex/" + fname,
                        "size": len(data)})
        ok += 1
        time.sleep(0.4)

    for it in items:
        name = it["name"]
        if it.get("kind") == "admrul":
            # 저장된 본문 HTML에서 첨부 링크 추출
            path = os.path.join(BUNDLE, it["file"])
            if not os.path.exists(path):
                continue
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
                         lambda u=url: http_get(u))
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
                             {"bylSeq": b, "title": t}))
                found += 1
            print(f"[법령] {name}: 별표 {found}건 (목록 {len(opts)}건)")

    with open(ANNEX_MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"created_at": datetime.now(KST).isoformat(timespec="seconds"),
                   "annexes": annexes}, f, ensure_ascii=False, indent=1)
    print(f"\n완료: 신규 {ok}건, 기존 {skip}건, 실패 {fail}건 → {ANNEX_DIR}")


if __name__ == "__main__":
    main()

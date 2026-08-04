# -*- coding: utf-8 -*-
"""substance_collector.py — 화학물질 규제 목록 수집 (인터넷 PC에서 실행).

국가법령정보센터는 유해화학물질 지정고시의 별표(물질 수천 건)를 파일로 제공하지
않고 별도 조회 화면으로 안내한다. 그래서 이 목록만 화학물질종합정보시스템
(ICIS, 기후에너지환경부)의 공개 조회 API에서 받아온다.

수집 대상 (화학물질관리법 분류):
  1 인체등유해성물질(구 유독물질) / 2 제한물질 / 3 금지물질
  4 사고대비물질 / 5 허가물질

결과: bundle/substances_icis.json  → importer.py가 substances 테이블에 통합
"""
import sys, io, os, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "bundle", "substances_icis.json")
URL = "https://icis.mcee.go.kr/chmClsCl/chmClsClListJson.do"
KST = timezone(timedelta(hours=9))

CATEGORIES = {
    "1": "인체등유해성물질",   # 인체급성·인체만성·생태유해성물질 (구 유독물질)
    "2": "제한물질",
    "3": "금지물질",
    "4": "사고대비물질",
    "5": "허가물질",
}


def fetch(chm_type, page):
    data = urllib.parse.urlencode({
        "search_nm": "", "pageNo": page, "search_type": "1",
        "order_str_start": "", "order_str_end": "", "order_type": "",
        "chmCls_type": chm_type}).encode()
    req = urllib.request.Request(URL, data=data, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SafetyLawCollector/1.0",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def collect_category(chm_type, label):
    first = fetch(chm_type, 1)
    total = int(first.get("totalCount") or 0)
    if total == 0:
        print(f"[{label}] 0건")
        return []
    per = len(first.get("list") or []) or 10
    pages = -(-total // per)
    items, seen = [], set()

    def add(rows):
        for it in rows:
            cas = (it.get("hlhsnCasNo") or "").strip()
            name = (it.get("hlhsnKoreanNm") or "").strip().lstrip("·").strip()
            eng = (it.get("hlhsnEngNm") or "").strip().lstrip("·").strip()
            sn = it.get("hlhsnSn")
            key = (cas, name)
            if key in seen:
                continue          # 중복은 건너뛰되 나머지 행은 계속 처리
            seen.add(key)
            items.append({"category": label, "cas": cas, "name_ko": name,
                          "name_en": eng, "no": int(sn) if sn else None})

    add(first.get("list") or [])
    for p in range(2, pages + 1):
        try:
            add(fetch(chm_type, p).get("list") or [])
        except Exception as e:
            print(f"  ! {label} p{p} 실패: {e}")
        if p % 25 == 0:
            print(f"  {label}: {len(items)}/{total}")
        time.sleep(0.25)
    print(f"[{label}] {len(items)}건 수집 (총 {total}건 표기)")
    return items


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    all_items = []
    for t, label in CATEGORIES.items():
        try:
            all_items += collect_category(t, label)
        except Exception as e:
            print(f"[{label}] 수집 실패: {e}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({
            "created_at": datetime.now(KST).isoformat(timespec="seconds"),
            "source": "화학물질종합정보시스템(ICIS) 화학물질 분류정보",
            "source_url": "https://icis.mcee.go.kr/chmClsCl/chmClsClList.do",
            "items": all_items,
        }, f, ensure_ascii=False, indent=1)

    by = {}
    for it in all_items:
        by[it["category"]] = by.get(it["category"], 0) + 1
    print(f"\n완료: 총 {len(all_items)}건 → {OUT}")
    for k, v in by.items():
        print(f"  {k}: {v}건")


if __name__ == "__main__":
    main()

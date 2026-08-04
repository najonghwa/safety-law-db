# -*- coding: utf-8 -*-
"""kosha_collector.py — KOSHA Guide(안전보건기술지침) 수집기.

1단계(키 불필요, 지금 동작): 공공데이터포털의 KOSHA Guide 목록 CSV를 내려받아
   bundle/kosha_list.csv 저장 → importer가 지침번호·명칭을 검색 DB에 통합.
2단계(선택): 공공데이터포털 무료 회원가입 후 「기술지원규정(KOSHA GUIDE) 조회 서비스」
   (data.go.kr/data/15144147) 활용신청으로 받은 serviceKey를 kosha_config.json에
   넣으면, 지침별 PDF 다운로드 링크를 받아 원문까지 수집한다.

실행: python kosha_collector.py
"""
import sys, io, os, csv, json, urllib.request, urllib.parse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(BASE, "bundle")
CSV_OUT = os.path.join(BUNDLE, "kosha_list.csv")
PDF_DIR = os.path.join(BUNDLE, "kosha_pdf")
CFG_PATH = os.path.join(BASE, "kosha_config.json")

# 공공데이터포털 파일데이터(로그인 불필요 직링크) — KOSHA Guide 목록
LIST_URL = ("https://www.data.go.kr/cmm/cmm/fileDownload.do"
            "?atchFileId=FILE_000000002982735&fileDetailSn=1&insertDataPrcus=N")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SafetyLawCollector/1.0"


def http_get(url, timeout=60, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data


def fetch_catalog():
    raw = http_get(LIST_URL, binary=True)
    text = raw.decode("cp949", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or "지침번호" not in ",".join(rows[0]):
        raise RuntimeError("목록 CSV 형식이 예상과 다릅니다")
    os.makedirs(BUNDLE, exist_ok=True)
    with open(CSV_OUT, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"KOSHA Guide 목록 {len(rows)-1}건 저장 → {CSV_OUT}")
    return rows


def fetch_pdfs():
    """serviceKey가 있으면 조회 서비스로 지침별 다운로드 링크를 받아 PDF 수집.

    엔드포인트: apis.data.go.kr/B552468/koshaguide/getKoshaGuide (callApiId=1050 필수)
    응답 항목: techGdlnNo(지침번호), techGdlnNm(명칭), fileDownloadUrl
    """
    if not os.path.exists(CFG_PATH):
        print("kosha_config.json 없음 — PDF 수집 생략 (목록만 사용).")
        print('  전문 수집을 원하면: data.go.kr 회원가입 → "기술지원규정(KOSHA GUIDE)')
        print('  조회 서비스" 활용신청 → {"serviceKey": "발급키"} 를 kosha_config.json에 저장')
        return
    with open(CFG_PATH, encoding="utf-8") as f:
        key = json.load(f).get("serviceKey", "").strip()
    if not key or "붙여넣기" in key:
        print("serviceKey 비어 있음 — PDF 수집 생략")
        return
    os.makedirs(PDF_DIR, exist_ok=True)
    base = "https://apis.data.go.kr/B552468/koshaguide/getKoshaGuide"
    page, saved, failed, total = 1, 0, 0, None
    while True:
        url = (f"{base}?serviceKey={urllib.parse.quote(key)}"
               f"&pageNo={page}&numOfRows=100&returnType=json&callApiId=1050")
        try:
            data = json.loads(http_get(url).decode("utf-8"))
        except Exception as e:
            print(f"조회 서비스 호출 실패(p{page}): {e}")
            break
        body = data.get("body", {})
        total = body.get("totalCount", total)
        items = body.get("items", {}).get("item", [])
        if not items:
            break
        for it in items:
            no = (it.get("techGdlnNo") or "").strip()
            link = it.get("fileDownloadUrl")
            if not no or not link:
                continue
            dst = os.path.join(PDF_DIR, f"{no}.pdf")
            if os.path.exists(dst) and os.path.getsize(dst) > 1000:
                continue
            try:
                data_pdf = http_get(link, timeout=90)
                if data_pdf[:4] != b"%PDF":
                    raise ValueError("PDF 아님")
                with open(dst, "wb") as f:
                    f.write(data_pdf)
                saved += 1
                if saved % 50 == 0:
                    print(f"  PDF {saved}건 저장…")
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  ! {no} 실패: {e}")
        print(f"페이지 {page} 완료 (전체 {total}건)")
        page += 1
    print(f"PDF 신규 {saved}건, 실패 {failed}건 → {PDF_DIR}")


if __name__ == "__main__":
    fetch_catalog()
    fetch_pdfs()

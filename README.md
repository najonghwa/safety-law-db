# 안전법규 통합 DB

안전·환경 법령(화관법·산안법·위험물법 등)을 국가법령정보센터·공공데이터포털에서
API로 수집해 **조 단위 검색 DB(SQLite)** 로 만드는 도구.

DB를 만든 뒤 Claude Code에서 그 폴더를 열면, Claude가 DB를 검색해
**근거 조문을 인용하며** 답합니다. (별도 LLM 서버 불필요 — Claude Code가 직접 검색)

이 저장소에는 **코드만** 있습니다. DB·수집 원본 같은 대용량은 아래 절차로 직접
생성합니다(30분~1시간, 인터넷 필요).

---

## 1. 준비물

- Python 3.11+
- 패키지: `pip install pypdf gethwp olefile`
  (별표·물질 목록 파싱용. 없으면 해당 부분만 빠지고 나머지는 동작)
- (선택) KOSHA 지침 PDF 원문까지 받으려면 data.go.kr 무료 키
  - [기술지원규정(KOSHA GUIDE) 조회 서비스](https://www.data.go.kr/data/15144147/openapi.do) 활용신청(자동승인)
  - 발급받은 **일반 인증키(Decoding)**로 `kosha_config.json` 생성:
    ```json
    {"serviceKey": "발급받은_키"}
    ```
  - 이 파일은 `.gitignore`로 커밋 제외됨 (키는 각자 관리). 없어도 법령·별표·물질은 다 받아짐

## 2. 데이터 수집 → DB 생성

인터넷 되는 PC에서 순서대로:

```bash
python collector.py            # 법령·고시 본문 (law.go.kr, 키 불필요)
python annex_collector.py      # 별표 (HWP/텍스트)
python kosha_collector.py      # KOSHA 지침 목록·PDF (키 있으면 원문까지)
python substance_collector.py  # 유해화학물질 등 물질 목록 (ICIS)
python importer.py             # 위 수집물 → data/laws.db 생성
python verify_db.py            # 품질 감사 (손실·누락 점검)
```

`verify_db.py`가 "전 항목 통과"로 끝나면 정상입니다.

위 여섯 줄을 한 번에 돌리려면 `python update_db.py` (윈도우는 `DB갱신.bat`
더블클릭). 중간에 하나라도 실패하면 거기서 멈춥니다 — 깨진 DB가 다음 단계로
넘어가지 않게 하기 위해서입니다.

### 법령 개정 반영

`collector.py`는 **바뀐 법령만** 다시 받습니다(lsiSeq·시행일 비교). 개정이 없어도
수집일은 갱신되고, 그 날짜가 검색 결과에 함께 표시됩니다.

## 3. Claude Code로 검색

`dist/she-law-db/` 가 Claude Code용 폴더입니다. 만든 DB를 넣어줍니다:

```bash
mkdir -p dist/she-law-db/data
cp data/laws.db dist/she-law-db/data/
```

그다음 **Claude Code에서 `dist/she-law-db` 폴더를 열고** 질문하면 됩니다:

```
독성가스를 창고에 보관하려면 어떤 허가가 필요해?
디메틸아민(CAS 124-40-3)은 어떤 규제에 걸려?
```

`.claude/skills/she-law` 스킬과 `CLAUDE.md` 규칙에 따라, Claude가
`search_law.py`로 DB를 검색하고 근거 조문·원문을 인용해 답합니다.
DB에 없는 내용은 평소처럼 Claude가 답하고, 그 부분은 "클로드 탐색"으로 표시합니다. 답 맨 앞 한 줄
(`근거: 법령 DB` / `근거: 법령 DB + 클로드 탐색` / `근거: 클로드 탐색`)로 출처를 구분합니다.
DB는 Claude의 답을 제한하려는 것이 아니라, 원문으로 확인되는 부분을 늘리려는 것입니다.

### Claude가 알아서 DB를 보게 하기 — MCP 서버 (권장)

`dist/she-law-db/.mcp.json`이 `she_law_mcp.py`를 Claude Code의 MCP 서버로 등록합니다. 그 폴더를
열면 Claude Code가 도구(`gather_evidence`·`search_law`·`get_article`·`check_substance`·`list_laws`)를
자동 인식하고(처음 한 번 "허용"), 법규 질문이 오면 먼저 DB를 보고 근거를 인용해 답합니다.
표준 라이브러리만 쓰므로 설치할 게 없습니다. 목적은 검색기가 아니라 **클로드가 이 DB를 본 다음
답하게 하는 것**이라 이 경로가 기본입니다.

여러 사람이 쓸 때는 DB를 복사하지 않아도 됩니다 — 한 명이 `python she_law_mcp.py --http 0.0.0.0:8780`으로
서버를 켜고, 나머지는 `claude mcp add --transport http -s user she-law http://서버IP:8780/mcp` 한 줄로 붙습니다.
서버는 조문 검색만 하고, 답변은 각자의 Claude Code가 만들므로 토큰도 각자의 계정에서 나갑니다.

```bash
cd dist/she-law-db
python she_law_mcp.py --selftest "유해화학물질 영업허가를 받으려면?"   # 근거 묶음이 그대로 출력되면 정상
```

### 직접 검색해보기 (터미널)

```bash
cd dist/she-law-db
python search_law.py find "유해화학물질 영업허가"
python search_law.py substance 124-40-3
python search_law.py laws 화학물질관리법
```

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| `collector.py` | 법령·고시 본문 수집 (law.go.kr, 인증키 불필요) |
| `annex_collector.py` | 별표 수집 (HWP → 텍스트) |
| `kosha_collector.py` | KOSHA 지침 목록·PDF (data.go.kr 키 필요) |
| `substance_collector.py` | 물질 규제 목록 (ICIS) |
| `importer.py` | 수집물 → SQLite DB (파싱·CAS 추출·NULL 정리·별표 청크) |
| `verify_db.py` | **DB 품질 감사** — 수집 대상 대조·손실률·누락·검색 스팟체크 |
| `update_db.py` / `DB갱신.bat` | 수집→적재→감사를 한 번에 실행 |
| `make_web_db.py` | KOSHA를 뺀 경량 DB 생성 (웹 배포용) |
| `ocr_scans.py` | 스캔본 PDF·그림 별표 OCR 사이드카 (Windows, winocr) |
| `laws.json` | **수집 대상 법령 목록** — 여기에 이름 추가하면 법 늘어남 |
| `synonyms.json` | 현장용어→법령용어 사전 (폐액→지정폐기물 등) |
| `dist/she-law-db/she_law_mcp.py` | **Claude Code MCP 서버** — 로컬 DB 또는 중앙 API(SHE_LAW_API) |
| `dist/she-law-db/.mcp.json` | MCP 등록 설정 (폴더를 열면 자동 인식) |
| `dist/she-law-db/search_law.py` | 검색 CLI (MCP가 없을 때의 대안, 표준 라이브러리만) |
| `dist/she-law-db/CLAUDE.md` | Claude 답변 규칙 (DB 근거는 인용, DB 밖은 "클로드 탐색" 표시) |
| `dist/she-law-db/.claude/skills/` | Claude Code 스킬 정의 |

## 2026-10에 추가된 것

- 법령 24건 — 토양환경보전법·환경오염시설통합관리법·오존층보호법·잔류성오염물질관리법·
  소음진동관리법·전기안전관리법·액화석유가스법·도시가스사업법(각 법·시행령·시행규칙). 82건.
- **부칙**: 시행일·경과조치·적용례를 조문으로 수록(`jo_short='부칙'`, 4,260조). "언제부터 적용되나",
  "기존 허가는 어떻게 되나"의 근거가 여기 있다. 예전 파서는 부칙 앞에서 끊어 한 줄도 담지 않았다.
- **서식(별지)**: 법령 서식·고시 별지 1,170종을 `chapter='서식'`으로 수록. 검색에서는 뒤로 밀리고,
  양식 자체를 물을 때만 앞으로 온다.
- **시행예정**: 현행 본문에 섞인 시행예정 조문(law.go.kr의 `babl` 표시)은 제목에 `[시행일: …]`을
  붙인다. 판 전체가 시행 전인 법령은 이름에 `(시행예정 YYYY-MM-DD)`를 붙여 별도 항목으로 받는다 —
  단, 법령 페이지에 그 판이 적혀 있을 때만(연혁 목록은 JS로 따로 불러와 여기서는 못 본다).
- `ocr_scans.py`: 글자 없는 KOSHA 스캔본 PDF와 그림뿐인 별표를 Windows OCR(winocr)로 읽어
  사이드카(.txt / .ocr.txt)로 둔다. importer가 원본에서 글자가 안 나오면 이걸 쓴다. winocr이 없으면
  건너뛴다(`pip install winocr pymupdf pillow`). 해상도가 낮은 흐름도 그림은 읽히지 않아 비워 둔다.
- KOSHA: 공공데이터포털 목록 CSV가 2023-11에서 멈춰 있어, 조회 API(`kosha_api.json`)에만 있는
  2024~2026년 지침 237건을 importer가 보탠다. PDF 링크가 없는 지침(552건)은 제목만 들어간다.
- 검색: 부칙·서식은 짧아서 bm25 점수를 독식한다("유해화학물질 영업허가" 상위 30건 중 28건이
  부칙이었다). 검색 SQL의 ORDER BY에 벌점(부칙 +4, 서식 +6)을 둬 본문 조문이 앞에 오게 한다.

## 법령 추가

`laws.json`에 법령명(국가법령정보센터 공식 명칭)을 추가하고
`collector.py` → `importer.py`를 다시 실행. 고시는 `admrul` 배열에 추가.

## 알아둘 점

- 법령 데이터는 공공데이터(무료). 시행일은 법제처 표기 기준 현행본.
- HWP·PDF 파싱 과정에서 일부 별표가 잘리는 경우가 있어, **초기 셋업 시
  `verify_db.py`로 반드시 점검**하는 것을 권장. (과거 NULL 문자로 별표 본문이
  통째로 잘린 사례가 있어 importer에 정리 로직을 넣어둠)
- 수집이 중간에 끊기면 법령 하나가 통째로 빠질 수 있는데, 건수 합계만 봐서는
  드러나지 않는다. `verify_db.py`의 [1] 검사가 `laws.json`과 이름을 하나씩
  대조해 잡는다. (실제로 화학물질관리법이 빠진 채 나머지 검사를 전부 통과한 적 있음)
- 별표는 법령 판(lsi_seq)이 바뀌면 다시 받는다. 예전에는 법령명+별표번호만 같으면
  건너뛰어, 본문은 새 판인데 별표는 옛 판인 상태가 생겼다. `verify_db.py` [1]이 잡는다.
- 수집이 시간 초과로 실패하면 목록에서 빼지 않고 이전 판을 유지한 채 종료코드 1을 낸다.
  `DB갱신.bat`이 거기서 멈추면 그냥 다시 실행하면 된다 — 실패분만 재시도된다.
- 고시 첨부(별표) 링크는 law.go.kr이 수시로 바꾸므로 매번 새로 받는다.
- 답변은 참고용 — 법적 판단 전 원문(law.go.kr) 확인 필요.

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
근거를 못 찾으면 지어내지 않고 "확인 불가"로 답합니다.

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
| `make_web_db.py` | KOSHA를 뺀 경량 DB 생성 (웹 배포용, 100MB → 37MB) |
| `laws.json` | **수집 대상 법령 목록** — 여기에 이름 추가하면 법 늘어남 |
| `synonyms.json` | 현장용어→법령용어 사전 (폐액→지정폐기물 등) |
| `dist/she-law-db/search_law.py` | Claude가 호출하는 검색 CLI (표준 라이브러리만) |
| `dist/she-law-db/CLAUDE.md` | Claude 답변 규칙 (인용 강제·근거 없으면 거부) |
| `dist/she-law-db/.claude/skills/` | Claude Code 스킬 정의 |

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
- 답변은 참고용 — 법적 판단 전 원문(law.go.kr) 확인 필요.

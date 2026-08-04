# 안전법규 통합 DB

안전·환경 법령(화관법·산안법·위험물법 등)을 국가법령정보센터·공공데이터포털에서
API로 수집해 조 단위 검색 DB로 만들고, Claude가 근거 조문을 인용해 답하도록
구성한 도구.

이 저장소에는 **코드만** 들어 있습니다. DB·수집 원본 같은 대용량 파일은
아래 절차대로 직접 만들면 됩니다(30분~1시간, 인터넷 필요).

---

## 빠른 시작

### 1. 준비물

- Python 3.11+
- 패키지: `pip install numpy pypdf gethwp olefile`
  (없어도 검색은 되지만, 별표 파싱·물질 매핑·AI 임베딩 일부 기능이 빠집니다)
- (선택) 로컬 LLM으로 AI 질의를 쓰려면 [ollama](https://ollama.com) + `ollama pull qwen2.5:7b bge-m3`

### 2. KOSHA API 키 (선택)

KOSHA 기술지침 PDF 원문까지 받으려면 공공데이터포털 무료 키가 필요합니다.
없어도 나머지는 다 동작합니다.

1. [기술지원규정(KOSHA GUIDE) 조회 서비스](https://www.data.go.kr/data/15144147/openapi.do)
   활용신청(자동승인)
2. 발급받은 **일반 인증키(Decoding)**로 `kosha_config.json` 생성:
   ```json
   {"serviceKey": "발급받은_키"}
   ```
   (이 파일은 .gitignore로 커밋에서 제외됩니다 — 키는 각자 관리)

### 3. 데이터 수집 → DB 생성

인터넷 되는 PC에서 순서대로 실행:

```bash
python collector.py            # 법령·고시 본문 (law.go.kr)
python annex_collector.py      # 별표 (HWP/텍스트)
python kosha_collector.py      # KOSHA 지침 목록·PDF
python substance_collector.py  # 유해화학물질 등 물질 목록 (ICIS)
python importer.py             # 위 수집물 → data/laws.db 생성
python verify_db.py            # 품질 감사 (손실·누락 점검)
```

`verify_db.py`가 "전 항목 통과"로 끝나면 DB가 정상입니다.

### 4. 실행

- **대시보드(웹 UI)**: `python app.py` → http://localhost:8777
  또는 `안전법규DB_실행.bat` 더블클릭
- **AI 질의**를 쓰려면 `ollama serve`가 떠 있어야 합니다.
  임베딩 먼저: `python embedder.py`

---

## Claude Code(스킬)로 쓰기

`dist/she-law-db/` 폴더가 Claude Code용 배포 패키지입니다.
DB만 만들어 넣으면 됩니다:

```bash
mkdir -p dist/she-law-db/data
cp data/laws.db dist/she-law-db/data/
```

그다음 그 폴더를 Claude Code에서 열고 질문하면, `.claude/skills/she-law` 스킬과
`CLAUDE.md` 규칙에 따라 근거 조문을 인용해 답합니다.

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| `collector.py` | 법령·고시 본문 수집 (law.go.kr, 인증키 불필요) |
| `annex_collector.py` | 별표 수집 |
| `kosha_collector.py` | KOSHA 지침 목록·PDF (data.go.kr 키 필요) |
| `substance_collector.py` | 물질 규제 목록 (ICIS) |
| `importer.py` | 수집물 → SQLite DB (파싱·CAS 추출·NULL 정리) |
| `embedder.py` | 조문 임베딩 (AI 질의용, ollama) |
| `verify_db.py` | **DB 품질 감사** — 손실률·누락·검색 스팟체크 |
| `app.py` | 대시보드 웹서버 + 검색/AI질의/제품검토 API |
| `rag.py` | AI 질의 파이프라인 (검색→인용검증→거부게이트) |
| `make_mail_package.py` | 사내 반입용 txt 패키지 생성 |
| `laws.json` | **수집 대상 법령 목록** — 여기에 이름 추가하면 법 늘어남 |
| `synonyms.json` | 현장용어→법령용어 사전 |
| `llm_config.json` | LLM 주소·모델 (사내 API 도입 시 여기만 교체) |

## 법령 추가하기

`laws.json`에 법령명(국가법령정보센터 공식 명칭)을 추가하고
`collector.py` → `importer.py`를 다시 돌리면 됩니다.
고시는 `admrul` 배열에 추가.

## 알아둘 점

- 법령 데이터는 공공데이터(무료). 시행일은 법제처 표기 기준 현행본.
- HWP·PDF 파싱 과정에서 일부 별표가 잘리는 경우가 있어, `verify_db.py`로
  매 수집 후 점검하는 것을 권장합니다.
- 답변은 참고용 — 법적 판단 전 원문(law.go.kr) 확인 필요.

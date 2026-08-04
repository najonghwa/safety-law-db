# -*- coding: utf-8 -*-
"""embedder.py — 조문 임베딩 생성 (RAG 준비 단계).

data/laws.db 의 모든 조문을 임베딩해 data/vectors.db 에 저장한다.
임베딩 키는 (법령명+조라벨+본문)의 해시라서, importer로 DB를 재생성해도
내용이 안 바뀐 조문은 재임베딩하지 않는다 (개정분만 비용 발생).

요구사항: ollama serve 실행 중 + bge-m3 모델 (ollama pull bge-m3)
실행: python embedder.py
"""
import sys, io, os, re, json, sqlite3, hashlib, struct, time, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
LAWS_DB = os.path.join(BASE, "data", "laws.db")
VEC_DB = os.path.join(BASE, "data", "vectors.db")
CFG = json.load(open(os.path.join(BASE, "llm_config.json"), encoding="utf-8"))
BATCH = 16

DELETED_RE = re.compile(r"^제\d+조(의\d+)?\s*(\(.*?\)\s*)?삭제\s*[<〈]")


def embed_texts(texts):
    body = json.dumps({"model": CFG["embed_model"], "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        CFG["embed_base_url"] + "/embeddings", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + CFG.get("api_key", "")})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [d["embedding"] for d in data["data"]]


def embed_text_of(law_name, jo_label, content):
    return f"{law_name} {jo_label}\n{content[:2500]}"


def main():
    if not os.path.exists(LAWS_DB):
        print("data/laws.db 없음 — importer.py 먼저 실행")
        sys.exit(1)
    lcon = sqlite3.connect(LAWS_DB)
    vcon = sqlite3.connect(VEC_DB)
    vcon.execute("CREATE TABLE IF NOT EXISTS vecs("
                 "hash TEXT PRIMARY KEY, vec BLOB, dim INTEGER, model TEXT)")
    have = {r[0] for r in vcon.execute(
        "SELECT hash FROM vecs WHERE model=?", (CFG["embed_model"],))}

    rows = lcon.execute(
        "SELECT a.article_id, l.name, a.jo_label, a.content "
        "FROM articles a JOIN laws l ON l.law_id=a.law_id").fetchall()

    todo = []  # (hash, text)
    skipped_deleted = 0
    seen = set()
    for _aid, name, jo, content in rows:
        if DELETED_RE.match(content):
            skipped_deleted += 1
            continue
        text = embed_text_of(name, jo, content)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if h in have or h in seen:
            continue
        seen.add(h)
        todo.append((h, text))

    print(f"전체 조문 {len(rows)}개 / 삭제조문 제외 {skipped_deleted}개 / "
          f"신규 임베딩 대상 {len(todo)}개 (기존 {len(have)}개 보유)")

    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        vecs = embed_texts([t for _, t in batch])
        for (h, _), v in zip(batch, vecs):
            blob = struct.pack(f"{len(v)}f", *v)
            vcon.execute("INSERT OR REPLACE INTO vecs VALUES(?,?,?,?)",
                         (h, blob, len(v), CFG["embed_model"]))
        vcon.commit()
        done = min(i + BATCH, len(todo))
        rate = done / max(time.time() - t0, 1e-9)
        eta = (len(todo) - done) / max(rate, 1e-9)
        print(f"  {done}/{len(todo)}  ({rate:.1f}/s, 남은시간 ~{eta/60:.1f}분)")

    vcon.close()
    lcon.close()
    print(f"완료: {VEC_DB}")


if __name__ == "__main__":
    main()

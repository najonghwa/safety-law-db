# -*- coding: utf-8 -*-
"""ocr_scans.py — 글자가 없는 PDF·그림 별표를 OCR로 텍스트화 (인터넷 PC, 윈도우 전용).

대상
  1) bundle/kosha_pdf/*.pdf 중 텍스트 레이어가 없는 스캔본 → bundle/kosha_ocr/{지침번호}.txt
  2) bundle/annex/*.hwp|hwpx 중 추출 글자가 300자 미만이고 그림이 들어 있는 별표
     → 같은 이름 뒤에 .ocr.txt

importer는 원본에서 글자가 거의 안 나오면 이 사이드카 파일을 대신 쓴다. 사이드카가 없으면
예전처럼 제목만 들어간다(없어도 깨지지 않는다).

Windows 내장 OCR(winocr)을 쓴다 — pip install winocr pymupdf pillow. 한국어 언어팩이 있어야 한다.
흐름도·도장 같은 그림은 글자 배열이 깨질 수 있다. 숫자 기준은 원본 그림을 확인할 것.

실행: python ocr_scans.py            (이미 만든 사이드카는 건너뜀)
      python ocr_scans.py --force    (전부 다시)
"""
import sys, io, os, json, time, zlib, zipfile, glob

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(BASE, "bundle")
PDF_DIR = os.path.join(BUNDLE, "kosha_pdf")
OCR_DIR = os.path.join(BUNDLE, "kosha_ocr")
MAX_PAGES = 40          # 지침 한 건당 OCR 상한 (대부분 10~30쪽)
TEXT_LAYER_MIN = 200    # 이보다 글자가 많으면 스캔본이 아니라고 본다
ANNEX_MIN = 300         # 별표 추출 글자가 이보다 적고 그림이 있으면 OCR 대상


def ocr_image(img):
    import winocr
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width < 80 or img.height < 40:     # 버튼·아이콘 같은 조각은 건너뜀
        return ""
    res = winocr.recognize_pil_sync(img, "ko")
    txt = res["text"] if isinstance(res, dict) else getattr(res, "text", "")
    return (txt or "").strip()


def ocr_pdf(path):
    import fitz
    from PIL import Image
    doc = fitz.open(path)
    if len("".join(p.get_text() for p in doc).strip()) >= TEXT_LAYER_MIN:
        return None                             # 텍스트 레이어 있음 — OCR 불필요
    parts = []
    for i, page in enumerate(doc):
        if i >= MAX_PAGES:
            break
        pix = page.get_pixmap(dpi=170)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        t = ocr_image(img)
        if t:
            parts.append(t)
    return "\n".join(parts)


def hwp_images(path):
    """HWP(OLE)의 BinData 스트림 → PIL 이미지들. 대개 raw deflate로 압축돼 있다."""
    import olefile
    from PIL import Image
    out = []
    if not olefile.isOleFile(path):
        return out
    ole = olefile.OleFileIO(path)
    for s in ole.listdir():
        if not s or s[0] != "BinData":
            continue
        data = ole.openstream(s).read()
        for cand in (data, ):
            try:
                cand = zlib.decompress(data, -15)
            except Exception:
                pass
            try:
                out.append(Image.open(io.BytesIO(cand)).copy())
                break
            except Exception:
                continue
    ole.close()
    return out


def hwpx_images(path):
    from PIL import Image
    out = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("BinData/"):
                try:
                    out.append(Image.open(io.BytesIO(z.read(n))).copy())
                except Exception:
                    pass
    return out


def main():
    force = "--force" in sys.argv
    t0 = time.time()
    try:
        import winocr, fitz  # noqa: F401
    except ImportError as e:
        # OCR은 있으면 좋은 것이지 필수가 아니다. 없으면 제목만 들어가고 나머지는 멀쩡하다.
        print(f"OCR 건너뜀 — {e.name} 미설치 (설치: pip install winocr pymupdf pillow). 스캔본·그림 별표는 제목만 들어갑니다.")
        return

    # 1) KOSHA 스캔본 PDF
    os.makedirs(OCR_DIR, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    done = skipped = failed = 0
    print(f"[1/2] KOSHA PDF {len(pdfs)}건 검사")
    for k, p in enumerate(pdfs, 1):
        no = os.path.splitext(os.path.basename(p))[0]
        dst = os.path.join(OCR_DIR, no + ".txt")
        if os.path.exists(dst) and not force:
            skipped += 1
            continue
        try:
            txt = ocr_pdf(p)
        except Exception as e:
            failed += 1
            print(f"  ! {no}: {e}")
            continue
        if txt is None:
            continue                            # 텍스트 레이어 있음
        if len(txt) < 100:
            failed += 1
            print(f"  ! {no}: OCR 결과 {len(txt)}자 — 버림")
            continue
        with open(dst, "w", encoding="utf-8") as f:
            f.write(txt)
        done += 1
        if done % 20 == 0:
            print(f"  … {done}건 완료 ({k}/{len(pdfs)} 검사, {time.time()-t0:.0f}초)")
    print(f"  KOSHA OCR: 신규 {done}건, 기존 {skipped}건, 실패 {failed}건")

    # 2) 그림 별표 (HWP/HWPX)
    sys.path.insert(0, BASE)
    from importer import extract_annex_text
    ax = json.load(open(os.path.join(BUNDLE, "annex_manifest.json"), encoding="utf-8"))["annexes"]
    done2 = 0
    print(f"[2/2] 별표 {len(ax)}건 중 그림 별표 찾기")
    for a in ax:
        p = os.path.join(BUNDLE, a["file"])
        ext = os.path.splitext(p)[1].lower()
        if ext not in (".hwp", ".hwpx") or not os.path.exists(p):
            continue
        dst = p + ".ocr.txt"
        if os.path.exists(dst) and not force:
            continue
        if len(extract_annex_text(p)) >= ANNEX_MIN:
            continue
        try:
            imgs = hwp_images(p) if ext == ".hwp" else hwpx_images(p)
            texts = [t for t in (ocr_image(im) for im in imgs) if t]
        except Exception as e:
            print(f"  ! [{a['law_name'][:20]}] {a['label']}: {e}")
            continue
        txt = "\n".join(texts)
        if len(txt) < 60:
            continue                            # 버튼 그림 등 — 쓸 내용 없음
        with open(dst, "w", encoding="utf-8") as f:
            f.write(txt)
        done2 += 1
        print(f"  v [{a['law_name'][:24]}] {a['label']}: 그림 {len(imgs)}개 → {len(txt)}자")
    print(f"  그림 별표 OCR: {done2}건")
    print(f"완료 ({time.time()-t0:.0f}초)")


if __name__ == "__main__":
    main()

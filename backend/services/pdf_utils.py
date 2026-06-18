"""
PDF 工具: 渲染 + Word 转换
直接 import PyMuPDF, 不再调子进程
"""
import os
import fitz
from concurrent.futures import ThreadPoolExecutor
import subprocess
from config import OUTPUTS_DIR


def pdf_to_images(pdf_path: str, out_dir: str, dpi: int = 216):
    """渲染 PDF 为 PNG 图片列表"""
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    image_paths = []

    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        img_path = os.path.join(out_dir, f"page_{i + 1:03d}.png")
        pix.save(img_path)
        image_paths.append(img_path)

    doc.close()
    return image_paths


def pdf_to_images_batch(pdf_path: str, out_dir: str, dpi: int = 216):
    """多线程版 (更快, 用于大批量 PDF)"""
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    total = len(doc)

    def render_page(i):
        page = doc[i]
        pix = page.get_pixmap(dpi=dpi)
        img_path = os.path.join(out_dir, f"page_{i + 1:03d}.png")
        pix.save(img_path)
        return img_path

    with ThreadPoolExecutor(max_workers=4) as ex:
        image_paths = list(ex.map(render_page, range(total)))

    doc.close()
    return image_paths


def word_to_pdf(word_path: str, out_dir: str) -> str:
    """Word → PDF via LibreOffice (仅 Windows)"""
    os.makedirs(out_dir, exist_ok=True)

    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "soffice.com",
        "soffice",
    ]

    soffice = None
    for c in candidates:
        if os.path.exists(c):
            soffice = c
            break

    if not soffice:
        raise RuntimeError("LibreOffice 未找到，请确认已安装 LibreOffice")

    base_name = os.path.splitext(os.path.basename(word_path))[0]
    expected_pdf = os.path.join(out_dir, base_name + ".pdf")

    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, word_path],
        timeout=180,
        capture_output=True,
    )

    if not os.path.exists(expected_pdf):
        raise RuntimeError("LibreOffice Word→PDF 转换失败")

    return expected_pdf


def is_pdf_file(filename: str) -> bool:
    return filename.lower().endswith('.pdf')


def is_word_file(filename: str) -> bool:
    return filename.lower().endswith(('.doc', '.docx'))

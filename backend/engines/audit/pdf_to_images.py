#!/usr/bin/env python3
"""Render PDF pages to PNG images using fitz (PyMuPDF). No soffice involved."""
import fitz
import sys
import os

pdf_path = sys.argv[1]
out_dir = sys.argv[2]
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 144

doc = fitz.open(pdf_path)
os.makedirs(out_dir, exist_ok=True)
zoom = dpi / 72
mat = fitz.Matrix(zoom, zoom)

for i in range(len(doc)):
    page = doc[i]
    pix = page.get_pixmap(matrix=mat)
    img_path = os.path.join(out_dir, f"page_{i+1:04d}.jpg")
    pix.save(img_path)
    print(img_path)

doc.close()

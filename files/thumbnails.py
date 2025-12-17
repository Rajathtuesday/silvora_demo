# files/thumbnails.py

import io
from PIL import Image
import fitz  # PyMuPDF
from django.conf import settings
from .r2_storage import R2Storage


THUMB_SIZE = (300, 300)
THUMB_FORMAT = "JPEG"
THUMB_QUALITY = 75


def generate_thumbnail(local_file_path, filename, user_id, upload_id):
    """
    Generates thumbnail and uploads to R2.
    Returns thumbnail_key or None.
    """
    try:
        ext = filename.lower().rsplit(".", 1)[-1]
        image = None

        # --------------------
        # IMAGE FILES
        # --------------------
        if ext in ("jpg", "jpeg", "png", "webp"):
            image = Image.open(local_file_path)
            image.thumbnail(THUMB_SIZE)

        # --------------------
        # PDF FILES (first page)
        # --------------------
        elif ext == "pdf":
            doc = fitz.open(local_file_path)
            page = doc.load_page(0)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image = Image.frombytes(
                "RGB",
                [pix.width, pix.height],
                pix.samples,
            )
            image.thumbnail(THUMB_SIZE)

        else:
            return None  # unsupported

        # --------------------
        # Encode thumbnail
        # --------------------
        buf = io.BytesIO()
        image.convert("RGB").save(
            buf,
            THUMB_FORMAT,
            quality=THUMB_QUALITY,
            optimize=True,
        )
        buf.seek(0)

        # --------------------
        # Upload to R2
        # --------------------
        thumb_key = f"{user_id}/{upload_id}/thumb.jpg"
        r2 = R2Storage()

        r2.client.put_object(
            Bucket=r2.bucket,
            Key=thumb_key,
            Body=buf,
            ContentType="image/jpeg",
        )

        return thumb_key

    except Exception as e:
        print("🔥 Thumbnail generation failed:", e)
        return None

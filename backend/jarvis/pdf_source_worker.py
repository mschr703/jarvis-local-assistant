"""Getrennter Kurzprozess für öffentliche Text-PDFs; nur JSON über stdout."""

import io
import json
import sys

from pypdf import PdfReader, apply_configuration
from pypdf.errors import LimitReachedError

MAX_PDF_BYTES = 2_000_000
MAX_PAGES = 50
MAX_READ_PAGES = 12
MAX_TEXT_CHARS = 10_000
MAX_DECODED_STREAM_BYTES = 8_000_000


def extract(payload: bytes, *, chat_mode: bool = False) -> dict:
    if len(payload) > MAX_PDF_BYTES or not payload.startswith(b"%PDF-"):
        raise ValueError("PDF-Signatur fehlt oder Datei ist zu groß")
    try:
        with apply_configuration(
            disable_legacy_handling=True,
            maximum_declared_stream_length=MAX_DECODED_STREAM_BYTES,
            array_based_stream_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            zlib_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            lzw_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            run_length_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            brotli_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            jbig2_maximum_output_length=MAX_DECODED_STREAM_BYTES,
            image_maximum_buffer_size=MAX_DECODED_STREAM_BYTES,
            stream_decoding_work_maximum_length=32_000_000,
            stream_parsing_work_maximum_count=250_000,
            xmp_maximum_input_length=1_000_000,
            page_tree_maximum_entries=100,
            xform_maximum_invocations_per_extraction=100,
        ):
            reader = PdfReader(io.BytesIO(payload), strict=False)
            if reader.is_encrypted:
                raise ValueError("Verschlüsselte PDFs werden nicht gelesen")
            total = len(reader.pages)
            if not 1 <= total <= MAX_PAGES:
                raise ValueError("PDF hat keine Seiten oder überschreitet 50 Seiten")
            read = min(total, MAX_READ_PAGES)
            parts = []
            for index in range(read):
                text = reader.pages[index].extract_text() or ""
                if text.strip():
                    parts.append(f"Seite {index + 1}\n{text}")
            content = "\n".join(parts)[:8000 if chat_mode else MAX_TEXT_CHARS]
            if len(content.strip()) < (1 if chat_mode else 150):
                raise ValueError("PDF hat keine ausreichende Textschicht; OCR ausstehend")
            return {"text": content, "pages_total": total, "pages_read": read}
    except LimitReachedError as exc:
        raise ValueError("PDF überschreitet das Entpackungs- oder Parserlimit") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF ist beschädigt oder nicht lesbar") from exc


def main() -> int:
    try:
        if sys.argv[1:] not in ([], ["--chat"]):
            raise ValueError("Ungültiger PDF-Modus")
        payload = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        result = extract(payload, chat_mode=sys.argv[1:] == ["--chat"])
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from io import BytesIO

from pypdf import PdfWriter

from app.pdf_utils import apply_metadata, document_content_hash, extract_text, read_pdf_info, sha256_bytes


def make_pdf(title: str = "Memo") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": title})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_sha256_stable():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abd") != sha256_bytes(b"abc")


def test_read_and_assign_metadata():
    original = make_pdf()
    digest = document_content_hash(original)
    stamped = apply_metadata(
        original,
        title="Field Notes",
        author="Ada Geologist",
        tags=["basalt", "internal"],
        document_id="11111111-1111-1111-1111-111111111111",
        document_hash=digest,
    )
    info = read_pdf_info(stamped)
    assert info["Title"] == "Field Notes"
    assert info["Author"] == "Ada Geologist"
    assert info["document_id"] == "11111111-1111-1111-1111-111111111111"
    assert info["document_hash"] == digest
    assert document_content_hash(stamped) == digest


def test_extract_text_does_not_raise_on_blank_page():
    original = make_pdf()
    text = extract_text(original)
    assert isinstance(text, str)

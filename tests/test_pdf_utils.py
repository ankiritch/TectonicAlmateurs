from io import BytesIO

from pypdf import PdfWriter

from app.pdf_utils import apply_metadata, extract_text, read_pdf_info, sha256_bytes


def make_pdf(text: str = "Hello archive", title: str = "Memo") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": title})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_sha256_stable():
    assert sha256_bytes(b"abc") == sha256_bytes(b"abc")
    assert sha256_bytes(b"abc") != sha256_bytes(b"abd")


def test_read_and_assign_metadata():
    original = make_pdf()
    stamped = apply_metadata(
        original,
        title="Field Notes",
        author="Ada Geologist",
        tags=["basalt", "internal"],
    )
    info = read_pdf_info(stamped)
    assert info["Title"] == "Field Notes"
    assert info["Author"] == "Ada Geologist"
    assert "basalt" in info["Keywords"]
    assert info["Producer"] == "TectonicAlmateurs"


def test_extract_text_does_not_raise_on_blank_page():
    original = make_pdf()
    text = extract_text(original)
    assert isinstance(text, str)

"""Optional Linux PDF export through the installed LibreOfficeKit library.

Uses the stable LibreOfficeKit Office/Document ABI prefix (destroy/load/saveAs).
This avoids desktop IPC for headless environments where the soffice CLI cannot
start. A full LibreOffice installation is required, independent of Python deps.
"""
import argparse
import ctypes as C
import tempfile
from pathlib import Path


class Office(C.Structure):
    pass


class Document(C.Structure):
    pass


OfficePointer = C.POINTER(Office)
DocumentPointer = C.POINTER(Document)


class OfficeClass(C.Structure):
    _fields_ = [
        ('size', C.c_size_t),
        ('destroy', C.CFUNCTYPE(None, OfficePointer)),
        ('load', C.CFUNCTYPE(DocumentPointer, OfficePointer, C.c_char_p)),
        ('error', C.CFUNCTYPE(C.c_char_p, OfficePointer)),
    ]


class DocumentClass(C.Structure):
    _fields_ = [
        ('size', C.c_size_t),
        ('destroy', C.CFUNCTYPE(None, DocumentPointer)),
        ('save', C.CFUNCTYPE(C.c_int, DocumentPointer, C.c_char_p, C.c_char_p, C.c_char_p)),
    ]


Office._fields_ = [('cls', C.POINTER(OfficeClass))]
Document._fields_ = [('cls', C.POINTER(DocumentClass))]


def render(pptx: Path, pdf: Path, install: Path) -> None:
    library = C.CDLL(str(install / 'libsofficeapp.so'))
    library.libreofficekit_hook_2.argtypes = [C.c_char_p, C.c_char_p]
    library.libreofficekit_hook_2.restype = OfficePointer
    pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='brochure-lok-') as profile:
        office = library.libreofficekit_hook_2(str(install).encode(), Path(profile).as_uri().encode())
        if not office:
            raise RuntimeError('LibreOfficeKit initialization failed')
        try:
            document = office.contents.cls.contents.load(office, str(pptx.resolve()).encode())
            if not document:
                raise RuntimeError(office.contents.cls.contents.error(office))
            try:
                if not document.contents.cls.contents.save(document, str(pdf.resolve()).encode(), b'pdf', None):
                    raise RuntimeError(office.contents.cls.contents.error(office))
            finally:
                document.contents.cls.contents.destroy(document)
        finally:
            office.contents.cls.contents.destroy(office)
    print(f'Exported {pdf}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pptx', type=Path)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--install', type=Path, default=Path('/usr/lib/libreoffice/program'))
    args = parser.parse_args()
    render(args.pptx, args.pdf, args.install)

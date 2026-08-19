"""Build a small but real epub, so the suite can run without a library.

Every test that exercises the reader or the cards needs an actual book; a
fixture that ships as a binary blob is opaque and one that assumes the
developer's own shelf does not run on a fresh checkout or in CI. Writing the
file here keeps both honest -- it goes through the same OPF, NCX and XHTML
parsing as anything else.
"""

import os
import zipfile

CONTAINER = """<?xml version="1.0"?>
<container version="1.0"
    xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
        media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

WORDS = ("the quick brown fox jumps over a lazy dog while morning light "
         "falls across the shelf and settles on a page that no one has "
         "turned since winter began in earnest").split()


def paragraph(seed, words=70):
    out = [WORDS[(seed * 7 + i * 13) % len(WORDS)] for i in range(words)]
    out[0] = out[0].capitalize()
    return " ".join(out) + "."


def chapter_xhtml(number, paragraphs=48):
    body = "\n".join(f"    <p>{paragraph(number * 31 + i)}</p>"
                     for i in range(paragraphs))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter {number}</title></head>
  <body>
    <h1>Chapter {number}</h1>
{body}
  </body>
</html>
"""


def write_epub(path, title="A Test Of Patience", author="Nobody At All",
               chapters=5):
    """Write a valid, readable epub to `path`. Returns the path."""
    items = [(f"c{i}", f"chapter{i}.xhtml") for i in range(1, chapters + 1)]
    manifest = "\n".join(
        f'    <item id="{i}" href="{h}" media-type="application/xhtml+xml"/>'
        for i, h in items)
    spine = "\n".join(f'    <itemref idref="{i}"/>' for i, _ in items)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0"
         unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:identifier id="bookid">urn:uuid:almari-test-book</dc:identifier>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
{manifest}
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>
"""
    navpoints = "\n".join(f"""  <navPoint id="np{n}" playOrder="{n}">
    <navLabel><text>Chapter {n}</text></navLabel>
    <content src="{h}"/>
  </navPoint>""" for n, (_, h) in enumerate(items, 1))
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:almari-test-book"/></head>
  <docTitle><text>{title}</text></docTitle>
  <navMap>
{navpoints}
  </navMap>
</ncx>
"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # The mimetype entry has to be first and stored, not deflated.
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        for n, (_, h) in enumerate(items, 1):
            z.writestr(f"OEBPS/{h}", chapter_xhtml(n))
    return path

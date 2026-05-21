"""
Generates a minimal sample CSF PDF for testing.
Uses only Python stdlib + pdfplumber (already installed).

Run:
    python3 make_sample_csf.py

Outputs: sample_csf.pdf
"""

import struct, zlib, time

# Minimal PDF writer — no external dependencies
def make_pdf(text_pages: list[str], output_path: str):
    objects = []

    def add(obj): objects.append(obj); return len(objects)

    pages_ids = []
    for page_text in text_pages:
        lines = page_text.split("\n")
        stream_lines = ["BT", "/F1 10 Tf", "40 780 Td", "12 TL"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_lines.append(f"({safe}) Tj T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1", errors="replace")
        cstream = zlib.compress(stream)

        content_id = add(None)
        page_id = add(None)
        pages_ids.append((page_id, content_id, len(cstream), cstream))

    catalog_id = add(None)
    pages_dict_id = add(None)
    font_id = add(None)

    body = b"%PDF-1.4\n"
    offsets = []

    def write_obj(oid, data: bytes):
        offsets.append(len(body))
        return f"{oid} 0 obj\n".encode() + data + b"\nendobj\n"

    # Build objects in order
    raw_objects = []
    page_obj_ids = []

    # Font
    raw_objects.append((font_id, (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )))

    # Pages dict placeholder
    page_ref_list = " ".join(f"{p[0]} 0 R" for p in pages_ids)
    raw_objects.append((pages_dict_id, (
        f"<< /Type /Pages /Count {len(pages_ids)} "
        f"/Kids [{page_ref_list}] >>".encode()
    )))

    # Catalog
    raw_objects.append((catalog_id, (
        f"<< /Type /Catalog /Pages {pages_dict_id} 0 R >>".encode()
    )))

    # Content streams + page dicts
    for page_id, content_id, clen, cdata in pages_ids:
        raw_objects.append((content_id, (
            f"<< /Filter /FlateDecode /Length {clen} >>\n"
            f"stream\n".encode() + cdata + b"\nendstream"
        )))
        raw_objects.append((page_id, (
            f"<< /Type /Page /Parent {pages_dict_id} 0 R "
            f"/MediaBox [0 0 595 842] "
            f"/Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )))

    out = b"%PDF-1.4\n"
    obj_offsets = {}
    for oid, data in raw_objects:
        obj_offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + data + b"\nendobj\n"

    xref_offset = len(out)
    out += b"xref\n"
    out += f"0 {len(raw_objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for oid in range(1, len(raw_objects) + 1):
        off = obj_offsets.get(oid, 0)
        out += f"{off:010d} 00000 n \n".encode()

    out += (
        f"trailer\n<< /Size {len(raw_objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    with open(output_path, "wb") as f:
        f.write(out)
    print(f"Written: {output_path}")


SAMPLE_PAGE_1 = """\
CONSTANCIA DE SITUACION FISCAL
Servicio de Administracion Tributaria (SAT)
Republica Mexicana

RFC: BKRY920415AB3
Nombre / Razon Social: PANADERIA Y PASTELERIA LA NUEVA ESPERANZA S.A. de C.V.
Fecha de alta: 15 de abril de 1992
Estado: ACTIVO

DOMICILIO FISCAL
Calle Benito Juarez No. 142, Col. Centro
Ciudad de Mexico, CDMX, C.P. 06000
Mexico

ACTIVIDADES ECONOMICAS
Codigo    Descripcion                              Porcentaje
461121    Panificacion tradicional (pan, bolillos)      65%
461130    Pasteleria y reposteria fina                  25%
461190    Venta de cafe y bebidas calientes              10%

OBLIGACIONES FISCALES
- IVA mensual
- ISR trimestral (personas morales)
- IEPS (No aplica)

Regimen Fiscal: General de Ley Personas Morales
Giro Comercial: Alimentos elaborados
Canal de ventas: Tienda fisica / mostrador
URL del negocio: www.panaderiaesperanza.com.mx
"""

SAMPLE_PAGE_2 = """\
Informacion adicional del contribuyente

Tipo de persona: Moral
Numero de empleados: 12
Domicilio de actividad: Igual al fiscal

Productos principales:
- Pan de caja y pan artesanal
- Pasteles de fondant y reposteria especializada
- Bebidas calientes (cafe de olla, capuchino)
- Galletas y panqueleria

Clientes principales: Publico general (B2C), restaurantes locales (B2B minoritario)

Este documento es valido para tramites oficiales.
Fecha de emision: 21 de Mayo de 2026
Folio: SAT-2026-0093847
"""


if __name__ == "__main__":
    make_pdf([SAMPLE_PAGE_1, SAMPLE_PAGE_2], "sample_csf.pdf")
    print("Upload this file to the PoC app to test the flow.")

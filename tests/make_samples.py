"""Create small sample file pairs in ./samples for trying out Nexus.

The VDI 3805 samples are synthetic: they follow the general record layout
(record type;index;fields...;) but are not taken from a real manufacturer file.
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def w(path, text, enc="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=enc, newline="") as fh:
        fh.write(text)


def main():
    # --- source code
    w(f"{ROOT}/code/left.py", """import os

def greet(name):
    \"\"\"Say hello.\"\"\"
    print("Hello, " + name)

def add(a, b):
    return a + b

for i in range(3):
    greet("world")
""")
    w(f"{ROOT}/code/right.py", """import os
import sys

def greet(name, punctuation="!"):
    \"\"\"Say hello.\"\"\"
    print("Hello, " + name + punctuation)

def add(a, b):
    return a + b

for i in range(5):
    greet("Welt")
""")
    # --- CSV (German style: ; separator, comma decimals)
    w(f"{ROOT}/csv/left.csv", "Artikel;Bezeichnung;Preis;Menge\n1001;Heizkörper Typ 22;189,90;4\n1002;Thermostatkopf;24,50;10\n1003;Rohr 15mm;3,20;100\n1004;Pressfitting;2,10;50\n", "cp1252")
    w(f"{ROOT}/csv/right.csv", "Artikel;Bezeichnung;Preis;Menge\n1001;Heizkörper Typ 22;199,90;4\n1003;Rohr 15mm;3,20;120\n1004;Pressfitting;2,10;50\n1005;Ventil DN15;12,00;8\n", "cp1252")
    # --- Excel
    import openpyxl
    for side, rows in (("left", [["ID", "Name", "Power kW", "Price"], [1, "Pump A", 1.5, 420], [2, "Pump B", 2.2, 510], [3, "Fan C", 0.75, 199]]),
                       ("right", [["ID", "Name", "Power kW", "Price"], [1, "Pump A", 1.5, 435], [3, "Fan C", 0.75, 199], [4, "Fan D", 1.1, 260]])):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Products"
        for r in rows:
            ws.append(r)
        ws.append(["Total", "", "", "=SUM(D2:D4)" if side == "left" else "=SUM(D2:D5)"])
        if side == "right":
            ws["B2"].font = openpyxl.styles.Font(bold=True)
        ws2 = wb.create_sheet("Notes" if side == "left" else "Remarks")
        ws2.append(["Version", "Author", "Comment"])
        ws2.append(["1", "S. S.", "initial list of pumps and fans for the heating system"])
        from openpyxl.workbook.defined_name import DefinedName
        dn = DefinedName("PriceList", attr_text="Products!$D$2:$D$" + ("4" if side == "left" else "5"))
        try:
            wb.defined_names["PriceList"] = dn
        except TypeError:
            wb.defined_names.append(dn)
        os.makedirs(f"{ROOT}/excel", exist_ok=True)
        wb.save(f"{ROOT}/excel/{side}.xlsx")
    # --- Word
    import docx
    for side in ("left", "right"):
        d = docx.Document()
        d.add_heading("Angebot Heizungsanlage" if side == "left" else "Angebot Heizungs- und Lüftungsanlage", 1)
        p = d.add_paragraph()
        p.add_run("Sehr geehrte Damen und Herren,").bold = side == "right"
        d.add_paragraph("wir bieten Ihnen folgende Leistungen an." if side == "left" else "wir bieten Ihnen gerne folgende Leistungen an.")
        t = d.add_table(rows=3, cols=3)
        data = [["Pos", "Leistung", "Preis"], ["1", "Kessel", "4.500 €"], ["2", "Montage", "1.200 €" if side == "left" else "1.350 €"]]
        for i, row in enumerate(data):
            for j, v in enumerate(row):
                t.cell(i, j).text = v
        d.add_paragraph("Mit freundlichen Grüßen")
        os.makedirs(f"{ROOT}/word", exist_ok=True)
        d.save(f"{ROOT}/word/{side}.docx")
    # --- VDI 3805 (synthetic)
    left = """010;1;3805;2;MUSTER;Muster GmbH;20240115;1;
100;1;1;Heizkörper Kompakt;HK22;
110;1;1;1;22;300;400;G1/2 IG;
110;1;1;2;22;300;500;G1/2 IG;
110;1;1;3;22;300;600;G1/2 IG;
200;1;1;1;55;45;20;
"""
    right = """010;1;3805;2;MUSTER;Muster GmbH;20250301;2;
100;1;1;Heizkörper Kompakt;HK22;
110;1;1;1;22;300;400,0;G1/2 IG;
110;1;1;2;22;300;550;G3/4 AG;
110;1;1;4;22;300;800;G1/2 IG;
200;1;1;1;55;45;20;
"""
    w(f"{ROOT}/vdi3805/left.002", left, "cp1252")
    w(f"{ROOT}/vdi3805/right.002", right, "cp1252")
    # --- VDI 3805 in the real Blatt 1 layout (invented manufacturer and values):
    # field 2 = index, indices restart below each parent, decimal sub-records, '\;' escapes
    real_v1 = """010;6;201507;MUSTER;20240115;https://www.example.com;Testdatei\\;synthetisch;;;;;20240115;deu;DE;202010;0;0;
100;1;10;Plattenheizkörper;P;;20240115;;;
110;1;1;Typ 22;Muster 22;;20240115;;;
200;1;bis 1.0 MPa;
300;1;Weiß;
700;1;1;300;100;Typ 22;REG-1;M;;;;;;0;0;;22;0;0;;;;;;;;;;;;;;;;;90;;
710.01;1;400;
710.01;2;500;
710.02;1;75;65;20;1.3;450.;
720;1;1;;1;;;1;;;;;;;;;;
700;2;2;600;100;Typ 22;REG-2;M;;;;;;0;0;;22;0;0;;;;;;;;;;;;;;;;;90;;
710.01;1;400;
710.01;2;500;
710.01;3;600;
710.02;1;75;65;20;1.3;610.;
970;1;Plattenheizkörper;;;;;
970.01;1;1;E;Vorlauf;NIPP;2999;DN;15;15;0;-25.;0.;300.;0.;0.;1.;0.;1.;0.;
970.03;1;G;QUAD;0;0;0;1;0;0;0;1;0;BL;100;BH;;;;;;;;;;;
"""
    real_v2 = real_v1.replace("710.01;3;600;", "710.01;3;600;\n710.01;4;700;") \
        .replace("700;2;2;600;100", "700;2;2;600;110").replace("1.3;450.;", "1.30;450.;") \
        .replace("15;15;0;-25.", "20;20;0;-25.").replace("20240115;https", "20250301;https")
    w(f"{ROOT}/vdi3805/PART06_MUSTER_2024.vdi", real_v1, "cp1252")
    w(f"{ROOT}/vdi3805/PART06_MUSTER_2025.vdi", real_v2, "cp1252")
    # --- XML product catalogue (ISO 16757 style, synthetic) + XSD
    xsd = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
  <xs:element name="Catalogue">
    <xs:complexType><xs:sequence>
      <xs:element name="Product" maxOccurs="unbounded">
        <xs:complexType><xs:sequence>
          <xs:element name="Variant" maxOccurs="unbounded">
            <xs:complexType><xs:sequence>
              <xs:element name="Property" maxOccurs="unbounded">
                <xs:complexType><xs:simpleContent><xs:extension base="xs:string">
                  <xs:attribute name="name" type="xs:string" use="required"/>
                  <xs:attribute name="unit" type="xs:string"/>
                </xs:extension></xs:simpleContent></xs:complexType>
              </xs:element>
              <xs:element name="Port" minOccurs="0" maxOccurs="unbounded">
                <xs:complexType><xs:attribute name="id" use="required"/><xs:attribute name="connection" use="required"/></xs:complexType>
              </xs:element>
              <xs:element name="Geometry" minOccurs="0"><xs:complexType><xs:attribute name="ref" use="required"/></xs:complexType></xs:element>
            </xs:sequence><xs:attribute name="id" type="xs:string" use="required"/></xs:complexType>
          </xs:element>
        </xs:sequence><xs:attribute name="id" type="xs:string" use="required"/></xs:complexType>
      </xs:element>
    </xs:sequence></xs:complexType>
  </xs:element>
</xs:schema>
"""
    cat_left = """<?xml version="1.0" encoding="UTF-8"?>
<Catalogue xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="catalogue.xsd">
  <Product id="HK22">
    <Variant id="HK22-300-400">
      <Property name="Height" unit="mm">300</Property>
      <Property name="Length" unit="mm">400</Property>
      <Property name="HeatOutput" unit="W">512</Property>
      <Port id="P1" connection="G1/2 IG"/>
      <Geometry ref="geo/hk22_300_400.stp"/>
    </Variant>
    <Variant id="HK22-300-500">
      <Property name="Height" unit="mm">300</Property>
      <Property name="Length" unit="mm">500</Property>
      <Property name="HeatOutput" unit="W">640</Property>
      <Port id="P1" connection="G1/2 IG"/>
      <Geometry ref="geo/hk22_300_500.stp"/>
    </Variant>
  </Product>
</Catalogue>
"""
    cat_right = """<?xml version="1.0" encoding="UTF-8"?>
<Catalogue xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="catalogue.xsd">
  <Product id="HK22">
    <Variant id="HK22-300-500">
      <Property name="Height" unit="mm">300</Property>
      <Property name="Length" unit="mm">500</Property>
      <Property name="HeatOutput" unit="W">655</Property>
      <Port id="P1" connection="G3/4 AG"/>
    </Variant>
    <Variant id="HK22-300-400">
      <Property name="Height" unit="mm">300.0</Property>
      <Property name="Length" unit="mm">400</Property>
      <Property name="HeatOutput" unit="W">512</Property>
      <Port id="P1" connection="G1/2 IG"/>
      <Geometry ref="geo/hk22_300_400.stp"/>
    </Variant>
    <Variant id="HK22-300-600">
      <Property name="Height" unit="mm">300</Property>
      <Property name="Length">600</Property>
      <Port id="P1" connection="G1/2 IG"/>
      <Geometry ref="geo/hk22_300_600.stp"/>
    </Variant>
  </Product>
</Catalogue>
"""
    w(f"{ROOT}/xml/catalogue.xsd", xsd)
    w(f"{ROOT}/xml/catalogue_v1.xml", cat_left)
    w(f"{ROOT}/xml/catalogue_v2.xml", cat_right)
    w(f"{ROOT}/xml/invalid.xml", cat_left.replace('<Port id="P1" connection="G1/2 IG"/>', '<Port id="P1"/>', 1))
    # --- JSON
    w(f"{ROOT}/json/left.json", '{"project": "A", "items": [{"id": 1, "name": "Pump", "kW": 1.5}, {"id": 2, "name": "Fan", "kW": 0.75}]}')
    w(f"{ROOT}/json/right.json", '{"project": "A", "items": [{"id": 2, "name": "Fan", "kW": 0.8}, {"id": 1, "name": "Pump", "kW": 1.5}, {"id": 3, "name": "Valve"}]}')
    # --- three-way merge
    w(f"{ROOT}/merge/base.txt", "line 1\nline 2\nline 3\nline 4\n")
    w(f"{ROOT}/merge/mine.txt", "line 1\nline 2 changed by me\nline 3\nline 4\n")
    w(f"{ROOT}/merge/theirs.txt", "line 1\nline 2\nline 3\nline 4 changed by them\nline 5\n")
    # --- binary
    os.makedirs(f"{ROOT}/binary", exist_ok=True)
    open(f"{ROOT}/binary/left.bin", "wb").write(bytes(range(256)) * 4)
    open(f"{ROOT}/binary/right.bin", "wb").write(bytes(range(256)) * 2 + b"INSERTED" + bytes(range(256)) * 2)
    # --- folders
    for side in ("left", "right"):
        base = f"{ROOT}/folders/{side}"
        w(f"{base}/readme.txt", "Project notes\nversion 1\n" if side == "left" else "Project notes\nversion 2\n")
        w(f"{base}/same.txt", "identical content\n")
        w(f"{base}/src/main.c", "int main(void){return 0;}\n")
    w(f"{ROOT}/folders/left/only_left.txt", "x\n")
    w(f"{ROOT}/folders/right/only_right.txt", "y\n")
    w(f"{ROOT}/folders/left/old_name.txt", "renamed file content\n")
    w(f"{ROOT}/folders/right/new_name.txt", "renamed file content\n")
    w(f"{ROOT}/folders/left/dup_a.txt", "duplicate\n")
    w(f"{ROOT}/folders/left/src/dup_b.txt", "duplicate\n")
    print("samples written to", ROOT)


if __name__ == "__main__":
    sys.exit(main())

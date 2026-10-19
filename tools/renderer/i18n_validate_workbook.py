"""Verify the exported review workbook against its source inventory."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    messages, locations = inventory["messages"], inventory["locations"]
    identifiers = {row["id"] for row in messages}
    assert len(identifiers) == len(messages), "Duplicate message IDs"
    assert {row["id"] for row in locations} == identifiers, "Unlinked messages or source locations"
    catalog = json.loads((args.root / "dev/CSLModernMapRenderer/resources/i18n/zh-CN.json").read_text(encoding="utf-8"))
    assert {row["key"] for row in locations if row["path"].endswith("resources/i18n/zh-CN.json")} == set(catalog), "Incomplete resource coverage"
    line_counts = {}
    for row in locations:
        if row["path"] not in line_counts:
            source = args.root / row["path"]
            assert source.is_file(), f"Missing source: {source}"
            line_counts[row["path"]] = len(source.read_text(encoding="utf-8-sig").splitlines())
        assert 1 <= row["line"] <= line_counts[row["path"]], f"Invalid source line: {row}"
    with zipfile.ZipFile(args.workbook) as archive:
        strings = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        def sheet(index):
            doc = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            values = {}
            for cell in doc.findall("s:sheetData/s:row/s:c", NS):
                value = cell.find("s:v", NS)
                text = value.text if value is not None else ""
                values[cell.get("r")] = strings[int(text)] if cell.get("t") == "s" else text
            pane = doc.find("s:sheetViews/s:sheetView/s:pane", NS)
            assert pane is not None and pane.get("xSplit") == "2" and pane.get("ySplit") == "5", "Incorrect frozen panes"
            assert not doc.findall(".//s:f", NS), "Review workbook should contain literal source text, not formulas"
            return doc, values
        review, values = sheet(1)
        assert sum(key.startswith("A") and key[1:].isdigit() and int(key[1:]) >= 6 for key in values) == len(messages)
        for i, row in enumerate(messages, 6):
            for column, key in (("A", "id"), ("C", "original"), ("D", "reviewed"), ("E", "status")):
                assert values.get(column + str(i), "") == row[key], f"Mismatched value: {column}{i}"
        validations = review.find("s:dataValidations", NS)
        assert validations is not None and len(validations) == 1
        assert validations[0].get("sqref") == f"E6:E{len(messages)+5}"
        assert validations[0].find("s:formula1", NS).text == '"待校对,已确认,有疑问,不纳入"'
        _, source_values = sheet(2)
        assert sum(key.startswith("A") and key[1:].isdigit() and int(key[1:]) >= 6 for key in source_values) == len(locations)
        expected_locations = Counter((row["id"], row["key"], row["path"], row["line"], row["mechanism"], row["context"]) for row in locations)
        actual_locations = Counter((source_values.get("A" + str(i), ""), source_values.get("B" + str(i), ""),
            source_values.get("C" + str(i), ""), int(source_values["D" + str(i)]),
            source_values.get("E" + str(i), ""), source_values.get("F" + str(i), "")) for i in range(6, len(locations)+6))
        assert actual_locations == expected_locations, "Mismatched exported source locations"
        tables = [name for name in archive.namelist() if name.startswith("xl/tables/table") and name.endswith(".xml")]
        assert len(tables) == 2
        for name in tables:
            table = ET.fromstring(archive.read(name))
            assert table.find("s:autoFilter", NS) is not None, "Missing table filters"
    print(json.dumps({"verified": True, "messages": len(messages), "locations": len(locations),
        "resourceKeys": len(catalog), "sourceFilesWithLocations": len(line_counts),
        "checks": ["all original and editable values", "stable IDs", "source provenance", "complete catalog keys", "table filters", "status dropdown", "frozen rows and columns", "no accidental formulas"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()

"""The accept/reject table must account for every finding of both reviews."""
import os
import re
import unittest

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "REVIEW_ROUND2.md")


def rows():
    out = {}
    with open(DOC) as fh:
        for line in fh:
            if re.match(r"\| [CF]\d+ \|", line):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                out[cells[0]] = cells
    return out


class ReviewRecord(unittest.TestCase):
    def test_every_finding_has_exactly_one_row(self):
        r = rows()
        self.assertEqual(set(r), {"C%d" % i for i in range(1, 22)} | {"F%d" % i for i in range(1, 25)})
        self.assertEqual(len(r), 45)

    def test_verdict_counts(self):
        counts = {}
        for cells in rows().values():
            word = cells[3].split(" ")[0]
            counts[word] = counts.get(word, 0) + 1
        self.assertEqual(counts, {"FIXED": 32, "DUPLICATE": 8, "PARTIAL": 4, "REJECTED": 1})

    def test_duplicates_name_an_existing_row_of_the_other_kind_or_same_issue(self):
        r = rows()
        for fid, cells in r.items():
            if cells[3].startswith("DUPLICATE"):
                target = re.search(r"of ([CF]\d+)", cells[3]).group(1)
                self.assertIn(target, r)
                self.assertNotEqual(target, fid)
                self.assertFalse(r[target][3].startswith("DUPLICATE"), fid)

    def test_no_em_dashes_in_lab_files(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        dash = chr(0x2014)
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for name in files:
                if name.endswith((".py", ".md", ".json", ".yml", ".txt")):
                    with open(os.path.join(base, name), encoding="utf-8") as fh:
                        self.assertNotIn(dash, fh.read(), os.path.join(base, name))


if __name__ == "__main__":
    unittest.main()

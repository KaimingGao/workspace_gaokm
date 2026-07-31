import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.ops.package_info import REMOVED_SHIM_PATHS, build_quant_package_info
from quant.ops.shim_audit import audit_quant_shim_imports


class TestP41ShimsRemoved(unittest.TestCase):
    def test_removed_shim_files_absent(self):
        still_present = []
        for rel in REMOVED_SHIM_PATHS:
            path = os.path.join(ROOT, rel.replace("/", os.sep)).rstrip(os.sep)
            if os.path.exists(path):
                still_present.append(rel)
        self.assertEqual(still_present, [])

    def test_package_info_marks_shims_removed(self):
        out = build_quant_package_info()
        self.assertTrue(out.get("shims_removed"))
        self.assertEqual(out["shim_paths"], [])
        self.assertGreaterEqual(len(out["removed_shim_paths"]), 10)

    def test_skills_quant_tool_config_remains(self):
        path = os.path.join(ROOT, "skills", "quant", "tool_config.json")
        self.assertTrue(os.path.isfile(path))

    def test_import_audit_still_passes(self):
        out = audit_quant_shim_imports()
        self.assertTrue(out["ok"], out.get("offenders"))


if __name__ == "__main__":
    unittest.main()

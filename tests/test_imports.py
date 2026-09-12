import os
import sys
import json
import unittest

# Ensure repo root is on sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

class TestRijiPackage(unittest.TestCase):
    def test_riji_package_metadata(self):
        import riji
        self.assertTrue(hasattr(riji, "__version__"))
        self.assertEqual(riji.__version__, "1.0.0")
        self.assertTrue(hasattr(riji, "__author__"))
        self.assertIn("Rchin", riji.__author__)

    def test_riji_submodules_importable(self):
        import riji.coloc as coloc
        import riji.coloc_outputs as coloc_outputs
        self.assertTrue(callable(getattr(coloc, "main", None)))
        self.assertTrue(callable(getattr(coloc, "run", None)))

    def test_cell_viability_models_exist(self):
        import riji
        riji_dir = os.path.dirname(riji.__file__)
        cv_dir = os.path.join(riji_dir, "cell_viability")

        for model_name in [
            "default_dead_model.json",
            "default_pick_model.json",
            "default_pick_model_confluent.json"
        ]:
            model_path = os.path.join(cv_dir, model_name)
            self.assertTrue(os.path.isfile(model_path), f"Model file {model_name} missing from {cv_dir}")
            with open(model_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.assertIsInstance(data, dict), f"Model {model_name} must parse to a JSON object"

    def test_run_riji_launcher(self):
        import run_riji
        self.assertTrue(hasattr(run_riji, "main"))

if __name__ == "__main__":
    unittest.main()

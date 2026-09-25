"""Step 1 smoke test: the runtime package structure imports cleanly.

Runs with the standard library only:

    python -m unittest discover -v

(Also collectable by pytest once it is added to the toolchain by Member 4.)
"""

import importlib
import unittest


class SmokeTest(unittest.TestCase):
    def test_import_aura(self):
        aura = importlib.import_module("aura")
        self.assertTrue(aura.__doc__)
        self.assertIn("AURA", aura.__doc__)

    def test_import_aura_runtime(self):
        runtime = importlib.import_module("aura.runtime")
        self.assertTrue(runtime.__doc__)
        self.assertIn("runtime", runtime.__doc__)
        # The package shell must expose its own location for later wiring.
        self.assertTrue(runtime.__file__)

    def test_import_is_idempotent(self):
        first = importlib.import_module("aura.runtime")
        second = importlib.import_module("aura.runtime")
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()

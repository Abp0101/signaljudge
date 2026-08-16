import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signaljudge.models import ValidationError
from signaljudge.provider import LiveOddsProvider


class ProviderSecurityTests(unittest.TestCase):
    def test_rejects_non_allowlisted_sport(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = LiveOddsProvider(Path(directory))
            with self.assertRaises(ValidationError):
                provider.fetch("https://attacker.invalid", api_key="not-a-real-key")

    def test_missing_secret_fails_before_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            provider = LiveOddsProvider(Path(directory))
            with self.assertRaises(ValidationError):
                provider.fetch("baseball_mlb")


if __name__ == "__main__":
    unittest.main()


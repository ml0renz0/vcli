"""Publication sanity checks: verified casts, safe deploy and mobile player."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
class PagesContracts(unittest.TestCase):
    def test_readme_mobile_previews(self):
        text=(ROOT/"README.md").read_text()
        self.assertIn('docs/demo-previews/playground.svg',text)
        self.assertIn('docs/demo-previews/saas.svg',text)
        self.assertIn('https://ml0renz0.github.io/vcli/#saas',text)
    def test_workflow_security_and_player(self):
        workflow=(ROOT/".github/workflows/publish-demos.yml").read_text()
        self.assertIn("actions/deploy-pages@v4",workflow)
        self.assertIn("head_repository.full_name == github.repository",workflow)
        self.assertIn("validate-published-casts.py",workflow)
        self.assertNotIn("git push",workflow)
        player=(ROOT/"docs/asciinema-player.html").read_text()
        self.assertIn("AsciinemaPlayer.create",player)
        self.assertIn("DecompressionStream",player)
        self.assertIn("fit:'width'",player)
if __name__=="__main__":
    unittest.main()

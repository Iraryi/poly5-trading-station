from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "contracts"


class TestExampleContracts(unittest.TestCase):
    def test_telemetry_jsonl_parse(self):
        rows = [json.loads(x) for x in (ROOT / "example_events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(len(rows), len({row["event_id"] for row in rows}))
        for row in rows:
            self.assertEqual("2.0", row["schema_version"])
            self.assertIn(row["mode"], ("monitor", "paper", "replay", "live"))
            self.assertIn("EXAMPLE_NOT_LIVE", row["quality_flags"])

    def test_proposal_has_evidence_and_does_not_claim_live(self):
        row = json.loads((ROOT / "example_ai_proposal.json").read_text(encoding="utf-8"))
        self.assertEqual("draft", row["status"])
        self.assertIn("evidence_event_ids", row)
        self.assertEqual("unknown", row["confidence"])

    def test_json_schemas_if_validator_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("Optional jsonschema not installed")
        telemetry_schema = json.loads((ROOT / "telemetry_event.schema.json").read_text(encoding="utf-8"))
        proposal_schema = json.loads((ROOT / "ai_proposal.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(telemetry_schema)
        jsonschema.Draft202012Validator.check_schema(proposal_schema)
        for line in (ROOT / "example_events.jsonl").read_text(encoding="utf-8").splitlines():
            jsonschema.validate(json.loads(line), telemetry_schema)
        jsonschema.validate(json.loads((ROOT / "example_ai_proposal.json").read_text(encoding="utf-8")), proposal_schema)


if __name__ == "__main__":
    unittest.main()


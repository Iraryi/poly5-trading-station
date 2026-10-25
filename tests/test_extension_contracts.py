import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ExtensionContractTests(unittest.TestCase):
    def test_json_decodes(self):
        for file in (ROOT/'contracts').glob('*.json'):
            with self.subTest(file=file.name):
                json.loads(file.read_text(encoding='utf8'))

    def test_extension_examples(self):
        schema = json.loads((ROOT/'contracts'/'extension_manifest.schema.json').read_text('utf8'))
        self.assertIn('capabilities', schema['properties'])
        for filename in ('sample_extension_btc_5m.json','sample_extension_eth_5m_future.json'):
            sample = json.loads((ROOT/'contracts'/filename).read_text('utf8'))
            self.assertEqual(sample['contract_version'], '3.0')
            self.assertEqual(sample['status'], 'placeholder')
            self.assertFalse('LIVE_AUTOMATION' in sample['capabilities'])

    def test_account_fixture_never_fakes_balance(self):
        data = json.loads((ROOT/'contracts'/'sample_account_event.json').read_text('utf8'))
        self.assertEqual(data['source_status'], 'simulated')
        self.assertIsNone(data['payload']['cash_usdc'])
        self.assertIn('NO_LIVE_ACCOUNT',data['quality_flags'])

    @unittest.skipUnless(__import__('importlib').util.find_spec('jsonschema') is not None, 'jsonschema optional')
    def test_json_schemas(self):
        from jsonschema import Draft202012Validator
        contracts = ROOT/'contracts'
        pairs=[('extension_manifest.schema.json','sample_extension_btc_5m.json'),
               ('extension_manifest.schema.json','sample_extension_eth_5m_future.json'),
               ('account_event.schema.json','sample_account_event.json')]
        for schema_file,fixture in pairs:
            with self.subTest(fixture=fixture):
                schema=json.loads((contracts/schema_file).read_text('utf8'))
                sample=json.loads((contracts/fixture).read_text('utf8'))
                Draft202012Validator(schema).validate(sample)

if __name__=='__main__':unittest.main()

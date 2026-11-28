import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import instabox_adapter


class FakeResponse:
    def __init__(self, data, status=200):
        self.data = json.dumps(data).encode()
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, n=-1): return self.data[:n]


class InstaboxAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secret_file = Path(self.tmp.name) / 'provider-keys.json'
        self.secret_file.write_text(json.dumps({'instabox-worker': {'api_key': 'ibk_test_restricted_key'}}))
    def tearDown(self): self.tmp.cleanup()

    @patch('instabox_adapter.urllib.request.urlopen')
    def test_status_returns_only_sanitized_machine_and_usage_fields(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({'machines':[{'id':'m_test123','name':'ai','state':'paused','createdAt':'2026-11-19T11:00:47Z','spec':{'vcpus':4,'memMiB':8192,'persistentDiskGiB':20},'maxRunMinutes':120,'apiKey':'must-not-leak'}]}),
            FakeResponse({'snapshots':2,'storedBytes':0,'allocatedDiskGiB':20,'limits':{'maxMachines':1,'maxSnapshots':5,'maxStoredGiB':200},'cost':{'periodStart':'2026-11-01T00:00:00Z','periodEnd':'2026-12-01T00:00:00Z','freeSandboxHours':32.17,'costUSD':0,'overageUSD':0,'remainingUSD':0}}),
        ]
        result = instabox_adapter.status(self.secret_file)
        self.assertEqual(result['machines'][0]['state'], 'paused')
        self.assertEqual(result['machines'][0]['memory_gib'], 8.0)
        self.assertEqual(result['usage']['free_sandbox_hours'], 32.17)
        self.assertEqual(result['usage']['cost_usd'], 0)
        self.assertNotIn('apiKey', json.dumps(result))
        self.assertEqual(urlopen.call_count, 2)

    @patch('instabox_adapter.urllib.request.urlopen')
    def test_machine_action_only_allows_resume_or_suspend(self, urlopen):
        urlopen.return_value = FakeResponse({'operationId':'op_123'})
        result = instabox_adapter.machine_action(self.secret_file, 'm_test123', 'resume')
        self.assertTrue(result['ok'])
        self.assertEqual(result['action'], 'resume')
        with self.assertRaises(instabox_adapter.InstaboxError):
            instabox_adapter.machine_action(self.secret_file, 'm_test123', 'delete')
        self.assertEqual(urlopen.call_count, 1)

    def test_missing_or_wrong_prefix_key_is_rejected(self):
        self.secret_file.write_text('{}')
        with self.assertRaises(instabox_adapter.InstaboxError):
            instabox_adapter.status(self.secret_file)
        self.secret_file.write_text(json.dumps({'instabox-worker': {'api_key':'not-a-key'}}))
        with self.assertRaises(instabox_adapter.InstaboxError):
            instabox_adapter.status(self.secret_file)


if __name__ == '__main__':
    unittest.main()

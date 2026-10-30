"""Runtime tutorial downloads must read fixed local bytes, without internet access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import official_assets as assets
from openmc_runtime import Error


class OfflineAssets(unittest.TestCase):
    def test_urllib_compatible_response_reads_verified_bytes_and_refuses_modified_or_unknown_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'config').mkdir();cache=root/'.runtime/official-assets';cache.mkdir(parents=True)
            payload=b'actual local file bytes';target=cache/'example';target.write_bytes(payload)
            url='https://example.invalid/fixed';item={'id':'example','request_url':url,'download_url':url,
                    'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            (root/'config/official-assets.json').write_text(json.dumps({'sources':[item]}))
            with patch.object(assets,'ROOT',root),patch.object(assets,'fetch_file',side_effect=AssertionError('runtime must not download')):
                with assets.local_response(url) as response:
                    self.assertEqual(response.status,200);self.assertEqual(response.info()['Content-Length'],str(len(payload)))
                    self.assertEqual(response.read(),payload)
                with self.assertRaisesRegex(Error,'未发起网络请求'):assets.local_response('https://example.invalid/unregistered')
                target.write_bytes(payload+b'changed')
                with self.assertRaisesRegex(Error,'已改变'):assets.local_response(url)


if __name__=='__main__':unittest.main()

"""All result visualization families must be discoverable through the public list."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import openmc_artifacts as artifacts

class ArtifactListing(unittest.TestCase):
    def test_mesh_and_convergence_images_and_data_are_listed(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'work').mkdir()
            names=[f'{family}-abc.{suffix}' for family in ('plot','mesh','convergence')
                   for suffix in ('png','svg','json','csv')]
            for name in names:(p/name).write_text('result')
            (p/'not-an-artifact.txt').write_text('private')
            with patch.object(artifacts.jobs,'job_directory',return_value=p), \
                 patch.object(artifacts.jobs,'read',return_value={'provenance':{},'output_files':{}}), \
                 patch.object(artifacts,'relative',side_effect=lambda v:v.name), \
                 patch.object(artifacts,'workspace',return_value=p):
                result=artifacts.list_artifacts('job','a'*32)
            self.assertEqual({x['path'] for x in result['artifacts']},set(names))
            self.assertTrue(all(x['server_path']==str(p/x['path']) and x['sha256'] for x in result['artifacts']))
            self.assertFalse(result['download_supported'])
if __name__=='__main__':unittest.main()

"""User facts: submitted densities must not be inferred from nuclide ratios."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mcp'))
from result_facts import submitted_materials, selected_value_rows, value_table


class ResultFacts(unittest.TestCase):
    def test_verified_densities_preserve_units_and_ignore_composition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'work').mkdir()
            raw = b'<materials><material id="1"><density value="10.1" units="g/cm3"/><nuclide name="U235" ao="0.1"/></material><material id="2"><density value="0.7" units="macro"/></material><material id="3"><density units="sum"/></material></materials>'
            path = root/'work/materials.xml'; path.write_bytes(raw)
            record = {'input_files': {'materials.xml': hashlib.sha256(raw).hexdigest()}}
            result = submitted_materials(root, record)
            self.assertEqual(result['status'], 'verified')
            self.assertEqual([m['density'] for m in result['materials']], [
                {'value':10.1, 'units':'g/cm3'}, {'value':0.7, 'units':'macro'}, {'value':None, 'units':'sum'}])
            path.write_bytes(raw.replace(b'10.1', b'20.1'))
            self.assertEqual(submitted_materials(root, record)['status'], 'unavailable')
            self.assertEqual(submitted_materials(root, record)['materials'], [])
            self.assertEqual(submitted_materials(root, {})['status'], 'unavailable')

    def test_model_xml_and_symlink_are_not_confused_with_current_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'work').mkdir()
            raw = b'<model><materials><material id="4"><density value="2" units="g/cm3"/></material></materials></model>'
            path = root/'work/model.xml'; path.write_bytes(raw)
            record = {'input_files': {'model.xml':hashlib.sha256(raw).hexdigest()}}
            self.assertEqual(submitted_materials(root, record)['materials'][0]['density']['value'], 2)
            real = root/'other.xml'; path.rename(real); path.symlink_to(real)
            self.assertEqual(submitted_materials(root, record)['status'], 'unavailable')

    def test_delivery_table_preserves_reversed_coordinate_value_pairs(self):
        rows = selected_value_rows({'score':'fission','nuclide':'total','mean':[31.,11.],
            'std_dev':[3.,1.], 'mesh_indices':[[2,1,0],[0,0,0]], 'mesh_centers':[[3,2,0],[1,1,0]]})
        self.assertEqual(rows[0]['mesh_bin'], [3,2,1])
        self.assertEqual(rows[0]['mean'], 31.)
        self.assertIn('[3, 2, 1] | fission | total | 31.0 | 3.0', value_table(rows))


if __name__ == '__main__':
    unittest.main()

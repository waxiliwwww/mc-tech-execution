"""Real SDK geometry plotting, not transport; preserve all input bytes on success/failure.
Run with the configured OpenMC Python. No nuclear database is required for plots.
"""
import contextlib, hashlib, importlib.util, os
from pathlib import Path
import sys, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
@unittest.skipUnless(importlib.util.find_spec('openmc'), '需要OpenMC SDK；绘图无输运')
class ViewIsolation(unittest.TestCase):
    def hashes(self,root):
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file() and 'views' not in p.relative_to(root).parts}
    def render(self,root,*,executable=None,**kw):
        import openmc,openmc_worker
        previous=Path.cwd()
        try:
            os.chdir(root)
            with contextlib.redirect_stdout(__import__('io').StringIO()):
                return openmc_worker.execute('view-geometry',dict(_expected_version=openmc.__version__,
                    _openmc_exec=executable or str(Path(sys.executable).parent/'openmc'),filename='views/slice.png',**kw))
        finally:os.chdir(previous)
    def model(self,root,unbounded=False,combined=False):
        import openmc
        mat=openmc.Material();mat.set_density('g/cm3',1.);mat.add_nuclide('H1',1.)
        region=(+openmc.XPlane(x0=-10.71)&-openmc.XPlane(x0=10.71)&
                +openmc.YPlane(y0=-10.71)&-openmc.YPlane(y0=10.71))
        if unbounded:region=+openmc.ZPlane(z0=-5.) & -openmc.ZPlane(z0=5.)
        model=openmc.Model(openmc.Geometry([openmc.Cell(fill=mat,region=region)]),openmc.Materials([mat]))
        plot=openmc.SlicePlot();plot.width=(21.42,21.42);model.plots=[plot]
        if combined:model.export_to_model_xml(root/'model.xml')
        else:model.export_to_xml(root)
        # This exact file must survive, including non-default bytes / multiple plots.
        other=openmc.SlicePlot();other.width=(31.,32.)
        openmc.Plots([plot,other]).export_to_xml(root/'plots.xml')
        (root/'plot_1.png').write_bytes(b'original user image')
    def test_repeated_default_and_explicit_views_preserve_split_and_unified_input(self):
        for combined in (False,True):
            with self.subTest(combined=combined),tempfile.TemporaryDirectory() as d:
                root=Path(d);self.model(root,combined=combined);before=self.hashes(root)
                value=self.render(root,pixels=80)
                __import__('json').dumps(value,allow_nan=False)
                self.assertAlmostEqual(value['width'][0],21.42)
                self.assertAlmostEqual(value['width'][1],21.42)
                self.assertEqual(value['width_source'],'geometry_bounding_box')
                self.assertTrue((root/'views/slice.png').read_bytes().startswith(b'\x89PNG'))
                explicit=self.render(root,origin=[1,2,0],width=[8],pixels=80)
                self.assertEqual(explicit['width'],[8.,8.]);self.assertEqual(explicit['origin'],[1.,2.,0.])
                self.assertEqual(self.hashes(root),before)
    def test_unbounded_plane_requires_explicit_window_without_altering_input(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.model(root,unbounded=True);before=self.hashes(root)
            with self.assertRaisesRegex(ValueError,'bounded'):self.render(root,pixels=80)
            self.assertEqual(self.hashes(root),before)
            value=self.render(root,origin=[0,0,0],width=[10,20],pixels=80)
            self.assertEqual(value['width'],[10.,20.]);self.assertEqual(self.hashes(root),before)
    def test_relative_dagmc_reference_is_absolute_only_in_scratch(self):
        import openmc,xml.etree.ElementTree as ET
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'nested').mkdir();asset=root/'nested/geom.h5m';asset.write_bytes(b'path-only fixture')
            universe=openmc.DAGMCUniverse('nested/geom.h5m')
            openmc.Model(openmc.Geometry(universe)).export_to_model_xml(root/'model.xml')
            tree=ET.parse(root/'model.xml');tree.find('.//dagmc_universe').set('filename','nested/geom.h5m')
            tree.write(root/'model.xml')
            before=self.hashes(root);seen=[]
            def renderer(plot,**kw):
                scratch=Path(kw['cwd']);tree=ET.parse(scratch/'model.xml')
                value=tree.find('.//dagmc_universe').get('filename')
                self.assertEqual(Path(value),asset)
                self.assertNotEqual(scratch,root);self.assertTrue((scratch/'model.xml').is_file())
                seen.append(scratch)
                return SimpleNamespace(data=b'\x89PNG\r\n\x1a\nfixture')
            with patch.object(openmc.SlicePlot,'to_ipython_image',renderer):
                self.render(root,origin=[0,0,0],width=[10,10],pixels=80)
            self.assertEqual(self.hashes(root),before)
            self.assertTrue(seen);self.assertFalse(seen[0].exists())
    def test_plot_process_failure_leaves_input_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.model(root);before=self.hashes(root)
            # Both SDK and native-engine fallback must fail. A missing executable
            # exercises the real subprocess boundary, rather than mocking only
            # the first renderer and accidentally allowing fallback success.
            with self.assertRaisesRegex(ValueError,'切片图渲染失败.*引擎直接绘图'):
                self.render(root,executable=str(root/'missing-openmc'),pixels=80)
            self.assertEqual(self.hashes(root),before)
            self.assertFalse((root/'views/slice.png').exists())
if __name__=='__main__':unittest.main()

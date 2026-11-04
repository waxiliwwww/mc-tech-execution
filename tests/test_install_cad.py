"""Deployment-boundary tests for the CAD installer; no downloads in the default suite."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.util.spec_from_file_location('mc_install_cad', ROOT / 'scripts/install_cad.py')
installer = importlib.util.module_from_spec(loader)
loader.loader.exec_module(installer)


class CadInstallPublication(unittest.TestCase):
    def test_config_preserves_other_settings_and_uses_venv_not_base_interpreter(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            base = root / 'base-python'
            base.write_text('not executed')
            python = root / 'cadenv/bin/python'
            python.parent.mkdir(parents=True)
            python.symlink_to(base)
            config = root / 'openmc.local.json'
            original = {'python_command': ['my-openmc-python'], 'openmc_command': ['my-openmc'],
                        'min_free_disk_bytes': 12345, 'deployment_note': {'owner': 'retained'},
                        'cad_timeout_seconds': 450}
            config.write_text(json.dumps(original))
            installer.update_config(config, python, root / 'native/lib')
            actual = json.loads(config.read_text())
            self.assertEqual({k: actual[k] for k in original}, original)
            self.assertEqual(actual['cad_python_command'], [str(python)])
            self.assertNotEqual(actual['cad_python_command'], [str(base)])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_failed_config_serialization_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / 'openmc.local.json'
            target.write_text('{"keep":"unchanged"}\n')
            original = target.read_bytes()
            with self.assertRaises(ValueError):
                installer.atomic_json(target, {'invalid': float('nan')})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(name).glob('.cad-pending-*')), [])

    def test_tampered_or_linked_native_archive_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as name:
            archive = Path(name) / 'library.deb'
            archive.write_bytes(b'correct')
            expected = installer.digest(archive)
            self.assertTrue(installer.package_valid(archive, 7, 'sha256', expected))
            archive.write_bytes(b'corrupt')
            self.assertFalse(installer.package_valid(archive, 7, 'sha256', expected))
            link = Path(name) / 'link.deb'
            link.symlink_to(archive)
            self.assertFalse(installer.package_valid(link, 7, 'sha256', installer.digest(archive)))

    def test_deployment_probe_and_manifest_are_in_source_package(self):
        manifest = json.loads((ROOT / 'config/cad-runtime.json').read_text())
        probe = ROOT / manifest['probe_step']
        self.assertEqual(installer.digest(probe), manifest['probe_sha256'])
        self.assertIn('SI_UNIT(.MILLI.,.METRE.)', probe.read_text())
        self.assertIn('cad-to-dagmc', installer.pinned_versions())
        self.assertIn('cadquery', installer.pinned_versions())
        self.assertIn('gmsh', installer.pinned_versions())

    def test_quickstart_exposes_cad_opt_out_without_running_setup(self):
        result = subprocess.run([str(ROOT / 'quickstart.sh'), '--help'], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--skip-cad', result.stdout)
        self.assertIn('CAD', result.stdout)
        self.assertIn('--cad-only', result.stdout)
        self.assertIn('--cad-prefix', result.stdout)
        subprocess.run(['bash', '-n', str(ROOT / 'quickstart.sh')], check=True)

    def test_existing_unmatched_environment_and_configuration_are_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            prefix = root / 'existing-cad'
            venv.EnvBuilder(with_pip=False).create(prefix)
            config = root / 'openmc.local.json'
            config.write_text('{"keep":"original configuration"}')
            before = config.read_bytes()
            with self.assertRaisesRegex(ValueError, '未原地改装'):
                installer.install(prefix, Path(os.sys.executable), root / 'native', config)
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse((prefix / '.mc-cad-install-pending.json').exists())
            self.assertFalse((root / 'native').exists())

    def test_quickstart_cad_only_rejects_conflicting_or_missing_inputs(self):
        for arguments, expected in [
            (['--cad-only', '--skip-cad'], '--cad-only与--skip-cad'),
            (['--cad-prefix'], '--cad-prefix需要目录'),
            (['--cad-only', '--openmc-prefix', '/nonexistent/mc-install-test'], '需要已有OpenMC Python'),
        ]:
            result = subprocess.run([str(ROOT / 'quickstart.sh'), *arguments], text=True,
                                    capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)

    def test_low_space_refuses_a_new_environment_before_writing_configuration(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / 'openmc.local.json'
            config.write_text(json.dumps({'min_free_disk_bytes': 2 ** 64,
                                          'disk_space_check_paths': []}))
            before = config.read_bytes()
            prefix = root / 'new-cad'
            with self.assertRaisesRegex(ValueError, '空间不足'):
                installer.install(prefix, Path(os.sys.executable), root / 'native', config)
            self.assertFalse(prefix.exists())
            self.assertEqual(config.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()

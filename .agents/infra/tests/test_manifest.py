import tempfile,unittest,shutil,zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentinfra.atomic import AtomicWriteError, atomic_write_bytes
from agentinfra.manifest import ManifestError, render, safe_extract, verify
from agentinfra.release_source import build_deployment_tree

SOURCE=Path(__file__).resolve().parents[2]

class TestManifest(unittest.TestCase):
    def test_safe_extract_creates_new_governance_but_never_grants_mutation_authority(self):
        with tempfile.TemporaryDirectory(dir=SOURCE) as td:
            work=Path(td);archive=work/'release.zip';destination=work/'extracted'
            with zipfile.ZipFile(archive,'w') as package:
                package.writestr('.agents/INDEX.md',b'# immutable governance\n')
                package.writestr('RELEASE.json',b'{"schema": 1}\n')
            try:
                members=safe_extract(archive,destination)
            except AtomicWriteError as exc:
                self.fail(f'new disposable governance extraction was rejected: {exc}')
            self.assertEqual(members,['.agents/INDEX.md','RELEASE.json'])
            self.assertEqual((destination/'.agents'/'INDEX.md').read_bytes(),b'# immutable governance\n')
            with self.assertRaises(ManifestError):
                safe_extract(archive,destination)
            with self.assertRaises(AtomicWriteError):
                atomic_write_bytes(destination/'.agents'/'late.txt',b'forbidden',root=destination)

    def test_project_extensions_do_not_invalidate_framework_manifest(self):
        with tempfile.TemporaryDirectory(dir=SOURCE) as td:
            r=Path(td)/'deployment';build_deployment_tree(SOURCE,r);(r/'RELEASE.json').unlink()
            (r/'.agents'/'MANIFEST.sha256').write_text(render(r))
            (r/'.agents'/'local-modules'/'x').mkdir(parents=True);(r/'.agents'/'local-modules'/'x'/'module.toml').write_text('[module]\nid="x"\n')
            (r/'.agents'/'laws'/'project').mkdir(parents=True,exist_ok=True);(r/'.agents'/'laws'/'project'/'x.toml').write_text('# local')
            (r/'.agents'/'project.md').write_text('# local')
            ok,detail=verify(r);self.assertTrue(ok,detail)

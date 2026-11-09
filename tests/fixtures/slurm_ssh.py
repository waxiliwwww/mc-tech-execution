"""SSH protocol-shaped local fixture: exercise quoting/stdin/streams, no network."""
import os
from pathlib import Path
import subprocess
import sys
root=Path(os.environ['SLURM_FIXTURE_ROOT']);root.mkdir(exist_ok=True)
(root/'ssh-host').write_text(sys.argv[-2])
sys.exit(subprocess.run(sys.argv[-1],shell=True,executable='/bin/sh').returncode)

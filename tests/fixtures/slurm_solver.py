"""Transport-test solver: writes bytes; it is explicitly NOT OpenMC."""
from pathlib import Path
import os
import sys
if '--version' in sys.argv:
    print('OpenMC '+os.environ.get('SLURM_SOLVER_VERSION','0.16.0')+' controlled-test-fixture');sys.exit(0)
p=Path('materials.xml').read_bytes()
Path('statepoint.6.h5').write_bytes(b'CONTROLLED-TRANSPORT-FIXTURE\n'+p)
Path('tallies.out').write_text('fixture flux 0.25 +/- 0.01\n')
if os.environ.get('SLURM_SOLVER_CHANGE_INPUT'):Path('settings.xml').write_text('<settings/>')
print('fixture log')
if os.environ.get('SLURM_SOLVER_LOG'):print('x'*10000)
sys.exit(int(os.environ.get('SLURM_SOLVER_EXIT','0')))

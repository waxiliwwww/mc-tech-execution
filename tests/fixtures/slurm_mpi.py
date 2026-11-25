"""Controlled launcher argument check; does not implement MPI parallelism."""
import json,os,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
assert args[0]=='-n'
Path('controlled-mpi.json').write_text(json.dumps({'processes':int(args[1]),'threads':os.environ.get('OMP_NUM_THREADS'),'real_mpi':False}))
sys.exit(subprocess.run(args[2:]).returncode)

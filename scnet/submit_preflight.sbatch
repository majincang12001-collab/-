#!/bin/bash
#SBATCH --job-name=manga-dcu-preflight
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=dcu:1
#SBATCH --time=00:10:00
#SBATCH --output=manga-dcu-preflight-%j.out
#SBATCH --error=manga-dcu-preflight-%j.err

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SLURM_SUBMIT_DIR:-$SCRIPT_DIR}"
python3 "$SCRIPT_DIR/dcu_preflight.py"

#!/bin/bash
#SBATCH --job-name=manga-dcu-preflight
#SBATCH --partition=hx1hdtest
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=dcu:1
#SBATCH --time=00:10:00
#SBATCH --output=manga-dcu-preflight-%j.out
#SBATCH --error=manga-dcu-preflight-%j.err

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$HOME}"
python3 /path/to/dcu_preflight.py

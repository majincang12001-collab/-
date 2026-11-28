#!/usr/bin/env bash
# Read-only SCNet login-node inspection. Does not submit jobs or allocate DCUs.
set -u
printf '\n== Available partitions ==\n'
if command -v whichpartition >/dev/null 2>&1; then whichpartition; else echo 'whichpartition unavailable'; fi
printf '\n== DCU-capable queue/node summary ==\n'
if command -v sinfo >/dev/null 2>&1; then sinfo -o '%P %a %l %D %G %N'; else echo 'sinfo unavailable'; fi
printf '\n== Account TRES/quota association ==\n'
if command -v sacctmgr >/dev/null 2>&1; then sacctmgr show assoc -P Format=Account,User,GrpSubmit,GrpJobs,GrpTRES; else echo 'sacctmgr unavailable'; fi
printf '\n== Home storage ==\n'
if command -v df >/dev/null 2>&1; then df -h "$HOME"; else echo 'df unavailable'; fi
printf '\nRead-only inspection complete. No job was submitted.\n'
#!/usr/bin/env python3
"""Compile legacy project records into auditable modular pipeline artifacts."""
import argparse, json
from pathlib import Path
from production_pipeline import compile_production
ROOT=Path(__file__).resolve().parent

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--project-id',required=True); a=ap.parse_args()
    projects=json.loads((ROOT/'data/projects.json').read_text('utf-8'))
    project=next((x for x in projects if x.get('id')==a.project_id),None)
    if not project: raise SystemExit('project not found')
    prod_path=ROOT/'data/production'/f'{a.project_id}.json'
    if not prod_path.exists(): raise SystemExit('production record not found')
    production=json.loads(prod_path.read_text('utf-8'))
    artifacts=compile_production(production,project,project.get('files',[]),ROOT/'data/uploads')
    out=ROOT/'data/production'/f'{a.project_id}_pipeline'
    out.mkdir(parents=True,exist_ok=True)
    for name,obj in artifacts.items():
        (out/f'{name}.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'project_id':a.project_id,'artifact_dir':str(out),'artifacts':list(artifacts),
      'qc_status':artifacts['qc_report']['status'],'qc_summary':artifacts['qc_report']['summary'],
      'regeneration_jobs':len(artifacts['regeneration_brief']['jobs'])},ensure_ascii=False,indent=2))
if __name__=='__main__': main()

"""Minimal NVIDIA API Catalog OpenAI-compatible client; API key stays server-side."""
import json, os, urllib.request, urllib.error

class ProviderNotConfigured(RuntimeError): pass

def chat(messages, model=None, timeout=90):
    key=os.environ.get('NVIDIA_API_KEY','').strip()
    if not key: raise ProviderNotConfigured('NVIDIA_API_KEY is not configured')
    base=os.environ.get('NVIDIA_BASE_URL','https://integrate.api.nvidia.com/v1').rstrip('/')
    model=model or os.environ.get('NVIDIA_MODEL','nvidia/llama-3.3-nemotron-super-49b-v1')
    body=json.dumps({'model':model,'messages':messages,'temperature':0.2,'max_tokens':1200}).encode()
    req=urllib.request.Request(base+'/chat/completions',data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp: data=json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError('NVIDIA API returned HTTP '+str(e.code)) from None
    except urllib.error.URLError:
        raise RuntimeError('NVIDIA API connection failed') from None
    return data['choices'][0]['message']['content']

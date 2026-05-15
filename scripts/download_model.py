"""下载模型配置文件"""
import os
import time
from pathlib import Path

target_dir = Path.home() / 'Desktop/协同工作流/auto-agent-workflow/models/qwen3.5-4b'
target_dir.mkdir(parents=True, exist_ok=True)

print(f'Target: {target_dir}')
print('Trying direct Hugging Face endpoint...')

os.environ['HF_ENDPOINT'] = 'https://huggingface.co'

try:
    from huggingface_hub import hf_hub_download
    t0 = time.time()
    for filename in ['config.json', 'tokenizer_config.json', 'generation_config.json']:
        print(f'  Downloading {filename}...')
        try:
            path = hf_hub_download(
                repo_id='Qwen/Qwen3.5-4B-Instruct',
                filename=filename,
                cache_dir=str(target_dir),
                local_dir=str(target_dir),
                local_dir_use_symlinks=False,
            )
            sz = os.path.getsize(path)
            print(f'    OK: {sz} bytes')
        except Exception as e:
            print(f'    Failed: {e}')
    elapsed = time.time() - t0
    print(f'Done in {elapsed:.1f}s')

    for f in sorted(os.listdir(target_dir)):
        fp = os.path.join(target_dir, f)
        if os.path.isfile(fp) and not f.endswith('.lock'):
            print(f'  {f}: {os.path.getsize(fp)} bytes')
except Exception as e:
    print(f'Fatal: {e}')
    import traceback
    traceback.print_exc()

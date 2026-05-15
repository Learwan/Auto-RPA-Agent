"""从 HF-Mirror 下载 Qwen3.5-4B 4-bit 量化模型 (MLX 格式)"""
import os
import sys
import time

MODEL_DIR = '/Users/lear/Desktop/协同工作流/auto-agent-workflow/models/qwen3.5-4b-4bit'
os.makedirs(MODEL_DIR, exist_ok=True)

os.environ['HF_HUB_CACHE'] = MODEL_DIR
os.environ['HF_HOME'] = MODEL_DIR
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

from huggingface_hub import snapshot_download

MODEL_ID = 'mlx-community/Qwen3.5-4B-4bit'

print(f'模型目录: {MODEL_DIR}')
print('HF 端点: hf-mirror.com')
print(f'模型: {MODEL_ID}')
print('格式: MLX 4-bit 量化 (Apple Silicon 优化)')
print(f'开始时间: {time.strftime("%H:%M:%S")}')
print()

t0 = time.time()
try:
    model_path = snapshot_download(
        MODEL_ID,
        cache_dir=MODEL_DIR,
        local_dir=MODEL_DIR,
    )
    elapsed = time.time() - t0
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)
    print(f'\n下载完成! 耗时: {mins}m{secs}s')
    print(f'路径: {model_path}')
    print()

    files = sorted(os.listdir(model_path))
    total = 0
    for f in files:
        fp = os.path.join(model_path, f)
        if os.path.isfile(fp):
            sz = os.path.getsize(fp)
            total += sz
            if sz > 1024 * 1024:
                print(f'  {f}  {sz/(1024**2):.0f} MB')
            else:
                print(f'  {f}  {sz} B')
        elif os.path.isdir(fp):
            print(f'  {f}/  (目录)')

    gb = total / (1024**3)
    print(f'\n总计: {gb:.2f} GB ({len(files)} 个条目)')
    print(f'完成时间: {time.strftime("%H:%M:%S")}')

except Exception as e:
    elapsed = time.time() - t0
    print(f'\n下载失败 (耗时 {elapsed:.0f}s): {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

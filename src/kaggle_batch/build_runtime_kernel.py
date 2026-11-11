"""Rebuild the llama-server runtime on the current Kaggle image (CUDA 13).

Same llama.cpp commit and cmake flags as qwen36-inbox-16k-20260923. The CUDA
user-space libraries the binary links against are bundled next to it so a later
Kaggle image change cannot break the runtime again.
"""
import glob, hashlib, json, os, shutil, subprocess, time
from pathlib import Path
LLAMA_COMMIT = '4e416ee7308dd6b581796f1a6241276cd5982691'
OUT = Path('/kaggle/working'); TEMP = Path('/kaggle/tmp/qwen36'); TEMP.mkdir(parents=True, exist_ok=True)
START = time.monotonic(); REPORT = {'llama_commit': LLAMA_COMMIT, 'stages': {}}
def save():
    REPORT['elapsed_seconds'] = time.monotonic() - START
    (OUT / 'build-report.json').write_text(json.dumps(REPORT, indent=2))
def run(args, timeout, log=None, cwd=None, env=None):
    if log:
        with (OUT / log).open('a') as stream:
            return subprocess.run(args, timeout=timeout, stdout=stream, stderr=subprocess.STDOUT, cwd=cwd, check=True, env=env)
    return subprocess.run(args, timeout=timeout, capture_output=True, text=True, cwd=cwd, check=True, env=env)
try:
    print('STAGE hardware', flush=True)
    REPORT['hardware'] = run(['nvidia-smi'], 20).stdout
    print('STAGE build', flush=True); t = time.monotonic()
    source = TEMP / 'llama.cpp'
    run(['git', 'clone', '--filter=blob:none', '--no-checkout', 'https://github.com/ggml-org/llama.cpp.git', str(source)], 240, 'build.log')
    run(['git', 'checkout', LLAMA_COMMIT], 180, 'build.log', source)
    drivers = [p for pattern in ['/usr/lib/x86_64-linux-gnu/libcuda.so.1', '/usr/local/nvidia/lib64/libcuda.so.1',
        '/usr/local/cuda*/lib64/stubs/libcuda.so', '/usr/local/cuda*/targets/x86_64-linux/lib/stubs/libcuda.so'] for p in glob.glob(pattern)]
    assert drivers, 'CUDA driver or link stub not found'
    REPORT['cuda_driver_link'] = drivers[0]
    run(['cmake', '-S', str(source), '-B', str(source/'build'), '-DGGML_CUDA=ON', '-DCMAKE_CUDA_ARCHITECTURES=75',
         '-DCUDA_cuda_driver_LIBRARY='+drivers[0], '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_APP=OFF'], 300, 'build.log')
    run(['cmake', '--build', str(source/'build'), '--config', 'Release', '-j', '4', '--target', 'llama-server'], 3000, 'build.log')
    REPORT['stages']['build_seconds'] = time.monotonic() - t; save()
    bindir = source/'build/bin'; binary = bindir/'llama-server'
    print('STAGE bundle', flush=True)
    bundled = []
    for target in [binary, *bindir.glob('*.so*')]:
        for line in run(['ldd', str(target)], 30).stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == '=>' and '/cuda' in parts[2] and '/nvidia/' not in parts[2]:
                name, real = parts[0], os.path.realpath(parts[2])
                if not (bindir/name).exists():
                    shutil.copy2(real, bindir/name); bundled.append({'name': name, 'from': real, 'bytes': (bindir/name).stat().st_size})
    # cuBLAS loads cublasLt lazily; make sure it travels too.
    for real in glob.glob('/usr/local/cuda/targets/x86_64-linux/lib/libcublasLt.so.*'):
        soname = 'libcublasLt.so.' + Path(real).name.split('.so.')[1].split('.')[0]
        if not (bindir/soname).exists():
            shutil.copy2(os.path.realpath(real), bindir/soname); bundled.append({'name': soname, 'from': real, 'bytes': (bindir/soname).stat().st_size})
    REPORT['bundled'] = bundled
    print('STAGE verify', flush=True)
    clean = {k: v for k, v in os.environ.items() if k != 'LD_LIBRARY_PATH'}
    env = {**clean, 'LD_LIBRARY_PATH': str(bindir) + ':/usr/local/nvidia/lib64'}
    REPORT['ldd'] = run(['ldd', str(binary)], 30, env=env).stdout
    assert 'not found' not in REPORT['ldd'], REPORT['ldd']
    version = run([str(binary), '--version'], 30, env=env); REPORT['version'] = version.stdout + version.stderr
    help_text = run([str(binary), '--help'], 30, env=env).stdout
    for flag in ['--fit', '--ctx-size', '--split-mode', '--reasoning-budget']:
        assert flag in help_text, flag
    print('STAGE cache_runtime', flush=True)
    run(['tar', '-czf', str(OUT/'llama-runtime.tar.gz'), '-C', str(source/'build'), 'bin'], 900)
    with (OUT/'llama-runtime.tar.gz').open('rb') as stream:
        REPORT['runtime_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    REPORT['runtime_bytes'] = (OUT/'llama-runtime.tar.gz').stat().st_size
    REPORT['ok'] = True
except BaseException as exc:
    REPORT['ok'] = False; REPORT['error'] = type(exc).__name__ + ': ' + str(exc)[-3000:]
    raise
finally:
    save(); print(json.dumps({k: v for k, v in REPORT.items() if k != 'hardware'}, indent=1)[-6000:], flush=True)

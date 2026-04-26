"""Quick test: verify gcc works and run 1 round of testing"""
import sys, os, traceback

# Force MinGW in PATH
os.environ["PATH"] = r"C:\msys64\mingw64\bin" + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quick_test_results.txt")

try:
    results = []

    # Test 1: gcc
    import shutil, subprocess
    gcc = shutil.which("gcc") or r"C:\msys64\mingw64\bin\gcc.exe"
    results.append(f"[1] gcc: {gcc}, exists: {os.path.exists(gcc)}")

    # Test 2: Compile
    work_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "executor_work")
    os.makedirs(work_dir, exist_ok=True)
    source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "targets", "cjson")

    harness = os.path.join(work_dir, "test_harness.c")
    with open(harness, 'w') as f:
        f.write('#include "cJSON.h"\n#include <stdio.h>\n#include <stdlib.h>\n')
        f.write('int main() {\n')
        f.write('  const char *json = "{\\"key\\": \\"value\\"}";\n')
        f.write('  cJSON *obj = cJSON_Parse(json);\n')
        f.write('  if (obj) {\n')
        f.write('    char *p = cJSON_Print(obj);\n')
        f.write('    if (p) { printf("%s\\n", p); free(p); }\n')
        f.write('    cJSON_Delete(obj);\n')
        f.write('  } else { printf("parse failed\\n"); }\n')
        f.write('  return 0;\n}\n')

    exe = os.path.join(work_dir, "test_harness.exe")
    cjson_c = os.path.join(source_dir, "cJSON.c")
    cmd = [gcc, cjson_c, harness, "-o", exe, "-I", source_dir, "-Wall", "-g"]
    results.append(f"[2] Compile: {' '.join(cmd)}")

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    results.append(f"[2] rc={r.returncode}")
    if r.stderr.strip():
        results.append(f"[2] stderr: {r.stderr[:300]}")
    results.append(f"[2] {'SUCCESS' if r.returncode == 0 else 'FAILED'}")

    # Test 3: Run
    if os.path.exists(exe):
        r = subprocess.run([exe], capture_output=True, text=True, timeout=5)
        results.append(f"[3] rc={r.returncode}, stdout={r.stdout[:200].strip()}")
        results.append(f"[3] {'SUCCESS' if r.returncode == 0 else 'FAILED'}")
    else:
        results.append("[3] exe not found")

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(results))

except Exception as e:
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"ERROR: {e}\n{traceback.format_exc()}")

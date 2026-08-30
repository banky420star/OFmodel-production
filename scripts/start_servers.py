"""Start API + Next.js frontend servers in background."""
import subprocess, time, os, signal, sys

# Kill any existing processes on our ports
for port in [8001, 3000]:
    subprocess.run(f"kill $(lsof -i :{port} -t) 2>/dev/null", shell=True)
time.sleep(1)

# Clean DB for fresh start
db_path = os.path.join(os.path.dirname(__file__), "..", "apps", "api", "persona_studio.db")
if os.path.exists(db_path):
    os.remove(db_path)

# Start API
api_dir = os.path.join(os.path.dirname(__file__), "..", "apps", "api")
api_log = open("/tmp/persona_api.log", "w")
api_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"],
    cwd=api_dir, stdout=api_log, stderr=subprocess.STDOUT,
    start_new_session=True
)
print(f"API started: PID={api_proc.pid}")

# Wait for API to be ready
for i in range(20):
    time.sleep(1)
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=2)
        if r.status == 200:
            print("API is ready on port 8001")
            break
    except:
        pass
else:
    print("WARNING: API may not be ready yet")

# Start Next.js frontend
web_dir = os.path.join(os.path.dirname(__file__), "..", "apps", "web")
web_log = open("/tmp/persona_web.log", "w")
web_proc = subprocess.Popen(
    ["npx", "next", "dev", "-p", "3000"],
    cwd=web_dir, stdout=web_log, stderr=subprocess.STDOUT,
    start_new_session=True
)
print(f"Next.js started: PID={web_proc.pid}")

# Wait for frontend
for i in range(30):
    time.sleep(1)
    try:
        import urllib.request
        r = urllib.request.urlopen("http://127.0.0.1:3000", timeout=2)
        if r.status == 200:
            print("Frontend is ready on port 3000")
            break
    except:
        pass
else:
    print("WARNING: Frontend may not be ready yet")

print("\nServers running:")
print(f"  API:       http://localhost:8001  (PID {api_proc.pid})")
print(f"  Frontend:  http://localhost:3000  (PID {web_proc.pid})")
print(f"  API Docs:  http://localhost:8001/docs")

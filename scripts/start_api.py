#!/usr/bin/env python3
"""Start Persona Studio API server as a daemon."""
import subprocess
import sys
import os

# Change to the api directory
os.chdir(os.path.join(os.path.dirname(__file__), '..', 'apps', 'api'))

# Start uvicorn
proc = subprocess.Popen(
    [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8001'],
    stdout=open('/tmp/persona_api.log', 'w'),
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
print(f"API server started on PID {proc.pid}")
print(f"Log: /tmp/persona_api.log")
print(f"URL: http://127.0.0.1:8001")

import subprocess
import time
import sys
import os
import webbrowser
import urllib.request
import urllib.error

URL = "http://localhost:8001"

def wait_for_server(timeout=60):
    print("... Waiting for server to be ready...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"{URL}/health", timeout=1)
            print(" ready!")
            return True
        except Exception:
            print(".", end="", flush=True)
            time.sleep(0.5)
    print(" timed out.")
    return False

def run_app():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(">> Starting RAG Chatbot...")

    backend = subprocess.Popen([sys.executable, "RAG_Frontend.py"])

    if wait_for_server():
        webbrowser.open(URL)
        print(f" Open: {URL}")
        print("Press Ctrl+C to stop.\n")
    else:
        print("ERROR: Server did not start in time. Check for errors above.")
        backend.terminate()
        return

    try:
        while True:
            if backend.poll() is not None:
                print("ERROR: Server stopped unexpectedly.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        backend.terminate()
        print("\n-- Stopped.")

if __name__ == "__main__":
    run_app()

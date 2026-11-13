"""FlowHMR web demo: drop a video into the page, get SMPL-H motion in a 3D viewer.

Usage (from the repo root, V2M env):
    python app.py                                 # models/flowhmr_latest, port 8080
    python app.py --ckpt <ckpt> --port 8080 --device cuda:0

Options: python app.py --help. Implementation: tools/web/server.py.
"""
import os
import runpy

if __name__ in ("__main__", "__mp_main__"):
    from flowhmr.runtime_environments import RUNTIME

    RUNTIME.configure_process()
if __name__ == "__main__":
    # Keep this guarded launcher as __main__. Windows spawn reopens that file
    # when starting the camera worker, which does not need to import Flask.
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "web", "server.py")
    runpy.run_path(server_path, run_name="flowhmr_web_server")["main"]()

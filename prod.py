#!/usr/bin/env python
"""Production server (waitress), same pattern as vds-sternwarte."""
import os
import signal
import sys

from paste.translogger import TransLogger
from waitress import serve

from webapp import create_app

print("STARTING PHOTONLEDGER")
app = create_app()
# as PID 1 (container without init) SIGTERM is ignored unless handled: stop at once on "docker stop"
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

serve(
    TransLogger(app, setup_console_handler=os.getenv("SETUP_CONSOLE_HANDLER", "false").lower() in ("true", "1", "t")),
    listen=os.getenv("LISTEN", "0.0.0.0:5080"),
    threads=int(os.getenv("THREADS", "4")),
)

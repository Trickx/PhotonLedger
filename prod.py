#!/usr/bin/env python
"""Production server (waitress), same pattern as vds-sternwarte."""
import os

from paste.translogger import TransLogger
from waitress import serve

from webapp import create_app

print("STARTING PHOTONLEDGER")
app = create_app()

serve(
    TransLogger(app, setup_console_handler=os.getenv("SETUP_CONSOLE_HANDLER", "false").lower() in ("true", "1", "t")),
    listen=os.getenv("LISTEN", "0.0.0.0:5080"),
    threads=int(os.getenv("THREADS", "4")),
)

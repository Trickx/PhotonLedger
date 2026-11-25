#!/usr/bin/env python
"""Development server: python app.py  (or: flask --app app.py run)"""
from webapp import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5080)

"""Tests never read the developer's config.json (it may name real archives): set before webapp is imported."""
import os

os.environ["PHOTONLEDGER_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-config.json")

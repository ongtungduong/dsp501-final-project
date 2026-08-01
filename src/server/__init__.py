"""HTTP API for the Shazam clone.

A thin FastAPI shell over ``shazam.matcher.identify``: no DSP logic lives
here, only request handling, configuration, and process wiring.
"""

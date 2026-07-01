"""GIMP bridge: protocol, external client, GIMP-side server, and launcher.

The bridge abstracts whether GIMP is live (user-launched) or headless
(server-launched). Tools talk only to `BridgeClient`; they never know the mode.
"""

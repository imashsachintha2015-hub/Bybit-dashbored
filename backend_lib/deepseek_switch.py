"""The one switch for every DeepSeek call this project can make.

DeepSeek is OFF unless DEEPSEEK_ENABLED is 1/true/yes/on. While it is off nothing in this repo sends a
request to the provider: a DEEPSEEK_API_KEY still sitting in .env or in the host's variables (Railway) is
ignored, and each feature uses the local fallback it already had -- keyword news scoring, the local quant
market read, WAIT on a chart read, default post-trade notes.

server.py, deepseek_market_reader.py and market_knowledge.py all read their key through deepseek_key(), so
this is the only place that decides.
"""
import os

_ON = ("1", "true", "yes", "on")


def deepseek_enabled(env=None):
    env = os.environ if env is None else env
    return str(env.get("DEEPSEEK_ENABLED") or "").strip().lower() in _ON


def deepseek_key(env=None):
    """The API key to send, or "" when DeepSeek is switched off or no key is set."""
    env = os.environ if env is None else env
    if not deepseek_enabled(env):
        return ""
    return str(env.get("DEEPSEEK_API_KEY") or "").strip()

"""Explicit opt-in live call; default is offline mock."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.agent.models import Message, ModelRequest, ProviderError
from packages.agent.providers import MockProvider, from_environment


async def run(live):
    provider = None
    try:
        provider = from_environment() if live else MockProvider()
        request = ModelRequest(
            messages=[Message(role="user", content="Reply with exactly: OK")], max_output_tokens=32
        )
        result = await provider.generate(request)
        print(result.model_dump_json())
        return 0
    except ProviderError as exc:
        print(
            json.dumps(
                {
                    "mode": "live" if live else "mock",
                    "error": exc.code,
                    "retryable": exc.retryable,
                    "request_id": exc.request_id,
                }
            )
        )
        return 1
    finally:
        if live and provider is not None:
            await provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    sys.exit(asyncio.run(run(parser.parse_args().live)))

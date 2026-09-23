"""Check settings, or make one explicit synthetic request with --live. No dataset."""
import argparse
import sys
import time

from .config import AIConfig
from .errors import AIUnavailable
from .provider import ChatCompletionsTransport, strict_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send one small synthetic API request; uses API credits")
    args = parser.parse_args(argv)
    try:
        config = AIConfig.from_env()
        config.endpoint()
        if args.live:
            response = ChatCompletionsTransport(config).complete(
                system='Return JSON {"ok":true}. This is a connection test with no financial data.',
                payload={"check": "connection_only"},
                schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"], "additionalProperties": False},
                deadline=time.monotonic() + config.timeout_seconds, max_tokens=1024)
            result = strict_json(response)
            if type(result) is not dict or set(result) != {"ok"} or result["ok"] is not True:
                raise AIUnavailable("invalid_response")
        # Do not print the key, configured URL, provider response, or prompt.
        print("AI: подключение проверено без финансовых данных." if args.live
              else "AI: настройки проверены. Сетевых запросов не было; для проверки доступа добавьте --live.")
        return 0
    except AIUnavailable as error:
        print(f"AI: {error.code}. Ключ и финансовые данные не выводятся.", file=sys.stderr)
        return 2
    except (ValueError, TypeError, RecursionError):
        print("AI: invalid_response.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
import sys

import uvicorn

from .app import create_app
from .demo import demo_snapshot


def main():
    parser = argparse.ArgumentParser(description='Local Money Graph API, UI and file import')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', help='Completed run directory or snapshot JSON')
    source.add_argument('--demo', action='store_true', help='Explicitly synthetic UI data')
    source.add_argument('--empty', action='store_true', help='Open the interface to upload a new dataset')
    parser.add_argument('--static-dir', help='Optional frontend/dist directory for an installed package')
    parser.add_argument('--data', help='Optional raw Parquet directory for graph/transactions')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    try:
        app = create_app(demo_snapshot() if args.demo else args.snapshot, data_dir=args.data, static_dir=args.static_dir)
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f'Cannot load snapshot: {exc}', file=sys.stderr)
        raise SystemExit(2) from exc
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()

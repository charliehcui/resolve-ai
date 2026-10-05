"""Run unmodified simulator services in an evaluation-only environment."""
import argparse
import os
from pathlib import Path


def configure_tokens() -> None:
    from simulator.services import common

    common.SERVICE_TOKEN_FILE = Path(os.environ["EVAL_SERVICE_TOKEN_FILE"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=["merchant", "platform", "warehouse", "worker", "proxy"], required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--upstream")
    args = parser.parse_args()
    configure_tokens()
    if args.service == "worker":
        from simulator.services.worker import recover_interrupted_tasks, run_forever

        if os.getenv('EVAL_WORKER_READY_FILE'):
            recover_interrupted_tasks()
            Path(os.environ['EVAL_WORKER_READY_FILE']).write_text('startup recovery completed', encoding='utf-8')
        run_forever(0.05)
    elif args.service == "proxy":
        from evals.proxy import serve_proxy

        serve_proxy(args.port, args.upstream)
    else:
        import uvicorn

        uvicorn.run(f"simulator.services.{args.service}:app", host="127.0.0.1", port=args.port, log_level="error", access_log=False)


if __name__ == "__main__":
    main()

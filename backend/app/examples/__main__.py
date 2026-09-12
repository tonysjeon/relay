import argparse
import math

from app import relay
from app.examples.linear import linear_workflow
from app.examples.retry import retry_workflow
from app.services.queue import JOB_QUEUE
from app.workers.parallel import parallel_workflow
from app.workers.recovery_demo import recovery_workflow

EXAMPLES = {
    "linear": linear_workflow,
    "parallel": parallel_workflow,
    "retry": retry_workflow,
    "crash": recovery_workflow,
}


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seconds must be a positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("seconds must be a positive finite number")
    return seconds


def main() -> None:
    parser = argparse.ArgumentParser(description="Start a Relay example workflow")
    parser.add_argument("example", choices=EXAMPLES)
    parser.add_argument("--company", default="Stripe")
    parser.add_argument(
        "--seconds",
        type=positive_seconds,
        default=30,
        help="Duration of the crash example's long-running step",
    )
    parser.add_argument("--queue", default=JOB_QUEUE)
    args = parser.parse_args()
    run_id = relay.run(
        EXAMPLES[args.example],
        {"company": args.company, "seconds": args.seconds},
        queue_name=args.queue,
    )
    print(run_id)


if __name__ == "__main__":
    main()

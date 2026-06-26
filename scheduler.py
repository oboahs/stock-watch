from __future__ import annotations

import argparse
import time

import schedule

from config_loader import load_watchlist
from main import run_daily_pipeline
from utils import LOGGER, load_environment


def schedule_jobs(send_email: bool = False) -> None:
    load_environment()
    config = load_watchlist()
    scheduler_cfg = config.get("scheduler", {})
    times = scheduler_cfg.get("times") or ["08:30", "12:30", "16:00"]
    for run_time in times:
        schedule.every().day.at(str(run_time)).do(run_daily_pipeline, send_email=send_email)
        LOGGER.info("scheduled daily pipeline at %s", run_time)


def run_scheduler(send_email: bool = False) -> None:
    schedule_jobs(send_email=send_email)
    while True:
        schedule.run_pending()
        time.sleep(30)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stock Watch Assistant scheduler")
    parser.add_argument("--send-email", action="store_true", help="send report email after scheduled runs")
    parser.add_argument("--run-once", action="store_true", help="run once immediately and exit")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.run_once:
        run_daily_pipeline(send_email=args.send_email)
    else:
        run_scheduler(send_email=args.send_email)


#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run every paired campaign condition, or replay one saved run's inputs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dropnet import Contact, Message
from dropnet.experiments import campaign, run_one


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=30)
    parser.add_argument('--output', type=Path, help='new output folder (must not exist)')
    parser.add_argument('--replay', type=Path, help='saved inputs JSON')
    parser.add_argument('--policy', choices=['direct', 'epidemic', 'inventory', 'compact'], default='epidemic')
    parser.add_argument('--loss', type=float, default=0)
    parser.add_argument('--capacity', type=int, default=65536)
    args = parser.parse_args()
    if args.replay:
        data = json.loads(args.replay.read_text(encoding='utf-8'))
        metrics = run_one([Message(**m) for m in data['messages']],
                          [Contact(**(c | {'loss': args.loss})) for c in data['contacts']],
                          args.policy, 100000 + data['seed'], args.capacity, 3600)
        print(json.dumps(metrics, indent=2))
    else:
        folder = args.output or Path(__file__).resolve().parent / 'outputs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        print(campaign(folder, args.seeds).resolve())


if __name__ == '__main__':
    main()

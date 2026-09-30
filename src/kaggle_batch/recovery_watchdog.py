"""11-minute scheduler tick for the Kaggle-only campaign.

The scheduler, not this wrapper, owns lane health, rotation and recovery policy.
"""
import json
from lane_scheduler import tick
from snapshot_retention import locked_prune


def main():
    try:
        retention=locked_prune()
    except Exception as exc:
        retention={'state':'error','error_type':type(exc).__name__}
    report=tick()
    report['snapshot_retention']=retention
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()

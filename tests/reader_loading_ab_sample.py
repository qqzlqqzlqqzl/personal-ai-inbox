"""One full A/B sample; this alone never constitutes a five-pair comparison."""
import argparse
import reader_loading_performance

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair-index', type=int, choices=range(1, 6), required=True)
    args, rest = parser.parse_known_args()
    raise SystemExit(reader_loading_performance.main(rest, pair_index=args.pair_index))

"""Command-line entry point."""
import argparse


def main() -> None:
    """Run a reproducible masonry demonstration."""
    parser = argparse.ArgumentParser(description="MasonMind robotic masonry research demo")
    parser.add_argument("--milestone", type=int, default=10, choices=range(1, 11))
    parser.add_argument("--scenario", default="clean")
    parser.add_argument("--variant", default="B3", choices=["B0", "B1", "B2", "B3"])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--text", default="A small three-course wall")
    parser.add_argument("--no-video", action="store_true")
    args = parser.parse_args()
    from masonmind.demo import run_demo
    run_demo(args)


if __name__ == "__main__":
    main()

"""Launch the optional local-only review/build interface."""
import argparse
from masonmind.config import ROOT


def main() -> None:
    """Serve the UI on loopback; public sharing is disabled."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    from masonmind.ui import create_app
    create_app().queue().launch(server_name="127.0.0.1", server_port=args.port, share=False,
                                allowed_paths=[str((ROOT / "runs").resolve())], inbrowser=False)


if __name__ == "__main__":
    main()

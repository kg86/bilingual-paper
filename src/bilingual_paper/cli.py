from __future__ import annotations

import argparse

from . import arxiv


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bilingual-paper",
        description="Translate an arXiv HTML paper into a bilingual (English + target language) HTML page.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    arxiv_parser = commands.add_parser(
        "arxiv",
        help="translate an arXiv HTML paper into a bilingual HTML page",
        description=arxiv.DESCRIPTION,
    )
    arxiv.add_arguments(arxiv_parser)
    args = parser.parse_args()
    arxiv.run(args)


if __name__ == "__main__":
    main()

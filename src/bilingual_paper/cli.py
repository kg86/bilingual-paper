from __future__ import annotations

import argparse

from . import arxiv, pdf


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bilingual-paper",
        description="Translate an arXiv HTML paper or a PDF paper into a bilingual (English + target language) HTML page.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    arxiv_parser = commands.add_parser(
        "arxiv",
        help="translate an arXiv HTML paper into a bilingual HTML page",
        description=arxiv.DESCRIPTION,
    )
    arxiv.add_arguments(arxiv_parser)
    arxiv_parser.set_defaults(run=arxiv.run)
    pdf_parser = commands.add_parser(
        "pdf",
        help="translate a PDF paper into a bilingual HTML page",
        description=pdf.DESCRIPTION,
    )
    pdf.add_arguments(pdf_parser)
    pdf_parser.set_defaults(run=pdf.run)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()

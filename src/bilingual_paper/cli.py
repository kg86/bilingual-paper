from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from . import arxiv
from .audit import audit, audit_source
from .clean import clean_inventory
from .gemini import DEFAULT_MODEL
from .html import render
from .inventory import extract
from .io import load, load_target_lang, save
from .languages import DEFAULT_LANG, LANGUAGES
from .translate import translate_inventory

LANG_CHOICES = sorted(LANGUAGES)


def main() -> None:
    parser = argparse.ArgumentParser(prog="bilingual-paper", description="Bilingual (English + target language) versions of papers: a PDF pipeline (inventory -> clean -> translate -> render -> audit) and `arxiv` for arXiv HTML papers.")
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory"); inventory.add_argument("pdf", type=Path); inventory.add_argument("output", type=Path)
    cleaner = commands.add_parser("clean"); cleaner.add_argument("inventory", type=Path); cleaner.add_argument("output", type=Path); cleaner.add_argument("--model", default=DEFAULT_MODEL); cleaner.add_argument("--batch-size", type=int, default=12)
    template = commands.add_parser("translate-template"); template.add_argument("inventory", type=Path); template.add_argument("output", type=Path); template.add_argument("--target-lang", default=DEFAULT_LANG, choices=LANG_CHOICES)
    translator = commands.add_parser("translate"); translator.add_argument("inventory", type=Path); translator.add_argument("output", type=Path); translator.add_argument("--model", default=DEFAULT_MODEL); translator.add_argument("--batch-size", type=int, default=12); translator.add_argument("--target-lang", default=None, choices=LANG_CHOICES, help=f"default: the output file's recorded language, else {DEFAULT_LANG}")
    renderer = commands.add_parser("render"); renderer.add_argument("inventory", type=Path); renderer.add_argument("output", type=Path); renderer.add_argument("--title", default="Bilingual PDF Reader"); renderer.add_argument("--target-lang", default=None, choices=LANG_CHOICES, help="default: read from the inventory file")
    checker = commands.add_parser("audit"); checker.add_argument("inventory", type=Path); checker.add_argument("html", type=Path)
    source_checker = commands.add_parser("audit-source"); source_checker.add_argument("pdf", type=Path); source_checker.add_argument("inventory", type=Path)
    arxiv_parser = commands.add_parser("arxiv", help="translate an arXiv HTML paper into a bilingual HTML page", description=arxiv.DESCRIPTION); arxiv.add_arguments(arxiv_parser)
    args = parser.parse_args()
    if args.command == "arxiv": arxiv.run(args)
    elif args.command == "inventory": save(args.output, extract(args.pdf))
    elif args.command == "clean": clean_inventory(load(args.inventory), args.output, model=args.model, batch_size=args.batch_size)
    elif args.command == "translate-template": save(args.output, [replace(p, translation="") for p in load(args.inventory)], target_lang=args.target_lang)
    elif args.command == "translate": translate_inventory(load(args.inventory), args.output, model=args.model, batch_size=args.batch_size, target_lang=args.target_lang)
    elif args.command == "render": render(load(args.inventory, require_translation=True), args.output, args.title, target_lang=args.target_lang or load_target_lang(args.inventory))
    elif args.command == "audit":
        failures = audit(load(args.inventory, require_translation=True), args.html)
        if failures: raise SystemExit("\n".join(failures))
        print("coverage audit passed")
    else:
        failures = audit_source(args.pdf, load(args.inventory))
        if failures: raise SystemExit("\n".join(failures))
        print("source audit passed")


if __name__ == "__main__": main()

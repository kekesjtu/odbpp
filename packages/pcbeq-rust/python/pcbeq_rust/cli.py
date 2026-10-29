from . import backend


def main(argv=None):
    from pcbeq.cli import build_parser
    from pcbeq.application import run
    from pcbeq.diagnostics import Diagnostics
    parser = build_parser(prog="pcbeq-rust")
    with backend():
        return run(parser.parse_args(argv), Diagnostics())

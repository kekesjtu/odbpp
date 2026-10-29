from . import backend


def main(argv=None):
    from pcbkit.cli import main as run
    with backend():
        return run(argv, prog="pcbkit-rust")

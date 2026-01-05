from argparse import Namespace
from dataclasses import dataclass


@dataclass
class Rules(object):
    add_meta: bool
    condense_lines: bool
    condense_lines_sparse: bool

    tag: str | None

    @classmethod
    def from_args(cls, args: Namespace) -> "Rules":
        args_map = {
            arg_name: getattr(args, arg_name) for arg_name in cls.__dataclass_fields__
        }

        return cls(**args_map)

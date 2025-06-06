from dataclasses import dataclass


@dataclass
class Flags:
    force_save: bool = False
    include_participate_in_works: bool = False

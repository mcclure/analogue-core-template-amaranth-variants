from pathlib import Path

from amaranth.back import verilog

from .platform import IntelPlatform
from .toplevel import Toplevel


toplevel = Toplevel()
with open(Path(__file__).parent.parent.parent / "core" / "amaranth_core.v", "w") as f:
    f.write(verilog.convert(toplevel, platform=IntelPlatform, name="amaranth_core", strip_internal_attrs=True))

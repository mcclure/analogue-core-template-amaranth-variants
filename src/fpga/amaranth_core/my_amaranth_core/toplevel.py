from amaranth import *
from amaranth.lib import wiring, data
from amaranth.lib.wiring import In, Out


class Toplevel(wiring.Component):
    clk             : In(1)
    rst             : In(1)
    init_done       : Out(1)

    user1           : Out(1)
    user2           : In(1)

    dbg_tx          : Out(1)
    dbg_rx          : In(1)

    video_rgb_clk   : Out(1)
    video_rgb_clk90 : Out(1)
    video_rgb       : Out(data.StructLayout({"b": 8, "g": 8, "r": 8}))
    video_de        : Out(1)
    video_skip      : Out(1)
    video_vs        : Out(1)
    video_hs        : Out(1)

    audio_clk       : Out(1) # aka audio_mclk
    audio_sync      : Out(1) # aka audio_lrck
    audio_adc       : In(1)
    audio_dac       : Out(1)

    cont1_key       : In(32)
    cont2_key       : In(32)
    cont3_key       : In(32)
    cont4_key       : In(32)
    cont1_joy       : In(32)
    cont2_joy       : In(32)
    cont3_joy       : In(32)
    cont4_joy       : In(32)
    cont1_trig      : In(16)
    cont2_trig      : In(16)
    cont3_trig      : In(16)
    cont4_trig      : In(16)

    def elaborate(self, platform):
        m = Module()

        m.domains.boot = boot = ClockDomain(reset_less=True)
        m.domains.sync = sync = ClockDomain(async_reset=True)
        m.d.comb += [
            boot.clk.eq(self.clk),
            sync.clk.eq(self.clk),
            sync.rst.eq(self.rst),
        ]

        m.d.boot += self.init_done.eq(1)

        # ------------------------------ user code below this line --------------------------------

        video_rgb_r = Signal(4, reset=0b1100)
        m.d.sync += video_rgb_r.eq(Cat(video_rgb_r[3], video_rgb_r[:3]))
        m.d.comb += [
            self.video_rgb_clk.eq(video_rgb_r[0]),
            self.video_rgb_clk90.eq(video_rgb_r[1]),
        ]

        # clk90  __/¯¯¯\_
        # clk    /¯¯¯\___
        # rgb    X--------
        video_stb = Signal()
        m.d.comb += [
            video_stb.eq(~self.video_rgb_clk & ~self.video_rgb_clk90)
        ]

        VID_H_BPORCH = 10
        VID_H_ACTIVE = 800
        VID_H_TOTAL  = VID_H_ACTIVE + VID_H_BPORCH*2 + 5
        VID_V_BPORCH = 10
        VID_V_ACTIVE = 450
        VID_V_TOTAL  = VID_V_ACTIVE + VID_V_BPORCH*2 + 5

        assert 47 <= (74250000 / 4 / VID_V_TOTAL / VID_H_TOTAL) < 61, "Pixel clock out of range"

        with m.If(video_stb):
            x_count = Signal(10)
            y_count = Signal(10)

            m.d.sync += [
                self.video_vs.eq((x_count == 0) & (y_count == 0)),
                # HS must occur at least 3 cycles after VS
                self.video_hs.eq(x_count == 3),
            ]

            m.d.sync += x_count.eq(x_count + 1)
            with m.If(x_count == VID_H_TOTAL - 1):
                m.d.sync += x_count.eq(0)
                m.d.sync += y_count.eq(y_count + 1)
                with m.If(y_count == VID_V_TOTAL - 1):
                    m.d.sync += y_count.eq(0)

            m.d.sync += [
                # inactive screen areas must be black
                self.video_de.eq(0),
                self.video_rgb.eq(0)
            ]

            with m.If((x_count >= VID_H_BPORCH) & (x_count < VID_H_ACTIVE + VID_H_BPORCH)):
                with m.If((y_count >= VID_V_BPORCH) & (y_count < VID_V_ACTIVE + VID_V_BPORCH)): 
                    m.d.sync += self.video_de.eq(1)
                    def rgb(r,g,b):
                        return [self.video_rgb.r.eq(r), self.video_rgb.g.eq(g), self.video_rgb.b.eq(b)]
                    with m.If(y_count == VID_V_BPORCH):   # Top row red
                        m.d.sync += rgb(0xFF, 0, 0)
                    with m.Elif(y_count == VID_V_ACTIVE + VID_V_BPORCH - 1): # Bottom row yellow
                        m.d.sync += rgb(0xFF, 0xFF, 0x80)
                    with m.Elif(x_count == VID_H_BPORCH): # Left column green
                        m.d.sync += rgb(0, 0xFF, 0)
                    with m.Elif(x_count == VID_H_ACTIVE + VID_H_BPORCH - 1): # Right column blue
                        m.d.sync += rgb(0, 0, 0xFF)
                    with m.Elif(x_count[0] ^ y_count[0]): # Remaining pixels, alternate black
                        m.d.sync += rgb(0, 0, 0)
                    with m.Else():                        # ...and magenta
                        m.d.sync += rgb(0xa0, 0x00, 0x80) 

        return m


def simulate():
    from amaranth.sim import Simulator

    sim = Simulator(Toplevel())
    sim.add_clock(1/74.25e6)
    with sim.write_vcd("dump.vcd"):
        sim.run_until(10e-3, run_passive=True)


def capture_frame():
    import png
    from amaranth.sim import Simulator

    top = Toplevel()
    def bench():
        rows = []
        while not (yield top.video_vs): yield
        while (yield top.video_vs): yield
        # after negedge of vs
        while True:
            cols = []
            while not (yield top.video_hs): yield
            while not ((yield top.video_vs) or (yield top.video_de)): yield
            if (yield top.video_vs):
                break
            while True:
                while (yield top.video_rgb_clk90): yield
                while not (yield top.video_rgb_clk90): yield
                # at posedge of clk90
                if (yield top.video_de):
                    cols.append((yield top.video_rgb.r))
                    cols.append((yield top.video_rgb.g))
                    cols.append((yield top.video_rgb.b))
                else:
                    break
            print(f"row {len(rows)}: {len(cols) // 3} cols")
            rows.append(cols)
            with open("frame.png", "wb") as file:
                png.Writer(len(rows[0]) // 3, len(rows), greyscale=False).write(file, rows)
        print(f"{len(rows)} rows")

    sim = Simulator(top)
    sim.add_clock(1/74.25e6)
    sim.add_sync_process(bench)
    sim.run()


def generate():
    from pathlib import Path
    from amaranth.back import verilog
    from .platform import IntelPlatform

    toplevel = Toplevel()
    with open(Path(__file__).parent.parent.parent / "core" / "amaranth_core.v", "w") as f:
        f.write(verilog.convert(toplevel, platform=IntelPlatform, name="amaranth_core", strip_internal_attrs=True))

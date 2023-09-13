from amaranth import *
from amaranth.lib import wiring
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
    video_rgb       : Out(24)
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

        VID_V_BPORCH = 10
        VID_V_ACTIVE = 480
        VID_V_TOTAL  = 495
        VID_H_BPORCH = 10
        VID_H_ACTIVE = 600
        VID_H_TOTAL  = 625

        assert 47 <= (74250000 / 4 / VID_V_TOTAL / VID_H_TOTAL) < 61, "Pixel clock out of range"

        x_count     = Signal(10)
        y_count     = Signal(10)

        with m.If(video_stb):
            m.d.sync += [
                self.video_de.eq(0),
                self.video_skip.eq(0),
                self.video_vs.eq(0),
                self.video_hs.eq(0),
            ]

            m.d.sync += x_count.eq(x_count + 1)
            with m.If(x_count == VID_H_TOTAL - 1):
                m.d.sync += x_count.eq(0)
                m.d.sync += y_count.eq(y_count + 1)
                with m.If(y_count == VID_V_TOTAL - 1):
                    m.d.sync += y_count.eq(0)

            with m.If((x_count == 0) & (y_count == 0)):
                m.d.sync += self.video_vs.eq(1)
            
            # HS must occur at least 3 cycles after VS
            with m.If(x_count == 3):
                m.d.sync += self.video_hs.eq(1)

            # inactive screen areas must be black
            m.d.sync += self.video_rgb.eq(0)

            with m.If((x_count >= VID_H_BPORCH) & (x_count < VID_H_ACTIVE + VID_H_BPORCH)):
                with m.If((y_count >= VID_V_BPORCH) & (y_count <= VID_V_ACTIVE + VID_V_BPORCH)):
                    m.d.sync += [
                        self.video_de.eq(1),
                        self.video_rgb.eq(0xa00080)
                    ]

        return m


def simulate():
    from amaranth.sim import Simulator

    sim = Simulator(Toplevel())
    sim.add_clock(1/74.25e6)
    with sim.write_vcd("dump.vcd"):
        sim.run_until(10e-3, run_passive=True)

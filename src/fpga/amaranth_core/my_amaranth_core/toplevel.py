from amaranth import *
from amaranth.lib import wiring, data
from amaranth.lib.wiring import In, Out
import enum


class PixelClockDiv(wiring.Component):
    clk90   : Out(1) # Pixel clock, 90 deg trailing
    clk     : Out(1) # Pixel clock
    stb     : Out(1) # Single cycle strobe at rising edge of `clk`

    def __init__(self, ratio=4):
        super().__init__()

        assert ratio >= 4 and ratio % 4 == 0, "Ratio must be at least 4 and divisible by 4"
        self.ratio = ratio

    def elaborate(self, platform):
        m = Module()

        # clk90  __/¯¯¯\_
        # clk    /¯¯¯\___
        # rgb    X--------
        # Note clock rises one cycle AFTER rgb strobe

        # Generate bitmap with ratio/2 0s (low order) followed by ratio/2 1s (high order)
        clk_reg = Signal(self.ratio, reset=((1 << (self.ratio // 2)) - 1) << (self.ratio // 2))
        m.d.sync += clk_reg.eq(clk_reg.rotate_left(1))
        m.d.comb += [
            self.clk.eq(clk_reg[0]),
            self.clk90.eq(clk_reg[self.ratio // 4]),
        ]

        stb_reg = Signal(self.ratio, reset=1)
        m.d.sync += stb_reg.eq(stb_reg.rotate_left(1))
        m.d.comb += [
            self.stb.eq(stb_reg[0])
        ]

        return m

class AudioClockDiv(wiring.Component):
    stb_update     : Out(1) # Single cycle strobe
    stb            : Out(1)        # Single cycle strobe delayed by 1 cycle

    def __init__(self, ratio=2):
        super().__init__()

        assert ratio >= 2 and ratio % 2 == 0, "Ratio must be at least 2 and divisible by 2"
        self.ratio = ratio

    def elaborate(self, platform):
        m = Module()

        stb_reg_update = Signal(self.ratio, reset=1)
        m.d.sync += stb_reg_update.eq(stb_reg_update.rotate_right(1)) # Rotates right where pixel rotates left
        m.d.comb += [
            self.stb_update.eq(stb_reg_update[0])
        ]

        stb_reg = Signal(self.ratio, reset=2)
        m.d.sync += stb_reg.eq(stb_reg.rotate_right(1))
        m.d.comb += [
            self.stb.eq(stb_reg[0])
        ]

        return m


class RenderState(enum.Enum):
    TOP    = 0
    LEFT   = 1
    BOTTOM = 2
    RIGHT  = 3
    CURRENT = 4
    NEXT = 5

class RenderMode(enum.Enum):
    PLAIN = 0
    VERT = 1
    HORIZ = 2
    CHECKER = 3

ANIMATION_COUNTER_SIZE = 64
ANIMATION_COUNTER_MAX = 63
ROTATE_COUNTER_MAX = 3

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

    def __init__(self, *args):
        self.animation_counter = Signal(6) # 0..63 counter
        self.rotate1_counter   = Signal(2) # 0..3 counter
        self.rotate2_counter   = Signal(2) # 0..3 counter
        super().__init__(*args)

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

        # Video

        m.submodules.video_clk_div = video_clk_div = PixelClockDiv(ratio=8)
        m.d.comb += [
            self.video_rgb_clk.eq(video_clk_div.clk),
            self.video_rgb_clk90.eq(video_clk_div.clk90),
        ]

        # ~9.281 mhz clock; 60.022 fps
        VID_H_BPORCH = 4
        VID_H_ACTIVE = 400
        VID_H_TOTAL  = 408
        VID_V_BPORCH = 29
        VID_V_ACTIVE = 320
        VID_V_TOTAL  = 379

        assert 47 <= (74250000 / video_clk_div.ratio / VID_V_TOTAL / VID_H_TOTAL) < 61, "Pixel clock out of range"

        x_count = Signal(10)
        y_count = Signal(10)

        # Partial results for colors
        render_state = Signal(Shape.cast(RenderState))
        current_color_id = Signal(Shape.cast(RenderMode))
        next_color_id = Signal(Shape.cast(RenderMode))
        flash_color = Signal(24)
        current_flash_on = Signal(1)
        next_flash_on = Signal(1)
        rotate2_counter_anti = Signal(2)

        m.d.comb += rotate2_counter_anti.eq(ROTATE_COUNTER_MAX - self.rotate2_counter)

        def rgb(r,g,b):
            return [self.video_rgb.r.eq(r), self.video_rgb.g.eq(g), self.video_rgb.b.eq(b)]

        val = Const(VID_V_BPORCH, y_count.shape())
        with m.If((y_count >= val) & (y_count <= val + rotate2_counter_anti)):   # Top row red
            m.d.comb += render_state.eq(RenderState.TOP)
        
        val = Const(VID_V_ACTIVE + VID_V_BPORCH - 1, y_count.shape())
        with m.Elif((y_count <= val) & (y_count >= val - rotate2_counter_anti)): # Bottom row yellow
            m.d.comb += render_state.eq(RenderState.BOTTOM)

        val = Const(VID_H_BPORCH, x_count.shape())
        with m.Elif((x_count >= val) & (x_count <= val + rotate2_counter_anti)): # Left column green
            m.d.comb += render_state.eq(RenderState.LEFT)

        val = Const(VID_H_ACTIVE + VID_H_BPORCH - 1, x_count.shape())
        with m.Elif((x_count <= val) & (x_count >= val - rotate2_counter_anti)): # Right column blue
            m.d.comb += render_state.eq(RenderState.RIGHT)

        with m.Elif(y_count - VID_V_BPORCH > self.animation_counter * (VID_V_ACTIVE // ANIMATION_COUNTER_SIZE)):
            m.d.comb += render_state.eq(RenderState.NEXT)
        with m.Else(): # Remaining pixels, alternate black
            m.d.comb += render_state.eq(RenderState.CURRENT)

        m.d.comb += [
            current_color_id.eq(self.rotate1_counter+1),
            next_color_id.eq(self.rotate1_counter)
        ]

        with m.If(self.rotate1_counter[0] ^ self.rotate2_counter[0]):
            m.d.comb += flash_color.eq(0x0)
        with m.Else():
            m.d.comb += flash_color.eq(0xFFFFFF)

        for [flash_on, id] in [[current_flash_on, current_color_id], [next_flash_on, next_color_id]]:
            with m.Switch(id):
                with m.Case(RenderMode.PLAIN):
                    m.d.comb += flash_on.eq(0)
                with m.Case(RenderMode.VERT):
                    m.d.comb += flash_on.eq(x_count[0])
                with m.Case(RenderMode.HORIZ):
                    m.d.comb += flash_on.eq(y_count[0])
                with m.Case(RenderMode.CHECKER):
                    m.d.comb += flash_on.eq(x_count[0] ^ y_count[0])

        with m.If(video_clk_div.stb):
            # Decide color for next pixel
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

                    # NEW FRAME LOGIC
                    m.d.sync += self.animation_counter.eq(self.animation_counter + 1)

                    with m.If(self.animation_counter == ANIMATION_COUNTER_MAX):                    
                        m.d.sync += self.rotate1_counter.eq(self.rotate1_counter + 1)
                        with m.If(self.rotate1_counter == ROTATE_COUNTER_MAX):
                            m.d.sync += self.rotate2_counter.eq(self.rotate2_counter + 1)

            m.d.sync += [
                # inactive screen areas must be black
                self.video_de.eq(0),
                self.video_rgb.eq(0)
            ]

            with m.If((x_count >= VID_H_BPORCH) & (x_count < VID_H_ACTIVE + VID_H_BPORCH)):
                with m.If((y_count >= VID_V_BPORCH) & (y_count < VID_V_ACTIVE + VID_V_BPORCH)):
                    m.d.sync += self.video_de.eq(1)
                    with m.Switch(render_state):
                        with m.Case(RenderState.TOP): # Red
                            m.d.sync += rgb(0xFF, 0, 0)
                        with m.Case(RenderState.BOTTOM): # Yellow
                            m.d.sync += rgb(0xFF, 0xFF, 0x80)
                        with m.Case(RenderState.LEFT): # Green
                            m.d.sync += rgb(0, 0xFF, 0)
                        with m.Case(RenderState.RIGHT): # Blue
                            m.d.sync += rgb(0, 0, 0xFF)
                        for [case, flash_on, invert] in [[RenderState.CURRENT, current_flash_on, 0x0], [RenderState.NEXT, next_flash_on, 0xFFFFFF]]:
                            with m.Case(case):
                                with m.If(flash_on): # Remaining pixels, alternate black
                                    m.d.sync += self.video_rgb.eq(flash_color^invert)
                                with m.Else():
                                    m.d.sync += rgb(0xa0, 0x00, 0x80) # Magenta

        # Audio

        # Recreate Analogue i2s protocol from core_top.v

        m.submodules.i2s_clk_div = i2s_clock_div = AudioClockDiv(ratio=4)

        audgen_accum = Signal(22)
        audgen_mclk = Signal(1)
        CYCLE_48KHZ = Const(122880 * 2, Shape(width=22))
        CYCLE_OVERFLOW = Const(742500, Shape(width=22))

        m.d.sync += audgen_accum.eq(audgen_accum + CYCLE_48KHZ)
        with m.If(audgen_accum >= CYCLE_OVERFLOW):
            m.d.sync += [
                audgen_mclk.eq(~audgen_mclk),
                audgen_accum.eq(audgen_accum - CYCLE_OVERFLOW + CYCLE_48KHZ)
            ]

        audgen_sclk_stb_update = Signal(1)
        audgen_sclk_stb = Signal(1)
        m.d.comb += [
            audgen_sclk_stb.eq(i2s_clock_div.stb),
            audgen_sclk_stb_update.eq(i2s_clock_div.stb_update)
        ]

        audgen_lrck_cnt = Signal(5)
        audgen_lrck = Signal(1)     # Serial clock
        audgen_dac = Signal(1)      # Output value

        # User logic
        audgen_osc = Signal(8)      # Counter for square wave
        audgen_high = Signal(1)     # High when square wave high

        with m.If(audgen_sclk_stb_update): # Update late as possible (could do so as early as implied falling edge...)
            m.d.sync += audgen_dac.eq( Mux(audgen_lrck_cnt < 4, 0, audgen_high) )

            # 48khz * 64
            m.d.sync += audgen_lrck_cnt.eq( audgen_lrck_cnt + 1 )

            
            with m.If(audgen_lrck_cnt == 31): # Audio logic on final lrck tick before rise (1 cycle before stb_update)
                m.d.sync += audgen_lrck.eq( ~audgen_lrck )

                # User logic
                with m.If(audgen_osc < 109): # Alternating every 109 stereo samples gets us on average a wave-cycle every 109 audio frames = 440hz
                    m.d.sync += audgen_osc.eq( audgen_osc + 1 )
                with m.Else():
                    m.d.sync += [
                        audgen_osc.eq( 1 ), # note audgen_osc is currently EQUAL to 109
                        audgen_high.eq( ~audgen_high )
                    ]

        # Module output
        m.d.comb += [
            self.audio_clk.eq(audgen_mclk),  # Clock
            self.audio_dac.eq(audgen_dac),   # Output
            self.audio_sync.eq(audgen_lrck), # Word select ("Active")
        ]

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

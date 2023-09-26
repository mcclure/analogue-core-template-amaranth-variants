from amaranth import *
from amaranth.lib import wiring, data
from amaranth.lib.wiring import In, Out
import enum

# ~9.281 mhz clock; 60.022 fps
VID_DIV_RATIO = 8
VID_H_BPORCH = 4
VID_H_ACTIVE = 400
VID_H_TOTAL  = 408
VID_V_BPORCH = 29
VID_V_ACTIVE = 320
VID_V_TOTAL  = 379

assert 47 <= (74250000 / VID_DIV_RATIO / VID_V_TOTAL / VID_H_TOTAL) < 61, "Pixel clock out of range"

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

    audio_mclk      : Out(1)
    audio_lrck      : Out(1) # A better name would be audio_select but this is what the i2s standard calls it
    audio_adc       : In(1)  # Unused
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

        # Video interface

        m.submodules.video_clk_div = video_clk_div = PixelClockDiv(ratio=VID_DIV_RATIO)
        m.d.comb += [
            self.video_rgb_clk.eq(video_clk_div.clk),
            self.video_rgb_clk90.eq(video_clk_div.clk90),
        ]

        video_x_count = Signal(10)
        video_y_count = Signal(10)

        # Audio interface

        audgen_silenced = Signal(1)
        audgen_channel_select = Signal(1)
        audgen_channel_internal = Signal(4)
        audgen_bit_update_stb = Signal(1)
        audgen_word_update_stb = Signal(1)
        audgen_dac = Signal(1)

        # App interface

        video_hsync_stb = Signal(1)
        video_vsync_stb = Signal(1)
        video_active = Signal(1)

        m.d.comb += [
            video_hsync_stb.eq(video_clk_div.stb & (video_x_count == VID_H_TOTAL - 1)),
            video_vsync_stb.eq(video_clk_div.stb & (video_x_count == VID_H_TOTAL - 1) & (video_y_count == VID_V_TOTAL - 1)),
            video_active.eq((video_x_count >= VID_H_BPORCH) & (video_x_count < VID_H_ACTIVE + VID_H_BPORCH) &
                (video_y_count >= VID_V_BPORCH) & (video_y_count < VID_V_ACTIVE + VID_V_BPORCH))
        ]

        self.app_elaborate(platform, m,
            video_clk_div.stb, video_hsync_stb, video_vsync_stb, video_x_count, video_y_count, video_active, self.video_rgb,
            audgen_silenced, audgen_channel_select, audgen_channel_internal, audgen_bit_update_stb, audgen_word_update_stb, audgen_dac)

        # Draw

        with m.If(video_clk_div.stb):
            # Vertical and horizontal sync
            m.d.sync += [
                self.video_vs.eq((video_x_count == 0) & (video_y_count == 0)),
                # HS must occur at least 3 cycles after VS
                self.video_hs.eq(video_x_count == 3),
            ]

            # Iterate screen "beam"
            m.d.sync += video_x_count.eq(video_x_count + 1)
            with m.If(video_x_count == VID_H_TOTAL - 1):
                m.d.sync += video_x_count.eq(0)
                m.d.sync += video_y_count.eq(video_y_count + 1)
                with m.If(video_y_count == VID_V_TOTAL - 1):
                    m.d.sync += video_y_count.eq(0)

            # inactive screen areas must be black
            m.d.sync += [
                self.video_de.eq(video_active)
            ]

        # Audio

        # Recreate Analogue i2s protocol from core_top.v
        # FIXME: This could be done more elegantly with Amaranth builtins instead of logic

        # Clocks

        CYCLE_48KHZ = Const(122880 * 2, Shape(width=22))
        CYCLE_OVERFLOW_VALUE = 742500
        CYCLE_OVERFLOW = Const(CYCLE_OVERFLOW_VALUE, Shape(width=22))

        audgen_accum = Signal(22, reset=CYCLE_OVERFLOW_VALUE) # Master clock
        audgen_mclk = Signal(1)
        audgen_mclk_stb = Signal(1) # Trigger on first cycle (because of accum value)

        audgen_slck_count = Signal(2, reset=3) # Serial clock # TODO: Collapse slck_count into lrck_count?
        audgen_slck = Signal(1)
        audgen_slck_update = Signal(1, reset=1) # Trigger on first cycle

        audgen_lrck_count = Signal(8) # Left-right select
        audgen_lrck = Signal(1)

        # "Master clock"
        # This produces a cycle of 1/48000/256 seconds
        m.d.sync += audgen_accum.eq(audgen_accum + CYCLE_48KHZ)
        m.d.comb += audgen_mclk_stb.eq(audgen_accum >= CYCLE_OVERFLOW)
        with m.If(audgen_mclk_stb):
            m.d.sync += [
                audgen_mclk.eq(~audgen_mclk),
                audgen_accum.eq(audgen_accum - CYCLE_OVERFLOW + CYCLE_48KHZ)
            ]

        # "Serial clock"
        # 4x period master clock, produces a cycle of 1/48000/64 seconds
        m.d.comb += audgen_slck.eq( ~audgen_slck_count[1] ) # Use counter bit as clock
        m.d.sync += audgen_slck_update.eq(0) # Update strobe is usually 0

        with m.If(audgen_mclk_stb & (~audgen_mclk)):
            m.d.sync += [
                audgen_slck_count.eq( audgen_slck_count + 1 )
            ]
            with m.If(audgen_slck_count == 2): # We are halfway through slck low, so this is a good time to run updates.
                m.d.sync += [ # FIXME: Would probably be ok to move this forward or backward?
                    audgen_slck_update.eq(1)
                ]

        # "Left-right clock" (channel select)
        # 256x period master clock / 64x period serial clock, cycle is audio-rate 48khz
        m.d.comb += audgen_lrck.eq(audgen_lrck_count[7]) # Use counter bit as clock
        with m.If(audgen_mclk_stb & (~audgen_mclk)):
            m.d.sync += [
                audgen_lrck_count.eq( audgen_lrck_count + 1 )
            ]

        # Audio generate

        m.d.comb += [ # For app interface
            audgen_channel_select.eq(audgen_lrck),
            audgen_word_update_stb.eq(0), # Will override below
            audgen_bit_update_stb.eq(0), # Will override below
        ]

        # Bits of audgen_lrck_count: ABCCCCDD
        # D: audgen_slck_count equivalent; C: audgen_channel_internal; B: audgen_silenced; A: audgen_lrck
        audgen_lrck_internal = Signal(5)
        m.d.comb += [
            audgen_channel_internal.eq(audgen_lrck_count[2:6]),
            audgen_silenced.eq(audgen_lrck_count[6]),
            audgen_lrck_internal.eq(audgen_lrck_count[2:7]) # BCCCC (audgen_channel_internal + audgen_silenced)
        ]

        with m.If(audgen_slck_update): # Update late as possible (could do so as early as implied falling edge...)
            m.d.comb += audgen_bit_update_stb.eq(1)

            with m.If(audgen_lrck_internal == 23): # Audio logic halfway through "silenced" period (FIXME could move forward or back-- Analogue sample code did this on lrck falling edge)
                m.d.comb += audgen_word_update_stb.eq(1)

        # Module output
        m.d.comb += [
            self.audio_mclk.eq(audgen_mclk),     # Master clock-- 4x the serial clock or 256x select
            self.audio_dac.eq(audgen_dac),       # Output
            self.audio_lrck.eq(audgen_lrck),     # Word select (channel)
        ]

        return m

    # "User logic"
    def app_elaborate(self, platform, m,
            video_pixel_stb, video_hsync_stb, video_vsync_stb, video_x_count, video_y_count, video_active, video_rgb_out,
            audio_silenced, audio_channel_select, audio_channel_internal, audio_bit_update_stb, audio_word_update_stb, audio_dac_out):
        # App: Test pattern
        # 3 effects:
        #     * Outer sides are red, green, yellow and blue. Every 4 "beats" they grow narrower, until on the 4th beat they are 1 pixel wide.
        #     * Every "beat" the background alternates (vertical wipe) between magenta, vertical stripes, horizontal stripes, and checkerboard (stripes and checkers alternate black and white)
        #     * Every "beat" a square wave C note rises, on beat 4 it rises one octave higher.

        # Setup

        animation_counter = Signal(6) # 0..63 counter
        rotate1_counter   = Signal(2) # 0..3 counter
        rotate2_counter   = Signal(2) # 0..3 counter

        # Partial results for colors
        render_state = Signal(Shape.cast(RenderState))
        current_color_id = Signal(Shape.cast(RenderMode))
        next_color_id = Signal(Shape.cast(RenderMode))
        flash_color = Signal(24)
        current_flash_on = Signal(1)
        next_flash_on = Signal(1)
        rotate2_counter_anti = Signal(2)

        m.d.comb += rotate2_counter_anti.eq(ROTATE_COUNTER_MAX - rotate2_counter)

        def rgb(r,g,b):
            return [video_rgb_out.r.eq(r), video_rgb_out.g.eq(g), video_rgb_out.b.eq(b)]

        val = Const(VID_V_BPORCH, video_y_count.shape())
        with m.If((video_y_count >= val) & (video_y_count <= val + rotate2_counter_anti)):   # Top row red
            m.d.comb += render_state.eq(RenderState.TOP)

        val = Const(VID_V_ACTIVE + VID_V_BPORCH - 1, video_y_count.shape())
        with m.Elif((video_y_count <= val) & (video_y_count >= val - rotate2_counter_anti)): # Bottom row yellow
            m.d.comb += render_state.eq(RenderState.BOTTOM)

        val = Const(VID_H_BPORCH, video_x_count.shape())
        with m.Elif((video_x_count >= val) & (video_x_count <= val + rotate2_counter_anti)): # Left column green
            m.d.comb += render_state.eq(RenderState.LEFT)

        val = Const(VID_H_ACTIVE + VID_H_BPORCH - 1, video_x_count.shape())
        with m.Elif((video_x_count <= val) & (video_x_count >= val - rotate2_counter_anti)): # Right column blue
            m.d.comb += render_state.eq(RenderState.RIGHT)

        with m.Elif(video_y_count - VID_V_BPORCH > animation_counter * (VID_V_ACTIVE // ANIMATION_COUNTER_SIZE)):
            m.d.comb += render_state.eq(RenderState.NEXT)
        with m.Else(): # Remaining pixels, alternate black
            m.d.comb += render_state.eq(RenderState.CURRENT)

        m.d.comb += [
            current_color_id.eq(rotate1_counter+1),
            next_color_id.eq(rotate1_counter)
        ]

        with m.If(rotate1_counter[0] ^ rotate2_counter[0]):
            m.d.comb += flash_color.eq(0x0)
        with m.Else():
            m.d.comb += flash_color.eq(0xFFFFFF)

        for [flash_on, id] in [[current_flash_on, current_color_id], [next_flash_on, next_color_id]]:
            with m.Switch(id):
                with m.Case(RenderMode.PLAIN):
                    m.d.comb += flash_on.eq(0)
                with m.Case(RenderMode.VERT):
                    m.d.comb += flash_on.eq(video_x_count[0])
                with m.Case(RenderMode.HORIZ):
                    m.d.comb += flash_on.eq(video_y_count[0])
                with m.Case(RenderMode.CHECKER):
                    m.d.comb += flash_on.eq(video_x_count[0] ^ video_y_count[0])

        # Animation

        # New frame logic
        with m.If(video_vsync_stb):
            m.d.sync += animation_counter.eq(animation_counter + 1)

            with m.If(animation_counter == ANIMATION_COUNTER_MAX):
                m.d.sync += rotate1_counter.eq(rotate1_counter + 1)
                with m.If(rotate1_counter == ROTATE_COUNTER_MAX):
                    m.d.sync += rotate2_counter.eq(rotate2_counter + 1)

        # Draw logic
        with m.If(video_pixel_stb):            # inactive screen areas must be black
            m.d.sync += [
                video_rgb_out.eq(0)
            ]

            # Color selection for live pixels
            with m.If(video_active):
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
                                m.d.sync += video_rgb_out.eq(flash_color^invert)
                            with m.Else():
                                m.d.sync += rgb(0xa0, 0x00, 0x80) # Magenta

        # Audio

        audio_high = Signal(1)       # High when square wave high (1 bit dac effectively)
        audio_output_word_bit = Signal(1) # As audio_channel_internal increments scrolls through bits 0b0000011111111111, MSB first

        audgen_osc_phase = Signal(7)  # Counter for square wave
        audgen_osc_wave = Signal(5)   # Counter for square waves on octaves C2 through C6 inclusive (msb is C2, lsb is C6)
        audgen_osc_wave_select = Signal(4) # Which bit in audio_osc_wave to output?

        m.d.comb += audio_output_word_bit.eq(audio_channel_internal <= 5) # 1 bit dac state

        m.d.comb += audgen_osc_wave_select.eq( 4-(rotate1_counter + (rotate2_counter == 3)) ) # Video state

        with m.If(audio_bit_update_stb):
            # Convert above state logic to a waveform—- alternate 0b0000011111111111 and 0b1111100000000000 words
            m.d.sync += audio_dac_out.eq( Mux(audio_silenced, 0, audio_output_word_bit ^ audio_high) )

        with m.If(audio_word_update_stb):
            # Audio generation user logic
            with m.If(audgen_osc_phase < 46): # Alternating every 46 stereo samples gets us on average a wave-cycle every 46 audio frames ~= 1046.5hz = C6
                m.d.sync += audgen_osc_phase.eq( audgen_osc_phase + 1 )
            with m.Else():
                m.d.sync += [
                    audgen_osc_phase.eq( 1 ), # note audgen_osc_phase is currently EQUAL to 46
                    audgen_osc_wave.eq( audgen_osc_wave + 1 ),

                    # Set square wave high or low by selecting an octave from the osc_wave bitstring
                    # Pattern is C2 C3 C4 C5, C2 C3 C4 C5, C2 C3 C4 C5, C3 C4 C5 C6
                    # Notice crossing streams: Which octave we select is based on the *graphics* state
                    # Also notice msb is lowest frequency so we want to count from msb to lsb 
                    audio_high.eq( audgen_osc_wave.bit_select( audgen_osc_wave_select , 1 ) )
                ]


def simulate():
    from amaranth.sim import Simulator

    sim = Simulator(Toplevel())
    sim.add_clock(1/74.25e6)
    with sim.write_vcd("dump.vcd"):
        sim.run_until(10e-3, run_passive=True)


def capture_frame():
    import soundfile
    from amaranth.sim import Simulator

    top = Toplevel()
    def bench():
        written = 0
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


def capture_wav():
    import numpy as np
    import soundfile as sf
    from amaranth.sim import Simulator

    FILE_NAME = "log.wav"
    SAMPLE_RATE = 48000
    CHUNK_SIZE = SAMPLE_RATE//200
    SHRT_MAX = 32767 # No python library source for this?
    USHRT_CONVERT = 1<<16

    top = Toplevel()
    def bench():
        written = 0
        last_printed = 0

        while not (yield top.audio_mclk): yield

        while True:
            frames = []

            # Do i2s from the speaker end
            for _ in range(CHUNK_SIZE):
                frame = []
                for channel in range(2):
                    sample = 0

                    for _ in range(16):
                        sample <<= 1
                        sample |= yield top.audio_dac
                        lrck = yield top.audio_lrck
                        assert lrck == channel, f"Unexpected lrck [channel select] value (wanted {channel}, got {lrck})"
                        for _ in range(4): # Serial step
                            while (yield top.audio_mclk): yield
                            while not (yield top.audio_mclk): yield

                    if sample > SHRT_MAX: # Reinterpret unsigned as signed
                        sample -= USHRT_CONVERT
                    frame.append(sample)

                    for _ in range(16): # Blank space
                        for _ in range(4): # Serial step
                            while (yield top.audio_mclk): yield
                            while not (yield top.audio_mclk): yield
                frames.append(frame)

            # If this is first byte open write to truncate, otherwise open readwrite...
            with sf.SoundFile(FILE_NAME, mode = 'w', samplerate=SAMPLE_RATE, channels=2, subtype='PCM_16') \
                    if written == 0 \
                    else sf.SoundFile(FILE_NAME, mode = 'r+') \
                    as outfile:
                if written > 0: # ... then seek to end to append
                    outfile.seek(0,sf.SEEK_END)
                outfile.write(np.array(frames, dtype=np.int16))

            written += CHUNK_SIZE
            if written >= last_printed+SAMPLE_RATE:
                print(f"{written//SAMPLE_RATE} seconds written")
                last_printed = written

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

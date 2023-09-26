# "Business logic" / Game logic / App logic

from amaranth import *
from amaranth.lib import wiring, data
from amaranth.lib.wiring import In, Out
import enum

from .resolution import *
from .toplevel import Toplevel


class AppToplevel(Toplevel):
    def app_elaborate(self, platform, m,
            video_pixel_stb, video_hsync_stb, video_vsync_stb, video_x_count, video_y_count, video_active, video_rgb_out,
            audio_silenced, audio_channel_select, audio_channel_internal, audio_bit_update_stb, audio_word_update_stb, audio_dac_out):
        # App: Rule 30?

        # Setup

        reset_value = (1 << (VID_H_ACTIVE//2)) + (1 << (VID_H_ACTIVE//4))
        topline_state = Signal(VID_H_ACTIVE)
        active_state = Signal(VID_H_ACTIVE, reset=reset_value)
        audgen_state = Signal(VID_H_ACTIVE, reset=reset_value)
        need_topline_backcopy = Signal(1) # Fires 1 cycle after first-row hsync 

        reset_value = None # Take me unto thine arms, GC

        # Partial results for colors

        flash_color = Signal(24)

        # flash_color is our 1 bit video output (black/white)
        with m.If(active_state[0]):
            m.d.comb += flash_color.eq(0x0)
        with m.Else():
            m.d.comb += flash_color.eq(0xFFFFFF)

        # Animation

        # Draw logic
        m.d.sync += [
            need_topline_backcopy.eq(0)
        ]
        with m.If(video_pixel_stb):            # inactive screen areas must be black
            m.d.sync += [
                video_rgb_out.eq(0)
            ]

            # Color selection for live pixels
            with m.If(video_active):
                m.d.sync += [
                    video_rgb_out.eq(flash_color),
                    active_state.eq(active_state.rotate_right(1)) # We are always displaying the least significant bit
                ]

            # Row finished
            with m.If(video_hsync_stb):
                # Perform rule 30
                for i in range(VID_H_ACTIVE): # For each col
                    # Calculate indices
                    pre = (i+(VID_H_ACTIVE-1))%VID_H_ACTIVE
                    nex = (i+1)%VID_H_ACTIVE
                    # Output signal
                    at = active_state[i]
                    # Input signal
                    cat = Cat(Cat(active_state[pre], at), active_state[nex])
                    # Cellular automaton definition (rule 30)
                    with m.Switch(cat):
                        with m.Case(0b000):
                            m.d.sync += at.eq(0)
                        with m.Case(0b001):
                            m.d.sync += at.eq(1)
                        with m.Case(0b010):
                            m.d.sync += at.eq(1)
                        with m.Case(0b011):
                            m.d.sync += at.eq(1)
                        with m.Case(0b100):
                            m.d.sync += at.eq(1)
                        with m.Case(0b101):
                            m.d.sync += at.eq(0)
                        with m.Case(0b110):
                            m.d.sync += at.eq(0)
                        with m.Case(0b111):
                            m.d.sync += at.eq(0)

                # 1 cycle after first row is done performing CA, make that the new topline
                with m.If(video_y_count == VID_V_BPORCH):
                    m.d.sync += need_topline_backcopy.eq(1)

            # Screen finished
            with m.If(video_vsync_stb):
                # Set audio state and new active state from most recent topline state
                m.d.sync += [
                    audgen_state.eq(topline_state),
                    active_state.eq(topline_state),
                ]

        with m.If(need_topline_backcopy):
            m.d.sync += topline_state.eq(active_state)

        # Audio

        audio_high = Signal(1)       # High when square wave high (1 bit dac effectively)
        audio_output_word_bit = Signal(1) # As audio_channel_internal increments scrolls through bits 0b0000011111111111, MSB first

        m.d.comb += audio_output_word_bit.eq(audio_channel_internal <= 5) # 1 bit dac state

        m.d.comb += audio_high.eq( audgen_state[0] )  # Audio play is always lowest bit of audio state

        with m.If(audio_bit_update_stb):
            # Convert above state logic to a waveform—- alternate 0b0000011111111111 and 0b1111100000000000 words
            m.d.sync += audio_dac_out.eq( Mux(audio_silenced, 0, audio_output_word_bit ^ audio_high) )

        with m.If(audio_word_update_stb):
            # Audio generation app logic
            with m.If(~(video_pixel_stb & video_vsync_stb)): # Don't collide with end-of-screen copy
                m.d.sync += audgen_state.eq( audgen_state.rotate_right(1) ) # After playing a bit, move to the next bit

# "Business logic" / Game logic / App logic

from amaranth import *
from amaranth.lib import wiring, data
from amaranth.lib.wiring import In, Out
import enum

from .resolution import *
from .toplevel import Toplevel


DEBUG_NO_OPENING_PAUSE = False
DEBUG_NO_CONTROLS = False

AUDIO_DIVISOR_BITS = 2

class AppToplevel(Toplevel):
    def app_elaborate(self, platform, m,
            video_pixel_stb, video_hsync_stb, video_vsync_stb, video_x_count, video_y_count, video_active, video_rgb_out,
            audio_silenced, audio_channel_select, audio_channel_internal, audio_bit_update_stb, audio_word_update_stb, audio_dac_out):
        # App: Rule 30?

        # Setup

        # CA mechanics
        line_reset_value = (1 << (VID_H_ACTIVE//2)) # Initial state value of first line
        topline_state = Signal(VID_H_ACTIVE, reset=line_reset_value)
        active_state = Signal(VID_H_ACTIVE, reset=line_reset_value)
        audgen_state = Signal(VID_H_ACTIVE, reset=line_reset_value)
        need_topline_backcopy = Signal(1) # Fires 1 cycle after first-row hsync

        # CA control mechanics
        frame_frozen = Signal(1, reset=0 if DEBUG_NO_OPENING_PAUSE else 1)

        # Initial pause
        opening_countdown_timer_reset_value = ((1<<6)-1)
        opening_countdown_timer = Signal(6, reset=0 if DEBUG_NO_OPENING_PAUSE else opening_countdown_timer_reset_value)
        opening_wants_frozen = Signal(1)
        opening_countdown_timer_late_reset = Signal(1)

        # Elective pause
        pause_key_wants_frozen = Signal(1)
        need_frozen_exception = Signal(1)

        # Audio mechanics
        audio_divide_counter = Signal(AUDIO_DIVISOR_BITS, reset = AUDIO_DIVISOR_BITS and ((1<<AUDIO_DIVISOR_BITS)-1))
        audio_divide_stb = Signal(1)

        line_reset_value = None # Take me unto thine arms, GC

        # Controls

        if DEBUG_NO_CONTROLS:
            m.d.comb += [
                pause_key_wants_frozen.eq(0),
                need_frozen_exception.eq(0)
            ]
        else:
            cont1_key_last = Signal(self.cont1_key.shape())
            m.d.sync += cont1_key_last.eq(self.cont1_key) # TODO: Debounce

            with m.If(self.cont1_key[15] & (~cont1_key_last[15])): # "Start"
                with m.If(self.cont1_key[14]): # "Select""
                    with m.If(pause_key_wants_frozen): # While paused, did select+start
                        m.d.sync += need_frozen_exception.eq(1) # Perform one step
                    with m.Else(): # While unpaused, did select+start
                        m.d.sync += [ # Perform one step then freeze for 64 frames
                            opening_countdown_timer_late_reset.eq(1),
                            need_frozen_exception.eq(1)
                        ]
                with m.Else():
                    m.d.sync += pause_key_wants_frozen.eq(~pause_key_wants_frozen)

        # Partial results for colors

        flash_color = Signal(24)

        # flash_color is our 1 bit video output (black/white)
        with m.If(active_state[0]):
            m.d.comb += flash_color.eq(0x0)
        with m.Else():
            m.d.comb += flash_color.eq(0xFFFFFF)

        # Animation

        # Draw logic

        m.d.comb += opening_wants_frozen.eq(opening_countdown_timer != 0)

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
            with m.If(video_hsync_stb & (video_y_count >= VID_V_BPORCH) & (video_y_count < VID_V_ACTIVE + VID_V_BPORCH - 1)):
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
                # (Unless we are in first second and frozen)
                with m.If(
                        (video_y_count == VID_V_BPORCH) & 
                        (~frame_frozen)):
                    m.d.sync += need_topline_backcopy.eq(1)

            # Screen finished
            with m.If(video_vsync_stb):
                # Is the next frame paused?
                m.d.sync += [
                    frame_frozen.eq(opening_wants_frozen | pause_key_wants_frozen),
                    active_state.eq(topline_state)
                ]
                with m.If(need_frozen_exception):
                    m.d.sync += frame_frozen.eq(0)

                # Reset frozen exception
                if not DEBUG_NO_CONTROLS:
                    with m.If(need_frozen_exception):
                        m.d.sync += need_frozen_exception.eq(0)

                # Service opening timer
                with m.If(opening_countdown_timer != 0):
                    m.d.sync += opening_countdown_timer.eq(opening_countdown_timer - 1)

                # Set audio state and new active state from most recent topline state
                with m.If(~frame_frozen): # Notice frozen for *just-finished* frame
                    m.d.sync += [
                        audgen_state.eq(topline_state),
                    ]

        with m.If(need_topline_backcopy): # Do last to override
            m.d.sync += [
                topline_state.eq(active_state),
                need_topline_backcopy.eq(0)
            ]

        with m.If(opening_countdown_timer_late_reset): # Do last to override
            m.d.sync += [
                opening_countdown_timer.eq(opening_countdown_timer_reset_value),
                opening_countdown_timer_late_reset.eq(0)
            ]

        # Audio

        audio_high = Signal(1)       # High when square wave high (1 bit dac effectively)
        audio_output_word_bit = Signal(1) # As audio_channel_internal increments scrolls through bits 0b0000011111111111, MSB first

        m.d.comb += audio_output_word_bit.eq(audio_channel_internal <= 5) # 1 bit dac state

        m.d.comb += audio_divide_stb.eq(audio_divide_counter == 0)
        m.d.comb += audio_high.eq( audgen_state[0] )  # Audio play is always lowest bit of audio state

        with m.If(audio_bit_update_stb):
            # Convert above state logic to a waveform—- alternate 0b0000011111111111 and 0b1111100000000000 words
            m.d.sync += audio_dac_out.eq( Mux(audio_silenced, 0, audio_output_word_bit ^ audio_high) )

        with m.If(audio_word_update_stb):
            # Audio generation app logic
            with m.If(~(video_pixel_stb & video_vsync_stb)): # Don't collide with end-of-screen copy
                with m.If(audio_divide_stb):
                    m.d.sync += audgen_state.eq( audgen_state.rotate_right(1) ) # After playing a bit, move to the next bit

                if AUDIO_DIVISOR_BITS>0:
                    m.d.sync += audio_divide_counter.eq( audio_divide_counter+1 )

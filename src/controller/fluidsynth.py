"""
FluidSynth process controller.

Manages the FluidSynth process lifecycle and communicates via TCP socket
for hot-swapping SoundFonts without restarting.
"""

import os
import signal
import subprocess
import sys
import time

from config import cfg
from controller.backend import AudioBackend
from controller.midi_monitor import MidiHotplugMonitor
from controller.socket_client import SocketClient


class FluidSynthController(AudioBackend):
    """Manages the FluidSynth process via TCP command interface."""
    process: subprocess.Popen | None = None
    current_font: str | None = None
    gain: float = cfg.audio.default_gain
    _midi_monitor: MidiHotplugMonitor | None = None
    _client: SocketClient = SocketClient()

    def _send_command(self, cmd: str) -> str | None:
        return self._client.send(cmd)

    def start(self, soundfont_path):
        """Start FluidSynth if not running, then load the SoundFont."""
        if not self.is_running():
            self._start_process(soundfont_path)
        else:
            self._swap_soundfont(soundfont_path)
        self.current_font = soundfont_path

    def _start_process(self, soundfont_path):
        """Launch the FluidSynth process."""
        self.stop()
        time.sleep(0.3)

        a = cfg.audio
        cmd = [
            "chrt", "-f", str(a.rt_priority),
            "taskset", "-c", a.cores,
            "fluidsynth",
            "-a", "alsa",
            "-o", f"audio.alsa.device={a.device}",
            "-o", f"audio.period-size={a.period_size}",
            "-o", f"audio.periods={a.periods}",
            "-o", f"synth.sample-rate={a.sample_rate}",
            "-o", f"synth.gain={self.gain}",
            "-o", f"shell.port={a.fluidsynth_port}",
            "-m", "alsa_seq",
            "-s",
            soundfont_path,
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            time.sleep(1.5)
            self._connect_midi()
            self._start_midi_monitor()
            return True
        except Exception as e:
            print(f"Error starting FluidSynth: {e}", file=sys.stderr)
            return False

    def _swap_soundfont(self, soundfont_path):
        """Hot-swap the SoundFont without restarting."""
        response = self._send_command(f"load {soundfont_path}")
        if response is None:
            self._start_process(soundfont_path)
            return

        sfid = 1
        for line in response.splitlines():
            if "ID" in line:
                try:
                    sfid = int(line.split()[-1])
                except ValueError:
                    pass
                break

        self._send_command(f"select 0 {sfid} 0 0")
        self._send_command("reset")

    def _start_midi_monitor(self) -> None:
        if self._midi_monitor:
            self._midi_monitor.stop()
        self._midi_monitor = MidiHotplugMonitor(on_connect=self._connect_midi)
        self._midi_monitor.start()

    def stop(self):
        """Kill the running FluidSynth process."""
        if self._midi_monitor:
            self._midi_monitor.stop()
            self._midi_monitor = None
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process = None

        subprocess.run(
            ["killall", "-9", "fluidsynth"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _connect_midi(self):
        """Connect all MIDI input devices to FluidSynth."""
        try:
            result = subprocess.run(
                ["aconnect", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            lines = result.stdout.split("\n")

            fluid_client = None
            midi_clients = []

            for line in lines:
                if line.startswith("client "):
                    parts = line.split(":")
                    client_num = int(parts[0].split()[1])
                    if "FLUID" in line or "Synth" in line:
                        fluid_client = client_num
                    elif client_num > 15:
                        midi_clients.append(client_num)

            if fluid_client:
                for mc in midi_clients:
                    if mc != fluid_client:
                        subprocess.run(
                            ["aconnect", f"{mc}:0", f"{fluid_client}:0"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
        except Exception as e:
            print(f"MIDI connect error: {e}", file=sys.stderr)

    def set_gain(self, gain):
        """Update gain live without restart."""
        self.gain = gain
        if self.is_running():
            self._send_command(f"gain {gain}")

    def is_running(self) -> bool:
        """Check if the FluidSynth process is alive."""
        if self.process:
            return self.process.poll() is None
        return False
    
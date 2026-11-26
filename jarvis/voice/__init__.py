"""Voice Engine: wake word -> speech recognition -> agent -> speech synthesis.

Three stages (see :mod:`jarvis.voice.session`):

* SLEEP  – only voice-activity detection + the wake-word detector run; the
           LLM never sees audio.
* ACTIVE – every utterance is transcribed and sent to the agent; the
           conversation context is kept; the microphone is muted while
           JARVIS speaks so its own voice is never taken as a command.
* back to SLEEP – on a stop phrase ("Всё, спасибо") or after
           ``idle_timeout_seconds`` of silence after JARVIS finished speaking.

Heavy dependencies (numpy, sounddevice, faster-whisper, piper) are imported
lazily, so the text chat keeps working when they are missing.
"""

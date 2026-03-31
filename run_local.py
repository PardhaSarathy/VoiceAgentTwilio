"""Run the Gemini Live voice agent locally using your microphone and speakers.

Usage:
    python run_local.py

Requires: pip install pipecat-ai[google,local]
On macOS: brew install portaudio

IMPORTANT: Use headphones for best results to avoid echo-based interruptions.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import InputTextRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.google.gemini_live import GeminiLiveLLMService
from pipecat.services.google.gemini_live.llm import GeminiModalities, GeminiVADParams
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from prompts import get_system_prompt

load_dotenv(override=True)

try:
    logger.remove(0)
except ValueError:
    pass
logger.add(sys.stderr, level="DEBUG")

google_api_key = os.getenv("GOOGLE_API_KEY")


async def main():
    transport = LocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=False,
            vad_audio_passthrough=True,
        ),
    )

    system_prompt = get_system_prompt()
    system_prompt += "\n\nA caller has just connected. Introduce yourself immediately without waiting for them to speak first."

    llm = GeminiLiveLLMService(
        api_key=google_api_key,
        system_instruction=system_prompt,
        settings=GeminiLiveLLMService.Settings(
            model="models/gemini-3.1-flash-live-preview",
            voice="Kore",
            modalities=GeminiModalities.AUDIO,
            temperature=0.6,
            max_tokens=200,
            vad=GeminiVADParams(
                start_sensitivity="START_SENSITIVITY_LOW",
                end_sensitivity="END_SENSITIVITY_LOW",
                silence_duration_ms=2000,
                prefix_padding_ms=500,
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            llm,
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=300,
        cancel_on_idle_timeout=True,
    )

    @task.event_handler("on_pipeline_started")
    async def on_pipeline_started(task, frame):
        # Wait for Gemini session to be fully connected
        while not llm._session:
            await asyncio.sleep(0.1)
        logger.info("Gemini session ready — injecting 'Hello' text to trigger greeting")
        # InputTextRawFrame goes through the same path as real speech:
        # process_frame → _send_user_text → send_realtime_input(text=...)
        await task.queue_frames([InputTextRawFrame(text="Hello.")])

    @task.event_handler("on_pipeline_error")
    async def on_pipeline_error(task, error):
        logger.error(f"Pipeline error: {error}")

    runner = PipelineRunner()

    logger.info("Starting local Gemini Live agent — speak into your microphone...")
    await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())

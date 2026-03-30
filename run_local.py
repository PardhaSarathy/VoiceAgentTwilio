"""Run the voice agent locally using your microphone and speakers.

Usage:
    python run_local.py
    python run_local.py --test   # use Deepgram TTS instead of Inworld

Requires: pip install pipecat-ai[local]
On macOS: brew install portaudio
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.inworld.tts import InworldTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from prompts import get_system_prompt

load_dotenv(override=True)

try:
    logger.remove(0)
except ValueError:
    pass
logger.add(sys.stderr, level="DEBUG")

deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
openai_api_key = os.getenv("OPENAI_API_KEY")
inworld_api_key = os.getenv("INWORLD_API_KEY")


async def main(testing: bool):
    transport = LocalAudioTransport(
        params=LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=0.7,
                    start_secs=0.2,
                    stop_secs=1.5,
                    min_volume=0.5,
                )
            ),
            vad_audio_passthrough=True,
        ),
    )

    stt = DeepgramSTTService(
        api_key=deepgram_api_key,
        live_options=LiveOptions(
            model="nova-2",
            language="en",
            punctuate=True,
            smart_format=True,
            interim_results=True,
            utterance_end_ms="1500",
            endpointing=300,
            keywords=["massage", "facial", "spa", "appointment", "booking",
                      "manicure", "pedicure", "aromatherapy", "scrub", "Ode Spa"],
        ),
    )

    llm = OpenAILLMService(
        api_key=openai_api_key,
        model="gpt-4o",
        params=OpenAILLMService.InputParams(
            temperature=0.7,
            max_tokens=150,
        ),
    )

    if testing:
        tts = DeepgramTTSService(
            api_key=deepgram_api_key,
            voice="aura-asteria-en",
            sample_rate=24000,
        )
    else:
        tts = InworldTTSService(
            api_key=inworld_api_key,
            voice_id="Arjun",
            model="inworld-tts-1.5-max",
        )

    messages = [
        {
            "role": "system",
            "content": get_system_prompt(),
        },
    ]

    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
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

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Local audio connected")
        messages.append({"role": "system", "content": "Please introduce yourself to the user."})
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @task.event_handler("on_pipeline_error")
    async def on_pipeline_error(task, error):
        logger.error(f"Pipeline error: {error}")

    runner = PipelineRunner()

    logger.info("Starting local agent — speak into your microphone...")
    await runner.run(task)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run voice agent locally")
    parser.add_argument(
        "-t", "--test", action="store_true", default=False,
        help="use Deepgram TTS instead of Inworld",
    )
    args, _ = parser.parse_known_args()

    asyncio.run(main(testing=args.test))

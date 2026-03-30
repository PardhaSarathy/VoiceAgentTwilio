import argparse
import json
import os
import sys

import uvicorn
from bot import run_bot
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.responses import FileResponse, JSONResponse, Response
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, VoiceResponse

load_dotenv(override=True)

try:
    logger.remove(0)
except ValueError:
    pass
logger.add(sys.stderr, level="DEBUG")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Twilio config ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
SERVER_URL = os.getenv("SERVER_URL", "").rstrip("/")

# Store customer name per call_sid so the bot can greet them by name
call_metadata: dict[str, dict] = {}


def _build_twiml_response() -> str:
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{SERVER_URL.replace('https://', '').replace('http://', '')}/ws")
    response.append(connect)
    response.pause(length=40)
    return str(response)


# ── Serve the web UI ──
@app.get("/")
async def serve_ui():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Health check ──
@app.get("/health")
async def health_check():
    return JSONResponse(content={"status": "healthy"})


# ── Inbound call webhook ──
@app.post("/twiml/inbound")
async def twiml_inbound(request: Request):
    form = await request.form()
    caller = form.get("From", "unknown")
    logger.info(f"Inbound call from {caller}")
    twiml = _build_twiml_response()
    return Response(content=twiml, media_type="application/xml")


# ── Outbound call trigger ──
@app.post("/call/outbound")
async def call_outbound(request: Request):
    body = await request.json()
    to_number = body.get("to")
    customer_name = body.get("name", "")

    if not to_number:
        return JSONResponse(status_code=400, content={"error": "Missing 'to' phone number"})

    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        return JSONResponse(status_code=500, content={"error": "Twilio credentials not configured"})

    twiml = _build_twiml_response()

    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            twiml=twiml,
        )
        # Store the customer name so the bot can use it when the WebSocket connects
        call_metadata[call.sid] = {"name": customer_name}
        logger.info(f"Outbound call initiated: SID={call.sid}, to={to_number}, name={customer_name}")
        return JSONResponse(content={"call_sid": call.sid, "status": call.status})
    except Exception as e:
        logger.error(f"Failed to initiate outbound call: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── WebSocket endpoint ──
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        start_data = websocket.iter_text()
        await start_data.__anext__()
        call_data = json.loads(await start_data.__anext__())
        stream_sid = call_data["start"]["streamSid"]
        call_sid = call_data["start"].get("callSid", "unknown")

        # Look up customer name from outbound call metadata
        meta = call_metadata.pop(call_sid, {})
        customer_name = meta.get("name", "")

        logger.info(f"WebSocket connected — stream_sid={stream_sid}, call_sid={call_sid}, customer={customer_name or 'inbound'}")
        await run_bot(websocket, stream_sid, app.state.testing, customer_name=customer_name)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        logger.info("WebSocket connection closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipecat Twilio Chatbot Server")
    parser.add_argument(
        "-t", "--test", action="store_true", default=False, help="set the server in testing mode"
    )
    args, _ = parser.parse_known_args()

    app.state.testing = args.test

    port = int(os.getenv("PORT", "8765"))
    uvicorn.run(app, host="0.0.0.0", port=port)

"""Basic FastAPI server with /status endpoint returning library versions."""

from fastapi import FastAPI
import fastapi
import httpx

app = FastAPI()


@app.get("/status")
def status():
    return {
        "fastapi_version": fastapi.__version__,
        "httpx_version": httpx.__version__,
    }

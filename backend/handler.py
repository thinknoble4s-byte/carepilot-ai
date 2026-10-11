"""AWS Lambda entry point (Function URL events) for the FastAPI app."""
from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")

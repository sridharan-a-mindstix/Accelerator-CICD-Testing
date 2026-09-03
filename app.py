"""Sample Application for CI/CD Pipeline Testing."""

import os
from flask import Flask, jsonify

app = Flask(__name__)

APP_NAME = os.getenv("APP_NAME", "sample-app")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")


@app.route("/")
def index():
    """Root endpoint returning service identity and status."""
    return jsonify(
        {
            "status": "ok",
            "application": APP_NAME,
            "version": APP_VERSION,
            "message": "CI/CD testing sample application is running successfully",
        }
    )


@app.route("/healthz")
def healthz():
    """Health check endpoint for Kubernetes liveness and readiness probes."""
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

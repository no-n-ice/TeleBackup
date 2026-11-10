#!/usr/bin/env python3
"""
routes/__init__.py — Flask Blueprint registration for modular route handlers.
"""
from .viewer import viewer_bp
from .archive import archive_bp
from .settings import settings_bp
from .update import update_bp
from .auth import auth_bp
from .investigator import investigator_bp
from .export import export_bp
from .stats import stats_bp
from .transcribe import transcribe_bp
from .folders import folders_bp


def register_blueprints(app):
    """Registers all application route blueprints onto the Flask app instance."""
    app.register_blueprint(viewer_bp)
    app.register_blueprint(archive_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(update_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(investigator_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(transcribe_bp)
    app.register_blueprint(folders_bp)

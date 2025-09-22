from models.model import App, InstalledApp, RecommendedApp
from extensions.ext_redis import redis_client
from extensions.ext_database import db
from controllers.console.wraps import only_edition_cloud
from controllers.console import api, console_ns
from constants.languages import supported_language
from configs import dify_config
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from flask import request
from flask_restx import Resource, fields, reqparse
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.exceptions import NotFound, Unauthorized

P = ParamSpec("P")
R = TypeVar("R")


def admin_required(view: Callable[P, R]):
    @wraps(view)
    def decorated(*args: P.args, **kwargs: P.kwargs):
        if not dify_config.ADMIN_API_KEY:
            raise Unauthorized("API key is invalid.")

        auth_header = request.headers.get("Authorization")
        if auth_header is None:
            raise Unauthorized("Authorization header is missing.")

        if " " not in auth_header:
            raise Unauthorized(
                "Invalid Authorization header format. Expected 'Bearer <api-key>' format.")

        auth_scheme, auth_token = auth_header.split(None, 1)
        auth_scheme = auth_scheme.lower()

        if auth_scheme != "bearer":
            raise Unauthorized(
                "Invalid Authorization header format. Expected 'Bearer <api-key>' format.")

        if auth_token != dify_config.ADMIN_API_KEY:
            raise Unauthorized("API key is invalid.")

        return view(*args, **kwargs)

    return decorated


@console_ns.route("/admin/retry-stuck-docs/status")
class RetryStuckDocsStatus(Resource):
    @api.doc("retry_stuck_docs_status")
    @api.doc(description=("Show last run time and last processed count for the stuck-docs recovery scheduler"))
    @admin_required
    def get(self):
        last_run = redis_client.get("retry_stuck_docs:last_run")
        last_count = redis_client.get("retry_stuck_docs:last_count")
        last_count_error = redis_client.get(
            "retry_stuck_docs:last_count_error")
        last_count_indexing = redis_client.get(
            "retry_stuck_docs:last_count_indexing")
        enabled = bool(
            getattr(
                dify_config,
                "ENABLE_RETRY_DATASET_DOCUMENTS_TASK",
                False,
            )
        )
        interval = getattr(
            dify_config,
            "RETRY_DATASET_DOCUMENTS_INTERVAL_MINUTES",
            None,
        )
        threshold = getattr(
            dify_config,
            "RETRY_DATASET_DOCUMENTS_THRESHOLD_MINUTES",
            None,
        )
        max_per_run = getattr(
            dify_config,
            "RETRY_DATASET_DOCUMENTS_MAX_PER_RUN",
            None,
        )

        return (
            {
                "last_run": float(last_run.decode()) if last_run else None,
                "last_count": int(last_count.decode()) if last_count else 0,
                "last_count_error": (int(last_count_error.decode()) if last_count_error else 0),
                "last_count_indexing": (int(last_count_indexing.decode()) if last_count_indexing else 0),
                "enabled": enabled,
                "interval_minutes": interval,
                "threshold_minutes": threshold,
                "max_per_run": max_per_run,
            },
            200,
        )


@console_ns.route("/admin/insert-explore-apps")
class InsertExploreAppListApi(Resource):
    @api.doc("insert_explore_app")
    @api.doc(description="Insert or update an app in the explore list")
    @api.expect(
        api.model(
            "InsertExploreAppRequest",
            {
                "app_id": fields.String(required=True, description="Application ID"),
                "desc": fields.String(description="App description"),
                "copyright": fields.String(description="Copyright information"),
                "privacy_policy": fields.String(description="Privacy policy"),
                "custom_disclaimer": fields.String(description="Custom disclaimer"),
                "language": fields.String(required=True, description="Language code"),
                "category": fields.String(required=True, description="App category"),
                "position": fields.Integer(required=True, description="Display position"),
            },
        )
    )
    @api.response(200, "App updated successfully")
    @api.response(201, "App inserted successfully")
    @api.response(404, "App not found")
    @only_edition_cloud
    @admin_required
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("app_id", type=str, required=True,
                            nullable=False, location="json")
        parser.add_argument("desc", type=str, location="json")
        parser.add_argument("copyright", type=str, location="json")
        parser.add_argument("privacy_policy", type=str, location="json")
        parser.add_argument("custom_disclaimer", type=str, location="json")
        parser.add_argument("language", type=supported_language,
                            required=True, nullable=False, location="json")
        parser.add_argument("category", type=str, required=True,
                            nullable=False, location="json")
        parser.add_argument("position", type=int, required=True,
                            nullable=False, location="json")
        args = parser.parse_args()

        app = db.session.execute(select(App).where(
            App.id == args["app_id"])).scalar_one_or_none()
        if not app:
            raise NotFound(f"App '{args['app_id']}' is not found")

        site = app.site
        if not site:
            desc = args["desc"] or ""
            copy_right = args["copyright"] or ""
            privacy_policy = args["privacy_policy"] or ""
            custom_disclaimer = args["custom_disclaimer"] or ""
        else:
            desc = site.description or args["desc"] or ""
            copy_right = site.copyright or args["copyright"] or ""
            privacy_policy = site.privacy_policy or args["privacy_policy"] or ""
            custom_disclaimer = site.custom_disclaimer or args["custom_disclaimer"] or ""

        with Session(db.engine) as session:
            recommended_app = session.execute(
                select(RecommendedApp).where(
                    RecommendedApp.app_id == args["app_id"])
            ).scalar_one_or_none()

            if not recommended_app:
                recommended_app = RecommendedApp(
                    app_id=app.id,
                    description=desc,
                    copyright=copy_right,
                    privacy_policy=privacy_policy,
                    custom_disclaimer=custom_disclaimer,
                    language=args["language"],
                    category=args["category"],
                    position=args["position"],
                )

                db.session.add(recommended_app)

                app.is_public = True
                db.session.commit()

                return {"result": "success"}, 201
            else:
                recommended_app.description = desc
                recommended_app.copyright = copy_right
                recommended_app.privacy_policy = privacy_policy
                recommended_app.custom_disclaimer = custom_disclaimer
                recommended_app.language = args["language"]
                recommended_app.category = args["category"]
                recommended_app.position = args["position"]

                app.is_public = True

                db.session.commit()

                return {"result": "success"}, 200


@console_ns.route("/admin/insert-explore-apps/<uuid:app_id>")
class InsertExploreAppApi(Resource):
    @api.doc("delete_explore_app")
    @api.doc(description="Remove an app from the explore list")
    @api.doc(params={"app_id": "Application ID to remove"})
    @api.response(204, "App removed successfully")
    @only_edition_cloud
    @admin_required
    def delete(self, app_id):
        with Session(db.engine) as session:
            recommended_app = session.execute(
                select(RecommendedApp).where(
                    RecommendedApp.app_id == str(app_id))
            ).scalar_one_or_none()

        if not recommended_app:
            return {"result": "success"}, 204

        with Session(db.engine) as session:
            app = session.execute(select(App).where(
                App.id == recommended_app.app_id)).scalar_one_or_none()

        if app:
            app.is_public = False

        with Session(db.engine) as session:
            installed_apps = (
                session.execute(
                    select(InstalledApp).where(
                        InstalledApp.app_id == recommended_app.app_id,
                        InstalledApp.tenant_id != InstalledApp.app_owner_tenant_id,
                    )
                )
                .scalars()
                .all()
            )

            for installed_app in installed_apps:
                session.delete(installed_app)

        db.session.delete(recommended_app)
        db.session.commit()

        return {"result": "success"}, 204

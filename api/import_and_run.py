import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv

from app_factory import create_app
from core.app.entities.app_invoke_entities import InvokeFrom
from core.helper import encrypter
from extensions.ext_database import db
from libs import rsa
from models.account import Account, Tenant, TenantAccountJoin
from models.dataset import Dataset
from models.model import App, UploadFile
from models.provider import (
    Provider,
    ProviderCredential,
    ProviderModel,
    ProviderModelCredential,
)
from services.app_dsl_service import AppDslService
from services.app_generate_service import AppGenerateService
from services.dataset_service import DatasetService
from services.entities.knowledge_entities.knowledge_entities import (
    DataSource,
    FileInfo,
    InfoList,
    KnowledgeConfig,
    ProcessRule,
    Rule,
    Segmentation,
)
from services.file_service import FileService
from services.model_provider_service import ModelProviderService
from services.plugin.plugin_service import PluginService

# Unset proxy environment variables to avoid httpx/requests issues with socks5h
for key in [
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
]:
    os.environ.pop(key, None)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


WORKFLOW_DIR = Path("/home/gw/opt/dify/workflows_local/essay_review")
STUDENT_ESSAY_PATH = WORKFLOW_DIR / "20250427-Amy-Redistribute Income.pdf"
MARK_SCHEME_PATH = WORKFLOW_DIR / "MS.pdf"
REFERENCE_ESSAY_PATH = WORKFLOW_DIR / "例文.pdf"
TEACHER_COMMENT_PATH = WORKFLOW_DIR / "teacher_comment.txt"
DSL_PATH = WORKFLOW_DIR / "eaasy_review_ao1_ao2_split.yml"
STORAGE_PATH = Path("storage")
UPLOAD_CACHE_PATH = WORKFLOW_DIR / ".upload_cache.json"


# Set endpoints for local execution
os.environ["CODE_EXECUTION_ENDPOINT"] = "http://localhost:8194"
os.environ["CODE_EXECUTION_API_KEY"] = "dify-sandbox"
os.environ["STORAGE_TYPE"] = "local"
os.environ["STORAGE_LOCAL_PATH"] = str(STORAGE_PATH)
os.environ["PLUGIN_DAEMON_URL"] = "http://localhost:5002"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "True"
STORAGE_PATH.mkdir(parents=True, exist_ok=True)


app = create_app()


def _mask_api_key(api_key: str | None) -> str:
    if not api_key:
        return "unset"
    prefix = api_key[:5]
    return f"{prefix}..."


def _update_dataset_ids(node: dict[str, Any], new_id: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "dataset_ids" and isinstance(value, list):
                node[key] = [new_id]
            else:
                _update_dataset_ids(value, new_id)
    elif isinstance(node, list):
        for item in node:
            _update_dataset_ids(item, new_id)


def _log_existing_credentials(tenant_id: str) -> None:
    creds = (
        db.session.query(ProviderCredential)
        .filter(ProviderCredential.tenant_id == tenant_id)
        .all()
    )
    provider_names: list[str] = [
        credential.provider_name for credential in creds
    ]
    logger.debug(
        "All credentials for tenant %s: %s", tenant_id, provider_names
    )


def _load_upload_cache() -> dict[str, Any]:
    if not UPLOAD_CACHE_PATH.exists():
        return {}

    try:
        return json.loads(UPLOAD_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - cache failures are non-fatal
        logger.warning(
            "Failed to read upload cache at %s",
            UPLOAD_CACHE_PATH,
            exc_info=True,
        )
        return {}


def _save_upload_cache(cache: dict[str, Any]) -> None:
    try:
        UPLOAD_CACHE_PATH.write_text(
            json.dumps(cache, indent=2),
            encoding="utf-8",
        )
    except Exception:  # pragma: no cover - cache failures are non-fatal
        logger.warning(
            "Failed to write upload cache at %s",
            UPLOAD_CACHE_PATH,
            exc_info=True,
        )


def _get_cached_upload(
    cache: dict[str, Any],
    *,
    key: str,
    path: Path,
    file_hash: str,
) -> UploadFile | None:
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None

    if entry.get("hash") != file_hash:
        return None

    if entry.get("path") != str(path):
        return None

    upload_file_id = entry.get("upload_file_id")
    if not upload_file_id:
        return None

    upload_file = (
        db.session.query(UploadFile)
        .filter(UploadFile.id == upload_file_id)
        .first()
    )

    if not upload_file:
        return None

    if upload_file.hash != file_hash:
        return None

    return upload_file


def _get_dsl_path() -> Path:
    """Return the DSL path, allowing override via ESSAY_DSL_PATH env var."""
    custom_path = os.environ.get("ESSAY_DSL_PATH")
    if custom_path:
        return Path(custom_path)
    return DSL_PATH


def run() -> None:
    with app.app_context():
        admin = (
            db.session.query(Account)
            .filter(Account.email == "admin@dify.ai")
            .first()
        )
        if not admin:
            admin = db.session.query(Account).first()
            if not admin:
                logger.error(
                    "No account found; create an admin account before running."
                )
                return

        logger.info("Using account: %s (ID: %s)", admin.email, admin.id)

        tenant_account_join = (
            db.session.query(TenantAccountJoin)
            .filter(TenantAccountJoin.account_id == admin.id)
            .first()
        )
        if not tenant_account_join:
            logger.error("No tenant found for this account.")
            return

        tenant = (
            db.session.query(Tenant)
            .filter(Tenant.id == tenant_account_join.tenant_id)
            .first()
        )
        admin.current_tenant = tenant
        logger.info("Using tenant: %s (ID: %s)", tenant.name, tenant.id)

        file_service = FileService(db.engine)
        files_to_upload = {
            "student_essay": STUDENT_ESSAY_PATH,
            "mark_scheme": MARK_SCHEME_PATH,
            "reference_essay": REFERENCE_ESSAY_PATH,
        }

        upload_cache = _load_upload_cache()

        uploaded_files = {}
        for key, path in files_to_upload.items():
            if not path.exists():
                logger.error("File not found: %s", path)
                return

            file_bytes = path.read_bytes()
            file_hash = hashlib.sha3_256(file_bytes).hexdigest()

            cached_upload = _get_cached_upload(
                upload_cache,
                key=key,
                path=path,
                file_hash=file_hash,
            )
            if cached_upload:
                uploaded_files[key] = cached_upload
                logger.info("Reused %s: %s", key, cached_upload.id)
                continue

            upload_file = file_service.upload_file(
                filename=path.name,
                content=file_bytes,
                mimetype="application/pdf",
                user=admin,
            )
            uploaded_files[key] = upload_file
            upload_cache[key] = {
                "path": str(path),
                "hash": file_hash,
                "upload_file_id": str(upload_file.id),
            }
            logger.info("Uploaded %s: %s", key, upload_file.id)

        _save_upload_cache(upload_cache)

        if not TEACHER_COMMENT_PATH.exists():
            logger.error("File not found: %s", TEACHER_COMMENT_PATH)
            return

        teacher_comment = TEACHER_COMMENT_PATH.read_text(encoding="utf-8")

        dataset_id_env = os.environ.get("ESSAY_DATASET_ID")
        dataset_name_env = os.environ.get("ESSAY_DATASET_NAME", "ecom_essay")

        if dataset_id_env:
            dataset = (
                db.session.query(Dataset)
                .filter(
                    Dataset.tenant_id == tenant.id,
                    Dataset.id == dataset_id_env,
                )
                .first()
            )

            if not dataset:
                logger.error(
                    "Dataset with id %s not found for tenant %s",
                    dataset_id_env,
                    tenant.id,
                )
                return

            logger.info(
                "Using existing dataset by id: %s (%s)",
                dataset.id,
                dataset.name,
            )
        else:
            dataset = (
                db.session.query(Dataset)
                .filter(
                    Dataset.tenant_id == tenant.id,
                    Dataset.name == dataset_name_env,
                )
                .first()
            )

            if not dataset:
                logger.info("Creating dataset '%s'...", dataset_name_env)
                dataset = DatasetService.create_empty_dataset(
                    tenant_id=tenant.id,
                    name=dataset_name_env,
                    description="Economics Essay Knowledge Base",
                    indexing_technique="high_quality",
                    account=admin,
                    permission="only_me",
                )
                logger.info("Dataset created: %s", dataset.id)
                logger.debug(
                    "DatasetService attributes: %s", dir(DatasetService)
                )

                file_ids = [
                    uploaded_files["student_essay"].id,
                    uploaded_files["mark_scheme"].id,
                    uploaded_files["reference_essay"].id,
                ]

                knowledge_config = KnowledgeConfig(
                    indexing_technique="high_quality",
                    data_source=DataSource(
                        info_list=InfoList(
                            data_source_type="upload_file",
                            file_info_list=FileInfo(file_ids=file_ids),
                        )
                    ),
                    process_rule=ProcessRule(
                        mode="custom",
                        rules=Rule(
                            segmentation=Segmentation(
                                max_tokens=500,
                                chunk_overlap=50,
                            )
                        ),
                    ),
                    doc_form="text_model",
                    doc_language="English",
                    embedding_model="text-embedding-3-small",
                    embedding_model_provider=(
                        "langgenius/openai_api_compatible/"
                        "openai_api_compatible"
                    ),
                )

                _, batch = DatasetService.save_document_with_dataset_id(
                    dataset=dataset,
                    knowledge_config=knowledge_config,
                    account=admin,
                )
                logger.info("Documents added to dataset. Batch: %s", batch)
            else:
                logger.info("Using existing dataset by name: %s", dataset.id)

        dsl_path = _get_dsl_path()
        if not dsl_path.exists():
            logger.error("File not found: %s", dsl_path)
            return

        yaml_content = dsl_path.read_text(encoding="utf-8")
        dsl_data = yaml.safe_load(yaml_content)
        _update_dataset_ids(dsl_data, dataset.id)
        yaml_content = yaml.dump(dsl_data)

        try:
            dependencies = dsl_data.get("dependencies", [])
            plugin_identifiers = []
            for dependency in dependencies:
                if dependency.get("type") == "marketplace":
                    value = dependency.get("value", {})
                    identifier = value.get(
                        "marketplace_plugin_unique_identifier"
                    )
                    if identifier:
                        plugin_identifiers.append(identifier)

            if plugin_identifiers:
                logger.info("Found plugins to install: %s", plugin_identifiers)
                for identifier in plugin_identifiers:
                    try:
                        logger.info("Installing plugin: %s", identifier)
                        PluginService.install_from_marketplace_pkg(
                            tenant.id, [identifier]
                        )
                        logger.info(
                            "Plugin %s installed successfully.", identifier
                        )
                    except Exception as exc:
                        if "already installed" in str(exc):
                            logger.info(
                                "Plugin %s already installed.", identifier
                            )
                        else:
                            logger.exception(
                                "Failed to install plugin %s", identifier
                            )

            try:
                rsa.get_decrypt_decoding(tenant.id)
            except Exception:
                logger.info("Generating RSA key for tenant %s", tenant.id)
                rsa.generate_key_pair(tenant.id)

            model_provider_service = ModelProviderService()
            api_key = os.environ.get("UIUIAPI_API_KEY")
            base_url = os.environ.get("UIUIAPI_BASE_URL")

            if not api_key or not base_url:
                logger.error(
                    "UIUIAPI_API_KEY and UIUIAPI_BASE_URL must be set before "
                    "running."
                )
                return

            logger.info(
                "API Key: %s (%s chars)", _mask_api_key(api_key), len(api_key)
            )
            logger.info("Base URL: %s", base_url)

            try:
                logger.info("Testing connection to models endpoint...")
                headers = {"Authorization": f"Bearer {api_key}"}
                response = requests.get(
                    f"{base_url}/models", headers=headers, timeout=10
                )
                logger.info(
                    "Models endpoint response: %s", response.status_code
                )
            except Exception:
                logger.exception("Models endpoint failed")

            providers: dict[str, dict[str, str]] = {
                "langgenius/openai_api_compatible/openai_api_compatible": {
                    "api_key": api_key,
                    "openai_api_key": api_key,
                    "endpoint_url": base_url,
                    "model": "gpt-4.1",
                    "model_type": "llm",
                    "context_size": "4096",
                    "max_tokens": "4096",
                    "mode": "chat",
                    "name": "gpt-4.1",
                }
            }

            for provider_name, credentials in providers.items():
                provider_creds = credentials.copy()
                try:
                    try:
                        model_provider_service._get_provider_configuration(
                            tenant.id,
                            provider_name,
                        )
                    except Exception:
                        logger.warning(
                            "Provider %s not found, skipping credentials.",
                            provider_name,
                        )
                        continue

                    if "openai_api_compatible" in provider_name:
                        model_creds = provider_creds.copy()
                        model_name = model_creds.pop("model")
                        model_type = model_creds.pop("model_type")
                        model_creds.pop("name", None)

                        if "endpoint_model_name" not in model_creds:
                            model_creds["endpoint_model_name"] = model_name

                        config_json = json.dumps(model_creds)
                        encrypted_config = encrypter.encrypt_token(
                            tenant.id,
                            config_json,
                        )

                        provider_model = (
                            db.session.query(ProviderModel)
                            .filter(
                                ProviderModel.tenant_id == tenant.id,
                                ProviderModel.provider_name == provider_name,
                                ProviderModel.model_name == model_name,
                                ProviderModel.model_type == model_type,
                            )
                            .first()
                        )

                        if not provider_model:
                            provider_model = ProviderModel(
                                tenant_id=tenant.id,
                                provider_name=provider_name,
                                model_name=model_name,
                                model_type=model_type,
                                is_valid=True,
                            )
                            db.session.add(provider_model)
                            db.session.flush()

                        credential = None
                        if provider_model.credential_id:
                            credential = (
                                db.session.query(ProviderModelCredential)
                                .filter(
                                    ProviderModelCredential.id
                                    == provider_model.credential_id
                                )
                                .first()
                            )

                        if not credential:
                            credential = ProviderModelCredential(
                                tenant_id=tenant.id,
                                provider_name=provider_name,
                                model_name=model_name,
                                model_type=model_type,
                                credential_name="default",
                                encrypted_config=encrypted_config,
                            )
                            db.session.add(credential)
                            db.session.flush()
                            provider_model.credential_id = credential.id
                        else:
                            credential.encrypted_config = encrypted_config

                        provider_model.is_valid = True
                        db.session.commit()
                        logger.info(
                            "Manually set credentials for %s model %s",
                            provider_name,
                            model_name,
                        )

                        provider_creds.pop("model", None)
                        provider_creds.pop("model_type", None)
                        provider_creds.pop("name", None)

                    _log_existing_credentials(tenant.id)

                    existing_creds = (
                        db.session.query(ProviderCredential)
                        .filter(
                            ProviderCredential.tenant_id == tenant.id,
                            ProviderCredential.provider_name == provider_name,
                        )
                        .all()
                    )

                    if existing_creds:
                        logger.info(
                            "Deleting %s existing credentials for %s",
                            len(existing_creds),
                            provider_name,
                        )
                        for credential in existing_creds:
                            db.session.delete(credential)

                        provider_record = (
                            db.session.query(Provider)
                            .filter(
                                Provider.tenant_id == tenant.id,
                                Provider.provider_name == provider_name,
                            )
                            .first()
                        )
                        if provider_record:
                            provider_record.credential_id = None
                            provider_record.is_valid = False

                        db.session.commit()

                    try:
                        model_provider_service.create_provider_credential(
                            tenant_id=tenant.id,
                            provider=provider_name,
                            credentials=provider_creds,
                            credential_name="default",
                        )
                        logger.info("Set credentials for %s", provider_name)
                    except Exception:
                        logger.exception(
                            "Failed to set credentials for %s", provider_name
                        )
                except Exception:
                    logger.exception(
                        "Error handling provider %s", provider_name
                    )

            try:
                embedding_provider = (
                    "langgenius/openai_api_compatible/openai_api_compatible"
                )
                embedding_model = "text-embedding-3-large"
                embedding_creds = {
                    "api_key": api_key,
                    "openai_api_key": api_key,
                    "endpoint_url": base_url,
                    "endpoint_model_name": embedding_model,
                    "context_size": "4096",
                    "max_tokens": "4096",
                }

                config_json = json.dumps(embedding_creds)
                encrypted_config = encrypter.encrypt_token(
                    tenant.id,
                    config_json,
                )

                provider_model = (
                    db.session.query(ProviderModel)
                    .filter(
                        ProviderModel.tenant_id == tenant.id,
                        ProviderModel.provider_name == embedding_provider,
                        ProviderModel.model_name == embedding_model,
                        ProviderModel.model_type == "text-embedding",
                    )
                    .first()
                )

                if not provider_model:
                    provider_model = ProviderModel(
                        tenant_id=tenant.id,
                        provider_name=embedding_provider,
                        model_name=embedding_model,
                        model_type="text-embedding",
                        is_valid=True,
                    )
                    db.session.add(provider_model)
                    db.session.flush()

                credential = None
                if provider_model.credential_id:
                    credential = (
                        db.session.query(ProviderModelCredential)
                        .filter(
                            ProviderModelCredential.id
                            == provider_model.credential_id
                        )
                        .first()
                    )

                if not credential:
                    credential = ProviderModelCredential(
                        tenant_id=tenant.id,
                        provider_name=embedding_provider,
                        model_name=embedding_model,
                        model_type="text-embedding",
                        credential_name="default",
                        encrypted_config=encrypted_config,
                    )
                    db.session.add(credential)
                    db.session.flush()
                    provider_model.credential_id = credential.id
                else:
                    credential.encrypted_config = encrypted_config

                provider_model.is_valid = True
                db.session.commit()
                logger.info(
                    "Manually set embedding model credentials for %s",
                    embedding_model,
                )

                pm = (
                    db.session.query(ProviderModel)
                    .filter(
                        ProviderModel.tenant_id == tenant.id,
                        ProviderModel.provider_name == embedding_provider,
                        ProviderModel.model_name == embedding_model,
                    )
                    .first()
                )
                if pm:
                    logger.info(
                        "Verified ProviderModel: %s %s %s valid=%s",
                        pm.provider_name,
                        pm.model_name,
                        pm.model_type,
                        pm.is_valid,
                    )

                pmc = None
                if pm and pm.credential_id:
                    pmc = (
                        db.session.query(ProviderModelCredential)
                        .filter(ProviderModelCredential.id == pm.credential_id)
                        .first()
                    )
                if pmc:
                    logger.info(
                        "Verified Credential: %s %s %s",
                        pmc.provider_name,
                        pmc.model_name,
                        pmc.model_type,
                    )

            except Exception:
                logger.exception("Failed to set embedding model credentials")

            # Configure rerank model credentials for gte-rerank-v2
            try:
                rerank_provider = (
                    "langgenius/openai_api_compatible/openai_api_compatible"
                )
                rerank_model = "gte-rerank-v2"
                rerank_creds = {
                    "api_key": api_key,
                    "openai_api_key": api_key,
                    "endpoint_url": base_url,
                    "endpoint_model_name": rerank_model,
                    "context_size": "4096",
                    "max_tokens": "4096",
                }

                config_json = json.dumps(rerank_creds)
                encrypted_config = encrypter.encrypt_token(
                    tenant.id,
                    config_json,
                )

                provider_model = (
                    db.session.query(ProviderModel)
                    .filter(
                        ProviderModel.tenant_id == tenant.id,
                        ProviderModel.provider_name == rerank_provider,
                        ProviderModel.model_name == rerank_model,
                        ProviderModel.model_type == "rerank",
                    )
                    .first()
                )

                if not provider_model:
                    provider_model = ProviderModel(
                        tenant_id=tenant.id,
                        provider_name=rerank_provider,
                        model_name=rerank_model,
                        model_type="rerank",
                        is_valid=True,
                    )
                    db.session.add(provider_model)
                    db.session.flush()

                credential = None
                if provider_model.credential_id:
                    credential = (
                        db.session.query(ProviderModelCredential)
                        .filter(
                            ProviderModelCredential.id
                            == provider_model.credential_id
                        )
                        .first()
                    )

                if not credential:
                    credential = ProviderModelCredential(
                        tenant_id=tenant.id,
                        provider_name=rerank_provider,
                        model_name=rerank_model,
                        model_type="rerank",
                        credential_name="default",
                        encrypted_config=encrypted_config,
                    )
                    db.session.add(credential)
                    db.session.flush()
                    provider_model.credential_id = credential.id
                else:
                    credential.encrypted_config = encrypted_config

                provider_model.is_valid = True
                db.session.commit()
                logger.info(
                    "Manually set rerank model credentials for %s",
                    rerank_model,
                )

            except Exception:
                logger.exception("Failed to set rerank model credentials")

        except Exception:
            logger.exception("Failed to parse/import workflow dependencies")

        app_dsl_service = AppDslService(db.session)

        logger.info("Importing app...")
        import_result = app_dsl_service.import_app(
            account=admin,
            import_mode="yaml-content",
            yaml_content=yaml_content,
        )

        if import_result.status == "failed":
            logger.error("Import failed: %s", import_result.error)
            return

        app_id = import_result.app_id
        logger.info("Imported App ID: %s", app_id)

        imported_app = db.session.query(App).filter(App.id == app_id).first()
        if not imported_app:
            logger.error("App not found in DB after import")
            return

        logger.info("App Name: %s", imported_app.name)

        inputs = {
            "student_essay": {
                "transfer_method": "local_file",
                "upload_file_id": str(uploaded_files["student_essay"].id),
                "type": "document",
            },
            "mark_scheme": {
                "transfer_method": "local_file",
                "upload_file_id": str(uploaded_files["mark_scheme"].id),
                "type": "document",
            },
            "reference_essay": {
                "transfer_method": "local_file",
                "upload_file_id": str(uploaded_files["reference_essay"].id),
                "type": "document",
            },
            "tearcher_comment": teacher_comment,
            "diagram_credit": 0,
            "upper_limit": 6,
        }

        files_arg = [
            {
                "transfer_method": "local_file",
                "upload_file_id": str(uploaded_files["student_essay"].id),
                "type": "document",
            },
            {
                "transfer_method": "local_file",
                "upload_file_id": str(uploaded_files["mark_scheme"].id),
                "type": "document",
            },
            {
                "transfer_method": "local_file",
                "upload_file_id": str(uploaded_files["reference_essay"].id),
                "type": "document",
            },
        ]

        args = {
            "inputs": inputs,
            "query": "Test Run",
            "files": files_arg,
            "conversation_id": None,
        }

        admin_id = admin.id
        tenant_id = tenant.id

        logger.info("Starting execution...")
        try:
            db.session.commit()

            admin = (
                db.session.query(Account)
                .filter(Account.id == admin_id)
                .first()
            )
            tenant = (
                db.session.query(Tenant)
                .filter(Tenant.id == tenant_id)
                .first()
            )
            imported_app = (
                db.session.query(App)
                .filter(App.id == app_id)
                .first()
            )

            if admin:
                _ = admin.id
                _ = admin.email
            if tenant:
                _ = tenant.id
                _ = tenant.name
            if imported_app:
                _ = imported_app.id
                _ = imported_app.name

            db.session.expunge(admin)
            db.session.expunge(tenant)
            db.session.expunge(imported_app)

            admin.current_tenant = tenant

            response = AppGenerateService.generate(
                app_model=imported_app,
                user=admin,
                args=args,
                invoke_from=InvokeFrom.DEBUGGER,
                streaming=True,
            )

            if isinstance(response, dict):
                logger.info("Response: %s", response)
            else:
                logger.info("Streaming response:")
                for chunk in response:
                    logger.info("%s", chunk)

        except Exception:
            logger.exception("Execution failed")


if __name__ == "__main__":
    run()


import json
import os
import sys

# Set environment variables
os.environ['STORAGE_TYPE'] = 'local'
os.environ['STORAGE_LOCAL_PATH'] = 'storage'
os.environ['DB_HOST'] = 'localhost'
os.environ['DB_PORT'] = '5432'
os.environ['DB_USERNAME'] = 'postgres'
os.environ['DB_PASSWORD'] = 'difyai123456'
os.environ['DB_DATABASE'] = 'dify'

from app_factory import create_app
from extensions.ext_database import db
from models.workflow import Workflow

app = create_app()


def dump_dsl(app_id):
    with app.app_context():
        workflow = db.session.query(Workflow).filter(Workflow.app_id == app_id).order_by(Workflow.created_at.desc()).first()
        if workflow:
            print(json.dumps(workflow.graph_dict, indent=2, ensure_ascii=False))
        else:
            print(json.dumps({"error": "No workflow found"}))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        dump_dsl(sys.argv[1])

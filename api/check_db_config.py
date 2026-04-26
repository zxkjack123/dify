
from sqlalchemy import text

from app_factory import create_app
from extensions.ext_database import db

app = create_app()

with app.app_context():
    print(f"DB URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
    try:
        with db.engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("Connection successful:", result.fetchone())
    except Exception as e:
        print("Connection failed:", e)

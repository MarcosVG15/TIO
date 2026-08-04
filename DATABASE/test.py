from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

def main():
    
    if not DATABASE_URL:
        print("DATABASE_URL is not set")
        return 1

    engine = create_engine(DATABASE_URL, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("connected ok")
        return 0
    except Exception as exc:
        print(f"connection failed: {exc.__class__.__name__}: {exc}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

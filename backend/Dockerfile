FROM python:3.12-slim

WORKDIR /app

# 依存だけ先に入れてレイヤキャッシュを効かせる
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# 起動時に init_db が alembic upgrade head ＋ シードを実行する
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
